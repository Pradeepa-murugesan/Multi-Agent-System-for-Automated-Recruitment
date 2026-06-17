import os
from datetime import datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from jose import JWTError, jwt

ALGORITHM          = 'HS256'
TOKEN_EXPIRE_HOURS = 24
COOKIE_NAME        = 'access_token'


def _secret() -> str:
    """Always read from env at call-time so load_dotenv() order never matters."""
    return os.getenv('SECRET_KEY', 'recruitment-ai-secret-2026')


def hash_password(plain: str) -> str:
    return generate_password_hash(plain, method='pbkdf2:sha256', salt_length=16)


def verify_password(plain: str, hashed: str) -> bool:
    return check_password_hash(hashed, plain)


def create_access_token(subject: str, expires_hours: int = TOKEN_EXPIRE_HOURS) -> str:
    now    = datetime.now(timezone.utc)
    expire = now + timedelta(hours=expires_hours)
    return jwt.encode(
        {'sub': subject, 'iat': now, 'exp': expire, 'type': 'access'},
        _secret(), algorithm=ALGORITHM,
    )


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except JWTError:
        return None


def set_auth_cookie(response, username: str) -> None:
    token = create_access_token(username)
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True, secure=False, samesite='Lax',
        max_age=TOKEN_EXPIRE_HOURS * 3600, path='/',
    )


def clear_auth_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path='/')
