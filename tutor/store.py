"""SQLite persistence. Every data operation is scoped to the signed-in user."""
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from tutor.security import new_salt, password_hash, verify_password, vault_key, encrypt, decrypt


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    salt BLOB NOT NULL, verifier BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    username TEXT PRIMARY KEY, failures INTEGER, locked_until REAL);
                CREATE TABLE IF NOT EXISTS credentials (
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    provider TEXT NOT NULL, ciphertext TEXT NOT NULL,
                    PRIMARY KEY(user_id, provider));
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL, messages TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    topic TEXT NOT NULL, score INTEGER NOT NULL, total INTEGER NOT NULL,
                    provider TEXT NOT NULL, model TEXT NOT NULL, created REAL NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, username, password):
        username = username.strip().lower()
        if not re.fullmatch(r'[a-z0-9_-]{3,32}', username):
            raise ValueError('Use 3–32 letters, numbers, underscores or hyphens for your username.')
        if not 12 <= len(password) <= 256:
            raise ValueError('Use a password between 12 and 256 characters.')
        salt, uid = new_salt(), uuid.uuid4().hex
        try:
            with self.db() as db:
                db.execute('INSERT INTO users VALUES (?,?,?,?)',
                           (uid, username, salt, password_hash(password, salt)))
        except sqlite3.IntegrityError:
            raise ValueError('That username is already taken.') from None

    def login(self, username, password):
        username = username.strip().lower()[:32]
        if len(password) > 256:
            raise ValueError('Invalid username or password.')
        failure = None
        with self.db() as db:
            # Serialize check + increment so parallel attempts cannot bypass the limit.
            db.execute('BEGIN IMMEDIATE')
            attempt = db.execute('SELECT * FROM login_attempts WHERE username=?', (username,)).fetchone()
            if attempt and attempt['locked_until'] > time.time():
                raise ValueError('Too many attempts. Try again in 5 minutes.')
            user = db.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            valid = verify_password(password, user['salt'] if user else b'0' * 16,
                                    user['verifier'] if user else b'0' * 32)
            if not user or not valid:
                count = (attempt['failures'] if attempt and not attempt['locked_until'] else 0) + 1
                db.execute('INSERT OR REPLACE INTO login_attempts VALUES (?,?,?)',
                           (username, count, time.time() + 300 if count >= 5 else 0))
                failure = 'Invalid username or password.'
            else:
                db.execute('DELETE FROM login_attempts WHERE username=?', (username,))
        if failure:
            raise ValueError(failure)
        return {'id': user['id'], 'username': user['username'], 'key': vault_key(password, user['salt'])}

    def save_key(self, uid, provider, key, value):
        if not value.strip() or len(value) > 4096:
            raise ValueError('Enter a valid API key.')
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO credentials VALUES (?,?,?)',
                       (uid, provider, encrypt(key, value.strip())))

    def get_key(self, uid, provider, key):
        with self.db() as db:
            row = db.execute('SELECT ciphertext FROM credentials WHERE user_id=? AND provider=?',
                             (uid, provider)).fetchone()
        return decrypt(key, row['ciphertext']) if row else ''

    def delete_key(self, uid, provider):
        with self.db() as db:
            db.execute('DELETE FROM credentials WHERE user_id=? AND provider=?', (uid, provider))

    def sessions(self, uid):
        with self.db() as db:
            return [dict(r) for r in db.execute(
                'SELECT id,title,updated FROM sessions WHERE user_id=? ORDER BY updated DESC', (uid,))]

    def load_session(self, uid, sid):
        with self.db() as db:
            row = db.execute('SELECT messages FROM sessions WHERE user_id=? AND id=?', (uid, sid)).fetchone()
        if not row:
            raise ValueError('Study session not found.')
        return json.loads(row['messages'])

    def save_session(self, uid, sid, messages):
        title = next((m['content'][:65] for m in messages if m['role'] == 'user'), 'New session')
        with self.db() as db:
            row = db.execute('SELECT user_id FROM sessions WHERE id=?', (sid,)).fetchone()
            if row and row['user_id'] != uid:
                raise ValueError('Study session not found.')
            db.execute('INSERT INTO sessions VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                       'title=excluded.title,messages=excluded.messages,updated=excluded.updated',
                       (sid, uid, title, json.dumps(messages), time.time()))

    def delete_session(self, uid, sid):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE user_id=? AND id=?', (uid, sid))

    def save_attempt(self, uid, attempt_id, topic, score, total, provider, model):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO attempts VALUES (?,?,?,?,?,?,?,?)',
                       (attempt_id, uid, topic, score, total, provider, model, time.time()))

    def attempts(self, uid):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM attempts WHERE user_id=? ORDER BY created DESC', (uid,))]

    def delete_account(self, uid):
        with self.db() as db:
            db.execute('DELETE FROM users WHERE id=?', (uid,))
