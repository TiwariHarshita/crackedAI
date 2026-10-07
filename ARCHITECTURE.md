# Architecture

## Request flow

Streamlit renders the interface and runs Python on the application server. A signed-in session contains the account ID and the derived vault key in server memory. It does not put provider keys in browser localStorage or send keys back to the browser after saving.

For tutoring, the UI selects a provider, decrypts only that provider's saved credential, builds the teaching prompt, and sends recent conversation context through the matching REST adapter. Successful question/answer pairs are stored in SQLite with provider and model metadata. Failed generations are not saved as completed exchanges.

Context is bounded to the most recent 24,000 characters and begins with a user message. This is a character budget, not a tokenizer-specific guarantee. The current prompt has a 6,000-character limit. Older messages remain visible and exportable, even when they are no longer sent to the provider. Changing provider sends the retained recent context to the newly selected provider.

For quizzes, the model returns JSON. The parser checks question count, four distinct options, a zero-based integer answer index, and an explanation. Only a validated quiz is rendered. Correct answers are kept server-side until submission. A quiz attempt UUID makes repeat submission idempotent. The database stores score/topic/model metadata; full active quiz content stays in the session and can be exported after submission. A browser-session reset loses an unfinished quiz.

## Data model

| Table | Contents | Scope |
| --- | --- | --- |
| users | UUID, normalized username, salt, password verifier | One record per account |
| credentials | Account ID, provider, encrypted API key | Unique account/provider pair |
| sessions | Conversation JSON, title, timestamp | Account-owned study sessions |
| attempts | Score, topic, provider/model, timestamp | Account-owned quiz history |
| login_attempts | Failed-login count, lockout timestamp | Normalized username |

All queries that access saved keys, sessions or attempts include the signed-in account ID. SQLite foreign keys cascade account deletion. Database calls use parameters rather than string-concatenated SQL. Connections are short-lived and have a busy timeout.

## Provider adapter choices

The adapters use requests rather than several vendor SDKs, keeping the install small. They return a common `{text, truncated}` object. Connection and response timeouts are finite; redirects are disabled; upstream error bodies are not displayed. There is no automatic provider/model fallback and no automatic paid-request retry.

OpenAI uses `store: false` on the Responses API. This does not promise zero provider-side retention; the provider's account policies still apply. Anthropic uses a system field separately from messages. Gemini maps assistant messages to its `model` role. Ollama uses its local chat endpoint and `stream: false`.

No tools or code-execution permissions are given to generated content. User text is rendered as Markdown with Streamlit's default HTML restrictions; only a fixed application stylesheet uses unsafe HTML.

## Extending this project

To add a provider, extend the provider allowlist, authentication headers, model-discovery parser and generation adapter. Add payload and response tests before enabling it in the UI. Do not implement arbitrary user-controlled base URLs with reused credentials.

For a public service, separate identity, inference, and persistence behind an authenticated backend. Migrate schema changes explicitly, add a real identity provider, central rate limits and quotas, and use managed secrets/key management. See SECURITY.md before deployment.
