# -*- coding: utf-8 -*-
"""ssh to the Solo Controller (10.1.1.1) that can get its own key back.

Why this exists: the Solo logs in to the Controller with a key (/home/root/.ssh/id_rsa). A Controller
settings reset or Full Install erases the Controller's authorized_keys, and every Solo -> Controller job
(Update Controller, the controller log viewer) then fails with "Permission denied".

OpenSSH asks for a password on its *controlling terminal*, not on stdin. A service has none, so a pty
that is merely connected to stdin/stdout never receives the prompt. run_tty() uses pty.fork(), which
makes the pty the child's controlling terminal, so the prompt arrives and can be answered.

run()                 key login; if the Controller refuses the key, restore it once (default root
                      password, real terminal) and try again
ensure_authorized()   the restore step on its own
Used by bin/golden_update/golden_update.py and modules/log_viewer/controller_install.py (and by
flash_mgr.py's controller step). Python 2.7 (Solo) and 3 (tests); no packages.
"""
from __future__ import print_function

import os
import re
import select
import subprocess
import threading
import time

try:
    import pty
except ImportError:                     # not on Windows; the helper is only useful on the Solo
    pty = None

CONTROLLER_IP = "10.1.1.1"
DEFAULT_PASSWORD = "TjSDBkAu"           # Open Solo's default root password
SSH_BIN = "ssh"
KEYGEN_BIN = "ssh-keygen"
KEY_PATH = "/home/root/.ssh/id_rsa"
REMOTE_AUTH_FILES = ("/home/root/.ssh/authorized_keys", "/root/.ssh/authorized_keys")
SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]

NOT_AUTHORIZED = ("The Solo isn't authorized on the Controller and its root password isn't the default. "
                  "Run the installer again with the controller on.")
UNREACHABLE = "Cannot reach the Controller over ssh. Is it on and linked to this Solo?"

_state = {"tried": False, "result": None, "detail": ""}


def _text(b):
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else b


def _key_args():
    return ["-i", KEY_PATH] if os.path.exists(KEY_PATH) else []


def ssh_args(cmd, batch, connect_timeout=10, host=None):
    args = [SSH_BIN] + SSH_OPTS + ["-o", "ConnectTimeout=%d" % connect_timeout] + _key_args()
    if batch:
        args += ["-o", "BatchMode=yes"]
    else:
        args += ["-o", "PubkeyAuthentication=no", "-o", "NumberOfPasswordPrompts=1"]
    return args + ["root@%s" % (host or CONTROLLER_IP), cmd]


def run_plain(args, data=None, timeout=30):
    """No terminal needed (key login). -> (rc, text); rc -9 when killed at the time limit."""
    try:
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        return 127, "cannot run %s: %s" % (args[0], e)
    hit = []

    def kill():
        hit.append(1)
        try:
            p.kill()
        except Exception:
            pass
    timer = threading.Timer(timeout, kill)
    timer.daemon = True
    timer.start()
    try:
        out = p.communicate(data)[0]
    finally:
        timer.cancel()
    return (-9 if hit else p.returncode), _text(out).replace("\r", "")


def run_tty(args, password=DEFAULT_PASSWORD, timeout=30):
    """Run args with a real controlling terminal and answer one 'password:' prompt.
    -> (rc, text, prompts)  where prompts is how many times a password was asked for."""
    if pty is None:
        return 255, "no terminal support on this system", 0
    pid, fd = pty.fork()
    if pid == 0:                                        # child: the pty slave is now its controlling terminal
        try:
            os.execvp(args[0], args)
        finally:
            os._exit(127)
    out, prompts, sent_at, start = [], 0, 0, time.time()
    status = None
    try:
        while True:
            if time.time() - start > timeout:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
                break
            r, _, _ = select.select([fd], [], [], 0.5)
            if r:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    data = b""
                if not data:
                    break
                out.append(data)
                tail = _text(b"".join(out)[-200:]).lower()
                if tail.rstrip().endswith("password:") and len(out) != sent_at:
                    prompts += 1
                    sent_at = len(out)
                    if prompts > 1:                      # asked again: the password was wrong
                        try:
                            os.kill(pid, 9)
                        except OSError:
                            pass
                        break
                    os.write(fd, (password + "\n").encode("utf-8"))
        _, status = os.waitpid(pid, 0)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    if status is None:
        rc = 255
    elif os.WIFEXITED(status):
        rc = os.WEXITSTATUS(status)
    else:
        rc = -os.WTERMSIG(status)
    return rc, _text(b"".join(out)).replace("\r", ""), prompts


def _refused(rc, text):
    return rc != 0 and "permission denied" in text.lower()


def key_login_ok(timeout=15):
    rc, text = run_plain(ssh_args("echo KEY_OK", True), None, timeout)
    return rc == 0 and "KEY_OK" in text, rc, text


def make_key():
    """Create the Solo's key if it is missing (like deploy_to_drone.py). -> True when a public key exists."""
    pub = KEY_PATH + ".pub"
    if os.path.exists(KEY_PATH) and os.path.exists(pub):
        return True
    d = os.path.dirname(KEY_PATH)
    try:
        if not os.path.isdir(d):
            os.makedirs(d)
        os.chmod(d, 0o700)
    except OSError:
        pass
    if os.path.exists(KEY_PATH) and not os.path.exists(pub):       # private key without its .pub
        rc, text = run_plain([KEYGEN_BIN, "-y", "-f", KEY_PATH], None, 20)
        if rc == 0 and text.strip():
            with open(pub, "w") as f:
                f.write(text.strip() + "\n")
            return True
        return False
    run_plain([KEYGEN_BIN, "-t", "rsa", "-N", "", "-f", KEY_PATH], None, 60)
    return os.path.exists(pub)


def _install_command(blob):
    """Every step is allowed to fail (a read-only /root must not stop /home/root); the key login that
    follows decides whether the key arrived."""
    parts = ["mkdir -p /home/root/.ssh /root/.ssh 2>/dev/null"]
    for f in REMOTE_AUTH_FILES:
        parts.append("(grep -qF '%s' %s 2>/dev/null || echo '%s' >> %s) 2>/dev/null" % (blob[0], f, blob[1], f))
        parts.append("chmod 600 %s 2>/dev/null" % f)
    parts.append("chmod 700 /home/root/.ssh /root/.ssh 2>/dev/null")
    parts.append("echo AUTH_OK")
    return "; ".join(parts)


def ensure_authorized(force=False):
    """Make sure the Controller accepts the Solo's key. -> (ok, message). Tries the password once per
    process unless force=True."""
    if _state["tried"] and not force and _state["result"] is not None:
        return _state["result"]
    res = _ensure()
    _state["tried"], _state["result"] = True, res
    return res


def _note(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    _state["detail"] = " | ".join(lines[-3:])[:300]


def _ensure():
    ok, rc, text = key_login_ok()
    if ok:
        return True, "ok"
    _note(text)
    if not _refused(rc, text):
        return False, UNREACHABLE
    if not make_key():
        return False, "The Solo has no ssh key and could not make one."
    try:
        with open(KEY_PATH + ".pub") as f:
            pub = f.read().strip()
    except (IOError, OSError):
        return False, "The Solo's public ssh key could not be read."
    fields = pub.split()
    if len(fields) < 2 or "'" in pub:
        return False, "The Solo's public ssh key is not in the expected format."
    rc, text, prompts = run_tty(ssh_args(_install_command((fields[1], pub)), False, 10))
    _note("password prompts: %d, exit %s; %s" % (prompts, rc, text))
    ok, rc2, text2 = key_login_ok()
    if ok:
        return True, "ok"
    _note("password prompts: %d, exit %s; %s || key login: %s" % (prompts, rc, text, text2))
    if "permission denied" in text.lower() or (prompts and "AUTH_OK" not in text):
        return False, NOT_AUTHORIZED
    return False, (NOT_AUTHORIZED if prompts else UNREACHABLE)


def run(cmd, data=None, timeout=60):
    """Run cmd on the Controller with the key; if the key is refused, restore it once and retry.
    -> (rc, text). When the Controller can't be logged in to, text is the explanation."""
    rc, text = run_plain(ssh_args(cmd, True), data, timeout)
    if _refused(rc, text):
        ok, msg = ensure_authorized()
        if not ok:
            return 255, msg
        rc, text = run_plain(ssh_args(cmd, True), data, timeout)
    return rc, text
