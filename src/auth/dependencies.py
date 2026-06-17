import os
from functools import wraps
from flask import request, redirect, url_for, jsonify
from .utils import decode_access_token, COOKIE_NAME

_API_PATHS = frozenset({
    '/process', '/send_email', '/refine_email',
    '/generate_jd', '/export_report', '/auth/login',
})


def _admin_username() -> str:
    """Read at call-time — immune to import-order races with load_dotenv."""
    return os.getenv('ADMIN_USERNAME', 'admin')


def _extract_token() -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return token
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:]
    return None


def _is_api_request() -> bool:
    return (
        request.path.startswith('/api/')
        or request.path in _API_PATHS
        or request.is_json
        or 'application/json' in request.headers.get('Accept', '')
    )


def get_current_username() -> str | None:
    token = _extract_token()
    if not token:
        return None
    payload = decode_access_token(token)
    return payload.get('sub') if payload else None


def require_auth(f):
    """
    JWT gate for every protected route.
    · Browser page  → validates HttpOnly cookie  → 302 /login on failure
    · API / SSE     → validates cookie or Bearer  → 401 JSON on failure
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token   = _extract_token()
        payload = decode_access_token(token) if token else None

        if not payload or payload.get('sub') != _admin_username():
            if _is_api_request():
                return jsonify({
                    'error': 'Unauthorized',
                    'message': 'Valid Bearer token required.',
                }), 401
            return redirect(url_for('login'))

        return f(*args, **kwargs)
    return decorated
