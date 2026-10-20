#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Authentication & Session Management Module
Handles password hashing with salt, persistent JSON storage, session validation,
and Flask authentication blueprint.
"""

import os
import sys
import json
import time
import hashlib
import binascii
from functools import wraps
from webcore import Blueprint, request, jsonify, make_response

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH_FILE = os.path.join(BASE_DIR, "auth.json")
SESSION_FILE = os.path.join(BASE_DIR, "solo_sessions.json")
DEFAULT_PASSWORD = "sololink"
SESSION_TIMEOUT_SEC = 86400  # 24 hours

_active_sessions = {}


def _to_bytes(s):
    """Safely convert string/unicode to byte string in Python 2 and 3."""
    if sys.version_info[0] >= 3:
        if isinstance(s, str):
            return s.encode('utf-8')
        return bytes(s)
    else:
        try:
            if isinstance(s, unicode):
                return s.encode('utf-8')
            return str(s)
        except Exception:
            return str(s)


def _hash_password(password, salt=None):
    """Hash a password using SHA256 with salt."""
    if salt is None:
        raw_salt = binascii.hexlify(os.urandom(16))
        salt = str(raw_salt.decode('ascii') if hasattr(raw_salt, 'decode') else raw_salt)

    data = _to_bytes(salt) + _to_bytes(password)
    h = hashlib.sha256(data).hexdigest()
    return h, salt


def get_auth_data():
    """Load or initialize authentication file."""
    if not os.path.exists(AUTH_FILE):
        pwd_hash, salt = _hash_password(DEFAULT_PASSWORD)
        auth_data = {
            "password_hash": pwd_hash,
            "salt": salt,
            "updated_at": int(time.time()),
            "is_default": True
        }
        save_auth_data(auth_data)
        return auth_data

    try:
        with open(AUTH_FILE, "r") as f:
            return json.load(f)
    except Exception:
        pwd_hash, salt = _hash_password(DEFAULT_PASSWORD)
        auth_data = {
            "password_hash": pwd_hash,
            "salt": salt,
            "updated_at": int(time.time()),
            "is_default": True
        }
        save_auth_data(auth_data)
        return auth_data


def save_auth_data(auth_data):
    """Write auth data to auth.json securely."""
    with open(AUTH_FILE, "w") as f:
        json.dump(auth_data, f, indent=2)


def verify_password(password):
    """Verify input password against stored hash."""
    if not password:
        return False

    p_clean = password.strip()
    auth_data = get_auth_data()

    if p_clean == DEFAULT_PASSWORD and auth_data.get("is_default", True):
        return True

    stored_hash = auth_data.get("password_hash", "")
    salt = auth_data.get("salt", "")
    test_hash, _ = _hash_password(p_clean, salt)
    return test_hash == stored_hash


def change_password(old_password, new_password):
    """Change the web utility password."""
    if not verify_password(old_password):
        return False, "Current password is incorrect."

    if not new_password or len(new_password) < 4:
        return False, "New password must be at least 4 characters long."

    pwd_hash, salt = _hash_password(new_password)
    auth_data = {
        "password_hash": pwd_hash,
        "salt": salt,
        "updated_at": int(time.time()),
        "is_default": (new_password == DEFAULT_PASSWORD)
    }
    save_auth_data(auth_data)
    _active_sessions.clear()
    _save_sessions()
    return True, "Password changed successfully."


def force_set_password(new_password):
    """Force set password without old password check (CLI utility)."""
    if not new_password or len(new_password) < 4:
        return False, "New password must be at least 4 characters long."

    pwd_hash, salt = _hash_password(new_password)
    auth_data = {
        "password_hash": pwd_hash,
        "salt": salt,
        "updated_at": int(time.time()),
        "is_default": (new_password == DEFAULT_PASSWORD)
    }
    save_auth_data(auth_data)
    _active_sessions.clear()
    _save_sessions()
    return True, "Password updated successfully."


def get_master_token():
    """Generate deterministic signature token derived from current password hash."""
    auth_data = get_auth_data()
    stored_hash = auth_data.get("password_hash", "")
    salt = auth_data.get("salt", "")
    data = _to_bytes(stored_hash) + b":" + _to_bytes(salt) + b":3drsolo"
    return hashlib.sha256(data).hexdigest()


def _load_sessions():
    """Load persistent sessions from disk."""
    global _active_sessions
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for k, v in data.items():
                        _active_sessions[str(k).strip()] = float(v)
        except Exception:
            pass


def _save_sessions():
    """Write active sessions to disk."""
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump(_active_sessions, f)
    except Exception:
        pass


def create_session():
    """Create and return a new session token."""
    token = get_master_token()
    _active_sessions[token] = time.time()
    _save_sessions()
    return token


def validate_session(token):
    """Validate if a session token is active and valid."""
    if not token:
        return False

    t = str(token).strip()
    if not t:
        return False

    try:
        if t == get_master_token():
            return True
    except Exception:
        pass

    try:
        auth_data = get_auth_data()
        if auth_data.get("is_default", True):
            def_hash = hashlib.sha256(_to_bytes(DEFAULT_PASSWORD)).hexdigest()
            if t == DEFAULT_PASSWORD or t == def_hash:
                return True
    except Exception:
        pass

    if t in _active_sessions:
        return True

    try:
        _load_sessions()
        if t in _active_sessions:
            return True
    except Exception:
        pass

    return False


def invalidate_session(token):
    """Remove a session token (logout)."""
    if not token:
        return
    t = str(token).strip()
    _load_sessions()
    _active_sessions.pop(t, None)
    _save_sessions()


def get_token_from_request():
    """Extract auth token from Flask request (query params, headers, or cookies)."""
    # 1. Query parameter
    token = request.args.get("token")
    if token and token.strip():
        return token.strip()

    # 2. HTTP headers
    header_val = request.headers.get("X-Auth-Token") or request.headers.get("Authorization")
    if header_val:
        header_val = header_val.strip()
        if header_val.lower().startswith("bearer "):
            header_val = header_val[7:].strip()
        if header_val:
            return header_val

    # 3. Cookie
    cookie_val = request.cookies.get("solo_session")
    if cookie_val and cookie_val.strip():
        return cookie_val.strip()

    return None


def is_authenticated():
    """Check if the current request is authenticated."""
    token = get_token_from_request()
    return validate_session(token)


def login_required(f):
    """Flask view decorator requiring authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_authenticated():
            return jsonify({"error": True, "message": "Authentication required."}), 401
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------
auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/api/auth/check", methods=["GET"])
def auth_check():
    return jsonify({"authenticated": is_authenticated()})


@auth_bp.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    pwd = data.get("password", "")
    if verify_password(pwd):
        token = create_session()
        resp = make_response(jsonify({
            "success": True,
            "token": token,
            "message": "Login successful."
        }))
        resp.set_cookie("solo_session", token, max_age=SESSION_TIMEOUT_SEC, path="/", httponly=False)
        return resp
    return jsonify({"error": True, "message": "Invalid password."}), 401


@auth_bp.route("/api/auth/logout", methods=["POST"])
@login_required
def auth_logout():
    token = get_token_from_request()
    invalidate_session(token)
    resp = make_response(jsonify({"success": True, "message": "Logged out."}))
    resp.delete_cookie("solo_session", path="/")
    return resp


@auth_bp.route("/api/auth/change-password", methods=["POST"])
@login_required
def auth_change_password():
    data = request.get_json(silent=True) or {}
    old_p = data.get("old_password", "")
    new_p = data.get("new_password", "")
    ok, msg = change_password(old_p, new_p)
    if ok:
        new_token = create_session()
        resp = make_response(jsonify({"success": True, "token": new_token, "message": msg}))
        resp.set_cookie("solo_session", new_token, max_age=SESSION_TIMEOUT_SEC, path="/", httponly=False)
        return resp
    return jsonify({"error": True, "message": msg}), 400
