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
import threading

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
    clear_sessions()  # every browser must log in with the new password
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
    clear_sessions()
    return True, "Password updated successfully."


def is_default_password():
    """True while the password is still the factory default ('sololink')."""
    try:
        return bool(get_auth_data().get("is_default", True))
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Sessions: a new random token per login, kept in memory (fast enough for the
# Cockpit's 10 Hz polling), expiring after SESSION_TIMEOUT_SEC without use and
# revoked by logout or a password change. Persisted to disk so a restart of the
# utility doesn't log everyone out (written on login/logout and at most every
# few minutes otherwise, to spare the Solo's flash storage).
# ---------------------------------------------------------------------------
SESSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "solo_sessions.json")
SESSION_PERSIST_SEC = 600

_sessions_lock = threading.Lock()
_sessions_loaded = [False]
_last_persist = [0.0]


def _new_token():
    raw = binascii.hexlify(os.urandom(24))
    return raw.decode('ascii') if hasattr(raw, 'decode') else raw


def _load_sessions():
    """Load persisted sessions once (only well-formed random tokens are accepted)."""
    if _sessions_loaded[0]:
        return
    _sessions_loaded[0] = True
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                now = time.time()
                for k, v in data.items():
                    k = str(k).strip()
                    try:
                        last = float(v)
                    except (TypeError, ValueError):
                        continue
                    if len(k) == 48 and now - last < SESSION_TIMEOUT_SEC:
                        _active_sessions[k] = last
        except Exception:
            pass


def _save_sessions():
    """Write active sessions to disk."""
    try:
        tmp = SESSION_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_active_sessions, f)
        if os.name == 'nt' and os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)          # Windows can't rename over a file
        os.rename(tmp, SESSION_FILE)
        _last_persist[0] = time.time()
    except Exception:
        pass


def _prune(now):
    for k in [k for k, v in _active_sessions.items() if now - v >= SESSION_TIMEOUT_SEC]:
        _active_sessions.pop(k, None)


def create_session():
    """Create and return a new random session token."""
    token = _new_token()
    now = time.time()
    with _sessions_lock:
        _load_sessions()
        _prune(now)
        _active_sessions[token] = now
        _save_sessions()
    return token


def validate_session(token):
    """True if token is a live session (refreshes its idle timer)."""
    if not token:
        return False
    t = str(token).strip()
    if not t:
        return False
    now = time.time()
    with _sessions_lock:
        _load_sessions()
        last = _active_sessions.get(t)
        if last is None:
            return False
        if now - last >= SESSION_TIMEOUT_SEC:
            _active_sessions.pop(t, None)
            _save_sessions()
            return False
        _active_sessions[t] = now
        if now - _last_persist[0] > SESSION_PERSIST_SEC:
            _save_sessions()
    return True


def invalidate_session(token):
    """Remove a session token (logout)."""
    if not token:
        return
    t = str(token).strip()
    with _sessions_lock:
        _load_sessions()
        _active_sessions.pop(t, None)
        _save_sessions()


def clear_sessions():
    """Log every browser out (password change)."""
    with _sessions_lock:
        _sessions_loaded[0] = True
        _active_sessions.clear()
        _save_sessions()


# ---------------------------------------------------------------------------
# Login throttling: after LOGIN_MAX_FAILURES wrong passwords from one address
# within LOGIN_WINDOW_SEC, that address must wait (doubling, up to 5 minutes).
# ---------------------------------------------------------------------------
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SEC = 300
LOGIN_BASE_LOCK_SEC = 30
LOGIN_MAX_LOCK_SEC = 300
_login_failures = {}      # ip -> {"times": [...], "locked_until": t, "locks": n}
_login_lock = threading.Lock()


def login_wait_seconds(ip):
    """Seconds this address must still wait before trying again (0 = allowed)."""
    with _login_lock:
        rec = _login_failures.get(ip)
        if not rec:
            return 0
        return max(0, int(round(rec.get("locked_until", 0) - time.time())))


def record_login_failure(ip):
    now = time.time()
    with _login_lock:
        rec = _login_failures.setdefault(ip, {"times": [], "locked_until": 0, "locks": 0})
        rec["times"] = [t for t in rec["times"] if now - t < LOGIN_WINDOW_SEC] + [now]
        if len(rec["times"]) >= LOGIN_MAX_FAILURES:
            rec["locks"] += 1
            rec["locked_until"] = now + min(LOGIN_MAX_LOCK_SEC, LOGIN_BASE_LOCK_SEC * (2 ** (rec["locks"] - 1)))
            rec["times"] = []
        if len(_login_failures) > 200:          # bound memory
            for k in list(_login_failures.keys())[:100]:
                _login_failures.pop(k, None)


def record_login_success(ip):
    with _login_lock:
        _login_failures.pop(ip, None)
