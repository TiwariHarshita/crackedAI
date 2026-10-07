"""Password verification and a separate password-derived encryption key."""
import base64
import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet, InvalidToken


def new_salt():
    return secrets.token_bytes(16)


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)


def verify_password(password, salt, expected):
    return hmac.compare_digest(password_hash(password, salt), expected)


def vault_key(password, salt):
    # Domain separation: do not store this derived key or use the verifier as a key.
    raw = hashlib.pbkdf2_hmac('sha256', password.encode(), b'tutor-vault-v1:' + salt, 600000)
    return base64.urlsafe_b64encode(raw)


def encrypt(key, value):
    return Fernet(key).encrypt(value.encode()).decode()


def decrypt(key, value):
    try:
        return Fernet(key).decrypt(value.encode()).decode()
    except InvalidToken:
        raise ValueError('Could not unlock the saved API key. Sign in again.') from None
