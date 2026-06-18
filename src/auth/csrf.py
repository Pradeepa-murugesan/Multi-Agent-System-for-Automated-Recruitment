import secrets
from flask import session

_SESSION_KEY = '_csrf_token'


def generate_csrf_token() -> str:
    if _SESSION_KEY not in session:
        session[_SESSION_KEY] = secrets.token_hex(32)
    return session[_SESSION_KEY]


def validate_csrf(submitted: str) -> bool:
    expected = session.get(_SESSION_KEY)
    if not expected or not submitted:
        return False
    return secrets.compare_digest(expected, submitted)
