"""Small REST adapters; no provider SDK or fixed model-version catalogue."""
import os
import re
from urllib.parse import quote

import requests

PROVIDERS = ('OpenAI', 'Anthropic', 'Gemini', 'Ollama')


class ProviderError(ValueError):
    pass


class Provider:
    def __init__(self, name, key=''):
        if name not in PROVIDERS:
            raise ProviderError('Unknown provider.')
        if name != 'Ollama' and not key:
            raise ProviderError('Save an API key for this provider in Settings first.')
        self.name = name
        # Never allow a user-supplied endpoint to receive another provider's secret.
        self.base = {
            'OpenAI': 'https://api.openai.com/v1',
            'Anthropic': 'https://api.anthropic.com/v1',
            'Gemini': 'https://generativelanguage.googleapis.com/v1beta',
            'Ollama': os.getenv('OLLAMA_BASE_URL', 'http://127.0.0.1:11434').rstrip('/'),
        }[name]
        self.headers = {'Content-Type': 'application/json'}
        if name == 'OpenAI':
            self.headers['Authorization'] = f'Bearer {key}'
        elif name == 'Anthropic':
            self.headers.update({'x-api-key': key, 'anthropic-version': '2023-06-01'})
        elif name == 'Gemini':
            self.headers['x-goog-api-key'] = key

    def request(self, method, path, payload=None, params=None):
        try:
            response = requests.request(method, self.base + path, headers=self.headers,
                                        json=payload, params=params, timeout=(10, 180),
                                        allow_redirects=False)
        except requests.RequestException:
            raise ProviderError('Could not reach the provider. Check your connection or Ollama server.') from None
        if not 200 <= response.status_code < 300:
            messages = {400: 'Request rejected. Check the model ID, context size and output limit.',
                        401: 'API key rejected. Replace it in Settings.',
                        403: 'Your account does not have access to this model.',
                        404: 'Model or endpoint not found. Refresh models or check the exact version.',
                        429: 'Provider rate limit or quota reached. Check billing, then try later.'}
            # Do not expose response bodies, which may echo secrets or prompts.
            raise ProviderError(messages.get(response.status_code,
                                f'Provider request failed (HTTP {response.status_code}).'))
        try:
            return response.json()
        except ValueError:
            raise ProviderError('Provider returned an unreadable response.') from None

    def models(self):
        names, cursor = [], None
        for _ in range(30):
            if self.name == 'Ollama':
                data = self.request('GET', '/api/tags')
                names = [m['name'] for m in data.get('models', [])]
                break
            params = {}
            if self.name == 'Anthropic':
                params = {'limit': 1000}
                if cursor:
                    params['after_id'] = cursor
            elif self.name == 'Gemini':
                params = {'pageSize': 1000}
                if cursor:
                    params['pageToken'] = cursor
            data = self.request('GET', '/models', params=params)
            if self.name == 'Gemini':
                names.extend(m['name'].removeprefix('models/') for m in data.get('models', [])
                             if 'generateContent' in m.get('supportedGenerationMethods', []))
                cursor = data.get('nextPageToken')
            else:
                names.extend(m['id'] for m in data.get('data', []))
                cursor = data.get('last_id') if data.get('has_more') else None
            if not cursor or self.name == 'OpenAI':
                break
        return sorted(set(names))

    def generate(self, model, system, messages, max_tokens=2048):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@-]{0,199}', model):
            raise ProviderError('Choose or enter a valid model ID first.')
        clean = [{'role': m['role'], 'content': m['content']} for m in messages]
        if self.name == 'OpenAI':
            data = self.request('POST', '/responses', {
                'model': model, 'instructions': system, 'input': clean,
                'max_output_tokens': max_tokens, 'store': False})
            text = '\n'.join(c.get('text', '') for item in data.get('output', [])
                             if item.get('type') == 'message' for c in item.get('content', [])
                             if c.get('type') == 'output_text')
            truncated = data.get('status') == 'incomplete'
        elif self.name == 'Anthropic':
            data = self.request('POST', '/messages', {
                'model': model, 'system': system, 'messages': clean, 'max_tokens': max_tokens})
            text = '\n'.join(c.get('text', '') for c in data.get('content', []) if c.get('type') == 'text')
            truncated = data.get('stop_reason') == 'max_tokens'
        elif self.name == 'Gemini':
            data = self.request('POST', '/models/' + quote(model.removeprefix('models/'), safe='') + ':generateContent', {
                'systemInstruction': {'parts': [{'text': system}]},
                'contents': [{'role': 'model' if m['role'] == 'assistant' else 'user',
                              'parts': [{'text': m['content']}]} for m in clean],
                'generationConfig': {'maxOutputTokens': max_tokens}})
            candidates = data.get('candidates', [])
            candidate = candidates[0] if candidates else {}
            text = '\n'.join(p.get('text', '') for p in candidate.get('content', {}).get('parts', [])
                             if not p.get('thought'))
            truncated = candidate.get('finishReason') == 'MAX_TOKENS'
        else:
            data = self.request('POST', '/api/chat', {
                'model': model, 'messages': [{'role': 'system', 'content': system}] + clean,
                'stream': False, 'options': {'num_predict': max_tokens}})
            text = data.get('message', {}).get('content', '')
            truncated = data.get('done_reason') == 'length'
        if not text.strip():
            raise ProviderError('No answer text returned. The model may have refused, exhausted its '
                                'reasoning budget, or not support text generation. Try a different model or higher output limit.')
        return {'text': text, 'truncated': truncated}
