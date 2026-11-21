# -*- coding: utf-8 -*-
"""Installs the flight log viewer on the Solo controller (10.1.1.1) over key-based ssh.

The controller keeps the Solo's telemetry in /log/solo.tlog*; this puts a small read-only web server
there (controller/log_server.py) so flights can be reviewed with only the controller and a phone.

Where things go on the controller:
  /log/logviewer/            server, scripts, the viewer's files (web/), copied .BIN logs (bin/)
                             /log survives controller updates
  /etc/rc.local              one marked block that starts /log/logviewer/run.sh at boot
                             (a controller Full Install / settings reset removes it: install again;
                              the Solo utility does that by itself when it was installed before)

Everything is idempotent. The caller (log_viewer_api.py) only allows install/remove while the Solo is
disarmed. Nothing here touches the Cube, MAVLink or the Cockpit.

Python 2.7 (Solo) and 3 (tests). Uses the Solo's own ssh client like pair_mgr.py does.
"""
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import time

CONTROLLER_IP = "10.1.1.1"
REMOTE_DIR = "/log/logviewer"
PORT = 80
STATE_FILE = "/log/logviewer_controller.json"      # on the Solo: remembers that it was installed
RC_LOCAL = "/etc/rc.local"
RC_BEGIN = "# BEGIN solo-web-utility-logviewer"
RC_END = "# END solo-web-utility-logviewer"
RC_LINE = "(/log/logviewer/run.sh >/dev/null 2>&1 </dev/null &)"
SSH_BASE = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
            "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes",
            "-o", "LogLevel=ERROR"]
HERE = os.path.dirname(os.path.abspath(__file__))
CONTROLLER_SRC = os.path.join(HERE, "controller")
VIEWER_SRC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "static", "log-viewer")
SCRIPTS = ("run.sh", "restart.sh", "stop.sh")
VIEWER_FILES = ("api.js", "chart.js", "dataflash.js", "tlog.js", "flight-panel.js", "log-viewer.js",
                "log-viewer.css", "log-worker.js")
STATUS_CACHE_SECONDS = 20
_cache = {"t": 0, "value": None}


# ---------- running ssh (with a timeout; Python 2.7 has none) ----------

def _run(args, data=None, timeout=20):
    """-> (returncode, output text). returncode -9 when it had to be killed after `timeout` seconds."""
    p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    hit = []

    def _kill():
        hit.append(1)
        try:
            p.kill()
        except Exception:
            pass
    timer = threading.Timer(timeout, _kill)
    timer.start()
    try:
        out = p.communicate(data)[0]
    finally:
        timer.cancel()
    text = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
    return (-9 if hit else p.returncode), text


def _authorize():
    """Restore the Solo's key on the controller (shared helper). -> (ok, message)."""
    d = os.path.dirname(HERE)
    if d not in sys.path:
        sys.path.insert(0, d)
    import controller_ssh
    return controller_ssh.ensure_authorized()


def _ssh(cmd, data=None, timeout=20, runner=None):
    return (runner or _run)(SSH_BASE + ["root@" + CONTROLLER_IP, cmd], data, timeout)


# ---------- the bundle ----------

def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _unix(data):
    return data.replace(b"\r\n", b"\n")


def bundle(controller_src=None, viewer_src=None):
    """[(path relative to /log/logviewer, bytes, mode)] and the bundle id (changes when any file changes)."""
    csrc = controller_src or CONTROLLER_SRC
    vsrc = viewer_src or VIEWER_SRC
    files = [("log_server.py", _unix(_read(os.path.join(csrc, "log_server.py"))), 0o755)]
    for name in SCRIPTS:
        files.append((name, _unix(_read(os.path.join(csrc, name))), 0o755))
    files.append(("web/index.html", _unix(_read(os.path.join(csrc, "index.html"))), 0o644))
    for name in VIEWER_FILES:
        files.append(("web/log-viewer/" + name, _read(os.path.join(vsrc, name)), 0o644))
    h = hashlib.sha1()
    for rel, data, _mode in files:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(data)
    ident = h.hexdigest()[:12]
    files.append(("VERSION", (ident + "\n").encode("ascii"), 0o644))
    return files, ident


def make_tar(files):
    buf = io.BytesIO()
    tar = tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT)
    now = int(time.time())
    for rel, data, mode in files:
        ti = tarfile.TarInfo(rel)
        ti.size = len(data)
        ti.mode = mode
        ti.mtime = now
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        tar.addfile(ti, io.BytesIO(data))
    tar.close()
    return buf.getvalue()


# ---------- /etc/rc.local ----------

def _strip_block(text):
    lines, out, skip = text.split("\n"), [], False
    for ln in lines:
        if ln.strip() == RC_BEGIN:
            skip = True
            continue
        if skip:
            if ln.strip() == RC_END:
                skip = False
            continue
        out.append(ln)
    return "\n".join(out)


def rc_local_with_hook(existing):
    """rc.local text that starts the viewer at boot (other lines kept; the block goes before the last 'exit')."""
    block = [RC_BEGIN, RC_LINE, RC_END]
    if not existing or not existing.strip():
        return "#!/bin/sh\n" + "\n".join(block) + "\nexit 0\n"
    lines = _strip_block(existing.replace("\r\n", "\n")).rstrip("\n").split("\n")
    at = None
    for k in range(len(lines) - 1, -1, -1):
        if lines[k].strip().startswith("exit"):
            at = k
            break
    if at is None:
        lines = lines + block + ["exit 0"]
    else:
        lines = lines[:at] + block + lines[at:]
    return "\n".join(lines) + "\n"


def rc_local_without_hook(existing):
    if not existing:
        return None
    return _strip_block(existing.replace("\r\n", "\n")).rstrip("\n") + "\n"


# ---------- state on the Solo ----------

def _load_state(path=None):
    try:
        with open(path or STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state, path=None):
    try:
        tmp = (path or STATE_FILE) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        getattr(os, "replace", os.rename)(tmp, path or STATE_FILE)
    except Exception:
        pass


def _http_ping(timeout=3):
    try:
        try:
            from urllib2 import urlopen
        except ImportError:
            from urllib.request import urlopen
        res = urlopen("http://%s:%d/api/logviewer/ping" % (CONTROLLER_IP, PORT), timeout=timeout)
        return json.loads(res.read().decode("utf-8"))
    except Exception:
        return None


# ---------- status / install / remove ----------

def status(runner=None, state_path=None, ping=None, use_cache=True, expect=None):
    """What the controller has now. One short ssh call (cached for a few seconds)."""
    now = time.time()
    if use_cache and runner is None and _cache["value"] is not None and now - _cache["t"] < STATUS_CACHE_SECONDS:
        return dict(_cache["value"])
    state = _load_state(state_path)
    out = {"url": "http://%s/" % CONTROLLER_IP, "reachable": False, "installed": False, "hook": False,
           "running": False, "version": None, "current": False,
           "wasInstalled": bool(state.get("installed")), "error": None}
    cmd = ("cat %s/VERSION 2>/dev/null; echo ---; grep -c solo-web-utility-logviewer %s 2>/dev/null; echo ---; "
           "ps | grep -c '[l]og_server.py'" % (REMOTE_DIR, RC_LOCAL))
    rc, text = _ssh(cmd, None, 12, runner)
    parts = [p.strip() for p in text.split("---")]
    if rc != 0 and len(parts) < 3:
        out["error"] = "Cannot reach the controller over ssh. Is the controller on and linked to this Solo?"
    elif len(parts) >= 3:
        out["reachable"] = True
        out["version"] = parts[0].split("\n")[0].strip() or None
        out["installed"] = out["version"] is not None
        out["hook"] = parts[1].split("\n")[0].strip() not in ("", "0")
        out["running"] = parts[2].split("\n")[0].strip() not in ("", "0")
    else:
        out["error"] = "Unexpected answer from the controller."
    if out["reachable"]:
        want = expect
        if want is None:
            try:
                want = bundle()[1]
            except Exception:
                want = None
        out["current"] = bool(out["installed"] and want and out["version"] == want)
        out["serving"] = bool((ping or _http_ping)()) if out["running"] else False
    if runner is None:
        _cache["t"], _cache["value"] = now, dict(out)
    return out


def install(runner=None, state_path=None, ping=None, sleep=None, controller_src=None, viewer_src=None):
    """Copy the viewer to the controller, hook it into boot and start it. -> status dict + "ok"."""
    _cache["value"] = None
    sleep = sleep or time.sleep
    rc, text = _ssh("echo ok", None, 10, runner)
    if (rc != 0 or "ok" not in text) and "permission denied" in text.lower():
        # the controller forgot the Solo's key (settings reset): put it back with the default password
        ok, msg = _authorize()
        if not ok:
            return {"ok": False, "error": msg}
        rc, text = _ssh("echo ok", None, 10, runner)
    if rc != 0 or "ok" not in text:
        return {"ok": False, "error": "Cannot reach the controller over ssh. Is the controller on and linked to this Solo?"}
    files, ident = bundle(controller_src, viewer_src)
    rc, text = _ssh("mkdir -p %s/bin %s/web/log-viewer && cd %s && tar -xf -" % (REMOTE_DIR, REMOTE_DIR, REMOTE_DIR),
                    make_tar(files), 60, runner)
    if rc != 0:
        return {"ok": False, "error": "Copying the viewer to the controller failed: %s" % text.strip()[:200]}
    rc, text = _ssh("cat %s 2>/dev/null" % RC_LOCAL, None, 10, runner)
    existing = text if rc == 0 else ""
    wanted = rc_local_with_hook(existing)
    if wanted != existing:
        rc, text = _ssh("cat > %s.new && chmod 755 %s.new && mv %s.new %s" % (RC_LOCAL, RC_LOCAL, RC_LOCAL, RC_LOCAL),
                        wanted.encode("utf-8"), 20, runner)
        if rc != 0:
            return {"ok": False, "error": "Could not set the controller to start the viewer at boot: %s" % text.strip()[:200]}
    rc, text = _ssh("sh %s/restart.sh" % REMOTE_DIR, None, 25, runner)
    if rc != 0:
        return {"ok": False, "error": "Could not start the viewer on the controller: %s" % text.strip()[:200]}
    serving = None
    for _ in range(8):
        serving = (ping or _http_ping)()
        if serving:
            break
        sleep(1)
    _save_state({"installed": True, "version": ident, "at": int(time.time())}, state_path)
    res = status(runner, state_path, ping, use_cache=False, expect=ident)
    res["ok"] = bool(serving)
    if not serving:
        res["error"] = "Installed, but the viewer did not answer on the controller yet. Try again in a few seconds."
    return res


def remove(runner=None, state_path=None):
    """Stop the viewer and take it out of boot. Copied .BIN logs in /log/logviewer/bin are kept."""
    _cache["value"] = None
    rc, text = _ssh("cat %s 2>/dev/null" % RC_LOCAL, None, 10, runner)
    if rc == 0:
        cleaned = rc_local_without_hook(text)
        if cleaned is not None and cleaned != text:
            rc2, t2 = _ssh("cat > %s.new && chmod 755 %s.new && mv %s.new %s" % (RC_LOCAL, RC_LOCAL, RC_LOCAL, RC_LOCAL),
                           cleaned.encode("utf-8"), 20, runner)
            if rc2 != 0:
                return {"ok": False, "error": "Could not edit the controller's boot script: %s" % t2.strip()[:200]}
    elif rc != 1:
        return {"ok": False, "error": "Cannot reach the controller over ssh. Is the controller on and linked to this Solo?"}
    _ssh("sh %s/stop.sh 2>/dev/null; cd %s && rm -rf web log_server.py run.sh restart.sh VERSION server.log server.log.1"
         % (REMOTE_DIR, REMOTE_DIR), None, 25, runner)
    _save_state({"installed": False, "at": int(time.time())}, state_path)
    res = status(runner, state_path, use_cache=False)
    res["ok"] = True
    return res
