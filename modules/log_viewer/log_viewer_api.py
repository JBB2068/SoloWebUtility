# -*- coding: utf-8 -*-
"""Flight log viewer backend: read-only access to the Cube's flight logs.

On a Solo the Cube streams its DataFlash log to the Solo's computer, where Open Solo's
dataflash_logger saves each flight as /log/dataflash/<n>.BIN and writes the newest number to
/log/dataflash/LASTLOG.TXT. This module only lists and sends those files.

Isolation rules (see static/log-viewer/ for the frontend):
  * solo_web_app.py loads this one file by path and calls handle() for /api/logviewer/* only.
  * Nothing here touches MAVLink, the Cockpit, flashing, parameters or telemetry. The only
    outside call is a passive armed check (cockpit_mgr.is_vehicle_armed(wake=False)), and any
    failure there is treated as "not armed".
  * Files are opened read-only and sent in small chunks; nothing is parsed on the Solo.

Routes (behind the utility's login):
  GET /api/logviewer/flightlogs     -> {"logs": [{n, name, size, mtime, latest, writing}], "armed": bool}
  GET /api/logviewer/flightlog/<n>  -> the file <n>.BIN (refused while the Solo is armed)

Python 2.7 (Solo) and 3 (tests).
"""
import json
import os
import re
import time

DATAFLASH_DIR = "/log/dataflash"
LASTLOG_FILE = "LASTLOG.TXT"
CHUNK = 64 * 1024
WRITING_SECONDS = 15          # modified this recently = still being written
PREFIX = "/api/logviewer/"
_LOG_NAME = re.compile(r"^(\d{1,6})\.BIN$", re.IGNORECASE)
_LOG_PATH = re.compile(r"^flightlog/(\d{1,6})$")


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
        logs.append({"n": int(m.group(1)), "name": name, "size": st.st_size,
                     "mtime": int(st.st_mtime), "writing": (now - st.st_mtime) < WRITING_SECONDS,
                     "latest": False})
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


def _send_json(req, obj, code=200):
    body = json.dumps(obj).encode("utf-8")
    req.send_response(code)
    req.send_header("Content-Type", "application/json")
    req.send_header("Content-Length", str(len(body)))
    req.send_header("Cache-Control", "no-cache")
    req.end_headers()
    req.wfile.write(body)


def _send_log(req, n, folder=None):
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
        req.send_header("Content-Disposition", 'attachment; filename="solo_flight_%d_%s.BIN"' % (n, stamp))
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


def handle(req, path, query=None, folder=None, armed=None):
    """Answer one /api/logviewer/* request. `req` is the BaseHTTPRequestHandler."""
    armed = _armed if armed is None else armed
    sub = path[len(PREFIX):] if path.startswith(PREFIX) else ""
    try:
        if sub == "flightlogs":
            return _send_json(req, {"dir": folder or DATAFLASH_DIR,
                                    "logs": list_flight_logs(folder), "armed": armed()})
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
