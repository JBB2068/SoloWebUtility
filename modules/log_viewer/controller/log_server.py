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
  /api/logviewer/findsolo    "Find my Solo": the Solo's last known position from the end of the newest
                             telemetry log that has one (also while armed; only reads the log's end)

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
import math
import os
import re
import socket
import struct
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


# ---------- Find my Solo: the last known position in the telemetry logs ----------
#
# The controller's tlog service keeps writing what the Solo sends until the link is lost, so the end of
# the newest telemetry log holds the Solo's last position even when the Solo is off or crashed. After a
# controller restart the logs move up by one (solo.tlog -> solo.tlog.1), so older logs are searched too.
# Only the end of a log is read (and only the new part while it grows); nothing is written.

MSG_HEARTBEAT, MSG_SYS_STATUS, MSG_GPS_RAW, MSG_GPOS, MSG_HOME = 0, 1, 24, 33, 242
CRC_EXTRA = {MSG_HEARTBEAT: 50, MSG_SYS_STATUS: 124, MSG_GPS_RAW: 24, MSG_GPOS: 104, MSG_HOME: 104}
BASE_LEN = {MSG_HEARTBEAT: 9, MSG_SYS_STATUS: 31, MSG_GPS_RAW: 30, MSG_GPOS: 28, MSG_HOME: 52}
FIND_WINDOWS = (256 * 1024, 2 * 1024 * 1024)    # how much of a log's end to read, then more if no fix there
HOME_HEAD = 1024 * 1024                         # where to look for a home point when the end has none
TRAIL_SECONDS = 3                               # one trail point every this many seconds of log
TRAIL_POINTS = 40
LIVE_SECONDS = 10                               # newest log written this recently = Solo still linked
GOOD_TIME = 1.5e9                               # a clock that reads before 2017 has not been set


def iter_frames(raw, wanted):
    """Good (CRC checked) MAVLink 1/2 frames from the Cube in a piece of .tlog.
    Yields (record time in us or None, msgid, payload padded to its base length, end offset)."""
    raw = bytes(raw)
    buf = bytearray(raw)
    n = len(buf)
    i = 0
    while True:
        m = _MAGIC.search(raw, i)
        if not m:
            return
        i = m.start()
        if buf[i] == 0xFE:
            if i + 6 > n:
                return
            ln = buf[i + 1]
            hdr = 6
            sysid, comp, mid = buf[i + 3], buf[i + 4], buf[i + 5]
            signed = 0
        else:
            if i + 10 > n:
                return
            ln = buf[i + 1]
            hdr = 10
            signed = 13 if (buf[i + 2] & 1) else 0
            sysid, comp = buf[i + 5], buf[i + 6]
            mid = buf[i + 7] | (buf[i + 8] << 8) | (buf[i + 9] << 16)
        crc_end = i + hdr + ln
        end = crc_end + 2 + signed
        if mid in wanted and sysid == 1 and comp == 1 and end <= n:
            crc = _crc_x25(raw[i + 1:crc_end])
            crc = _crc_x25(bytes(bytearray([CRC_EXTRA[mid]])), crc)
            if (crc & 0xFF) == buf[crc_end] and (crc >> 8) == buf[crc_end + 1]:
                pay = raw[i + hdr:crc_end]
                want = BASE_LEN[mid]
                if len(pay) < want:                     # MAVLink 2 drops trailing zero bytes
                    pay = pay + b"\x00" * (want - len(pay))
                t = None
                if i >= 8:
                    t = struct.unpack(">Q", raw[i - 8:i])[0]
                yield t, mid, pay, end
                i = end
                continue
        i += 1


def new_find_state():
    return {"pos": None, "gps": None, "home": None, "bat": None, "armed": None, "trail": [], "last_t": None}


def scan_find(raw, state):
    """Reads a piece of .tlog (oldest first) into state (newer data replaces older). -> end of the last good frame."""
    done = 0
    for t, mid, pay, end in iter_frames(raw, CRC_EXTRA):
        done = end
        if t is not None:
            state["last_t"] = t
        if mid == MSG_GPOS:
            _boot, lat, lon, alt, rel, vx, vy, vz, hdg = struct.unpack("<IiiiihhhH", pay[:28])
            if lat == 0 and lon == 0:
                continue
            state["pos"] = {"lat": lat / 1e7, "lon": lon / 1e7, "alt": alt / 1000.0, "rel": rel / 1000.0,
                            "vx": vx / 100.0, "vy": vy / 100.0, "vz": vz / 100.0,
                            "hdg": None if hdg == 65535 else hdg / 100.0, "t": t}
            tr = state["trail"]
            if t is None or not tr or tr[-1][2] is None or t - tr[-1][2] >= TRAIL_SECONDS * 1e6:
                tr.append([round(lat / 1e7, 7), round(lon / 1e7, 7), t])
                if len(tr) > TRAIL_POINTS:
                    del tr[:len(tr) - TRAIL_POINTS]
        elif mid == MSG_GPS_RAW:
            tus, lat, lon, alt, eph, _epv, vel, _cog, fix, sats = struct.unpack("<QiiiHHHHBB", pay[:30])
            state["gps"] = {"time_us": tus, "lat": lat / 1e7, "lon": lon / 1e7, "alt": alt / 1000.0,
                            "hdop": None if eph == 65535 else eph / 100.0, "vel": vel / 100.0,
                            "fix": fix, "sats": None if sats == 255 else sats, "t": t}
        elif mid == MSG_HOME:
            lat, lon, alt = struct.unpack("<iii", pay[:12])
            if lat or lon:
                state["home"] = {"lat": lat / 1e7, "lon": lon / 1e7, "alt": alt / 1000.0}
        elif mid == MSG_SYS_STATUS:
            mv = struct.unpack("<H", pay[14:16])[0]
            rem = struct.unpack("<b", pay[30:31])[0]
            state["bat"] = {"volts": None if mv in (0, 65535) else mv / 1000.0, "pct": None if rem < 0 else rem}
        elif mid == MSG_HEARTBEAT:
            state["armed"] = bool(bytearray(pay)[6] & 0x80)
    return done


def _read_at(path, start, size):
    with open(path, "rb") as f:
        f.seek(start)
        return f.read(size)


def scan_log_for_fix(path, st, cache):
    """The find state of one log, reading only what is new since the last call."""
    c = cache.get(path)
    if c is not None and st.st_size >= c["size"] and c.get("ino") == getattr(st, "st_ino", None):
        if st.st_size == c["size"]:
            return c["state"]
        if st.st_size - c["done"] <= FIND_WINDOWS[-1]:
            state = c["state"]
            raw = _read_at(path, c["done"], st.st_size - c["done"])
            got = scan_find(raw, state)
            if got:
                c["done"] += got
            c["size"] = st.st_size
            return state
    state = new_find_state()
    done = 0
    for w in FIND_WINDOWS:
        start = max(0, st.st_size - w)
        state = new_find_state()
        got = scan_find(_read_at(path, start, st.st_size - start), state)
        done = start + got if got else start
        if state["pos"] is not None or start == 0:
            break
    if state["home"] is None and st.st_size > FIND_WINDOWS[0]:
        head = new_find_state()
        scan_find(_read_at(path, 0, min(HOME_HEAD, st.st_size)), head)
        state["home"] = head["home"]
    cache[path] = {"size": st.st_size, "done": done, "state": state, "ino": getattr(st, "st_ino", None)}
    return state


def distance_bearing(lat1, lon1, lat2, lon2):
    """Metres and degrees (0 = north) from point 1 to point 2."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 2 * r * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return d, (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def find_solo(log_dir, cache=None, now=None):
    """The Solo's last known position from the controller's telemetry logs (newest log first)."""
    now = time.time() if now is None else now
    cache = {} if cache is None else cache
    found = []
    try:
        names = os.listdir(log_dir)
    except OSError:
        names = []
    for name in names:
        m = TLOG_NAME.match(name)
        if not m:
            continue
        path = os.path.join(log_dir, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_size >= 64:
            found.append((int(m.group(1)) if m.group(1) else 0, path, st))
    found.sort(key=lambda x: x[0])
    for k, path, st in found:
        try:
            s = scan_log_for_fix(path, st, cache)
        except (IOError, OSError):
            continue
        pos, gps = s["pos"], s["gps"]
        source = "position"
        if pos is None and gps is not None and gps["fix"] >= 2 and (gps["lat"] or gps["lon"]):
            pos = {"lat": gps["lat"], "lon": gps["lon"], "alt": gps["alt"], "rel": None, "vx": None, "vy": None,
                   "vz": None, "hdg": None, "t": gps["t"]}
            source = "gps"
        if pos is None:
            continue
        t = pos["t"] / 1e6 if pos["t"] else None
        age = None
        if t is not None and (t > GOOD_TIME) == (now > GOOD_TIME) and (k == 0 or t > GOOD_TIME):
            age = max(0, int(now - t))
        when = None                                      # real time of the fix: GPS time first
        if gps and gps["time_us"] > GOOD_TIME * 1e6:
            when = gps["time_us"] / 1e6
            if gps["t"] and pos["t"]:
                when += (pos["t"] - gps["t"]) / 1e6
        elif t is not None and t > GOOD_TIME:
            when = t
        live = bool(k == 0 and now - st.st_mtime <= LIVE_SECONDS and age is not None and age <= LIVE_SECONDS)
        out = {"found": True, "live": live, "log": "t%d" % k, "older_log": k > 0, "source": source,
               "lat": round(pos["lat"], 7), "lon": round(pos["lon"], 7),
               "alt_msl": round(pos["alt"], 1), "rel_alt": None if pos["rel"] is None else round(pos["rel"], 1),
               "speed": None if pos["vx"] is None else round(math.hypot(pos["vx"], pos["vy"]), 1),
               "climb": None if pos["vz"] is None else round(-pos["vz"], 1),
               "heading": None if pos["hdg"] is None else round(pos["hdg"]),
               "fix": gps["fix"] if gps else None, "sats": gps["sats"] if gps else None,
               "hdop": gps["hdop"] if gps else None, "gps_lost": bool(gps and gps["fix"] < 2),
               "time": None if when is None else int(when), "age": age,
               "armed": s["armed"], "battery": s["bat"], "home": None, "dist_home": None, "bearing_home": None,
               "trail": [[p[0], p[1]] for p in s["trail"]], "now": int(now)}
        last = [round(pos["lat"], 7), round(pos["lon"], 7)]
        if not out["trail"] or out["trail"][-1] != last:
            out["trail"].append(last)
        if s["home"]:
            h = s["home"]
            d, b = distance_bearing(h["lat"], h["lon"], pos["lat"], pos["lon"])
            out["home"] = {"lat": round(h["lat"], 7), "lon": round(h["lon"], 7)}
            out["dist_home"], out["bearing_home"] = int(round(d)), int(round(b)) % 360
        return out
    return {"found": False, "live": False, "now": int(now), "logs": len(found)}


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

    def _find(self):
        with self.server.find_lock:                  # one scan at a time; the cache makes repeats cheap
            return find_solo(self._cfg("log_dir"), self.server.find_cache)

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
            if path == "/api/logviewer/findsolo":
                return self._json(self._find())
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
        self.find_lock = threading.Lock()
        self.find_cache = {}


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
