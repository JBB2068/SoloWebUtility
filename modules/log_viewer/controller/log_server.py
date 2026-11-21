#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Flight log viewer server for the Solo controller (runs on the controller, Python 2.7).

Small, read-only HTTP server that shows the controller's own flight data in the same viewer the
Solo utility has on its Parameters page. Open http://10.1.1.1/ with a phone or PC on SoloLink Wi-Fi.

What it serves:
  /                          the viewer page (web/index.html)
  /log-viewer/<file>         the viewer's files (web/log-viewer/*, copied from the Solo utility)
  /api/logviewer/ping        {"ok": true, "version": ...}
  /api/logviewer/flightlogs  the controller's telemetry logs (/log/solo.tlog, .1 .. .9, written by
                             the controller's "tlog" service) and any Cube .BIN logs the Solo copied
                             to <root>/bin/<n>.BIN
  /api/logviewer/flightlog/<id>  one file, with Range support (t0..t9 = telemetry logs, b<n> = .BIN)

Rules it keeps (the controller also runs the radio, video and the Solo link):
  * read-only: files are opened "rb"; nothing is written except its own small log;
  * runs at the lowest CPU priority, sends files in 64 KB pieces, at most 3 downloads at once;
  * a file is refused (409) while the Solo is armed (last Cube heartbeat in /log/solo.tlog), so a
    log is only opened after landing; list and viewer files are always served;
  * listens only on the SoloLink address (10.1.1.1) by default, not on the home Wi-Fi;
  * no parsing of logs here: the browser reads them (in a Web Worker).

Python 2.7 (controller) and 3 (tests). No packages.
"""
from __future__ import print_function

import json
import os
import re
import socket
import sys
import threading
import time

try:                                    # Python 2
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
    from SocketServer import ThreadingMixIn
    from urlparse import urlparse
except ImportError:                     # Python 3 (tests)
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from urllib.parse import urlparse

DEFAULT_ROOT = "/log/logviewer"
DEFAULT_LOG_DIR = "/log"
DEFAULT_BIND = "10.1.1.1"
DEFAULT_PORT = 80
TLOG_NAME = re.compile(r"^solo\.tlog(?:\.(\d{1,2}))?$")
BIN_NAME = re.compile(r"^(\d{1,8})\.BIN$", re.IGNORECASE)
ID_TLOG = re.compile(r"^t(\d{1,2})$")
ID_BIN = re.compile(r"^b(\d{1,8})$")
STATIC_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}\.(js|css|html)$")
CHUNK = 64 * 1024
WRITING_SECONDS = 15            # modified this recently = still being written
MAX_DOWNLOADS = 3
_MAGIC = re.compile(b"[\\xfe\\xfd]")
ARMED_TAIL = 128 * 1024         # how much of the newest telemetry log to look at for the armed state
STALE_SECONDS = 30              # no new telemetry for this long = link gone = not armed
TYPES = {".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".html": "text/html; charset=utf-8"}


# ---------- armed state: last Cube heartbeat in the newest telemetry log ----------

def _crc_x25(data, crc=0xFFFF):
    for b in bytearray(data):
        tmp = b ^ (crc & 0xFF)
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def last_heartbeat_armed(data):
    """data: the tail of a .tlog (records of 8-byte time + MAVLink frame; it may start mid-record).
    Returns True / False for the newest heartbeat from the Cube (system 1, component 1), or None."""
    raw = bytes(data)
    buf = bytearray(raw)
    n = len(buf)
    best = None
    i = 0
    while True:
        m = _MAGIC.search(raw, i)
        if not m:
            break
        i = m.start()
        if buf[i] == 0xFE:
            if i + 6 > n:
                break
            ln = buf[i + 1]
            end = i + 6 + ln + 2
            if end > n:
                break
            sysid, comp, mid = buf[i + 3], buf[i + 4], buf[i + 5]
            pay = i + 6
            crc_end = i + 6 + ln
        else:
            if i + 10 > n:
                break
            ln = buf[i + 1]
            signed = 13 if (buf[i + 2] & 1) else 0
            end = i + 10 + ln + 2 + signed
            if end > n:
                break
            sysid, comp = buf[i + 5], buf[i + 6]
            mid = buf[i + 7] | (buf[i + 8] << 8) | (buf[i + 9] << 16)
            pay = i + 10
            crc_end = i + 10 + ln
        if mid == 0 and sysid == 1 and comp == 1:
            crc = _crc_x25(raw[i + 1:crc_end])
            crc = _crc_x25(b"\x32", crc)                      # HEARTBEAT crc_extra = 50
            if (crc & 0xFF) == buf[crc_end] and (crc >> 8) == buf[crc_end + 1]:
                base = buf[pay + 6] if ln > 6 else 0
                best = bool(base & 0x80)
                i = end
                continue
        i += 1
    return best


def is_armed(log_dir):
    """True only when the newest telemetry log is still growing and its last Cube heartbeat says armed."""
    path = os.path.join(log_dir, "solo.tlog")
    try:
        st = os.stat(path)
        if time.time() - st.st_mtime > STALE_SECONDS:
            return False
        with open(path, "rb") as f:
            f.seek(max(0, st.st_size - ARMED_TAIL))
            data = f.read(ARMED_TAIL)
        return bool(last_heartbeat_armed(data))
    except Exception:
        return False


# ---------- listing ----------

def _tlog_start(path):
    """Time of the first record (seconds since 1970), or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        if len(head) < 8:
            return None
        v = 0
        for b in bytearray(head):
            v = (v << 8) | b
        t = v / 1e6
        return int(t) if t > 1.5e9 else None
    except Exception:
        return None


def list_logs(log_dir, bin_dir, now=None):
    now = time.time() if now is None else now
    logs = []
    try:
        names = os.listdir(log_dir)
    except OSError:
        names = []
    for name in names:
        m = TLOG_NAME.match(name)
        if not m:
            continue
        k = int(m.group(1)) if m.group(1) else 0
        path = os.path.join(log_dir, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_size < 64:
            continue
        logs.append({"id": "t%d" % k, "kind": "tlog", "label": "Telemetry log", "size": st.st_size,
                     "mtime": int(st.st_mtime), "start": _tlog_start(path),
                     "writing": k == 0 and (now - st.st_mtime) < WRITING_SECONDS, "latest": k == 0})
    try:
        names = os.listdir(bin_dir)
    except OSError:
        names = []
    for name in names:
        m = BIN_NAME.match(name)
        if not m:
            continue
        try:
            st = os.stat(os.path.join(bin_dir, name))
        except OSError:
            continue
        n = int(m.group(1))
        logs.append({"id": "b%d" % n, "kind": "bin", "n": n, "label": "Log %d" % n, "size": st.st_size,
                     "mtime": int(st.st_mtime), "start": None, "writing": False, "latest": False})
    logs.sort(key=lambda d: d["mtime"], reverse=True)
    return logs


def resolve(log_dir, bin_dir, log_id):
    """File path for a log id (t0..t99 or b<n>), or None. Only these two shapes are accepted."""
    m = ID_TLOG.match(log_id)
    if m:
        k = int(m.group(1))
        path = os.path.join(log_dir, "solo.tlog" if k == 0 else "solo.tlog.%d" % k)
        return path if os.path.isfile(path) else None
    m = ID_BIN.match(log_id)
    if m:
        want = "%d.BIN" % int(m.group(1))
        try:
            for name in os.listdir(bin_dir):
                if name.upper() == want:
                    return os.path.join(bin_dir, name)
        except OSError:
            pass
    return None


def parse_range(header, size):
    """'bytes=a-b' -> (start, end) inclusive, None when there is no/odd Range header, False when unsatisfiable."""
    if not header:
        return None
    m = re.match(r"^bytes=(\d*)-(\d*)$", header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None
    if m.group(1) == "":
        n = int(m.group(2))
        if n == 0:
            return False
        return (max(0, size - n), size - 1)
    a = int(m.group(1))
    b = int(m.group(2)) if m.group(2) else size - 1
    if a >= size or b < a:
        return False
    return (a, min(b, size - 1))


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "SoloLogViewer/1"
    protocol_version = "HTTP/1.0"
    timeout = 30

    def log_message(self, fmt, *args):
        if self.server.cfg.get("verbose"):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    # -- small helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _cfg(self, key):
        return self.server.cfg[key]

    def _version(self):
        try:
            with open(os.path.join(self._cfg("root"), "VERSION"), "r") as f:
                return f.read().strip() or "0"
        except Exception:
            return "0"

    def _armed(self):
        fn = self.server.cfg.get("armed")
        if fn is not None:
            try:
                return bool(fn())
            except Exception:
                return False
        return is_armed(self._cfg("log_dir"))

    def _static(self, rel):
        base = os.path.join(self._cfg("root"), "web")
        path = os.path.join(base, rel)
        if not os.path.isfile(path):
            return self._json({"error": "Not found."}, 404)
        ext = os.path.splitext(path)[1].lower()
        with open(path, "rb") as f:
            body = f.read()
        if rel == "index.html":
            body = body.replace(b"%VERSION%", self._version().encode("ascii", "replace"))
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_log(self, log_id):
        path = resolve(self._cfg("log_dir"), self._cfg("bin_dir"), log_id)
        if path is None:
            return self._json({"error": "Log %s not found." % log_id[:20]}, 404)
        if self._armed():
            return self._json({"error": "Flight logs can be opened after landing, when the Solo is disarmed."}, 409)
        gate = self.server.gate
        if not gate.acquire(False):
            return self._json({"error": "The controller is busy sending other logs. Try again in a moment."}, 503)
        try:
            try:
                f = open(path, "rb")
            except (IOError, OSError) as ex:
                return self._json({"error": "Cannot open log: %s" % ex}, 500)
            try:
                st = os.fstat(f.fileno())
                size = st.st_size
                rng = parse_range(self.headers.get("Range"), size)
                if rng is False:
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
                start, end = rng if rng else (0, size - 1)
                length = max(0, end - start + 1) if size else 0
                stamp = time.strftime("%Y%m%d-%H%M", time.localtime(st.st_mtime))
                ext = "BIN" if log_id.startswith("b") else "tlog"
                self.send_response(206 if rng else 200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                if rng:
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                self.send_header("Content-Disposition", 'attachment; filename="controller_%s_%s.%s"' % (log_id, stamp, ext))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                if self.command == "HEAD":
                    return None
                f.seek(start)
                left = length
                while left > 0:
                    data = f.read(min(CHUNK, left))
                    if not data:
                        break
                    self.wfile.write(data)
                    left -= len(data)
            except (IOError, OSError, socket.error):
                pass                    # the browser went away; nothing to clean up
            finally:
                f.close()
        finally:
            gate.release()
        return None

    def do_GET(self):
        try:
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/log-viewer/"):
                name = path[len("/log-viewer/"):]
                if not STATIC_NAME.match(name):
                    return self._json({"error": "Not found."}, 404)
                return self._static("log-viewer/" + name)
            if path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            if path == "/api/logviewer/ping":
                return self._json({"ok": True, "version": self._version(), "mode": "controller"})
            if path == "/api/logviewer/flightlogs":
                return self._json({"mode": "controller", "dir": self._cfg("log_dir"), "version": self._version(),
                                   "logs": list_logs(self._cfg("log_dir"), self._cfg("bin_dir")),
                                   "armed": self._armed()})
            if path.startswith("/api/logviewer/flightlog/"):
                return self._send_log(path[len("/api/logviewer/flightlog/"):])
            return self._json({"error": "Not found."}, 404)
        except (IOError, OSError, socket.error):
            return None
        except Exception as ex:
            try:
                self._json({"error": "Log viewer: %s" % ex}, 500)
            except Exception:
                pass
            return None

    do_HEAD = do_GET

    def _no(self):
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _no


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 8

    def __init__(self, addr, cfg):
        HTTPServer.__init__(self, addr, Handler)
        self.cfg = cfg
        self.gate = threading.BoundedSemaphore(MAX_DOWNLOADS)


def make_config(root=DEFAULT_ROOT, log_dir=DEFAULT_LOG_DIR, armed=None, verbose=False):
    return {"root": root, "log_dir": log_dir, "bin_dir": os.path.join(root, "bin"), "armed": armed,
            "verbose": verbose}


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Read-only flight log viewer for the Solo controller")
    ap.add_argument("--bind", default=DEFAULT_BIND)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    try:
        os.nice(19)
    except Exception:
        pass
    cfg = make_config(args.root, args.log_dir, None, args.verbose)
    while True:                          # SoloLink Wi-Fi may not be up yet at boot: keep trying
        try:
            srv = Server((args.bind, args.port), cfg)
            break
        except (socket.error, OSError) as ex:
            sys.stderr.write("log viewer: cannot listen on %s:%d (%s); retrying\n" % (args.bind, args.port, ex))
            time.sleep(5)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
