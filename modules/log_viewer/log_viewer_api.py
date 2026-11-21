# -*- coding: utf-8 -*-
"""Flight log viewer backend: read-only access to the Cube's flight logs.

On a Solo the Cube streams its DataFlash log to the Solo's computer, where Open Solo's
dataflash_logger saves each flight as /log/dataflash/<n>.BIN and writes the newest number to
/log/dataflash/LASTLOG.TXT. This module lists and sends those files, and the copies of Cube SD card logs
that cube_sd.py saved in /log/cubesd.

Isolation rules (see static/log-viewer/ for the frontend):
  * solo_web_app.py loads this one file by path and calls handle() for /api/logviewer/* only.
  * Nothing here touches the Cube's link, the Cockpit, flashing, parameters or telemetry. The only
    outside calls are a passive armed check (cockpit_mgr.is_vehicle_armed(wake=False)), and any
    failure there is treated as "not armed", and two helper modules in this same folder, loaded by
    path: controller_install.py (the controller viewer's installer) and cube_sd.py (copies logs from
    the Cube's SD card, only when the page asks, only while disarmed).
  * Files are opened read-only and sent in small chunks; nothing is parsed on the Solo.

Routes (behind the utility's login):
  GET /api/logviewer/flightlogs     -> {"mode": "solo", "logs": [{id, kind, label, n, name, size, mtime, latest, writing}], "armed": bool}
  GET /api/logviewer/flightlog/<n>  -> the file <n>.BIN (refused while the Solo is armed)
  GET /api/logviewer/flightlog/c<n> -> a copy of Cube SD card log <n> that is on the Solo (same rules)
  GET /api/logviewer/cubesd         -> state of the Cube SD card job and the last list (never contacts the Cube)
  GET /api/logviewer/cubesd/list | /cubesd/copy/<n> | /cubesd/cancel
                                    -> read the Cube's log list / copy one log to the Solo / stop; need the header
                                       "X-Log-Viewer: 1"; list and copy are refused while armed
  GET /api/logviewer/controller     -> what the controller's own viewer has (installed, running, current)
  GET /api/logviewer/controller/install | /remove
                                    -> put the viewer on the controller (see controller_install.py) or take it off;
                                       needs the header "X-Log-Viewer: 1" (so another web page cannot trigger it)
                                       and is refused while armed

Python 2.7 (Solo) and 3 (tests).
"""
import json
import os
import re
import time

DATAFLASH_DIR = "/log/dataflash"
COPIES_DIR = "/log/cubesd"
LASTLOG_FILE = "LASTLOG.TXT"
CHUNK = 64 * 1024
WRITING_SECONDS = 15          # modified this recently = still being written
PREFIX = "/api/logviewer/"
_LOG_NAME = re.compile(r"^(\d{1,6})\.BIN$", re.IGNORECASE)
_LOG_PATH = re.compile(r"^flightlog/(\d{1,6})$")
_CUBE_LOG_PATH = re.compile(r"^flightlog/c(\d{1,8})$")
_CUBE_COPY = re.compile(r"^cubesd/copy/(\d{1,8})$")
_CUBE_NAME = re.compile(r"^(\d{1,8})\.BIN$")


_INSTALLER = []
_CUBESD = []


def _load_sibling(name, filename):
    mod = None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    try:
        try:
            import importlib.util as _ilu
            spec = _ilu.spec_from_file_location(name, path)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except ImportError:
            import imp
            mod = imp.load_source(name, path)
    except Exception:
        mod = None
    return mod


def _cubesd():
    """The Cube SD card job manager (cube_sd.py, loaded by path on first use). None if it cannot load."""
    if not _CUBESD:
        mod = _load_sibling("solo_log_viewer_cube_sd", "cube_sd.py")
        _CUBESD.append(mod.manager() if mod is not None else None)
    return _CUBESD[0]


def _installer():
    """controller_install.py, loaded by path (this module is itself loaded by path). None if it cannot load."""
    if not _INSTALLER:
        mod = None
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "controller_install.py")
        try:
            try:
                import importlib.util as _ilu
                spec = _ilu.spec_from_file_location("solo_log_viewer_controller", path)
                mod = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
            except ImportError:
                import imp
                mod = imp.load_source("solo_log_viewer_controller", path)
        except Exception:
            mod = None
        _INSTALLER.append(mod)
    return _INSTALLER[0]


def _armed():
    """Passive: last known state only, never wakes the telemetry link. Unknown = not armed."""
    try:
        import cockpit_mgr
        return bool(cockpit_mgr.is_vehicle_armed(wake=False))
    except Exception:
        return False


def _lastlog(folder):
    try:
        with open(os.path.join(folder, LASTLOG_FILE), "r") as f:
            return int(f.read().strip().split()[0])
    except Exception:
        return None


def list_flight_logs(folder=None, now=None):
    folder = folder or DATAFLASH_DIR
    now = time.time() if now is None else now
    logs = []
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    for name in names:
        m = _LOG_NAME.match(name)
        if not m:
            continue
        path = os.path.join(folder, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        n = int(m.group(1))
        logs.append({"id": str(n), "kind": "bin", "label": "Log %d" % n, "n": n, "name": name,
                     "size": st.st_size, "mtime": int(st.st_mtime),
                     "writing": (now - st.st_mtime) < WRITING_SECONDS, "latest": False})
    logs.sort(key=lambda d: d["n"], reverse=True)
    last = _lastlog(folder)
    for d in logs:
        if d["n"] == last:
            d["latest"] = True
            break
    else:
        if logs:
            logs[0]["latest"] = True
    return logs


def list_cube_copies(folder=None):
    """Cube SD card logs already copied to the Solo (never contacts the Cube)."""
    folder = folder or COPIES_DIR
    logs = []
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    for name in names:
        m = _CUBE_NAME.match(name)
        if not m:
            continue
        try:
            st = os.stat(os.path.join(folder, name))
        except OSError:
            continue
        n = int(m.group(1))
        logs.append({"id": "c%d" % n, "kind": "bin", "source": "cube", "label": "Cube SD log %d" % n, "n": n,
                     "name": name, "size": st.st_size, "mtime": int(st.st_mtime), "writing": False, "latest": False})
    logs.sort(key=lambda d: d["mtime"], reverse=True)
    return logs


def _send_json(req, obj, code=200):
    body = json.dumps(obj).encode("utf-8")
    req.send_response(code)
    req.send_header("Content-Type", "application/json")
    req.send_header("Content-Length", str(len(body)))
    req.send_header("Cache-Control", "no-cache")
    req.end_headers()
    req.wfile.write(body)


def _send_log(req, n, folder=None, prefix="solo_flight"):
    folder = folder or DATAFLASH_DIR
    path = os.path.join(folder, "%d.BIN" % n)
    if not os.path.isfile(path):
        # dataflash_logger writes upper case; accept any case without listing tricks
        alt = [x for x in (os.listdir(folder) if os.path.isdir(folder) else [])
               if x.upper() == "%d.BIN" % n]
        if not alt:
            return _send_json(req, {"error": "Flight log %d not found." % n}, 404)
        path = os.path.join(folder, alt[0])
    try:
        f = open(path, "rb")
    except (IOError, OSError) as ex:
        return _send_json(req, {"error": "Cannot open flight log %d: %s" % (n, ex)}, 500)
    try:
        size = os.fstat(f.fileno()).st_size
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(os.fstat(f.fileno()).st_mtime))
        req.send_response(200)
        req.send_header("Content-Type", "application/octet-stream")
        req.send_header("Content-Length", str(size))
        req.send_header("Content-Disposition", 'attachment; filename="%s_%d_%s.BIN"' % (prefix, n, stamp))
        req.send_header("Cache-Control", "no-cache")
        req.end_headers()
        left = size
        while left > 0:
            data = f.read(min(CHUNK, left))
            if not data:
                break
            req.wfile.write(data)
            left -= len(data)
    except (IOError, OSError):
        pass                    # the browser went away; nothing to clean up
    finally:
        f.close()


def _ours(req):
    """The page sets this header; another web page cannot (it would need a cross-origin preflight)."""
    try:
        hdrs = getattr(req, "headers", None)
        return hdrs is not None and hdrs.get("X-Log-Viewer") == "1"
    except Exception:
        return False


def _cubesd_call(req, sub, armed, mgr):
    """/api/logviewer/cubesd[/list|/copy/<n>|/cancel]."""
    mgr = mgr if mgr is not None else _cubesd()
    if mgr is None:
        return _send_json(req, {"error": "Reading the Cube's SD card is not available."}, 503)
    if sub == "cubesd":
        res = mgr.status()
        res["available"] = True
        return _send_json(req, res)
    m = _CUBE_COPY.match(sub)
    if sub not in ("cubesd/list", "cubesd/cancel") and not m:
        return _send_json(req, {"error": "Unknown log viewer request."}, 404)
    if not _ours(req):
        return _send_json(req, {"error": "Not allowed from here."}, 403)
    if sub == "cubesd/cancel":
        mgr.cancel()
        return _send_json(req, {"ok": True})
    if armed():
        return _send_json(req, {"error": "Logs can be fetched from the Cube after landing, when it is disarmed."}, 409)
    ok, msg = mgr.start_list(armed) if sub == "cubesd/list" else mgr.start_copy(int(m.group(1)), armed)
    res = mgr.status()
    res["ok"] = ok
    if not ok:
        res["error"] = msg
    return _send_json(req, res, 200 if ok else 409)


def _controller_call(req, sub, armed, installer):
    """/api/logviewer/controller[/install|/remove]."""
    inst = installer if installer is not None else _installer()
    if inst is None:
        return _send_json(req, {"error": "The controller viewer installer is not available."}, 503)
    if sub == "controller":
        return _send_json(req, inst.status())
    # changing the controller: only with our own header, only while disarmed
    if not _ours(req):
        return _send_json(req, {"error": "Not allowed from here."}, 403)
    if armed():
        return _send_json(req, {"error": "The controller can be changed after landing, when the Solo is disarmed."}, 409)
    res = inst.install() if sub == "controller/install" else inst.remove()
    return _send_json(req, res, 200 if res.get("ok") else 500)


def handle(req, path, query=None, folder=None, armed=None, installer=None, copies=None, cubesd=None):
    """Answer one /api/logviewer/* request. `req` is the BaseHTTPRequestHandler."""
    armed = _armed if armed is None else armed
    sub = path[len(PREFIX):] if path.startswith(PREFIX) else ""
    try:
        if sub == "flightlogs":
            return _send_json(req, {"mode": "solo", "dir": folder or DATAFLASH_DIR,
                                    "logs": list_flight_logs(folder) + list_cube_copies(copies), "armed": armed()})
        if sub == "cubesd" or sub.startswith("cubesd/"):
            return _cubesd_call(req, sub, armed, cubesd)
        if sub in ("controller", "controller/install", "controller/remove"):
            return _controller_call(req, sub, armed, installer)
        mc = _CUBE_LOG_PATH.match(sub)
        if mc:
            if armed():
                return _send_json(req, {"error": "Flight logs can be opened after landing, "
                                                 "when the Solo is disarmed."}, 409)
            n = int(mc.group(1))
            if not os.path.isfile(os.path.join(copies or COPIES_DIR, "%d.BIN" % n)):
                return _send_json(req, {"error": "Cube SD log %d not found." % n}, 404)
            return _send_log(req, n, copies or COPIES_DIR, "cube_log")
        m = _LOG_PATH.match(sub)
        if m:
            if armed():
                return _send_json(req, {"error": "Flight logs can be opened after landing, "
                                                 "when the Solo is disarmed."}, 409)
            return _send_log(req, int(m.group(1)), folder)
        return _send_json(req, {"error": "Unknown log viewer request."}, 404)
    except Exception as ex:
        try:
            _send_json(req, {"error": "Log viewer: %s" % ex}, 500)
        except Exception:
            pass
