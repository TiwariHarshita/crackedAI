"""Run with: python -m streamlit run app.py"""
import csv
import io
import json
import os
import time
import uuid
from pathlib import Path

import streamlit as st

from tutor.learning import bounded_history, parse_quiz, quiz_prompt, tutor_prompt
from tutor.providers import PROVIDERS, Provider
from tutor.store import Store

st.set_page_config(page_title='CrackedAI', page_icon='✦', layout='wide')
st.markdown('''<style>
.stApp {background: #f7f8fc;}
h1,h2,h3 {letter-spacing: -.035em;}
[data-testid="stSidebar"] {background: #edf0fa;}
.block-container {max-width: 1150px; padding-top: 2.6rem;}
[data-testid="stChatMessage"] {background: white; border: 1px solid #e5e8f2; border-radius: 14px;}
div.stButton > button[kind="primary"] {background:#5452cc; border-color:#5452cc;}
</style>''', unsafe_allow_html=True)
store = Store(Path(os.getenv('TUTOR_DATA_DIR', 'data')) / 'tutor.db')


def reset_session():
    st.session_state.clear()


def notice_error(error):
    st.error(str(error))


if 'user' in st.session_state:
    if time.time() - st.session_state.get('last_active', 0) > 1800:
        reset_session()
        st.info('Your session expired. Sign in again to unlock your keys.')
    else:
        st.session_state.last_active = time.time()

if 'user' not in st.session_state:
    st.caption('CrackedAI / YOUR PERSONAL STUDY SPACE')
    st.title('Understand it. Practise it. Make it stick.')
    st.write('Choose your AI model, work through a topic, and check what you know.')
    left, right = st.columns([1.1, 1], gap='large')
    with left:
        signin, signup = st.tabs(['Sign in', 'Create account'])
        with signin:
            with st.form('login', clear_on_submit=True):
                username = st.text_input('Username', max_chars=32)
                password = st.text_input('Password', type='password', max_chars=256)
                if st.form_submit_button('Open my study space', type='primary'):
                    try:
                        user = store.login(username, password)
                        reset_session()
                        st.session_state.user = user
                        st.session_state.last_active = time.time()
                        st.rerun()
                    except ValueError as e:
                        notice_error(e)
        with signup:
            with st.form('signup', clear_on_submit=True):
                new_user = st.text_input('Choose a username', max_chars=32)
                new_password = st.text_input('Choose a password', type='password', max_chars=256)
                repeat = st.text_input('Repeat password', type='password', max_chars=256)
                st.caption('At least 12 characters. Keep it safe: there is no password recovery.')
                if st.form_submit_button('Create account'):
                    try:
                        if new_password != repeat:
                            raise ValueError('Passwords do not match.')
                        store.register(new_user, new_password)
                        st.success('Account created. You can now sign in.')
                    except ValueError as e:
                        notice_error(e)
    with right:
        with st.container(border=True):
            st.subheader('One space. Your choice of AI.')
            st.write('**Learn** with explanations and follow-up questions.\n\n'
                     '**Practise** with interactive quizzes and answer explanations.\n\n'
                     '**Choose** OpenAI, Claude, Gemini, or local Ollama models.')
            st.caption('API keys are encrypted before saving. Cloud providers receive your prompts '
                       'when you use them. Ollama runs on the machine hosting this app.')
    st.stop()

user = st.session_state.user
uid = user['id']
st.session_state.setdefault('messages', [])
st.session_state.setdefault('sid', uuid.uuid4().hex)


def client(provider):
    return Provider(provider, store.get_key(uid, provider, user['key']) if provider != 'Ollama' else '')


def check_rate():
    now = time.time()
    times = [t for t in st.session_state.get('request_times', []) if now - t < 60]
    if len(times) >= 6:
        raise ValueError('Please wait a moment. Limit: 6 AI requests per minute in this session.')
    st.session_state.request_times = times + [now]


with st.sidebar:
    st.title('✦ CrackedAI')
    st.caption('A little clearer, every session.')
    page = st.radio('Workspace', ['Learn', 'Practise', 'Progress', 'Settings'])
    st.divider()
    provider = st.selectbox('AI provider', PROVIDERS)
    if st.button('Refresh available models', width='stretch'):
        try:
            with st.spinner('Fetching models…'):
                st.session_state['models_' + provider] = client(provider).models()
            if not st.session_state['models_' + provider]:
                st.warning('No models found. Check access, or install an Ollama model.')
        except ValueError as e:
            notice_error(e)
    available = st.session_state.get('models_' + provider, [])
    chosen = st.selectbox('Model / version', ['Enter exact model ID…'] + available, key='chosen_' + provider)
    model = (st.text_input('Exact model ID or version tag', key='model_' + provider,
                           help='Use a provider model ID, dated snapshot, or Ollama model:tag.').strip()
             if chosen == 'Enter exact model ID…' else chosen)
    st.caption('A model version is part of its ID. Availability depends on your provider account. '
               'Choose a text-generation model; not every listed model supports tutoring.')
    level = st.selectbox('Learning level', ['School', 'High school', 'Undergraduate', 'Graduate / research'])
    subject = st.selectbox('Subject', ['Computer science', 'Mathematics', 'Physics', 'Chemistry',
                                      'Biology', 'History', 'Other'])
    if subject == 'Other':
        subject = st.text_input('Your subject', max_chars=120) or 'General studies'
    max_tokens = st.select_slider('Maximum output tokens', [1024, 2048, 4096, 8192], value=4096)
    st.caption('Cloud requests may incur API charges. Reasoning can use part of this token budget.')
    st.divider()
    st.caption(f'Signed in as {user["username"]}')
    if st.button('Sign out', width='stretch'):
        reset_session()
        st.rerun()

if page == 'Settings':
    st.caption('YOUR CONNECTIONS')
    st.title('Bring your own model.')
    st.write('Save one API key per provider. Keys are never shown again after saving.')
    st.info('Cloud prompts are sent to the selected provider. Your ChatGPT, Claude or Gemini chat '
            'subscription is separate from API access and billing.')
    for name in PROVIDERS[:-1]:
        with st.container(border=True):
            st.subheader(name)
            has_key = bool(store.get_key(uid, name, user['key']))
            st.caption('Key saved · encrypted' if has_key else 'No API key saved')
            with st.form('key_' + name, clear_on_submit=True):
                value = st.text_input('API key', type='password', key='secret_' + name, max_chars=4096)
                if st.form_submit_button('Save / replace key'):
                    try:
                        store.save_key(uid, name, user['key'], value)
                        st.session_state.pop('models_' + name, None)
                        st.rerun()
                    except ValueError as e:
                        notice_error(e)
            a, b = st.columns(2)
            if a.button('Test connection', key='test_' + name):
                try:
                    models = client(name).models()
                    st.success(f'Connected. {len(models)} model IDs returned.')
                except ValueError as e:
                    notice_error(e)
            if b.button('Delete saved key', key='delete_' + name, disabled=not has_key):
                store.delete_key(uid, name)
                st.session_state.pop('models_' + name, None)
                st.rerun()
    st.subheader('Local Ollama')
    st.write('Start Ollama and pull a model on the app host, then select Ollama in the sidebar. '
             'No cloud API key is required. The administrator sets OLLAMA_BASE_URL.')
    st.subheader('Delete account')
    st.caption('Removes this account, saved keys, study sessions and scores from the active database. '
               'Backups and provider-side records are outside this action.')
    confirm = st.text_input('Type your username to confirm deletion')
    if st.button('Permanently delete my account', disabled=confirm != user['username']):
        store.delete_account(uid)
        reset_session()
        st.rerun()

elif page == 'Learn':
    st.caption('LEARN / ONE QUESTION AT A TIME')
    st.title('What do you want to understand?')
    style = st.selectbox('Teaching style', ['Step-by-step explanation', 'Socratic questions',
                                           'Concise revision notes', 'Worked examples'])
    with st.expander('Saved sessions and export'):
        sessions = store.sessions(uid)
        if sessions:
            selected = st.selectbox('Saved sessions', [s['id'] for s in sessions],
                                    format_func=lambda sid: next(s['title'] for s in sessions if s['id'] == sid))
            a, b = st.columns(2)
            if a.button('Open session'):
                st.session_state.messages = store.load_session(uid, selected)
                st.session_state.sid = selected
                st.rerun()
            confirm_delete = st.checkbox('Confirm deletion of selected session')
            if b.button('Delete session', disabled=not confirm_delete):
                store.delete_session(uid, selected)
                if st.session_state.sid == selected:
                    st.session_state.messages = []
                    st.session_state.sid = uuid.uuid4().hex
                st.rerun()
        if st.button('Start a new session'):
            st.session_state.messages = []
            st.session_state.sid = uuid.uuid4().hex
            st.rerun()
        export = '\n\n'.join(f'## {m["role"].title()}\n\n{m["content"]}' for m in st.session_state.messages)
        st.download_button('Download current conversation', export, 'study-session.md', 'text/markdown')
    if not st.session_state.messages:
        st.info('Try: “Explain database indexing with a small example” or “Help me understand eigenvalues.”')
    for message in st.session_state.messages:
        with st.chat_message(message['role']):
            st.markdown(message['content'])
            if message.get('model'):
                st.caption(f'{message["provider"]} · {message["model"]}')
    question = st.chat_input('Ask a question or follow up…', max_chars=6000)
    if question:
        try:
            if not model:
                raise ValueError('Choose a model in the sidebar first.')
            api = client(provider)
            check_rate()
            messages = st.session_state.messages + [{'role': 'user', 'content': question}]
            with st.spinner('Working through your question…'):
                result = api.generate(model, tutor_prompt(subject, level, style), bounded_history(messages), max_tokens)
            answer = result['text']
            if result['truncated']:
                answer += '\n\n*Response reached the output limit. Ask me to continue, or raise the limit.*'
            messages.append({'role': 'assistant', 'content': answer, 'provider': provider, 'model': model})
            store.save_session(uid, st.session_state.sid, messages)
            st.session_state.messages = messages
            st.rerun()
        except ValueError as e:
            notice_error(e)
            st.info('Your question was not saved. You can resend it after correcting the issue.')

elif page == 'Practise':
    st.caption('PRACTISE / FIND THE GAPS')
    st.title('A quick check of what you know.')
    with st.form('create_quiz'):
        topic = st.text_input('Quiz topic', placeholder='e.g. C++ virtual functions', max_chars=500)
        count = st.slider('Number of questions', 3, 10, 5)
        if st.form_submit_button('Generate quiz', type='primary'):
            try:
                if not topic.strip():
                    raise ValueError('Enter a topic first.')
                if not model:
                    raise ValueError('Choose a model in the sidebar first.')
                api = client(provider)
                check_rate()
                with st.spinner('Preparing your questions…'):
                    result = api.generate(model, tutor_prompt(subject, level, 'Quiz author'),
                                          [{'role': 'user', 'content': quiz_prompt(topic, subject, level, count)}], max_tokens)
                    questions = parse_quiz(result['text'], count)
                st.session_state.quiz = {'id': uuid.uuid4().hex, 'topic': topic, 'questions': questions,
                                         'provider': provider, 'model': model, 'submitted': False}
            except ValueError as e:
                notice_error(e)
    quiz = st.session_state.get('quiz')
    if quiz:
        st.subheader(quiz['topic'])
        st.caption(f'Created with {quiz["provider"]} · {quiz["model"]}')
        with st.form('answers_' + quiz['id']):
            choices = []
            for i, q in enumerate(quiz['questions']):
                choices.append(st.radio(f'{i + 1}. {q["question"]}', range(4), index=None,
                                        format_func=lambda n, opts=q['options']: opts[n],
                                        key=f'{quiz["id"]}_{i}', disabled=quiz['submitted']))
            if st.form_submit_button('Check answers', disabled=quiz['submitted']):
                if None in choices:
                    st.warning('Answer every question before submitting.')
                else:
                    score = sum(c == q['answer'] for c, q in zip(choices, quiz['questions']))
                    store.save_attempt(uid, quiz['id'], quiz['topic'], score, len(choices), quiz['provider'], quiz['model'])
                    quiz.update({'submitted': True, 'score': score, 'choices': choices})
                    st.rerun()
        if quiz['submitted']:
            st.success(f'Score: {quiz["score"]} / {len(quiz["questions"])}')
            for i, q in enumerate(quiz['questions']):
                with st.expander(f'{i + 1}. {"Correct" if quiz["choices"][i] == q["answer"] else "Review this"}', expanded=True):
                    st.write('Your answer: ' + q['options'][quiz['choices'][i]])
                    st.write('Correct answer: ' + q['options'][q['answer']])
                    st.write(q['explanation'])
            st.download_button('Download quiz and answers', json.dumps(quiz, indent=2), 'quiz.json', 'application/json')
        st.caption('AI-generated questions and answers can be wrong. Check disputed answers against your course material.')

elif page == 'Progress':
    st.caption('PROGRESS / YOUR PRACTICE RECORD')
    st.title('See what is sticking.')
    rows = store.attempts(uid)
    if not rows:
        st.info('Complete your first quiz to see your scores here.')
    else:
        a, b, c = st.columns(3)
        a.metric('Quizzes completed', len(rows))
        b.metric('Questions answered', sum(r['total'] for r in rows))
        c.metric('Overall accuracy', f'{100 * sum(r["score"] for r in rows) / sum(r["total"] for r in rows):.0f}%')
        table = [{'Topic': r['topic'], 'Score': f'{r["score"]}/{r["total"]}',
                  'Provider': r['provider'], 'Model': r['model'],
                  'Date (UTC)': time.strftime('%Y-%m-%d %H:%M', time.gmtime(r['created']))} for r in rows]
        st.dataframe(table, width='stretch', hide_index=True)
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(table[0]))
        writer.writeheader()
        # Neutralize spreadsheet formula injection in exported user/model text.
        writer.writerows({k: ("'" + str(v) if str(v).lstrip().startswith(('=', '+', '-', '@')) else v)
                          for k, v in row.items()} for row in table)
        st.download_button('Export progress', output.getvalue(), 'progress.csv', 'text/csv')
