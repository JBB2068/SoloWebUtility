#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ArduPilot Parameter Manager for 3DR Solo / Open Solo.
Reads, modifies, searches, and saves parameters over MAVLink or parameter files.
Compatible with Python 2.7 and Python 3.x.

Features:
- Fast binary MAVFTP download (@PARAM/param.pck) with session reset and struct decoding
- Robust fallback to PARAM_REQUEST_LIST with sliding inactivity timeout and paced batch retries
- Local parameter file management and search
"""

import os
import sys
import time
import json
import struct
import logging
import threading

try:
    from pymavlink import mavutil
except ImportError:
    mavutil = None

logger = logging.getLogger("param_mgr")

DEFAULT_PARAM_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "solo.param")
FALLBACK_PARAM_FILE = "/firmware/solo.param"

# Parameter metadata descriptions for common Solo / latest ArduCopter stable version parameters
PARAM_METADATA = {
    "GPS_TYPE": "GPS type (0: None, 1: AUTO/uBlox, 2: uBlox, 9: UAVCAN)",
    "GPS_NAVFILTER": "Navigation filter (6: Airborne < 1g)",
    "GPS_HDOP_GOOD": "GPS HDOP Good threshold (e.g. 200 = 2.0 HDOP)",
    "COMPASS_ORIENT": "Orientation of external compass (38: ROTATION_PITCH_180_YAW_270, 0: NONE)",
    "COMPASS_AUTO_ROT": "Automatic compass orientation detection (0: Disabled, 1: Enabled)",
    "COMPASS_TYPEMASK": "Mask compass types to ignore (15392)",
    "COMPASS_USE3": "Use 3rd compass (0: Disabled, 1: Enabled)",
    "LGR_ENABLE": "Landing Gear Enable (0: Disabled, 1: Enabled)",
    "LGR_OPTIONS": "Landing gear options (1: Retract on Arm, 2: Deploy on Disarm, 3: Auto in RTL/Land)",
    "LGR_SERVO_DEPLOY": "Landing gear servo deploy PWM (1000-2000)",
    "LGR_SERVO_RTRACT": "Landing gear servo retract PWM (1000-2000)",
    "SERVO6_FUNCTION": "Servo 6 output function (29: Landing Gear, 33: Motor 1)",
    "SERVO7_FUNCTION": "Servo 7 output function (29: Landing Gear)",
    "SERVO8_FUNCTION": "Servo 8 output function (28: Gripper / Bait Drop)",
    "SERVO10_FUNCTION": "Servo 10 output function (28: Relay / Bait Drop Servo)",
    "SERVO10_MIN": "Servo 10 closed PWM pulse width (\xce\xbcs, 1100)",
    "SERVO10_MAX": "Servo 10 open PWM pulse width (\xce\xbcs, 1900)",
    "SERVO10_TRIM": "Servo 10 resting closed PWM pulse width (\xce\xbcs, 1100)",
    "RELAY_PIN": "Relay trigger output pin (54: AUX1 / Solo accessory bay)",
    "RC6_OPTION": "RC channel 6 auxiliary function",
    "RC7_OPTION": "RC channel 7 auxiliary function",
    "RC8_OPTION": "RC channel 8 auxiliary function (167: Solo B Button)",
    "RC10_OPTION": "RC channel 10 auxiliary function (28: Relay 1 toggle)",
    "GRIP_ENABLE": "Gripper Enable (0: Disabled, 1: Enabled)",
    "GRIP_TYPE": "Gripper Type (1: Servo, 2: EPM)",
    "GRIP_GRAB": "Gripper Grab / Closed PWM (1000)",
    "GRIP_RELEASE": "Gripper Release / Open PWM (2000)",
    "WPNAV_SPEED": "Waypoint / Mission horizontal navigation speed (cm/s, e.g. 1500 = 15 m/s)",
    "WPNAV_SPEED_UP": "Waypoint climb speed (cm/s, e.g. 400 = 4 m/s)",
    "WPNAV_SPEED_DN": "Waypoint descent speed (cm/s, e.g. 300 = 3 m/s)",
    "LOIT_SPEED": "Loiter mode max horizontal speed (cm/s, e.g. 1200 = 12 m/s)",
    "LOIT_ACC_MAX": "Loiter max acceleration (cm/s/s)",
    "LOIT_BRK_ACCEL": "Loiter braking acceleration (cm/s/s)",
    "LOIT_BRK_DELAY": "Loiter braking delay (seconds)",
    "ANGLE_MAX": "Maximum lean angle (centidegrees, 2000 = 20\xc2\xb0, 3500 = 35\xc2\xb0)",
    "PILOT_SPEED_UP": "Pilot manual climb rate (cm/s)",
    "PILOT_SPEED_DN": "Pilot manual descent rate (cm/s)",
    "BATT_MONITOR": "Battery monitoring type (5: SMBus / Smart Battery / Solo)",
    "BATT_ARM_VOLT": "Minimum voltage required to arm (Volts)",
    "BATT_LOW_VOLT": "Low battery failsafe trigger voltage (Volts)",
    "BATT_CRT_VOLT": "Critical battery failsafe trigger voltage (Volts)",
    "BATT_LOW_MAH": "Low battery failsafe trigger capacity (mAh)",
    "BATT_CRT_MAH": "Critical battery failsafe trigger capacity (mAh)",
    "BATT_LOW_TIMER": "Low battery voltage timeout before triggering failsafe (seconds)",
    "BATT_FS_LOW_ACT": "Low battery failsafe action (1: Land, 2: RTL, 3: Smart RTL)",
    "BATT_FS_CRT_ACT": "Critical battery failsafe action (1: Land, 2: RTL, 3: Smart RTL)",
    "BATT_FS_VOLTSRC": "Voltage source for battery failsafe calculation (1: Raw voltage)",
    "MOT_BAT_VOLT_MIN": "Motor voltage scaling minimum threshold (Volts)",
    "MOT_BAT_VOLT_MAX": "Motor voltage scaling maximum threshold (Volts)",
    "MOT_BAT_CURR_MAX": "Motor current limit (Amps)",
    "RTL_ALT": "Return to Launch altitude above takeoff (centimeters, 4500 = 45m)",
    "RTL_SPEED": "Return to Launch horizontal speed (cm/s, 800 = 8 m/s)",
    "FENCE_ENABLE": "Geofence enabled (0: Disabled, 1: Enabled)",
    "FENCE_ALT_MAX": "Maximum geofence altitude (meters)",
    "SERIAL4_PROTOCOL": "Serial 4 communication protocol (1: MAVLink)",
    "SERIAL4_BAUD": "Serial 4 baud rate (230: 230400, 115: 115200)"
}


# ==========================================================================
# MAVFTP constants (see https://mavlink.io/en/services/ftp.html)
# ==========================================================================
FTP_PARAM_PATH = "@PARAM/param.pck"

OP_TERMINATE_SESSION = 1
OP_RESET_SESSIONS = 2
OP_OPEN_FILE_RO = 4
OP_READ_FILE = 5
OP_ACK = 128
OP_NAK = 129

NAK_EOF = 6

FTP_HDR_FMT = "<HBBBBBBI"          # seq, session, opcode, size, req_opcode, burst_complete, pad, offset
FTP_HDR_LEN = struct.calcsize(FTP_HDR_FMT)   # 12
FTP_PAYLOAD_LEN = 251
FTP_MAX_DATA = FTP_PAYLOAD_LEN - FTP_HDR_LEN  # 239

PCK_MAGIC = 0x671b
PCK_MAGIC_DEFAULTS = 0x671c

# AP_Param type -> (byte length, struct code)
PCK_TYPES = {
    1: (1, "<b"),   # INT8
    2: (2, "<h"),   # INT16
    3: (4, "<i"),   # INT32
    4: (4, "<f"),   # FLOAT
}


class FtpError(Exception):
    """MAVFTP is unavailable or failed; caller should fall back."""
    pass


class ParamFetchProgress(object):
    """Thread-safe live parameter download progress tracker."""
    def __init__(self):
        self.active = False
        self.current = 0
        self.total = 0
        self.percent = 0
        self.phase = "idle"  # idle, connecting, mavftp, stream, retry, done, error
        self.message = "Idle"
        self.last_update = 0.0
        self._lock = threading.Lock()

    def update(self, current=0, total=0, phase="running", message=""):
        with self._lock:
            self.active = (phase not in ("idle", "done", "error", "incomplete"))
            if current is not None:
                self.current = int(current)
            if total and int(total) > 0:
                self.total = int(total)
            if self.total > 0 and self.current > 0:
                pct = int((float(self.current) / float(self.total)) * 100.0)
                self.percent = max(0, min(100, pct))
            elif phase == "done":
                self.percent = 100
            else:
                self.percent = 0
            self.phase = phase
            if message:
                self.message = message
            self.last_update = time.time()

    def get_status(self):
        with self._lock:
            return {
                "active": self.active,
                "current": self.current,
                "total": self.total,
                "percent": self.percent,
                "phase": self.phase,
                "message": self.message,
                "last_update": self.last_update
            }

    def reset(self):
        with self._lock:
            self.active = False
            self.current = 0
            self.total = 0
            self.percent = 0
            self.phase = "idle"
            self.message = "Idle"
            self.last_update = time.time()


fetch_progress = ParamFetchProgress()


def _noop_log(msg):
    pass


def _clean_name(raw):
    """PARAM_VALUE.param_id (str / bytes / unicode) -> plain native str, NUL-stripped."""
    if isinstance(raw, (bytes, bytearray)) and not isinstance(raw, str):
        raw = bytes(raw).decode("ascii", "ignore")
    elif not isinstance(raw, str):
        try:
            raw = raw.encode("ascii", "ignore")
        except Exception:
            raw = str(raw)
    return str(raw).replace("\x00", "").strip()


def _clean_value(val):
    try:
        if isinstance(val, float) and val == int(val) and abs(val) < 1e15:
            return int(val)
    except (ValueError, OverflowError):
        pass
    return val


def decode_param_pck(data):
    """Decode ArduPilot param.pck bytes.

    Returns (params_list, num_params, total_params, with_defaults) where params_list
    is [(name, value, default_or_None), ...]. Raises ValueError on a malformed file.
    """
    data = bytes(data)
    if len(data) < 6:
        raise ValueError("param.pck too short (%d bytes)" % len(data))
    magic, num_params, total_params = struct.unpack("<HHH", data[0:6])
    if magic == PCK_MAGIC:
        with_defaults = False
    elif magic == PCK_MAGIC_DEFAULTS:
        with_defaults = True
    else:
        raise ValueError("bad param.pck magic 0x%04x" % magic)

    pos = 6
    end = len(data)
    last_name = ""
    params = []
    while len(params) < num_params:
        # leading zero bytes are padding (keeps a value from crossing a read packet)
        while pos < end and ord(data[pos:pos + 1]) == 0:
            pos += 1
        if pos + 2 > end:
            break
        b0 = ord(data[pos:pos + 1])
        b1 = ord(data[pos + 1:pos + 2])
        pos += 2
        ptype = b0 & 0x0f
        flags = (b0 >> 4) & 0x0f
        common_len = b1 & 0x0f
        name_len = ((b1 >> 4) & 0x0f) + 1
        if ptype not in PCK_TYPES:
            raise ValueError("unknown AP_Param type %d at byte %d" % (ptype, pos))
        if pos + name_len > end:
            break
        suffix = data[pos:pos + name_len]
        pos += name_len
        try:
            suffix = suffix.decode("ascii")
        except Exception:
            suffix = suffix.decode("ascii", "ignore")
        name = str(last_name[:common_len] + suffix)
        vlen, vfmt = PCK_TYPES[ptype]
        has_default = bool(flags & 1)
        need = vlen * (2 if has_default else 1)
        if pos + need > end:
            break
        value = struct.unpack(vfmt, data[pos:pos + vlen])[0]
        default = None
        if has_default:
            default = struct.unpack(vfmt, data[pos + vlen:pos + 2 * vlen])[0]
        pos += need
        params.append((name, _clean_value(value), _clean_value(default) if has_default else None))
        last_name = name
    return params, num_params, total_params, with_defaults


class MavFtp(object):
    def __init__(self, mav, log=None, reply_timeout=1.0, retries=5):
        self.mav = mav
        self.log = log or _noop_log
        self.reply_timeout = reply_timeout
        self.retries = retries
        self.seq = 0
        self.session = None

    def _send(self, opcode, session, offset, size, data=b""):
        self.seq = (self.seq + 1) & 0xffff
        hdr = struct.pack(FTP_HDR_FMT, self.seq, session, opcode, size, 0, 0, 0, offset)
        raw = hdr + data
        payload = list(bytearray(raw)) + [0] * (FTP_PAYLOAD_LEN - len(raw))
        self.mav.mav.file_transfer_protocol_send(
            0,
            getattr(self.mav, "target_system", 1) or 1,
            getattr(self.mav, "target_component", 1) or 1,
            payload)
        return self.seq

    def _wait_reply(self, seq, req_opcode, timeout):
        """Wait for the reply to request `seq`. Returns (opcode, session, size, offset, data) or None."""
        end = time.time() + timeout
        while True:
            remaining = end - time.time()
            if remaining <= 0:
                return None
            msg = self.mav.recv_match(type="FILE_TRANSFER_PROTOCOL", blocking=True, timeout=min(remaining, 0.25))
            if msg is None:
                continue
            raw = bytes(bytearray(msg.payload))
            if len(raw) < FTP_HDR_LEN:
                continue
            rseq, rsession, ropcode, rsize, rreq, _burst, _pad, roffset = struct.unpack(FTP_HDR_FMT, raw[:FTP_HDR_LEN])
            if rreq != req_opcode or rseq != ((seq + 1) & 0xffff):
                continue  # stale / unrelated reply
            return ropcode, rsession, rsize, roffset, raw[FTP_HDR_LEN:FTP_HDR_LEN + rsize]

    def transact(self, opcode, session, offset, size, data=b""):
        """Send a request, retrying on timeout. Returns reply tuple; raises FtpError on no reply."""
        for attempt in range(self.retries):
            seq = self._send(opcode, session, offset, size, data)
            reply = self._wait_reply(seq, opcode, self.reply_timeout)
            if reply is not None:
                return reply
        raise FtpError("no reply to FTP opcode %d (offset %d) after %d tries" % (opcode, offset, self.retries))

    def download(self, path, max_bytes=4 * 1024 * 1024, progress_callback=None):
        """Download a whole file as bytes. Raises FtpError if unsupported / failed."""
        if not hasattr(self.mav.mav, "file_transfer_protocol_send"):
            raise FtpError("pymavlink on this system has no FILE_TRANSFER_PROTOCOL message")

        # Clear any stale sessions left by a previous (crashed) transfer; reply is optional
        try:
            self._send(OP_RESET_SESSIONS, 0, 0, 0)
            time.sleep(0.05)
        except Exception as e:
            raise FtpError("cannot send FTP: %s" % str(e))

        opcode, session, size, offset, data = self.transact(OP_OPEN_FILE_RO, 0, 0, len(path) + 0, path.encode("ascii"))
        if opcode != OP_ACK:
            err = ord(data[0:1]) if data else -1
            raise FtpError("OpenFileRO NAK (error %d)" % err)
        file_size = struct.unpack("<I", data[:4])[0] if len(data) >= 4 else None
        self.session = session
        self.log("MAVFTP: opened %s (%s bytes), session %d" % (path, str(file_size), session))
        if progress_callback:
            try:
                progress_callback(0, file_size or 0, "mavftp", "MAVFTP opened %s" % path)
            except Exception:
                pass

        chunks = []
        got = 0
        try:
            while True:
                opcode, rsess, rsize, roffset, rdata = self.transact(OP_READ_FILE, session, got, FTP_MAX_DATA)
                if opcode == OP_NAK:
                    err = ord(rdata[0:1]) if rdata else -1
                    if err == NAK_EOF:
                        break
                    raise FtpError("ReadFile NAK (error %d) at offset %d" % (err, got))
                if roffset != got:
                    raise FtpError("ReadFile offset mismatch (%d != %d)" % (roffset, got))
                if not rdata:
                    break
                chunks.append(rdata)
                got += len(rdata)
                if progress_callback:
                    try:
                        progress_callback(got, file_size or got, "mavftp", "MAVFTP reading param.pck: %d bytes" % got)
                    except Exception:
                        pass
                if got > max_bytes:
                    raise FtpError("file larger than %d bytes" % max_bytes)
                if file_size is not None and got >= file_size:
                    break
        finally:
            try:
                self._send(OP_TERMINATE_SESSION, session, 0, 0)
            except Exception:
                pass
        return b"".join(chunks)


def fetch_via_mavftp(mav, log=None, progress_callback=None):
    """Primary method. Returns (params_dict, expected_total). Raises FtpError / ValueError."""
    log = log or _noop_log
    if progress_callback:
        try:
            progress_callback(0, 0, "mavftp", "Requesting @PARAM/param.pck via MAVFTP...")
        except Exception:
            pass
    ftp = MavFtp(mav, log=log)
    raw = ftp.download(FTP_PARAM_PATH, progress_callback=progress_callback)
    params, num_params, total_params, with_defaults = decode_param_pck(raw)
    if not params:
        raise ValueError("param.pck contained no parameters")
    if len(params) != num_params or num_params < total_params:
        raise ValueError("param.pck incomplete: decoded %d, header says %d of %d"
                         % (len(params), num_params, total_params))
    result = {}
    for name, value, _default in params:
        result[name] = value
    log("MAVFTP: decoded %d parameters from %d bytes" % (len(result), len(raw)))
    if progress_callback:
        try:
            progress_callback(len(result), total_params, "mavftp", "MAVFTP decoded %d / %d parameters" % (len(result), total_params))
        except Exception:
            pass
    return result, total_params


def _ranges(indices):
    """[3,4,5,9] -> '3-5, 9' (for readable diagnostics)."""
    out = []
    start = prev = None
    for i in sorted(indices):
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            out.append(str(start) if start == prev else "%d-%d" % (start, prev))
            start = prev = i
    if start is not None:
        out.append(str(start) if start == prev else "%d-%d" % (start, prev))
    return ", ".join(out[:20]) + (" ..." if len(out) > 20 else "")


def fetch_via_param_messages(mav, log=None, progress_callback=None, quiet_gap=1.5, first_data_timeout=12.0,
                             max_passes=5, extra_passes=7, hard_limit=240.0):
    """Fallback method. Returns (params_dict, expected_total, missing_indices).

    Phase 1: PARAM_REQUEST_LIST with a sliding inactivity timeout.
    Phase 2: PARAM_REQUEST_READ for missing indices. The autopilot silently drops
             read requests when its small request queue is full (and the UDP
             forwarder drops packets), so each pass uses SMALLER batches than the
             last (20, 10, 5, 2, 1 ...) with pacing between requests and a short
             per-batch re-send. Passes beyond `max_passes` are only used while the
             previous pass was still making progress.
    """
    log = log or _noop_log

    target_sys = getattr(mav, "target_system", 1) or 1
    target_comp = getattr(mav, "target_component", 1) or 1
    received = {}          # param_index -> (name, value)
    expected = [0]
    started = time.time()

    def pump(wait):
        msg = mav.recv_match(type="PARAM_VALUE", blocking=True, timeout=wait)
        if msg is None:
            return False
        idx = msg.param_index
        if getattr(msg, "param_count", 0) > 0:
            expected[0] = msg.param_count
        if idx is None or idx < 0 or (expected[0] and idx >= expected[0]):
            return True        # 65535 = reply to a SET, not part of the table
        name = _clean_name(msg.param_id)
        if not name:
            return True
        received[idx] = (name, _clean_value(msg.param_value))
        return True

    def complete():
        return expected[0] > 0 and len(received) >= expected[0]

    def missing_list():
        return [i for i in range(expected[0]) if i not in received]

    # --- Phase 1: full list stream with a SLIDING inactivity timeout ---
    mav.mav.param_request_list_send(target_sys, target_comp)
    last_rx = time.time()
    last_request = last_rx
    while not complete() and time.time() - started < hard_limit:
        if pump(0.25):
            last_rx = time.time()          # reset the timer on every PARAM_VALUE
            if progress_callback and (len(received) % 5 == 0 or len(received) == expected[0]):
                try:
                    progress_callback(len(received), expected[0], "stream", "Fetching %d / %s parameters" % (len(received), str(expected[0] or "...")))
                except Exception:
                    pass
            continue
        idle = time.time() - last_rx
        if not received:
            if time.time() - last_request > 4.0:
                mav.mav.param_request_list_send(target_sys, target_comp)
                last_request = time.time()
            if time.time() - started > first_data_timeout:
                break
        elif idle >= quiet_gap:
            break                           # stream went quiet with gaps; fill them below
    log("PARAM_REQUEST_LIST stream: %d of %s received" % (len(received), str(expected[0] or "?")))

    # --- Phase 2: request missing indices, shrinking batches, progress-aware passes ---
    batch_plan = [20, 10, 5, 2, 1]
    pass_no = 0
    prev_missing = None
    while expected[0] and not complete() and time.time() - started < hard_limit:
        pass_no += 1
        missing = missing_list()
        if pass_no > max_passes:
            if pass_no > max_passes + extra_passes or (prev_missing is not None and len(missing) >= prev_missing):
                break       # out of passes, or the last pass made no progress
        prev_missing = len(missing)
        batch_size = batch_plan[min(pass_no - 1, len(batch_plan) - 1)]
        log("Retry pass %d: %d missing (batch %d): %s" % (pass_no, len(missing), batch_size, _ranges(missing)))
        # let any in-flight stream data land before we start
        t_quiet = time.time()
        while time.time() - t_quiet < 0.4:
            if pump(0.1):
                t_quiet = time.time()
        for b in range(0, len(missing), batch_size):
            batch = [i for i in missing[b:b + batch_size] if i not in received]
            for attempt in range(2 if batch_size > 1 else 3):
                todo = [i for i in batch if i not in received]
                if not todo:
                    break
                for idx in todo:
                    mav.mav.param_request_read_send(target_sys, target_comp, b"", idx)
                    time.sleep(0.01)            # pace requests; avoids overflowing the autopilot queue
                last_rx = time.time()
                while time.time() - last_rx < 0.7 and time.time() - started < hard_limit:
                    if pump(0.1):
                        last_rx = time.time()   # sliding timeout within the batch too
                        if progress_callback and (len(received) % 2 == 0 or len(received) == expected[0]):
                            try:
                                progress_callback(len(received), expected[0], "retry", "Retrying missing %d / %s" % (len(received), str(expected[0] or "...")))
                            except Exception:
                                pass
                        if all(i in received for i in batch):
                            break
    still = missing_list() if expected[0] else []
    if still:
        log("Still missing after %d passes: %s" % (pass_no, _ranges(still)))
    result = {}
    for _idx, (name, value) in received.items():
        result[name] = value
    return result, expected[0] or len(result), still


def fetch_all_params(mav, log=None, progress_callback=None):
    """Download every parameter from an already-connected mavutil connection.

    Returns dict: ok (bool), params (dict), expected (int), method (str), message (str).
    """
    log = log or _noop_log
    if progress_callback:
        try:
            progress_callback(0, 0, "connecting", "Connecting to autopilot...")
        except Exception:
            pass
    try:
        params, total = fetch_via_mavftp(mav, log=log, progress_callback=progress_callback)
        if progress_callback:
            try:
                progress_callback(len(params), total, "done", "Fetched %d parameters via MAVFTP" % len(params))
            except Exception:
                pass
        return {"ok": True, "params": params, "expected": total, "method": "mavftp",
                "message": "Fetched %d parameters via MAVFTP (@PARAM/param.pck)." % len(params)}
    except (FtpError, ValueError, struct.error, AttributeError, TypeError) as e:
        log("MAVFTP unavailable (%s); falling back to PARAM_REQUEST_LIST." % str(e))
        if progress_callback:
            try:
                progress_callback(0, 0, "stream", "MAVFTP not supported; streaming parameters...")
            except Exception:
                pass

    params, total, still_missing = fetch_via_param_messages(mav, log=log, progress_callback=progress_callback)
    ok = bool(params) and not still_missing and len(params) >= total
    if ok:
        msg = "Fetched %d of %d parameters via PARAM_REQUEST_LIST + PARAM_REQUEST_READ." % (len(params), total)
        ph = "done"
    elif params:
        msg = ("Incomplete parameter fetch: received %d of %d (missing indices: %s). Please retry."
               % (len(params), total, _ranges(still_missing)))
        ph = "incomplete"
    else:
        msg = "Connected to the autopilot, but no parameter values were received (timeout)."
        ph = "error"

    if progress_callback:
        try:
            progress_callback(len(params), total, ph, msg)
        except Exception:
            pass

    return {"ok": ok, "params": params, "expected": total, "method": "param_request_list",
            "message": msg, "missing": still_missing}


def commit_param_mavlink(mav, name, value, max_retries=3, timeout_per_attempt=0.6):
    """Send PARAM_SET and wait for matching PARAM_VALUE confirmation from the autopilot.

    Returns (ok, confirmed_val, message).
    """
    if mavutil is None or mav is None:
        return False, None, "No active MAVLink connection to the autopilot."

    pname = _clean_name(name).upper()
    if not pname:
        return False, None, "Invalid parameter name."

    try:
        val_f = float(value)
    except (ValueError, TypeError) as e:
        return False, None, "Invalid numeric parameter value '%s': %s" % (str(value), str(e))

    # Ensure target_system and target_component are valid autopilot IDs (typically 1, 1)
    target_sys = getattr(mav, "target_system", 1)
    if not target_sys or target_sys == 254:
        target_sys = 1
    target_comp = getattr(mav, "target_component", 1)
    if not target_comp or target_comp == 254:
        target_comp = 1

    pname_bytes = pname.encode("ascii") if sys.version_info[0] >= 3 else str(pname)

    confirmed = False
    confirmed_val = None

    for attempt in range(1, max_retries + 1):
        try:
            mav.mav.param_set_send(
                target_sys,
                target_comp,
                pname_bytes,
                val_f,
                mavutil.mavlink.MAV_PARAM_TYPE_REAL32
            )
        except Exception as ex:
            return False, None, "Failed to send PARAM_SET for %s: %s" % (pname, str(ex))

        # Wait for matching PARAM_VALUE confirmation from the drone
        deadline = time.time() + timeout_per_attempt
        while time.time() < deadline:
            msg = mav.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.1)
            if msg is not None:
                ack_name = _clean_name(msg.param_id).upper()
                if ack_name == pname:
                    ack_val = float(msg.param_value)
                    diff = abs(ack_val - val_f)
                    rel_diff = diff / max(abs(val_f), 1.0)
                    if diff < 0.01 or rel_diff < 0.001:
                        confirmed = True
                        confirmed_val = _clean_value(msg.param_value)
                        break
                    else:
                        # Flight controller replied but rejected or clamped the value
                        confirmed = False
                        confirmed_val = _clean_value(msg.param_value)
                        return False, confirmed_val, "Pixhawk rejected value %s for %s (current value: %s)" % (
                            str(val_f), pname, str(confirmed_val))
        if confirmed:
            break

    if confirmed:
        return True, confirmed_val, "Confirmed %s = %s on Pixhawk" % (pname, str(confirmed_val))
    else:
        return False, None, "Pixhawk did not confirm parameter %s after %d attempt(s)." % (pname, max_retries)


def commit_batch_mavlink(mav, param_dict, max_retries=3, timeout_per_attempt=0.6):
    """Send PARAM_SET for multiple parameters and verify each against incoming PARAM_VALUE.

    Returns (all_ok, confirmed_dict, failed_dict, message).
    """
    confirmed = {}
    failed = {}

    for k, v in param_dict.items():
        ok, val, msg = commit_param_mavlink(mav, k, v, max_retries=max_retries, timeout_per_attempt=timeout_per_attempt)
        pname = _clean_name(k).upper()
        if ok:
            confirmed[pname] = val
        else:
            failed[pname] = msg
        time.sleep(0.02)  # 20ms pacing between parameter writes

    all_ok = (len(confirmed) == len(param_dict)) and (len(failed) == 0)
    if all_ok:
        msg = "Successfully committed and confirmed %d parameter(s) on Pixhawk." % len(confirmed)
    elif confirmed:
        msg = "Confirmed %d parameter(s), but %d failed: %s" % (
            len(confirmed), len(failed), ", ".join("%s: %s" % (p, failed[p]) for p in failed))
    else:
        msg = "Failed to commit parameters: autopilot did not confirm updates (%s)." % (
            ", ".join("%s: %s" % (p, failed[p]) for p in failed))

    return all_ok, confirmed, failed, msg


# ==========================================================================
# ParameterManager class
# ==========================================================================
class ParameterManager(object):
    def __init__(self):
        self.params = {}
        self.load_initial_params()

    def load_initial_params(self):
        """Load parameters from local file or defaults."""
        target_file = DEFAULT_PARAM_FILE
        if not os.path.exists(target_file) and os.path.exists(FALLBACK_PARAM_FILE):
            target_file = FALLBACK_PARAM_FILE
        
        if os.path.exists(target_file):
            self.load_from_file(target_file)
        else:
            # Seed with baseline parameters
            self.params = {
                "COMPASS_AUTO_ROT": 0.0,
                "COMPASS_ORIENT": 38.0,
                "SERIAL0_PROTOCOL": 1.0,
                "SERIAL1_PROTOCOL": 1.0,
                "SERIAL4_PROTOCOL": 1.0,
                "SERIAL4_BAUD": 230.0,
                "MNT1_TYPE": 2.0,
                "MNT1_PITCH_MAX": 0.0,
                "MNT1_PITCH_MIN": -90.0,
                "MNT1_YAW_MAX": 45.0,
                "MNT1_YAW_MIN": -45.0,
                "CAM1_TYPE": 3.0,
                "RC6_OPTION": 213.0,
                "RC7_OPTION": 166.0,
                "RC8_OPTION": 167.0,
                "FENCE_MARGIN": 0.0,
                "ARMING_CHECK": 5630.0,
                "BRD_HEAT_LOWMGN": 0.0,
                "BRD_SAFETY_DEFLT": 0.0,
                "ACRO_RP_P": 4.0,
                "ACRO_THR_MID": 0.4,
                "ACRO_YAW_P": 1.0,
                "AHRS_EKF_TYPE": 3.0,
                "AHRS_GPS_USE": 1.0,
                "AHRS_ORIENTATION": 12.0,
                "AHRS_RP_P": 0.21,
                "AHRS_YAW_P": 0.21,
                "ANGLE_MAX": 2000.0,
                "BATT_ARM_MAH": 250.0,
                "BATT_ARM_VOLT": 13.5,
                "BATT_CRT_MAH": 250.0,
                "BATT_CRT_VOLT": 13.5,
                "BATT_FS_CRT_ACT": 2.0,
                "BATT_FS_LOW_ACT": 2.0,
                "BATT_FS_VOLTSRC": 1.0,
                "BATT_LOW_MAH": 520.0,
                "BATT_LOW_TIMER": 10.0,
                "BATT_LOW_VOLT": 14.0,
                "BATT_MONITOR": 5.0,
                "BRD_PWM_VOLT_SEL": 1.0,
                "BRD_SAFETY_MASK": 16368.0,
                "BRD_SAFETYENABLE": 0.0,
                "BRD_TYPE": 3.0,
                "CIRCLE_RADIUS": 3000.0,
                "COMPASS_TYPEMASK": 15392.0,
                "COMPASS_USE3": 0.0,
                "EK2_ENABLE": 0.0,
                "EK3_ENABLE": 1.0,
                "EK3_IMU_MASK": 3.0,
                "FENCE_ACTION": 0.0,
                "FENCE_ALT_MAX": 122.0,
                "FENCE_ENABLE": 1.0,
                "FENCE_RADIUS": 0.0,
                "FENCE_TYPE": 1.0,
                "FLTMODE1": 5.0,
                "FLTMODE2": 5.0,
                "FLTMODE3": 5.0,
                "FLTMODE4": 5.0,
                "FLTMODE5": 5.0,
                "FLTMODE6": 5.0,
                "FRAME_CLASS": 1.0,
                "FRAME_TYPE": 1.0,
                "FS_EKF_ACTION": 2.0,
                "FS_GCS_ENABLE": 0.0,
                "FS_THR_VALUE": 950.0,
                "GPS_HDOP_GOOD": 200.0,
                "GPS_NAVFILTER": 6.0,
                "GPS_TYPE": 1.0,
                "LAND_ALT_LOW": 600.0,
                "LAND_SPEED": 45.0,
                "LAND_SPEED_HIGH": 220.0,
                "LOIT_ACC_MAX": 200.0,
                "LOIT_ANG_MAX": 30.0,
                "LOIT_BRK_ACCEL": 175.0,
                "LOIT_BRK_DELAY": 0.25,
                "LOIT_SPEED": 500.0,
                "MOT_BAT_CURR_MAX": 40.0,
                "MOT_BAT_VOLT_MAX": 16.8,
                "MOT_BAT_VOLT_MIN": 13.2,
                "MOT_PWM_MAX": 1900.0,
                "MOT_PWM_MIN": 1000.0,
                "MOT_SLEW_UP_TIME": 0.25,
                "MOT_SPIN_ARM": 0.08,
                "MOT_SPIN_MIN": 0.12,
                "MOT_SPIN_MAX": 0.965,
                "MOT_THST_EXPO": 0.8,
                "NTF_LED_TYPES": 22.0,
                "NTF_OREO_THEME": 1.0,
                "PHLD_BRAKE_ANGLE": 2500.0,
                "PHLD_BRAKE_RATE": 6.0,
                "PILOT_ACCEL_Z": 100.0,
                "PILOT_SPEED_DN": 133.0,
                "PILOT_SPEED_UP": 133.0,
                "PILOT_THR_BHV": 7.0,
                "PILOT_THR_FILT": 2.0,
                "PILOT_TKOFF_ALT": 214.0,
                "PILOT_TKOFF_DZ": 250.0,
                "RC1_MAX": 2000.0,
                "RC1_MIN": 1000.0,
                "RC2_MAX": 2000.0,
                "RC2_MIN": 1000.0,
                "RC3_MAX": 1890.0,
                "RC3_MIN": 1008.0,
                "RC3_TRIM": 1450.0,
                "RC4_MAX": 2000.0,
                "RC4_MIN": 1000.0,
                "RC6_MAX": 1520.0,
                "RC6_MIN": 1000.0,
                "RC6_TRIM": 1260.0,
                "RC7_MAX": 2000.0,
                "RC7_MIN": 1000.0,
                "RTL_ALT": 4500.0,
                "RTL_CLIMB_MIN": 1000.0,
                "RTL_SPEED": 800.0,
                "SERIAL1_BAUD": 921.0,
                "SERVO1_FUNCTION": 33.0,
                "SERVO2_FUNCTION": 34.0,
                "SERVO3_FUNCTION": 35.0,
                "SERVO4_FUNCTION": 36.0,
                "SERVO6_FUNCTION": 0.0,
                "SERVO7_FUNCTION": 0.0,
                "SERVO8_FUNCTION": 0.0,
                "LGR_ENABLE": 0.0,
                "GRIP_ENABLE": 0.0,
                "GRIP_TYPE": 0.0,
                "SR1_PARAMS": 100.0,
                "THR_DZ": 10.0,
                "WP_YAW_BEHAVIOR": 3.0,
                "WPNAV_SPEED": 300.0,
                "WPNAV_SPEED_DN": 240.0,
                "WPNAV_SPEED_UP": 320.0
            }
            self.save_to_file(DEFAULT_PARAM_FILE)

    def load_from_file(self, filepath):
        """Parse param file (space or comma separated or key=value)."""
        new_params = {}
        try:
            with open(filepath, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    
                    if "," in line:
                        parts = line.split(",", 1)
                    elif "=" in line:
                        parts = line.split("=", 1)
                    elif "\t" in line:
                        parts = line.split("\t", 1)
                    else:
                        parts = line.split(None, 1)
                    
                    if len(parts) == 2:
                        k = parts[0].strip()
                        v_str = parts[1].strip()
                        try:
                            v = float(v_str) if ("." in v_str or "e" in v_str.lower()) else int(float(v_str))
                        except Exception:
                            v = v_str
                        new_params[k] = v
            self.params = new_params
            return True, "Loaded %d parameters." % len(self.params)
        except Exception as e:
            return False, str(e)

    def save_to_file(self, filepath=DEFAULT_PARAM_FILE):
        """Save current parameters to standard param file format."""
        try:
            with open(filepath, "w") as f:
                f.write("# 3DR Solo Parameter File - Open Solo 4.7.2\n")
                for k in sorted(self.params.keys()):
                    val = self.params[k]
                    if isinstance(val, float):
                        if val.is_integer():
                            val_str = str(int(val))
                        else:
                            val_str = ("%.6f" % val).rstrip('0').rstrip('.')
                    else:
                        val_str = str(val)
                    f.write("%s %s\n" % (k, val_str))
            return True, "Saved to %s" % filepath
        except Exception as e:
            return False, str(e)

    def _get_mavlink_connection(self, timeout=4.0):
        """Establish safe UDP MAVLink connection without touching raw serial ports."""
        if mavutil is None:
            return None
        # Primary telemetry endpoints: 14560 is telem_forwarder on Solo, 14550 is fallback
        endpoints = [
            'udpin:127.0.0.1:14560',
            'udpin:0.0.0.0:14560',
            'udpout:127.0.0.1:14560',
            'udpout:10.1.1.10:14560',
            'udpin:127.0.0.1:14550',
            'udpin:0.0.0.0:14550',
            'udpout:127.0.0.1:14550',
            'udpout:10.1.1.10:14550'
        ]
        for ep in endpoints:
            try:
                # Do NOT pass source_component (incompatible with embedded pymavlink on Solo)
                # Use source_system=254 to avoid colliding with Solo Controller (system 255)
                mav = mavutil.mavlink_connection(ep, source_system=254)
                start_t = time.time()
                probe_timeout = min(1.2, timeout)
                while time.time() - start_t < probe_timeout:
                    # Transmit GCS heartbeat so telem_forwarder registers this UDP socket as active client
                    try:
                        mav.mav.heartbeat_send(
                            mavutil.mavlink.MAV_TYPE_GCS,
                            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                            0, 0, 0
                        )
                    except Exception:
                        pass
                    hb = mav.recv_match(type='HEARTBEAT', blocking=True, timeout=0.4)
                    if hb and hb.get_srcSystem() != 254:
                        mav.target_system = hb.get_srcSystem()
                        mav.target_component = hb.get_srcComponent()
                        return mav
                mav.close()
            except Exception:
                pass
        return None

    def fetch_live_mavlink(self, timeout=30.0, progress_callback=None):
        """Fetch all live parameters: MAVFTP @PARAM/param.pck first, PARAM_REQUEST_LIST gap-filling fallback."""
        def _cb(cur, tot=0, ph="running", msg=""):
            fetch_progress.update(cur, tot, ph, msg)
            if progress_callback:
                try:
                    progress_callback(cur, tot, ph, msg)
                except Exception:
                    pass

        fetch_progress.reset()
        fetch_progress.update(0, 0, "connecting", "Connecting to Pixhawk MAVLink...")

        if mavutil is None:
            fetch_progress.update(0, 0, "error", "pymavlink not installed.")
            return False, "pymavlink not installed."
        
        mav = self._get_mavlink_connection(timeout=4.0)
        if not mav:
            fetch_progress.update(0, 0, "error", "Could not connect to Pixhawk MAVLink (timeout waiting for heartbeat).")
            return False, "Could not connect to Pixhawk MAVLink (timeout waiting for heartbeat)."

        try:
            result = fetch_all_params(mav, log=lambda m: logger.info("param fetch: %s", m), progress_callback=_cb)
        except Exception as e:
            try:
                mav.close()
            except Exception:
                pass
            fetch_progress.update(0, 0, "error", str(e))
            return False, str(e)
        try:
            mav.close()
        except Exception:
            pass

        if not result["ok"]:
            fetch_progress.update(len(result.get("params", {})), result.get("expected", 0), "error", result.get("message", "Fetch failed"))
            return False, result["message"]
        for name, val in result["params"].items():
            self.params[name] = val
        self.save_to_file(DEFAULT_PARAM_FILE)
        fetch_progress.update(len(result["params"]), result.get("expected", len(result["params"])), "done", result["message"])
        return True, "Successfully fetched %d live parameters from Pixhawk (%s)." % (
            len(result["params"]), "MAVFTP param.pck" if result["method"] == "mavftp" else "PARAM_REQUEST_LIST")

    def set_param(self, name, value, send_mavlink=True):
        """Set a single parameter and commit to Pixhawk with PARAM_VALUE verification."""
        if not name:
            return False, "Invalid parameter name."
        k = _clean_name(name).upper()
        try:
            num_val = float(value) if "." in str(value) else int(value)
        except ValueError:
            num_val = value

        if send_mavlink and mavutil is not None:
            mav = self._get_mavlink_connection(timeout=3.5)
            if not mav:
                return False, "Could not connect to Pixhawk MAVLink (timeout waiting for heartbeat). Parameter was not saved."
            try:
                ok, confirmed_val, msg = commit_param_mavlink(mav, k, num_val)
            finally:
                try:
                    mav.close()
                except Exception:
                    pass

            if not ok:
                return False, msg
            num_val = confirmed_val

        self.params[k] = num_val
        self.save_to_file(DEFAULT_PARAM_FILE)
        return True, "Parameter %s confirmed and set to %s on Pixhawk." % (k, str(num_val))

    def set_batch_params(self, param_dict, send_mavlink=True):
        """Set multiple parameters at once with PARAM_VALUE verification for every parameter."""
        if not param_dict or not isinstance(param_dict, dict):
            return False, "No parameters provided to save."

        cleaned_dict = {}
        for k, v in param_dict.items():
            name = _clean_name(k).upper()
            try:
                num_val = float(v) if "." in str(v) else int(v)
            except ValueError:
                num_val = v
            cleaned_dict[name] = num_val

        if send_mavlink and mavutil is not None:
            mav = self._get_mavlink_connection(timeout=4.0)
            if not mav:
                return False, "Could not connect to Pixhawk MAVLink (timeout waiting for heartbeat). Parameters were not saved."
            try:
                all_ok, confirmed, failed, msg = commit_batch_mavlink(mav, cleaned_dict)
            finally:
                try:
                    mav.close()
                except Exception:
                    pass

            if not all_ok:
                if confirmed:
                    self.params.update(confirmed)
                    self.save_to_file(DEFAULT_PARAM_FILE)
                return False, msg

            self.params.update(confirmed)
            self.save_to_file(DEFAULT_PARAM_FILE)
            return True, msg
        else:
            self.params.update(cleaned_dict)
            self.save_to_file(DEFAULT_PARAM_FILE)
            return True, "Saved %d parameter(s) locally." % len(cleaned_dict)

    def search_params(self, query=""):
        """Return parameters matching search query with metadata."""
        query = query.strip().upper()
        results = []
        for k in sorted(self.params.keys()):
            desc = PARAM_METADATA.get(k, "")
            val = self.params[k]
            if not query or (query in k) or (query in desc.upper()) or (query in str(val).upper()):
                results.append({
                    "name": k,
                    "value": val,
                    "description": desc,
                    "type": type(val).__name__
                })
        return results

    def export_param_string(self):
        """Return entire param file as string."""
        lines = ["# 3DR Solo Parameter Export - Open Solo 4.7.2"]
        for k in sorted(self.params.keys()):
            val = self.params[k]
            if isinstance(val, float) and val.is_integer():
                val = int(val)
            lines.append("%s %s" % (k, str(val)))
        return "\n".join(lines)


# Singleton instance
mgr = ParameterManager()
