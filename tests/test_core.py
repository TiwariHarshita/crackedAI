import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from tutor.learning import bounded_history, parse_quiz
from tutor.providers import Provider, ProviderError
from tutor.security import encrypt, decrypt, vault_key
from tutor.store import Store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'tutor.db')
        self.store.register('alice', 'alice-password-123')
        self.store.register('bobby', 'bobby-password-456')
        self.alice = self.store.login('alice', 'alice-password-123')
        self.bob = self.store.login('bobby', 'bobby-password-456')

    def tearDown(self):
        self.tmp.cleanup()

    def test_keys_are_encrypted_and_isolated(self):
        a, b = self.alice, self.bob
        self.store.save_key(a['id'], 'OpenAI', a['key'], 'sk-private-test')
        self.assertEqual(self.store.get_key(a['id'], 'OpenAI', a['key']), 'sk-private-test')
        self.assertEqual(self.store.get_key(b['id'], 'OpenAI', b['key']), '')
        with self.store.db() as db:
            ciphertext = db.execute('SELECT ciphertext FROM credentials').fetchone()[0]
        self.assertNotIn('sk-private-test', ciphertext)
        with self.assertRaises(ValueError):
            decrypt(b['key'], ciphertext)

    def test_sessions_cannot_be_read_overwritten_or_deleted_by_other_user(self):
        a, b = self.alice['id'], self.bob['id']
        messages = [{'role': 'user', 'content': 'private question'}]
        self.store.save_session(a, 'session1', messages)
        with self.assertRaises(ValueError):
            self.store.load_session(b, 'session1')
        with self.assertRaises(ValueError):
            self.store.save_session(b, 'session1', [])
        self.store.delete_session(b, 'session1')
        self.assertEqual(self.store.load_session(a, 'session1'), messages)
        self.assertEqual(self.store.sessions(b), [])

    def test_key_replacement_and_deletion(self):
        a = self.alice
        for value in ('key-one', 'key-two'):
            self.store.save_key(a['id'], 'Gemini', a['key'], value)
        self.assertEqual(self.store.get_key(a['id'], 'Gemini', a['key']), 'key-two')
        self.store.delete_key(a['id'], 'Gemini')
        self.assertEqual(self.store.get_key(a['id'], 'Gemini', a['key']), '')

    def test_quiz_submission_is_idempotent(self):
        a = self.alice['id']
        for _ in range(2):
            self.store.save_attempt(a, 'attempt1', 'trees', 3, 5, 'Ollama', 'model:tag')
        self.assertEqual(len(self.store.attempts(a)), 1)
        self.assertEqual(self.store.attempts(self.bob['id']), [])

    def test_bad_login_and_lockout(self):
        for _ in range(5):
            with self.assertRaisesRegex(ValueError, 'Invalid'):
                self.store.login('alice', 'wrong')
        with self.assertRaisesRegex(ValueError, 'Too many'):
            self.store.login('alice', 'alice-password-123')

    def test_account_deletion_cascades(self):
        a = self.alice
        self.store.save_key(a['id'], 'OpenAI', a['key'], 'secret')
        self.store.save_session(a['id'], 's', [{'role': 'user', 'content': 'hi'}])
        self.store.save_attempt(a['id'], 'q', 'trees', 2, 3, 'OpenAI', 'model')
        self.store.delete_account(a['id'])
        self.assertEqual(self.store.sessions(a['id']), [])
        self.assertEqual(self.store.attempts(a['id']), [])
        self.assertEqual(self.store.get_key(a['id'], 'OpenAI', a['key']), '')

    def test_credentials_survive_restart(self):
        a = self.alice
        self.store.save_key(a['id'], 'OpenAI', a['key'], 'saved-secret')
        restarted = Store(self.store.path)
        login = restarted.login('alice', 'alice-password-123')
        self.assertEqual(restarted.get_key(login['id'], 'OpenAI', login['key']), 'saved-secret')


class QuizTests(unittest.TestCase):
    def setUp(self):
        self.question = {'question': '2+2?', 'options': ['1', '2', '3', '4'],
                         'answer': 3, 'explanation': 'Two plus two is four.'}

    def test_valid_fenced_json(self):
        text = '```json\n' + json.dumps({'questions': [self.question]}) + '\n```'
        self.assertEqual(parse_quiz(text, 1)[0]['answer'], 3)

    def test_rejects_invalid_quizzes(self):
        for field, value in [('answer', 4), ('answer', True), ('options', ['a', 'a', 'b', 'c']),
                             ('explanation', ''), ('question', None)]:
            q = dict(self.question, **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                parse_quiz(json.dumps({'questions': [q]}), 1)
        for raw in ['not json', '{}', '[]', '{"questions":[]}']:
            with self.assertRaises(ValueError):
                parse_quiz(raw, 1)

    def test_history_has_no_orphan_assistant(self):
        history = [{'role': 'user', 'content': 'abc'}, {'role': 'assistant', 'content': 'def'},
                   {'role': 'user', 'content': 'ghi'}]
        self.assertEqual(bounded_history(history, 6), [history[-1]])


class ProviderTests(unittest.TestCase):
    @patch('tutor.providers.requests.request')
    def test_cloud_request_formats(self, request):
        examples = {
            'OpenAI': {'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'answer'}]}]},
            'Anthropic': {'content': [{'type': 'text', 'text': 'answer'}]},
            'Gemini': {'candidates': [{'content': {'parts': [{'text': 'answer'}]}}]},
            'Ollama': {'message': {'content': 'answer'}}}
        for name, data in examples.items():
            with self.subTest(name=name):
                request.return_value = Mock(status_code=200, json=lambda: data)
                result = Provider(name, 'fake-secret').generate('model-v1', 'system',
                         [{'role': 'user', 'content': 'question', 'provider': 'metadata'}])
                self.assertEqual(result['text'], 'answer')
                args, kwargs = request.call_args
                self.assertNotIn('fake-secret', args[1])
                self.assertFalse(kwargs['allow_redirects'])
                self.assertNotIn('metadata', json.dumps(kwargs['json']))
                if name == 'OpenAI':
                    self.assertFalse(kwargs['json']['store'])
                    self.assertTrue(args[1].endswith('/responses'))
                if name == 'Ollama':
                    self.assertNotIn('Authorization', kwargs['headers'])
                if name == 'Gemini':
                    self.assertEqual(kwargs['headers']['x-goog-api-key'], 'fake-secret')

    @patch('tutor.providers.requests.request')
    def test_errors_do_not_echo_secrets(self, request):
        request.return_value = Mock(status_code=401, text='sk-SECRET')
        with self.assertRaises(ProviderError) as error:
            Provider('OpenAI', 'sk-SECRET').models()
        self.assertNotIn('sk-SECRET', str(error.exception))

    @patch('tutor.providers.requests.request')
    def test_gemini_pagination_and_capability_filter(self, request):
        request.side_effect = [
            Mock(status_code=200, json=lambda: {'models': [
                {'name': 'models/chat-v1', 'supportedGenerationMethods': ['generateContent']},
                {'name': 'models/embedding', 'supportedGenerationMethods': ['embedContent']}], 'nextPageToken': 'next'}),
            Mock(status_code=200, json=lambda: {'models': [
                {'name': 'models/chat-v2', 'supportedGenerationMethods': ['generateContent']}]})]
        self.assertEqual(Provider('Gemini', 'key').models(), ['chat-v1', 'chat-v2'])
        self.assertEqual(request.call_args.kwargs['params']['pageToken'], 'next')

    @patch('tutor.providers.requests.request')
    def test_anthropic_pagination(self, request):
        request.side_effect = [
            Mock(status_code=200, json=lambda: {'data': [{'id': 'v1'}], 'has_more': True, 'last_id': 'v1'}),
            Mock(status_code=200, json=lambda: {'data': [{'id': 'v2'}], 'has_more': False})]
        self.assertEqual(Provider('Anthropic', 'key').models(), ['v1', 'v2'])
        self.assertEqual(request.call_args.kwargs['params']['after_id'], 'v1')

    @patch('tutor.providers.requests.request')
    def test_empty_and_truncated_responses(self, request):
        request.return_value = Mock(status_code=200, json=lambda: {'output': []})
        with self.assertRaises(ProviderError):
            Provider('OpenAI', 'key').generate('v1', 's', [])
        request.return_value = Mock(status_code=200, json=lambda: {
            'message': {'content': 'partial'}, 'done_reason': 'length'})
        self.assertTrue(Provider('Ollama').generate('v1', 's', [])['truncated'])


if __name__ == '__main__':
    unittest.main()
