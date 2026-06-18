from functools import wraps
from flask import request, redirect, url_for, jsonify, g
from .utils import decode_access_token, COOKIE_NAME
from src.database.db import get_user_by_username

_API_PATHS = frozenset({
    '/process', '/send_email', '/refine_email',
    '/generate_jd', '/export_report', '/auth/login', '/auth/register',
})


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
    if hasattr(g, 'current_user') and g.current_user:
        return g.current_user.get('username')
    token = _extract_token()
    if not token:
        return None
    payload = decode_access_token(token)
    return payload.get('sub') if payload else None


def require_auth(f):
    """
    JWT gate — validates cookie (browser) or Authorization: Bearer (API).
    On success, sets g.current_user to the user dict from the DB.
    Browser failure → 302 /login; API/SSE failure → 401 JSON.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token   = _extract_token()
        payload = decode_access_token(token) if token else None

        if not payload:
            if _is_api_request():
                return jsonify({'error': 'Unauthorized',
                                'message': 'Valid Bearer token required.'}), 401
            return redirect(url_for('auth.login_page'))

        user = get_user_by_username(payload.get('sub', ''))
        if not user:
            if _is_api_request():
                return jsonify({'error': 'Unauthorized',
                                'message': 'Valid Bearer token required.'}), 401
            return redirect(url_for('auth.login_page'))

        g.current_user = user
        return f(*args, **kwargs)
    return decorated
