import hashlib
import hmac
import secrets

from starlette.requests import Request
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def generate_csrf_token() -> str:
    return secrets.token_hex(16)


_CSRF_SESSION_KEY = "_csrf"


def csrf_token(request: Request) -> str:
    """Das CSRF-Token der Session – wird beim ersten Zugriff erzeugt."""
    token = request.session.get(_CSRF_SESSION_KEY)
    if not token:
        token = generate_csrf_token()
        request.session[_CSRF_SESSION_KEY] = token
    return token


def verify_csrf(request: Request, submitted: str) -> bool:
    expected = request.session.get(_CSRF_SESSION_KEY)
    if not expected or not submitted:
        return False
    return hmac.compare_digest(expected, submitted)
