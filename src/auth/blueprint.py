import re
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, make_response, jsonify)
from .utils import (verify_password, hash_password, create_access_token,
                    set_auth_cookie, clear_auth_cookie, TOKEN_EXPIRE_HOURS)
from .csrf import generate_csrf_token, validate_csrf
from src.database.db import (get_user_by_email, create_user,
                              username_exists, email_exists)
from src.extensions import limiter

auth_bp = Blueprint('auth', __name__)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


# ─── Login ────────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET'])
def login_page():
    return render_template('auth/login.html', csrf_token=generate_csrf_token())


@auth_bp.route('/auth/login', methods=['POST'])
@limiter.limit("5 per minute")
def login():
    # JSON API login (Bearer token for programmatic clients)
    if request.is_json:
        data     = request.get_json(silent=True) or {}
        email    = data.get('email', '').strip().lower()
        password = data.get('password', '').strip()

        if not email or not password:
            return jsonify({'error': 'email and password are required'}), 400

        user = get_user_by_email(email)
        if not user or not verify_password(password, user['password_hash']):
            return jsonify({'error': 'Invalid credentials'}), 401

        token    = create_access_token(user['username'])
        response = jsonify({
            'access_token': token,
            'token_type':   'bearer',
            'expires_in':   TOKEN_EXPIRE_HOURS * 3600,
        })
        set_auth_cookie(response, user['username'])
        return response

    # Form login
    if not validate_csrf(request.form.get('csrf_token', '')):
        flash('Invalid form submission — please try again.', 'error')
        return redirect(url_for('auth.login_page'))

    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()

    if not email or not password:
        flash('Email and password are required.', 'error')
        return redirect(url_for('auth.login_page'))

    user = get_user_by_email(email)
    if not user or not verify_password(password, user['password_hash']):
        flash('Invalid email or password.', 'error')
        return redirect(url_for('auth.login_page'))

    response = make_response(redirect(url_for('index')))
    set_auth_cookie(response, user['username'])
    return response


# ─── Register ─────────────────────────────────────────────────────────────────

@auth_bp.route('/register', methods=['GET'])
def register_page():
    return render_template('auth/register.html', csrf_token=generate_csrf_token())


@auth_bp.route('/auth/register', methods=['POST'])
def register():
    if not validate_csrf(request.form.get('csrf_token', '')):
        flash('Invalid form submission — please try again.', 'error')
        return redirect(url_for('auth.register_page'))

    username = request.form.get('username', '').strip()
    email    = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '').strip()
    confirm  = request.form.get('confirm_password', '').strip()

    errors = []
    if not username:
        errors.append('Username is required.')
    elif len(username) < 3:
        errors.append('Username must be at least 3 characters.')
    if not email or not _EMAIL_RE.match(email):
        errors.append('A valid email address is required.')
    if not password:
        errors.append('Password is required.')
    elif len(password) < 8:
        errors.append('Password must be at least 8 characters.')
    if password and confirm and password != confirm:
        errors.append('Passwords do not match.')

    if not errors:
        if username_exists(username):
            errors.append('That username is already taken.')
        if email_exists(email):
            errors.append('An account with that email already exists.')

    if errors:
        for err in errors:
            flash(err, 'error')
        return redirect(url_for('auth.register_page'))

    create_user(username, email, hash_password(password))
    flash('Account created! Sign in to continue.', 'success')
    return redirect(url_for('auth.login_page'))


# ─── Logout ───────────────────────────────────────────────────────────────────

@auth_bp.route('/logout')
@auth_bp.route('/auth/logout')
def logout():
    response = make_response(redirect(url_for('auth.login_page')))
    clear_auth_cookie(response)
    return response
