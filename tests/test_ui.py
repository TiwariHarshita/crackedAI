"""Exercise the real Streamlit screens without sending paid API requests."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from tutor.store import Store


class UITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'TUTOR_DATA_DIR': self.tmp.name})
        self.env.start()
        store = Store(Path(self.tmp.name) / 'tutor.db')
        store.register('tester', 'test-password-123')
        self.app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py'), default_timeout=20)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def login(self):
        self.app.run()
        self.app.text_input[0].set_value('tester')
        self.app.text_input[1].set_value('test-password-123')
        self.app.button[0].click().run()
        self.assertEqual(len(self.app.exception), 0)

    def test_login_navigation_and_logout(self):
        self.login()
        for page in ['Settings', 'Progress', 'Practise', 'Learn']:
            self.app.sidebar.radio[0].set_value(page).run()
            self.assertEqual(len(self.app.exception), 0)
        next(b for b in self.app.button if b.label == 'Sign out').click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertNotIn('user', self.app.session_state)

    def test_save_key_chat_and_quiz(self):
        self.login()
        self.app.sidebar.radio[0].set_value('Settings').run()
        self.app.text_input(key='secret_OpenAI').set_value('sk-test-only')
        next(b for b in self.app.button if b.label == 'Save / replace key').click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.app.sidebar.text_input[0].set_value('model-test').run()
        self.app.sidebar.radio[0].set_value('Learn').run()
        with patch('tutor.providers.Provider.generate', return_value={'text': 'A clear explanation.', 'truncated': False}):
            self.app.chat_input[0].set_value('Explain trees').run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(len(self.app.chat_message), 2)
        self.app.sidebar.radio[0].set_value('Practise').run()
        import json
        q = {'question': 'Two plus two?', 'options': ['1', '2', '3', '4'], 'answer': 3, 'explanation': 'It is four.'}
        self.app.text_input[0].set_value('Arithmetic')
        with patch('tutor.providers.Provider.generate', return_value={'text': json.dumps({'questions': [q] * 5}), 'truncated': False}):
            next(b for b in self.app.button if b.label == 'Generate quiz').click().run()
        self.assertEqual(len(self.app.exception), 0)
        for radio in self.app.radio:
            if radio.label != 'Workspace':
                radio.set_value(3)
        next(b for b in self.app.button if b.label == 'Check answers').click().run()
        self.assertEqual(len(self.app.exception), 0)
        self.assertEqual(self.app.session_state['quiz']['score'], 5)
        self.app.sidebar.radio[0].set_value('Progress').run()
        self.assertEqual(self.app.metric[0].value, '1')


if __name__ == '__main__':
    unittest.main()
