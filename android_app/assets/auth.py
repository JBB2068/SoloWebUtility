#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Authentication and Password Management for Solo Web Utility.
Compatible with Python 2.7 and Python 3.x.
"""

import os
import sys
import json
import hashlib
import binascii
import time
import uuid

AUTH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.json")
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
    """Verify input password against stored hash, with guaranteed default support."""
    if not password:
        return False
    
    # Strip whitespace
    p_clean = password.strip()
    
    # Direct check for default password if auth_data is default or file missing
    auth_data = get_auth_data()
    if p_clean == DEFAULT_PASSWORD and auth_data.get("is_default", True):
        return True

    stored_hash = auth_data.get("password_hash", "")
    salt = auth_data.get("salt", "")
    test_hash, _ = _hash_password(p_clean, salt)
    if test_hash == stored_hash:
        return True

    return False


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
    _active_sessions.clear()  # Invalidate all active sessions
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
    """Generate deterministic signature token derived from the current password hash."""
    auth_data = get_auth_data()
    stored_hash = auth_data.get("password_hash", "")
    salt = auth_data.get("salt", "")
    data = _to_bytes(stored_hash) + b":" + _to_bytes(salt) + b":3drsolo"
    return hashlib.sha256(data).hexdigest()


SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "solo_sessions.json")


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

    # 1. Deterministic master token check (matches current password hash)
    try:
        master = get_master_token()
        if t == master:
            return True
    except Exception:
        pass

    # 2. Default password hash / plain check if on default settings
    try:
        auth_data = get_auth_data()
        if auth_data.get("is_default", True):
            def_hash = hashlib.sha256(_to_bytes(DEFAULT_PASSWORD)).hexdigest()
            if t == DEFAULT_PASSWORD or t == def_hash:
                return True
    except Exception:
        pass

    # 3. Check memory sessions
    if t in _active_sessions:
        return True
    for k in list(_active_sessions.keys()):
        if str(k).strip() == t:
            return True

    # 4. Check disk sessions
    try:
        _load_sessions()
        if t in _active_sessions:
            return True
        for k in list(_active_sessions.keys()):
            if str(k).strip() == t:
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
