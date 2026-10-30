#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
🛸 3DR Solo Cockpit Manager
Telemetry & Video Streaming Engine for 3DR Solo Web Utility.
Provides:
  - Live MAVLink v1/v2 telemetry parsing from ArduPilot over local UDP.
  - Video stream proxy receiving H.264 RTP from Sololink and broadcasting to browser.
  - Zero external dependencies - compatible with Python 2.7 & 3.x.
"""

import os
import sys
import math
import re
import time
import socket
import select
import struct
import threading

try:
    import queue
except ImportError:
    import Queue as queue

# Environment detection
IS_ON_SOLO = os.path.exists("/etc/sololink.conf") or os.path.exists("/firmware")
SOLO_COPTER_IP = "127.0.0.1" if IS_ON_SOLO else "10.1.1.10"
SOLO_COPTER_PORT = 14560
# telem_forwarder's local client ("inject") port on the Solo -- the same endpoint Open Solo's
# own shot manager uses (udpout:127.0.0.1:14560). Every local client gets its own copy of
# every MAVLink packet, so nothing is taken from Solex or anyone else. NEVER send to the
# Controller's 10.1.1.1:14550 from here: its telem_ctrl takes any sender with the Solo's IP
# as "the Solo's new address" and routes Solex's uplink (parameter requests) to us instead
# of the Pixhawk -- that is what left Solex stuck at "Reading Vehicle Parameters".
# Port 14550 on the Solo carries traffic *down* to the Controller/GCS, so not that either.
SOLO_TELEM_INJECT = ('127.0.0.1', 14560)
MAVLINK_IDLE_SECS = 10.0        # no browser for this long: close the MAVLink socket entirely
STREAM_MISSING_SECS = 3.0       # only ask for telemetry streams when ATTITUDE is really missing
SOLO_CONTROLLER_IP = "10.1.1.1"
SOLO_VIDEO_TCP_PORT = 5502
SOLO_VIDEO_UDP_PORT = 5600
# The Controller's app_server gives video to ONE app at a time and only takes the next one when
# the current app disconnects. Solex (or the 3DR app) also connects to the Solo's shot manager on
# TCP 5507, so a connection there from another device means an app wants video: the utility
# then gives up the video slot and only takes it back once that app has gone.
SOLO_SHOTMGR_PORT = 5507
APP_GONE_SECS = 3.0             # app must be gone this long before the utility takes video back


def find_app_on_shotmanager(port=SOLO_SHOTMGR_PORT, proc_file="/proc/net/tcp"):
    """IP of a device with an ESTABLISHED TCP connection to the Solo's shot manager, or None.
    Local (127.x) connections and the Solo's own address are ignored."""
    try:
        f = open(proc_file)
        try:
            lines = f.readlines()[1:]
        finally:
            f.close()
    except Exception:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) < 4 or parts[3] != "01":          # 01 = ESTABLISHED
            continue
        try:
            lport = int(parts[1].split(":")[1], 16)
            rhex = parts[2].split(":")[0]
        except Exception:
            continue
        if lport != port or len(rhex) != 8:
            continue
        ip = ".".join(str(int(rhex[i:i + 2], 16)) for i in (6, 4, 2, 0))   # little-endian
        if ip.startswith("127.") or ip in ("0.0.0.0", "10.1.1.10"):
            continue
        return ip
    return None

# ArduCopter Flight Modes
ARDUCOPTER_MODES = {
    0: "STABILIZE",
    1: "ACRO",
    2: "ALT_HOLD",
    3: "AUTO",
    4: "GUIDED",
    5: "LOITER",
    6: "RTL",
    7: "CIRCLE",
    9: "LAND",
    11: "DRIFT",
    13: "SPORT",
    14: "FLIP",
    15: "AUTOTUNE",
    16: "POSHOLD",
    17: "BRAKE",
    18: "THROW",
    19: "AVOID_ADSB",
    20: "GUIDED_NOGPS",
    21: "SMART_RTL",
    22: "FLOWHOLD",
    23: "FOLLOW",
    24: "ZIGZAG",
}

MODE_NAME_TO_ID = {v: k for k, v in ARDUCOPTER_MODES.items()}

GPS_FIX_TYPES = {
    0: "No GPS",
    1: "No Fix",
    2: "2D Fix",
    3: "3D Fix",
    4: "DGPS 3D",
    5: "RTK Float",
    6: "RTK Fixed"
}


HEARTBEAT_CRC_EXTRA = 50
AUTOPILOT_VERSION_MSGID = 148
AUTOPILOT_VERSION_CRC_EXTRA = 178
STATUSTEXT_MSGID = 253
STATUSTEXT_CRC_EXTRA = 83
# Messages whose content we act on are only accepted with a valid CRC
# (STATUSTEXT too: a random 0xFE/0xFD byte must never put garbage text on the OSD bar)
CHECKED_CRC_EXTRA = {0: HEARTBEAT_CRC_EXTRA, AUTOPILOT_VERSION_MSGID: AUTOPILOT_VERSION_CRC_EXTRA,
                     STATUSTEXT_MSGID: STATUSTEXT_CRC_EXTRA}

# OSD messages bar: only real vehicle STATUSTEXT messages (never the utility's own notes)
OSD_HISTORY_MAX = 20      # kept on the server
OSD_HISTORY_SENT = 15     # sent to the browser with each telemetry poll
MAV_SEVERITY_NAMES = {0: "EMERGENCY", 1: "ALERT", 2: "CRITICAL", 3: "ERROR",
                      4: "WARNING", 5: "NOTICE", 6: "INFO", 7: "DEBUG"}


def clean_statustext(raw):
    """STATUSTEXT text field (bytes/str, up to 50 chars, NUL padded) -> clean text."""
    if not isinstance(raw, str):
        raw = raw.decode('ascii', 'ignore')       # Python 3 bytes
    else:
        try:
            raw = raw.decode('ascii', 'ignore')   # Python 2 str -> unicode
        except AttributeError:
            pass                                  # Python 3 str
    text = raw.split('\x00', 1)[0]               # text ends at the first NUL
    return ' '.join(text.split())                 # trim and collapse whitespace
_ARDUCOPTER_BANNER = re.compile(r"ArduCopter V(\d+\.\d+(?:\.\d+)?)")


def _frame_crc_ok(data, start, end, crc_extra):
    """data[start:end] = frame without the magic byte and without the CRC;
    the two CRC bytes follow at data[end:end+2]."""
    if end + 2 > len(data):
        return False
    body = bytearray(data[start:end])
    body.append(crc_extra)
    got = bytearray(data[end:end + 2])
    return calc_x25_crc(body) == (got[0] | (got[1] << 8))


def _heartbeat_crc_ok(data, start, end):
    """HEARTBEAT frame check (CRC_EXTRA 50)."""
    return _frame_crc_ok(data, start, end, HEARTBEAT_CRC_EXTRA)


def calc_x25_crc(buf):
    """Compute X.25 / CRC16-MCRF4XX checksum for MAVLink packets."""
    crc = 0xffff
    for b in bytearray(buf):
        tmp = b ^ (crc & 0xff)
        tmp = (tmp ^ (tmp << 4)) & 0xff
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xffff
    return crc


class TelemetryState(object):
    """Thread-safe store for all live flight and vehicle telemetry."""
    def __init__(self):
        self.lock = threading.Lock()
        self.source = "mavlink"
        self.last_packet_time = 0.0
        self.packet_count = 0
        self.packet_rate = 0.0
        self.mavlink_connected = False
        self.mavlink_idle = True          # socket closed because no browser is using telemetry
        self.last_msg_time = {}           # msgid -> last time seen
        self.last_vehicle_heartbeat = 0.0
        self.copter_ip = SOLO_COPTER_IP
        self.copter_port = SOLO_COPTER_PORT
        
        # Vehicle status
        self.armed = False
        # ArduCopter version reported live by the Cube (AUTOPILOT_VERSION / boot banner)
        self.cube_version = None
        self.flight_mode = "LOITER"
        self.custom_mode = 5
        self.system_status = 3
        self.vibe_clip = 0
        self.last_status_text = "Listening for Solo MAVLink..."
        self.status_severity = 6
        self.mavlink_messages = [
            {"time": time.strftime("%H:%M:%S"), "text": "Listening for Solo MAVLink...", "severity": 6}
        ]
        self.msg_seq = 0              # bumps on every new log message (lets clients skip the log)
        # OSD messages bar (vehicle STATUSTEXT only). session changes when the utility
        # restarts, so the browser knows ids started over.
        self.osd_session = "%d" % int(time.time() * 1000)
        self.osd_seq = 0
        self.osd_messages = []
        
        # Attitude (Degrees)
        self.pitch = 0.0
        self.roll = 0.0
        self.yaw = 0.0
        self.pitch_rate = 0.0
        self.roll_rate = 0.0
        self.yaw_rate = 0.0
        
        # VFR HUD
        self.airspeed = 0.0
        self.groundspeed = 0.0
        self.heading = 0.0
        self.throttle = 0
        self.alt_msl = 0.0
        self.alt_rel = 0.0
        self.climb_rate = 0.0
        
        # Battery (Solo 4S LiPo)
        self.voltage = 0.0
        self.current = 0.0
        self.battery_remaining = 0
        self.consumed_mah = 0
        self.cell_count = 4
        self.cell_voltage = 0.0
        self.battery_temp = 25.0
        self.est_flight_time_min = 0.0
        
        # GPS
        self.gps_fix_type = 0
        self.gps_fix_label = "No GPS"
        self.satellites = 0
        self.hdop = 99.99
        self.vdop = 99.99
        self.lat = 0.0
        self.lon = 0.0
        
        # Navigation / Home
        self.home_lat = 0.0
        self.home_lon = 0.0
        self.home_alt = 0.0
        self.home_set = False
        self.distance_to_home = 0.0
        self.bearing_to_home = 0.0
        
        # Link quality
        self.rssi = 100
        self.remrssi = 100
        self.rxerrors = 0
        self.fixed = 0

    def add_message(self, text, severity=6):
        """Append a status message and preserve up to 100 entries."""
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "text": text,
            "severity": severity
        }
        self.last_status_text = text
        self.status_severity = severity
        self.mavlink_messages.append(entry)
        self.msg_seq += 1
        if len(self.mavlink_messages) > 100:
            self.mavlink_messages = self.mavlink_messages[-100:]

    def add_osd_message(self, text, severity, now=None):
        """Record one vehicle STATUSTEXT for the OSD bar. Caller holds self.lock."""
        try:
            severity = int(severity)
        except (TypeError, ValueError):
            severity = 6
        severity = min(max(severity, 0), 7)
        if now is None:
            now = time.time()
        self.osd_seq += 1
        entry = {
            "id": self.osd_seq,
            "text": text,
            "severity": severity,
            "severity_name": MAV_SEVERITY_NAMES[severity],
            "timestamp": round(now, 3),
            "time": time.strftime("%H:%M:%S", time.localtime(now)),
        }
        self.osd_messages.append(entry)
        if len(self.osd_messages) > OSD_HISTORY_MAX:
            self.osd_messages = self.osd_messages[-OSD_HISTORY_MAX:]
        return entry

    def osd_payload(self):
        """OSD bar data for the telemetry JSON. Caller holds self.lock."""
        recent = [dict(m) for m in self.osd_messages[-OSD_HISTORY_SENT:]]
        return {
            "session": self.osd_session,
            "seq": self.osd_seq,
            "latest": recent[-1] if recent else None,
            "messages": recent,
        }

    def update_distance_bearing(self):
        """Compute range and bearing to home waypoint if fix available."""
        if not self.home_set and self.gps_fix_type >= 3 and (self.lat != 0.0 or self.lon != 0.0):
            self.home_lat = self.lat
            self.home_lon = self.lon
            self.home_alt = self.alt_msl
            self.home_set = True
            self.add_message("Home position locked: %.6f, %.6f" % (self.home_lat, self.home_lon), 6)

        if self.home_set and (self.lat != 0.0 or self.lon != 0.0):
            dlat = math.radians(self.lat - self.home_lat)
            dlon = math.radians(self.lon - self.home_lon)
            lat1 = math.radians(self.home_lat)
            lat2 = math.radians(self.lat)
            a = math.sin(dlat/2.0)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2.0)**2
            c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
            self.distance_to_home = round(6371000.0 * c, 1)

            y = math.sin(dlon) * math.cos(lat2)
            x = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(dlon)
            brg = math.degrees(math.atan2(y, x))
            self.bearing_to_home = round((brg + 360.0) % 360.0, 1)

    def to_dict(self):
        with self.lock:
            now = time.time()
            is_alive = self.mavlink_connected and (now - self.last_packet_time < 3.0)

            vid_status = {}
            try:
                if 'video_manager' in globals() and video_manager:
                    vid_status = video_manager.get_status()
            except Exception:
                pass

            hdg = self.heading
            if hdg < 0 or hdg > 360:
                hdg = self.yaw
            norm_hdg = round((hdg % 360.0 + 360.0) % 360.0, 1)
            norm_yaw = round((self.yaw % 360.0 + 360.0) % 360.0, 1)

            return {
                # High-level link status
                "source": self.source,
                "mavlink_idle": self.mavlink_idle,
                "connected": is_alive,
                "mavlink_online": is_alive,
                "mavlink_connected": is_alive,
                "server_time": now,

                # Flat keys for backward compatibility
                "armed": self.armed,
                "flight_mode": self.flight_mode or "UNKNOWN",
                "custom_mode": self.custom_mode,
                "system_status": self.system_status,
                "pitch": round(self.pitch, 2),
                "roll": round(self.roll, 2),
                "yaw": norm_yaw,
                "airspeed": round(self.airspeed, 1),
                "groundspeed": round(self.groundspeed, 1),
                "heading": norm_hdg,
                "throttle": int(self.throttle),
                "alt_msl": round(self.alt_msl, 1),
                "alt_rel": round(self.alt_rel, 1),
                "climb_rate": round(self.climb_rate, 2),
                "voltage": round(self.voltage, 2),
                "cell_voltage": round(self.cell_voltage, 2),
                "current": round(self.current, 1),
                "battery_remaining": int(self.battery_remaining),
                "consumed_mah": int(self.consumed_mah),
                "battery_temp": round(self.battery_temp, 1),
                "est_flight_time_min": round(self.est_flight_time_min, 1),
                "gps_fix_type": int(self.gps_fix_type),
                "gps_fix_label": self.gps_fix_label,
                "satellites": int(self.satellites),
                "hdop": round(self.hdop, 2),
                "vdop": round(self.vdop, 2),
                "lat": self.lat,
                "lon": self.lon,
                "distance_to_home": round(self.distance_to_home, 1),
                "bearing_to_home": round(self.bearing_to_home, 1),
                "packet_rate": round(self.packet_rate, 1),
                "rssi": int(self.rssi),
                "last_status_text": self.last_status_text,
                "status_severity": self.status_severity,
                "mavlink_messages": self.mavlink_messages[-50:],

                # Nested schema expected by cockpit.js
                "attitude": {
                    "pitch": round(self.pitch, 2),
                    "roll": round(self.roll, 2),
                    "yaw": norm_yaw,
                    "pitch_rate": round(self.pitch_rate, 2),
                    "roll_rate": round(self.roll_rate, 2),
                    "yaw_rate": round(self.yaw_rate, 2)
                },
                "hud": {
                    "airspeed": round(self.airspeed, 1),
                    "groundspeed": round(self.groundspeed, 1),
                    "heading": norm_hdg,
                    "throttle": int(self.throttle),
                    "alt_msl": round(self.alt_msl, 1),
                    "alt_rel": round(self.alt_rel, 1),
                    "climb_rate": round(self.climb_rate, 2)
                },
                "battery": {
                    "voltage": round(self.voltage, 2),
                    "cell_voltage": round(self.cell_voltage, 2),
                    "current": round(self.current, 1),
                    "remaining_pct": int(self.battery_remaining),
                    "consumed_mah": int(self.consumed_mah),
                    "temperature_c": round(self.battery_temp, 1),
                    "flight_time_min": round(self.est_flight_time_min, 1)
                },
                "gps": {
                    "fix_type": int(self.gps_fix_type),
                    "fix_label": self.gps_fix_label,
                    "satellites": int(self.satellites),
                    "hdop": round(self.hdop, 2),
                    "vdop": round(self.vdop, 2),
                    "lat": self.lat,
                    "lon": self.lon,
                    "alt_msl": round(self.alt_msl, 1),
                    "distance_to_home": round(self.distance_to_home, 1),
                    "bearing_to_home": round(self.bearing_to_home, 1)
                },
                "nav": {
                    "distance_to_home": round(self.distance_to_home, 1),
                    "bearing_to_home": round(self.bearing_to_home, 1)
                },
                "status": {
                    "armed": self.armed,
                    "flight_mode": self.flight_mode or "UNKNOWN",
                    "custom_mode": self.custom_mode,
                    "copter_endpoint": "127.0.0.1:14560",
                    "packet_rate_hz": round(self.packet_rate, 1),
                    "rssi_pct": int(self.rssi),
                    "vibe_clip": int(self.vibe_clip),
                    "messages": self.mavlink_messages[-50:]
                },
                "messages": self.mavlink_messages[-50:],
                "messages_seq": self.msg_seq,
                "osd": self.osd_payload(),
                "video": vid_status
            }


class LiveMAVLinkClient(threading.Thread):
    """Low-latency direct UDP MAVLink client."""
    def __init__(self, state, host=SOLO_COPTER_IP, port=SOLO_COPTER_PORT):
        super(LiveMAVLinkClient, self).__init__()
        self.daemon = True
        self.state = state
        self.host = host
        self.port = port
        self.running = False
        self.sock = None
        self.seq = 0
        self.last_heartbeat_sent = 0.0
        self.last_stream_req_sent = 0.0
        self.last_version_req_sent = 0.0
        self.last_rate_time = time.time()
        self.pkt_counter = 0

    def get_socket(self):
        if self.sock is not None:
            return self.sock
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('0.0.0.0', 0))
            s.setblocking(False)
            self.sock = s
            return self.sock
        except Exception:
            return None

    def send_packet(self, packet_bytes):
        s = self.get_socket()
        if not s:
            return
        for dest in (SOLO_TELEM_INJECT,):
            try:
                s.sendto(packet_bytes, dest)
            except Exception:
                pass

    def build_mavlink_v1_packet(self, msgid, payload, crc_extra):
        self.seq = (self.seq + 1) & 0xff
        sysid = 254
        compid = 1
        plen = len(payload)
        header = struct.pack('<BBBBBB', 0xfe, plen, self.seq, sysid, compid, msgid)
        crc_data = header[1:] + payload + (bytes([crc_extra]) if sys.version_info[0] >= 3 else chr(crc_extra))
        crc = calc_x25_crc(crc_data)
        return header + payload + struct.pack('<H', crc)

    def send_gcs_heartbeat(self):
        """Send GCS heartbeat (msg ID 0, CRC extra 50)."""
        payload = struct.pack('<IBBBBB', 0, 6, 8, 0, 0, 3)
        pkt = self.build_mavlink_v1_packet(0, payload, 50)
        self.send_packet(pkt)

    def request_data_streams(self):
        """Request ArduPilot to stream telemetry at 10 Hz (msg ID 66, CRC extra 148)."""
        payload = struct.pack('<HBBBB', 10, 1, 1, 0, 1)
        pkt = self.build_mavlink_v1_packet(66, payload, 148)
        self.send_packet(pkt)

    def send_arm_command(self, arm=True):
        """Send MAV_CMD_COMPONENT_ARM_DISARM (400) via COMMAND_LONG."""
        param1 = 1.0 if arm else 0.0
        payload = struct.pack('<fffffffHBBB', param1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 400, 1, 1, 0)
        pkt = self.build_mavlink_v1_packet(76, payload, 152)
        self.send_packet(pkt)

    def request_autopilot_version(self):
        """MAV_CMD_REQUEST_AUTOPILOT_CAPABILITIES (520) -> the Cube answers with AUTOPILOT_VERSION."""
        payload = struct.pack('<fffffffHBBB', 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 520, 1, 1, 0)
        pkt = self.build_mavlink_v1_packet(76, payload, 152)
        self.send_packet(pkt)

    def send_set_mode_command(self, custom_mode):
        """Send SET_MODE (msg ID 11, CRC extra 89)."""
        payload = struct.pack('<IBB', int(custom_mode), 1, 1)
        pkt = self.build_mavlink_v1_packet(11, payload, 89)
        self.send_packet(pkt)

    def _close_socket(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def run(self):
        self.running = True
        while self.running:
            if not mavlink_wanted():
                # Nobody is using the web utility: no socket, no heartbeats, no requests.
                # telem_forwarder drops us as a client and Solex has the link to itself.
                if self.sock is not None or not self.state.mavlink_idle:
                    self._close_socket()
                    with self.state.lock:
                        self.state.mavlink_connected = False
                        self.state.mavlink_idle = True
                    self.last_heartbeat_sent = 0.0
                    self.last_stream_req_sent = 0.0
                    self.last_version_req_sent = 0.0
                time.sleep(0.2)
                continue
            if self.state.mavlink_idle:
                with self.state.lock:
                    self.state.mavlink_idle = False
            if getattr(self, 'paused', False):
                # Telemetry hold released (e.g. during native calibration)
                if self.sock is not None:
                    try:
                        self.sock.close()
                    except Exception:
                        pass
                    self.sock = None
                time.sleep(0.1)
                continue
            s = self.get_socket()
            if not s:
                time.sleep(1.0)
                continue

            now = time.time()

            # Heartbeat every 1 second
            if now - self.last_heartbeat_sent >= 1.0:
                self.send_gcs_heartbeat()
                self.last_heartbeat_sent = now

            # Ask for telemetry streams only if ATTITUDE isn't arriving already (normally the
            # Controller/Solex have set the rates; overriding them would disturb Solex).
            if now - self.last_stream_req_sent >= 5.0 and \
                    now - self.state.last_msg_time.get(30, 0.0) > STREAM_MISSING_SECS and \
                    now - self.state.last_vehicle_heartbeat < STREAM_MISSING_SECS:
                self.request_data_streams()
                self.last_stream_req_sent = now

            # Ask the Cube which ArduCopter it runs: every 5 s until known, then every 60 s
            if now - self.last_version_req_sent >= (60.0 if self.state.cube_version else 5.0):
                self.request_autopilot_version()
                self.last_version_req_sent = now

            # Packet rate calculation
            if now - self.last_rate_time >= 1.0:
                dt = now - self.last_rate_time
                with self.state.lock:
                    self.state.packet_rate = round(self.pkt_counter / dt, 1)
                self.pkt_counter = 0
                self.last_rate_time = now

            try:
                rlist, _, _ = select.select([s], [], [], 0.05)
            except Exception:
                time.sleep(0.05)
                continue

            if not rlist:
                if now - self.state.last_packet_time > 3.0:
                    with self.state.lock:
                        self.state.mavlink_connected = False
                        # the Cube may be rebooting into different firmware: ask again
                        self.state.cube_version = None
                continue

            try:
                data, addr = s.recvfrom(2048)
                if data:
                    self.pkt_counter += 1
                    self.parse_mavlink_bytes(data)
            except Exception:
                pass

    def parse_mavlink_bytes(self, data):
        idx = 0
        datalen = len(data)

        while idx < datalen:
            magic = ord(data[idx:idx+1]) if sys.version_info[0] < 3 else data[idx]

            # MAVLink v1 (0xFE)
            if magic == 0xFE:
                if idx + 6 > datalen:
                    break
                plen = ord(data[idx+1:idx+2]) if sys.version_info[0] < 3 else data[idx+1]
                sysid = ord(data[idx+3:idx+4]) if sys.version_info[0] < 3 else data[idx+3]
                compid = ord(data[idx+4:idx+5]) if sys.version_info[0] < 3 else data[idx+4]
                msgid = ord(data[idx+5:idx+6]) if sys.version_info[0] < 3 else data[idx+5]
                total_len = 6 + plen + 2
                if idx + total_len > datalen:
                    break
                # The stream is scanned for 0xFE, so random bytes can look like a
                # frame. Never let an unchecked "HEARTBEAT" change the armed state.
                if msgid in CHECKED_CRC_EXTRA and not _frame_crc_ok(data, idx + 1, idx + 6 + plen, CHECKED_CRC_EXTRA[msgid]):
                    idx += 1
                    continue
                payload = data[idx+6:idx+6+plen]
                self.decode_payload(msgid, payload, sysid, compid)
                idx += total_len

            # MAVLink v2 (0xFD)
            elif magic == 0xFD:
                if idx + 10 > datalen:
                    break
                plen = ord(data[idx+1:idx+2]) if sys.version_info[0] < 3 else data[idx+1]
                incompat_flags = ord(data[idx+2:idx+3]) if sys.version_info[0] < 3 else data[idx+2]
                sysid = ord(data[idx+5:idx+6]) if sys.version_info[0] < 3 else data[idx+5]
                compid = ord(data[idx+6:idx+7]) if sys.version_info[0] < 3 else data[idx+6]
                b7 = ord(data[idx+7:idx+8]) if sys.version_info[0] < 3 else data[idx+7]
                b8 = ord(data[idx+8:idx+9]) if sys.version_info[0] < 3 else data[idx+8]
                b9 = ord(data[idx+9:idx+10]) if sys.version_info[0] < 3 else data[idx+9]
                msgid = b7 | (b8 << 8) | (b9 << 16)
                sig_len = 13 if (incompat_flags & 0x01) else 0
                total_len = 10 + plen + 2 + sig_len
                if idx + total_len > datalen:
                    break
                if msgid in CHECKED_CRC_EXTRA and not _frame_crc_ok(data, idx + 1, idx + 10 + plen, CHECKED_CRC_EXTRA[msgid]):
                    idx += 1
                    continue
                payload = data[idx+10:idx+10+plen]
                self.decode_payload(msgid, payload, sysid, compid)
                idx += total_len
            else:
                idx += 1

    def decode_payload(self, msgid, payload, sysid=1, compid=1):
        if sysid == 254:
            return

        now = time.time()
        with self.state.lock:
            if not self.state.mavlink_connected:
                self.state.add_message("Connected to Solo MAVLink on %s:%d" % (self.state.copter_ip, self.state.copter_port), 6)
            
            self.state.last_packet_time = now
            self.state.mavlink_connected = True
            self.state.last_msg_time[msgid] = now

            try:
                # HEARTBEAT (0)
                if msgid == 0 and len(payload) >= 9:
                    custom_mode, mtype, autopilot, base_mode, sys_status = struct.unpack('<IBBBB', payload[:8])
                    if (autopilot == 3 or mtype in (1, 2, 3, 4, 12, 13, 14, 15)) and sysid not in (254, 255) \
                            and compid == 1 and mtype not in (6, 26, 27):
                        new_mode = ARDUCOPTER_MODES.get(custom_mode, "MODE %d" % custom_mode)
                        if self.state.flight_mode != new_mode:
                            self.state.add_message("Mode: %s" % new_mode, 6)
                            self.state.flight_mode = new_mode
                            self.state.custom_mode = custom_mode
                        
                        self.state.last_vehicle_heartbeat = now
                        is_armed = bool(base_mode & 128)
                        if self.state.armed != is_armed:
                            try:
                                sys.stdout.write("[%s] [telemetry] %s from sysid=%d compid=%d type=%d autopilot=%d base_mode=0x%02x\n" % (
                                    time.strftime("%H:%M:%S"), "ARMED" if is_armed else "DISARMED",
                                    sysid, compid, mtype, autopilot, base_mode))
                                sys.stdout.flush()
                            except Exception:
                                pass
                            self.state.add_message("MOTORS ARMED" if is_armed else "MOTORS DISARMED", 4 if is_armed else 6)
                            self.state.armed = is_armed
                            # home Wi-Fi is dropped by wifi_mgr.start_arm_interlock()
                        
                        self.state.system_status = sys_status

                # SYS_STATUS (1)
                elif msgid == 1 and len(payload) >= 19:
                    v_mv, c_ca, rem_pct = struct.unpack('<Hhb', payload[14:19])
                    if v_mv > 0:
                        self.state.voltage = round(v_mv / 1000.0, 2)
                        self.state.cell_voltage = round(self.state.voltage / 4.0, 2)
                    if c_ca >= 0:
                        self.state.current = round(c_ca / 100.0, 1)
                    if rem_pct >= 0:
                        if self.state.battery_remaining > 20 and rem_pct <= 20:
                            self.state.add_message("WARNING: Low Battery (%d%%)" % rem_pct, 4)
                        self.state.battery_remaining = rem_pct
                        if self.state.current > 2.0:
                            rem_ah = (rem_pct / 100.0) * 5.2
                            self.state.est_flight_time_min = round((rem_ah / self.state.current) * 60.0, 1)

                # GPS_RAW_INT (24)
                elif msgid == 24 and len(payload) >= 30:
                    t_usec, lat_e7, lon_e7, alt_mm, eph, epv, vel, cog, fix_t, sats = struct.unpack('<QiiiHHHHBB', payload[:30])
                    if fix_t >= 2 and self.state.gps_fix_type < 2:
                        self.state.add_message("GPS 3D Fix acquired (%d sats)" % sats, 6)
                    self.state.gps_fix_type = fix_t
                    self.state.gps_fix_label = GPS_FIX_TYPES.get(fix_t, "Fix %d" % fix_t)
                    self.state.satellites = sats
                    self.state.hdop = round(eph / 100.0, 2)
                    self.state.vdop = round(epv / 100.0, 2)
                    if fix_t >= 2:
                        self.state.lat = lat_e7 / 1e7
                        self.state.lon = lon_e7 / 1e7
                        self.state.alt_msl = round(alt_mm / 1000.0, 1)
                        self.state.update_distance_bearing()

                # ATTITUDE (30)
                elif msgid == 30 and len(payload) >= 28:
                    time_boot, roll, pitch, yaw, rspeed, pspeed, yspeed = struct.unpack('<Iffffff', payload[:28])
                    self.state.roll = round(math.degrees(roll), 2)
                    self.state.pitch = round(math.degrees(pitch), 2)
                    self.state.yaw = round((math.degrees(yaw) + 360.0) % 360.0, 2)
                    self.state.roll_rate = round(math.degrees(rspeed), 2)
                    self.state.pitch_rate = round(math.degrees(pspeed), 2)
                    self.state.yaw_rate = round(math.degrees(yspeed), 2)

                # GLOBAL_POSITION_INT (33)
                elif msgid == 33 and len(payload) >= 28:
                    time_boot, lat_e7, lon_e7, alt_msl_mm, alt_rel_mm, vx, vy, vz, hdg_cdeg = struct.unpack('<IiiiihhhH', payload[:28])
                    self.state.lat = lat_e7 / 1e7
                    self.state.lon = lon_e7 / 1e7
                    self.state.alt_msl = round(alt_msl_mm / 1000.0, 1)
                    self.state.alt_rel = round(alt_rel_mm / 1000.0, 1)
                    self.state.heading = round(hdg_cdeg / 100.0, 1)
                    self.state.climb_rate = round(-vz / 100.0, 2)
                    self.state.update_distance_bearing()

                # VFR_HUD (74)
                elif msgid == 74 and len(payload) >= 20:
                    aspd, gspd, hdg, thr, alt, climb = struct.unpack('<ffhHff', payload[:20])
                    self.state.airspeed = round(aspd, 1)
                    self.state.groundspeed = round(gspd, 1)
                    self.state.heading = round(float(hdg), 1)
                    self.state.throttle = int(thr)
                    self.state.alt_rel = round(alt, 1)
                    self.state.climb_rate = round(climb, 2)

                # BATTERY_STATUS (147)
                elif msgid == 147 and len(payload) >= 35:
                    consumed_mah, energy, temp = struct.unpack('<iih', payload[:10])
                    if consumed_mah > 0:
                        self.state.consumed_mah = consumed_mah
                    if temp > -100:
                        self.state.battery_temp = round(temp / 100.0, 1)

                # COMMAND_ACK (77)
                elif msgid == 77 and len(payload) >= 3:
                    cmd, result = struct.unpack('<HB', payload[:3])
                    if cmd == 520:
                        return   # our own background version request; not shown in the HUD log
                    res_map = {0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED", 3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS", 6: "CANCELLED"}
                    cmd_map = {400: "ARM/DISARM", 176: "DO_SET_MODE", 20: "NAV_TAKEOFF", 21: "NAV_LAND", 22: "NAV_RTL"}
                    cname = cmd_map.get(cmd, "CMD_%d" % cmd)
                    rname = res_map.get(result, "CODE_%d" % result)
                    self.state.add_message("ACK: %s -> %s" % (cname, rname), 4 if result != 0 else 6)

                # STATUSTEXT (253): goes to the message log AND the OSD bar
                elif msgid == STATUSTEXT_MSGID and len(payload) >= 2:
                    sev = ord(payload[0:1]) if sys.version_info[0] < 3 else payload[0]
                    raw_text = clean_statustext(payload[1:min(len(payload), 51)])
                    if raw_text:
                        self.state.add_message(raw_text, sev)
                        self.state.add_osd_message(raw_text, sev, now)
                        m = _ARDUCOPTER_BANNER.search(raw_text)
                        if m and compid == 1:
                            self.state.cube_version = m.group(1)
                            _remember_cube_version(m.group(1))

                # AUTOPILOT_VERSION (148): flight_sw_version = major<<24 | minor<<16 | patch<<8 | type
                elif msgid == AUTOPILOT_VERSION_MSGID and compid == 1:
                    payload = payload + b"\x00" * max(0, 20 - len(payload))   # MAVLink 2 trims trailing zeros
                    fsw = struct.unpack('<I', payload[16:20])[0]
                    major, minor, patch = (fsw >> 24) & 0xff, (fsw >> 16) & 0xff, (fsw >> 8) & 0xff
                    if major:
                        self.state.cube_version = "%d.%d.%d" % (major, minor, patch)
                        _remember_cube_version(self.state.cube_version)

                # RC_CHANNELS_RAW (35)
                elif msgid == 35 and len(payload) >= 22:
                    rssi = ord(payload[21:22]) if sys.version_info[0] < 3 else payload[21]
                    self.state.rssi = int(rssi * 100.0 / 255.0)

            except Exception:
                pass


def parse_rtp_h264(data, fu_buffer):
    """RFC 6184 H.264 RTP depacketizer for raw Annex-B NAL units (no loss handling;
    kept for compatibility -- the live pipeline uses RtpH264Depacketizer)."""
    if len(data) < 12:
        return [], fu_buffer
    payload = data[12:]
    if not payload:
        return [], fu_buffer
    
    nals = []
    nal_header = ord(payload[0:1]) if sys.version_info[0] < 3 else payload[0]
    nal_type = nal_header & 0x1F

    if 1 <= nal_type <= 23:
        nals.append(b'\x00\x00\x00\x01' + payload)
        fu_buffer = b''
    elif nal_type == 24:
        idx = 1
        while idx + 2 <= len(payload):
            nsize = struct.unpack('>H', payload[idx:idx+2])[0]
            idx += 2
            if idx + nsize <= len(payload):
                nals.append(b'\x00\x00\x00\x01' + payload[idx:idx+nsize])
            idx += nsize
        fu_buffer = b''
    elif nal_type == 28:
        if len(payload) < 2:
            return [], fu_buffer
        fu_header = ord(payload[1:2]) if sys.version_info[0] < 3 else payload[1]
        start_bit = bool(fu_header & 0x80)
        end_bit = bool(fu_header & 0x40)
        orig_type = fu_header & 0x1F
        reconstructed_nal_byte = struct.pack('B', (nal_header & 0xE0) | orig_type)
        
        if start_bit:
            fu_buffer = b'\x00\x00\x00\x01' + reconstructed_nal_byte + payload[2:]
        else:
            if fu_buffer:
                fu_buffer += payload[2:]
        
        if end_bit and fu_buffer:
            nals.append(fu_buffer)
            fu_buffer = b''
    
    return nals, fu_buffer


def _byte(buf, i):
    return ord(buf[i:i + 1]) if sys.version_info[0] < 3 else buf[i]


RTP_MAX_LOSS_GAP = 500               # a bigger forward jump is a stream restart, not loss
RTP_RESYNC_LATE_RUN = 8              # this many 'late' packets in a row = the stream restarted


class RtpH264Depacketizer(object):
    """RFC 6184 depacketizer that checks RTP sequence numbers.

    A fragmented NAL (FU-A) that lost any packet is dropped instead of being glued
    together from the pieces that did arrive: a truncated or merged slice is worse for
    the browser's decoder than a missing one. Later frames still go through (the
    decoder conceals the gap until the next keyframe), so the picture keeps moving."""

    def __init__(self):
        self.expected_seq = None
        self.ssrc = None
        self.fu_parts = None          # list of byte strings for the FU-A being rebuilt
        self.lost_packets = 0
        self.dropped_nals = 0
        self.late_packets = 0
        self.late_run = 0             # late packets in a row (a restarted stream looks like this)
        self.stream_restarts = 0

    def _restart(self):
        self.stream_restarts += 1
        self.late_run = 0
        if self.fu_parts is not None:
            self.fu_parts = None
            self.dropped_nals += 1

    def feed(self, data):
        """One RTP packet in -> list of complete Annex-B NAL units out."""
        if len(data) < 12:
            return []
        b0 = _byte(data, 0)
        if (b0 >> 6) != 2:                       # not RTP version 2
            return []
        seq = struct.unpack('>H', data[2:4])[0]
        hdr_len = 12 + 4 * (b0 & 0x0F)           # CSRC list
        if b0 & 0x10 and len(data) >= hdr_len + 4:   # header extension
            ext_words = struct.unpack('>H', data[hdr_len + 2:hdr_len + 4])[0]
            hdr_len += 4 + 4 * ext_words
        payload = data[hdr_len:]
        if b0 & 0x20 and payload:                # padding
            pad = _byte(payload, len(payload) - 1)
            payload = payload[:len(payload) - pad] if pad <= len(payload) else b''

        ssrc = struct.unpack('>I', data[8:12])[0]
        if ssrc != self.ssrc:
            # The Solo restarted its encoder (new SSRC and a random new sequence number):
            # follow the new stream instead of treating it as loss or as late packets.
            if self.ssrc is not None:
                self._restart()
            self.ssrc = ssrc
            self.expected_seq = None
        if self.expected_seq is not None and seq != self.expected_seq:
            gap = (seq - self.expected_seq) & 0xFFFF
            if gap < 0x8000 and gap <= RTP_MAX_LOSS_GAP:   # packets went missing
                self.lost_packets += gap
                self.late_run = 0
                if self.fu_parts is not None:
                    self.fu_parts = None
                    self.dropped_nals += 1
            elif gap < 0x8000:                    # far ahead: the stream jumped, resync
                self._restart()
            else:                                 # late / duplicate packet: ignore it ...
                self.late_packets += 1
                self.late_run += 1
                if self.late_run < RTP_RESYNC_LATE_RUN:
                    return []
                self._restart()                   # ... unless they keep coming: resync
        else:
            self.late_run = 0
        self.expected_seq = (seq + 1) & 0xFFFF

        if not payload:
            return []
        nal_header = _byte(payload, 0)
        nal_type = nal_header & 0x1F
        out = []
        if 1 <= nal_type <= 23:
            if self.fu_parts is not None:        # FU-A never finished
                self.fu_parts = None
                self.dropped_nals += 1
            out.append(b'\x00\x00\x00\x01' + payload)
        elif nal_type == 24:                     # STAP-A
            idx = 1
            while idx + 2 <= len(payload):
                nsize = struct.unpack('>H', payload[idx:idx + 2])[0]
                idx += 2
                if nsize and idx + nsize <= len(payload):
                    out.append(b'\x00\x00\x00\x01' + payload[idx:idx + nsize])
                idx += nsize
        elif nal_type == 28 and len(payload) >= 2:   # FU-A
            fu_header = _byte(payload, 1)
            if fu_header & 0x80:                 # start
                if self.fu_parts is not None:
                    self.dropped_nals += 1
                rebuilt = struct.pack('B', (nal_header & 0xE0) | (fu_header & 0x1F))
                self.fu_parts = [b'\x00\x00\x00\x01', rebuilt, payload[2:]]
            elif self.fu_parts is not None:
                self.fu_parts.append(payload[2:])
            # (a middle/end piece without its start is simply discarded)
            if fu_header & 0x40 and self.fu_parts is not None:
                out.append(b''.join(self.fu_parts))
                self.fu_parts = None
        return out


def _read_ue(bits, pos):
    """Exp-Golomb ue(v) from a '0'/'1' string. Returns (value, new_pos) or (None, pos)."""
    zeros = 0
    while pos < len(bits) and bits[pos] == '0':
        zeros += 1
        pos += 1
    if pos >= len(bits) or zeros > 16:
        return None, pos
    pos += 1
    if pos + zeros > len(bits):
        return None, pos
    rest = int(bits[pos:pos + zeros], 2) if zeros else 0
    return (1 << zeros) - 1 + rest, pos + zeros


def slice_is_intra(nal):
    """True for an I (or SI) slice. nal = Annex-B NAL with its 4-byte start code."""
    head = bytearray(nal[5:17])
    raw = bytearray()
    zeros = 0
    for b in head:                      # undo emulation prevention (00 00 03)
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        raw.append(b)
        zeros = zeros + 1 if b == 0 else 0
    bits = ''.join(format(b, '08b') for b in raw)
    first_mb, pos = _read_ue(bits, 0)
    if first_mb is None:
        return False
    slice_type, pos = _read_ue(bits, pos)
    return slice_type is not None and slice_type % 5 in (2, 4)


def relabel_as_idr(nal):
    """Mark a slice as IDR so the browser's muxer (JMuxer) treats it as a sync frame.
    This is what the utility always did for a viewer's first frame; it is now only
    used for I-slices and as the fallback when no keyframe arrives in time."""
    hdr = _byte(nal, 4)
    new = (hdr & 0xE0) | 5
    return nal[:4] + (chr(new) if sys.version_info[0] < 3 else bytes([new])) + nal[5:]


# Per-viewer buffering: a viewer that falls further behind than this is skipped
# forward to the next keyframe instead of building up delay.
VIEWER_MAX_BACKLOG_FRAMES = 8          # ~1/3 s at the Solo's 24 fps
GOP_CACHE_MAX_FRAMES = 72              # ~3 s; longer keyframe intervals just aren't cached
GOP_CACHE_FRESH_SECS = 0.5             # the cache is dropped after a gap in the video
KEYFRAME_WAIT_SECS = 1.0               # a viewer waits this long for an IDR/I-frame, then starts anyway
VIDEO_SOCKET_SNDBUF = 32768            # kernel send buffer per viewer (Linux doubles it)
VIDEO_SOCKET_TIMEOUT = 5.0             # a viewer that accepts nothing for this long is dropped
VIDEO_IP_TOS = 0x80                    # DSCP CS4 -> Wi-Fi WMM "video" access category


class VideoViewer(object):
    """One browser's video feed: a small frame-aware buffer filled by the receiver
    thread and drained by that browser's HTTP thread."""

    def __init__(self):
        self.cond = threading.Condition()
        self.chunks = []              # Annex-B NAL units waiting to be sent
        self.frames = 0               # slices (= frames on the Solo) in self.chunks
        self.waiting_idr = True       # start (and restart after a skip) on a keyframe
        self.waiting_since = time.time()
        self.skips = 0
        self.legacy_starts = 0
        self.closed = False

    def push(self, nal, nal_type, sps, pps, intra=False):
        """nal_type 1/5 = slice; intra = I-slice. Keyframe = IDR or I-slice."""
        with self.cond:
            if self.closed:
                return
            is_slice = nal_type in (1, 5)
            keyframe = nal_type == 5 or (nal_type == 1 and intra)
            if self.waiting_idr:
                if not is_slice:
                    return
                if not keyframe and time.time() - self.waiting_since < KEYFRAME_WAIT_SECS:
                    return            # wait briefly for a keyframe ...
                if not keyframe:
                    self.legacy_starts += 1   # ... the Solo sends few: start the old way
                self.waiting_idr = False
                self.chunks = []
                self.frames = 0
                if nal_type != 5:
                    nal = relabel_as_idr(nal)
                nal_type = 5
            elif is_slice and self.frames >= VIEWER_MAX_BACKLOG_FRAMES:
                # Too far behind (slow Wi-Fi, stalled tab): throw the backlog away and
                # resume at the next keyframe, so the delay never builds up.
                self.chunks = []
                self.frames = 0
                self.skips += 1
                if not keyframe:
                    self.waiting_idr = True
                    self.waiting_since = time.time()
                    return
                if nal_type != 5:
                    nal = relabel_as_idr(nal)
                nal_type = 5
            if nal_type == 5:
                # Every keyframe goes out with the current SPS/PPS, so a resolution
                # change (Solo varStreamRes) or a fresh decoder always has them.
                if sps:
                    self.chunks.append(sps)
                if pps:
                    self.chunks.append(pps)
            self.chunks.append(nal)
            if is_slice:
                self.frames += 1
            self.cond.notify()

    def preload(self, chunks):
        """Start a new viewer from the cached keyframe group (instant picture)."""
        with self.cond:
            self.chunks = list(chunks)
            self.frames = sum(1 for c in chunks if (_byte(c, 4) & 0x1F) in (1, 5))
            self.waiting_idr = False
            self.cond.notify()

    def take(self, timeout):
        """Everything queued, as one buffer (fewer, larger writes). None on timeout."""
        with self.cond:
            if not self.chunks and not self.closed:
                self.cond.wait(timeout)
            if not self.chunks:
                return None
            data = b''.join(self.chunks)
            self.chunks = []
            self.frames = 0
            return data

    def close(self):
        with self.cond:
            self.closed = True
            self.cond.notify()


class SoloVideoManager(threading.Thread):
    """
    Manages the 3DR Solo HDMI live video pipeline.
    1. Keeps a TCP keepalive to 10.1.1.1:5502 to register client IP for video.
    2. Binds UDP port 5600 for incoming RTP H.264 video packets.
    3. Depacketizes RTP into Annex-B H.264 NAL units, dropping damaged fragments.
    4. Caches SPS/PPS and the current keyframe group, and fans the stream out to
       browser viewers, each with a small frame-aware buffer.
    """
    def __init__(self, controller_ip=SOLO_CONTROLLER_IP, tcp_port=SOLO_VIDEO_TCP_PORT, udp_port=SOLO_VIDEO_UDP_PORT):
        super(SoloVideoManager, self).__init__()
        self.daemon = True
        self.controller_ip = controller_ip
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.running = False
        self.force_reconnect_flag = False
        
        self.lock = threading.Lock()
        self.subscribers = []
        self.sps = None
        self.pps = None
        self.gop = []                 # NAL units since the last keyframe (for instant start)
        self.last_frame_time = 0.0
        # keyframe diagnostics (shown in /api/video/status)
        self.nal_type_counts = {}
        self.keyframes_idr = 0
        self.keyframes_islice = 0
        self.last_key_time = 0.0
        self.frames_since_key = 0
        self.key_interval_frames = None
        self.total_skips = 0
        self.total_legacy_starts = 0
        self.depacketizer = RtpH264Depacketizer()
        
        # Diagnostics
        self.handshake_connected = False
        self.streaming_active = False
        self.app_holding_video = None     # IP of the app (Solex) the video slot was given to
        self._app_last_seen = 0.0
        self.packet_count = 0
        self.bytes_received = 0
        self.last_packet_time = 0.0
        self.kbps = 0.0
        self._rate_calc_time = time.time()
        self._rate_calc_bytes = 0

    def subscribe(self):
        """Register a new HTTP/browser viewer. Returns its VideoViewer."""
        viewer = VideoViewer()
        with self.lock:
            # Only a live keyframe group is useful; after a gap it would just be old video
            fresh = time.time() - self.last_frame_time < GOP_CACHE_FRESH_SECS
            if self.gop and self.sps and self.pps and fresh:
                viewer.preload([self.sps, self.pps] + self.gop)
            self.subscribers.append(viewer)
        return viewer

    def unsubscribe(self, viewer):
        """Unregister an HTTP/browser viewer."""
        with self.lock:
            if viewer in self.subscribers:
                self.subscribers.remove(viewer)
                self.total_skips += getattr(viewer, 'skips', 0)
                self.total_legacy_starts += getattr(viewer, 'legacy_starts', 0)
        try:
            viewer.close()
        except Exception:
            pass

    def request_reconnect(self):
        """RESTART VIDEO: re-register with the Controller and start viewers on a fresh keyframe."""
        self.force_reconnect_flag = True
        with self.lock:
            self.gop = []
            for v in self.subscribers:
                with v.cond:
                    v.chunks = []
                    v.frames = 0
                    v.waiting_idr = True
                    v.waiting_since = time.time()

    def broadcast_nal(self, nal_data):
        """Distribute a completed NAL unit to all viewers."""
        if len(nal_data) < 5:
            return
        nal_type = _byte(nal_data, 4) & 0x1F
        intra = nal_type == 1 and slice_is_intra(nal_data)
        with self.lock:
            self.nal_type_counts[nal_type] = self.nal_type_counts.get(nal_type, 0) + 1
            if nal_type == 7:
                self.sps = nal_data
                return
            if nal_type == 8:
                self.pps = nal_data
                return
            now = time.time()
            if now - self.last_frame_time > GOP_CACHE_FRESH_SECS:
                self.gop = []             # stream paused: cached frames are stale
            self.last_frame_time = now
            if nal_type in (1, 5):
                self.frames_since_key += 1
            if nal_type == 5 or intra:
                self.keyframes_idr += nal_type == 5
                self.keyframes_islice += intra
                if self.last_key_time:
                    self.key_interval_frames = self.frames_since_key
                self.last_key_time = now
                self.frames_since_key = 0
                self.gop = [nal_data if nal_type == 5 else relabel_as_idr(nal_data)]
            elif self.gop:
                if len(self.gop) >= GOP_CACHE_MAX_FRAMES:
                    self.gop = []
                else:
                    self.gop.append(nal_data)
            viewers = list(self.subscribers)
            sps, pps = self.sps, self.pps
        for v in viewers:
            try:
                v.push(nal_data, nal_type, sps, pps, intra)
            except Exception:
                pass

    def get_status(self):
        """Return video diagnostics dictionary."""
        now = time.time()
        is_active = (now - self.last_packet_time < 2.5) if self.last_packet_time > 0 else False
        return {
            "handshake_connected": self.handshake_connected,
            "yielded_to_app": self.app_holding_video,
            "streaming_active": is_active,
            "packet_count": self.packet_count,
            "kbps": round(self.kbps, 1),
            "subscribers": len(self.subscribers),
            "lost_packets": self.depacketizer.lost_packets,
            "dropped_nals": self.depacketizer.dropped_nals,
            "late_packets": self.depacketizer.late_packets,
            "stream_restarts": self.depacketizer.stream_restarts,
            "viewer_skips": self.total_skips + sum(getattr(v, 'skips', 0) for v in list(self.subscribers)),
            "viewer_fallback_starts": self.total_legacy_starts + sum(getattr(v, 'legacy_starts', 0) for v in list(self.subscribers)),
            "keyframes_idr": self.keyframes_idr,
            "keyframes_islice": self.keyframes_islice,
            "keyframe_interval_frames": self.key_interval_frames,
            "last_keyframe_sec_ago": round(now - self.last_key_time, 1) if self.last_key_time else None,
            "nal_types": dict((str(k), v) for k, v in self.nal_type_counts.items()),
            "last_packet_sec_ago": round(now - self.last_packet_time, 1) if self.last_packet_time > 0 else None
        }

    def _app_wants_video(self):
        """True while Solex (or another app) is connected to the shot manager, plus APP_GONE_SECS."""
        now = time.time()
        ip = find_app_on_shotmanager()
        if ip:
            if self.app_holding_video != ip:
                print("[video] %s connected to the shot manager: giving the video to it" % ip)
            self.app_holding_video = ip
            self._app_last_seen = now
            return True
        if self.app_holding_video and now - self._app_last_seen < APP_GONE_SECS:
            return True
        if self.app_holding_video:
            print("[video] %s has gone: the utility can take the video back" % self.app_holding_video)
        self.app_holding_video = None
        return False

    def _handshake_worker(self):
        """Background worker keeping the TCP handshake alive to 10.1.1.1:5502."""
        while self.running:
            sock = None
            if len(self.subscribers) == 0 or self._app_wants_video():
                self.handshake_connected = False
                import time
                time.sleep(1.0)
                continue

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(4.0)
                sock.connect((self.controller_ip, self.tcp_port))
                self.handshake_connected = True
                last_ping = 0.0
                
                while self.running:
                    if len(self.subscribers) == 0:
                        break
                    if self._app_wants_video():
                        break             # Solex is here: free the slot so it gets the video

                    now = time.time()
                    if now - last_ping >= 2.0:
                        try:
                            # 4-byte length prefix (serves as heartbeat to app_server.py)
                            sock.sendall(struct.pack('!I', 4))
                            last_ping = now
                        except Exception:
                            break

                    r, _, _ = select.select([sock], [], [], 1.0)
                    if getattr(self, 'force_reconnect_flag', False):
                        self.force_reconnect_flag = False
                        break
                        
                    if r:
                        try:
                            data = sock.recv(1024)
                            if not data:
                                break
                        except Exception:
                            break
            except Exception:
                self.handshake_connected = False
                import time
                time.sleep(1.0)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
                        
    def run(self):
        self.running = True
        
        hs_thread = threading.Thread(target=self._handshake_worker)
        hs_thread.daemon = True
        hs_thread.start()

        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except Exception:
            pass
        try:
            udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
        except Exception:
            pass
        if hasattr(socket, 'SIO_UDP_CONNRESET'):
            try:
                udp_sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except Exception:
                pass
        
        try:
            udp_sock.bind(('0.0.0.0', self.udp_port))
            udp_sock.settimeout(1.0)
        except Exception as e:
            print("[!] Could not bind UDP %d for video: %s" % (self.udp_port, e))
            return

        while self.running:
            try:
                data, addr = udp_sock.recvfrom(65536)
            except socket.timeout:
                continue
            except (socket.error, OSError):
                time.sleep(0.01)
                continue
            except Exception:
                time.sleep(0.01)
                continue

            now = time.time()
            self.packet_count += 1
            self.bytes_received += len(data)
            self.last_packet_time = now
            self._rate_calc_bytes += len(data)

            if now - self._rate_calc_time >= 1.0:
                elapsed = now - self._rate_calc_time
                self.kbps = (self._rate_calc_bytes * 8.0) / (elapsed * 1024.0)
                self._rate_calc_bytes = 0
                self._rate_calc_time = now

            for nal in self.depacketizer.feed(data):
                self.broadcast_nal(nal)

        try:
            udp_sock.close()
        except Exception:
            pass


# Global singletons
telemetry = TelemetryState()
mav_client = LiveMAVLinkClient(telemetry)
video_manager = SoloVideoManager()
_started = False


_last_demand = [0.0]
_keepalive_fns = []


def note_demand():
    """A browser is using telemetry (called by /api/telemetry, /api/status, actions)."""
    _last_demand[0] = time.time()


def add_keepalive(fn):
    """fn() -> True keeps the MAVLink link up without a browser (e.g. the home Wi-Fi arm
    interlock must keep seeing the armed state while the Controller is on home Wi-Fi)."""
    _keepalive_fns.append(fn)


def mavlink_wanted():
    if time.time() - _last_demand[0] < MAVLINK_IDLE_SECS:
        return True
    for fn in list(_keepalive_fns):
        try:
            if fn():
                return True
        except Exception:
            pass
    return False


def start_services():
    """Start telemetry and video receiver background threads."""
    global _started
    if not _started:
        mav_client.start()
        video_manager.start()
        _started = True


def get_telemetry_dict(msgs_since=None):
    """Current telemetry. msgs_since=None: legacy payload (message log included 3x).
    msgs_since=<int>: the log is sent once, as mavlink_messages, and only when it changed
    since that messages_seq (null otherwise) -- what the Cockpit HUD's 10 Hz poll uses."""
    d = telemetry.to_dict()
    if msgs_since is not None:
        log = d.pop("mavlink_messages", None)
        d.pop("messages", None)
        d.get("status", {}).pop("messages", None)
        changed = msgs_since != 'none' and msgs_since != d.get("messages_seq")
        d["mavlink_messages"] = log if changed else None
    return d


def get_video_status():
    """Return dictionary of video pipeline status."""
    return video_manager.get_status()


# Last version the Cube itself reported over MAVLink. An EMPTY file means "the Cube was
# just flashed; its version is not known until it reports again".
CUBE_VERSION_RECORD = "/log/solowebutility_cube_version"
PIX_VERSION_FILE = "/PIX_VERSION"   # written by Open Solo's pixhawk.py at boot only
_VERSION_NUM = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def _remember_cube_version(version):
    """Persist the live version (only written when it changes)."""
    try:
        old = None
        if os.path.exists(CUBE_VERSION_RECORD):
            with open(CUBE_VERSION_RECORD, "r") as f:
                old = f.read().strip()
        if old != version:
            with open(CUBE_VERSION_RECORD, "w") as f:
                f.write(version + "\n")
    except Exception:
        pass


def invalidate_cube_version():
    """Call right before the Cube is flashed: forget every known version until the Cube reports."""
    try:
        with telemetry.lock:
            telemetry.cube_version = None
    except Exception:
        pass
    try:
        with open(CUBE_VERSION_RECORD, "w") as f:
            f.write("")
    except Exception:
        pass


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except Exception:
        return None


def get_cube_version():
    """'ArduCopter x.y.z' as installed on the Cube, or "" while it is not known.
    1. Live MAVLink answer from the Cube.
    2. Otherwise the NEWER of: the last live answer (our record) and /PIX_VERSION (Open Solo,
       written at boot). An empty record (Cube just flashed) means unknown, so a /PIX_VERSION
       from before the flash is never shown."""
    try:
        with telemetry.lock:
            live = telemetry.cube_version
        if live:
            return "ArduCopter %s" % live
    except Exception:
        pass
    rec_t, pix_t = _mtime(CUBE_VERSION_RECORD), _mtime(PIX_VERSION_FILE)
    try:
        if rec_t is not None and (pix_t is None or rec_t >= pix_t):
            with open(CUBE_VERSION_RECORD, "r") as f:
                m = _VERSION_NUM.search(f.read())
            return ("ArduCopter %s" % m.group(1)) if m else ""
        if pix_t is not None:
            with open(PIX_VERSION_FILE, "r") as f:
                for line in f:
                    m = _VERSION_NUM.search(line)
                    if m and line.strip().lower() != "unknown":
                        return "ArduCopter %s" % m.group(1)
    except Exception:
        pass
    return ""


def is_vehicle_armed(wake=True, wait=2.5):
    """Return True if the vehicle is currently armed (thread-safe).
    wake=True (safety checks before flashing, calibration, Wi-Fi changes): if the link was
    idle, bring it up and wait briefly for a fresh heartbeat so the answer isn't stale.
    wake=False (background interlock): just report the last known state."""
    try:
        if 'telemetry' not in globals() or not telemetry:
            return False
        if wake:
            was_idle = telemetry.mavlink_idle
            note_demand()
            # only right after waking from idle (not when the Cube is simply offline)
            if was_idle and _started and time.time() - telemetry.last_vehicle_heartbeat > 3.0:
                deadline = time.time() + wait
                while time.time() < deadline and time.time() - telemetry.last_vehicle_heartbeat > 3.0:
                    time.sleep(0.1)
        with telemetry.lock:
            return bool(telemetry.armed)
    except Exception:
        pass
    return False


def handle_action(action, custom_mode=None):
    """
    Handle flight mode changes and disarming.
    CRITICAL SAFETY: Arm command execution is strictly controlled and reserved for the user.
    """
    if action == "set_mode":
        mode_id = None
        if isinstance(custom_mode, int):
            mode_id = custom_mode
        elif custom_mode is not None:
            clean = str(custom_mode).strip().upper()
            mode_id = MODE_NAME_TO_ID.get(clean)
            if mode_id is None:
                try:
                    mode_id = int(clean)
                except ValueError:
                    pass
        if mode_id is not None:
            mav_client.send_set_mode_command(mode_id)
            try:
                from pymavlink import mavutil
                for ep in ['udpout:%s:%d' % SOLO_TELEM_INJECT]:
                    try:
                        m = mavutil.mavlink_connection(ep, source_system=254)
                        m.mav.set_mode_send(1, mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode_id)
                        m.close()
                    except Exception:
                        pass
            except Exception:
                pass
            mode_name = ARDUCOPTER_MODES.get(mode_id, str(mode_id))
            telemetry.add_message("Dispatched Mode Change: %s" % mode_name, 6)
            return {"status": "ok", "message": "Mode change dispatched to %s" % mode_name}
        return {"status": "error", "message": "Invalid flight mode: %s" % str(custom_mode)}
    
    elif action == "disarm":
        mav_client.send_arm_command(False)
        telemetry.add_message("Dispatched: DISARM MOTORS", 6)
        return {"status": "ok", "message": "Disarm command dispatched"}

    elif action == "arm":
        # ARMING ONLY PERMITTED ON DIRECT USER DISPATCH
        mav_client.send_arm_command(True)
        telemetry.add_message("Dispatched: ARM MOTORS", 4)
        return {"status": "ok", "message": "Arm command dispatched"}

    return {"status": "error", "message": "Unknown action"}
