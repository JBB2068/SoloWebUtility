#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
🛸 3DR Solo Cockpit Flight Deck & Live Telemetry Server
Direct live MAVLink telemetry client & compact glass-cockpit server for 3DR Solo / Open Solo.
Dedicated strictly to LIVE copter MAVLink telemetry (no simulation).

Connects to Solo copter (10.1.1.10:14560) and controller (10.1.1.1:14550) via UDP.
Sends GCS heartbeats and stream requests to activate telem_forwarder.
Runs with zero external dependencies (Python 2.7 & 3.x standard library).
"""

import os
import sys
import time
import math
import json
import socket
import select
import struct
import threading

# Python 2 and 3 compatible HTTP server imports
if sys.version_info[0] >= 3:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from socketserver import ThreadingMixIn
    from urllib.parse import parse_qs, urlparse
else:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    from SocketServer import ThreadingMixIn
    from urlparse import parse_qs, urlparse

try:
    import queue
except ImportError:
    import Queue as queue

try:
    from pymavlink import mavutil
except ImportError:
    mavutil = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DEFAULT_PORT = 8088

# Solo Copter & Controller Default Endpoints
SOLO_COPTER_IP = "10.1.1.10"
SOLO_COPTER_PORT = 14560
SOLO_CONTROLLER_IP = "10.1.1.1"
SOLO_CONTROLLER_PORT = 14550

# Solo HDMI Video Stream Endpoints
SOLO_VIDEO_TCP_PORT = 5502
SOLO_VIDEO_UDP_PORT = 5600

# ArduCopter Flight Mode map
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
        self.copter_ip = SOLO_COPTER_IP
        self.copter_port = SOLO_COPTER_PORT
        
        # Vehicle status
        self.armed = False
        self.flight_mode = "LOITER"
        self.custom_mode = 5
        self.system_status = 3
        self.vibe_clip = 0
        self.last_status_text = "Listening for Solo MAVLink on 10.1.1.10:14560..."
        self.status_severity = 6
        self.mavlink_messages = [
            {"time": time.strftime("%H:%M:%S"), "text": "Listening for Solo MAVLink on 10.1.1.10:14560...", "severity": 6}
        ]
        
        # Attitude (Degrees & Radians)
        self.pitch = 0.0     # Degrees (-90 to +90)
        self.roll = 0.0      # Degrees (-180 to +180)
        self.yaw = 0.0       # Degrees (0 to 360)
        self.pitch_rate = 0.0
        self.roll_rate = 0.0
        self.yaw_rate = 0.0
        
        # VFR HUD
        self.airspeed = 0.0      # m/s
        self.groundspeed = 0.0    # m/s
        self.heading = 0.0        # deg
        self.throttle = 0         # %
        self.alt_msl = 0.0        # meters MSL
        self.alt_rel = 0.0        # meters AGL
        self.climb_rate = 0.0     # m/s (variometer)
        
        # Battery (Solo 4S LiPo: 16.8V max, 14.8V nom, ~13.8V min)
        self.voltage = 0.0        # V
        self.current = 0.0        # A
        self.battery_remaining = 0 # %
        self.consumed_mah = 0     # mAh
        self.cell_count = 4
        self.cell_voltage = 0.0   # V
        self.battery_temp = 25.0  # deg C
        self.est_flight_time_min = 0.0
        
        # GPS
        self.gps_fix_type = 0
        self.gps_fix_label = "No GPS"
        self.satellites = 0
        self.hdop = 9.99
        self.vdop = 9.99
        self.lat = 0.0
        self.lon = 0.0
        self.home_lat = 0.0
        self.home_lon = 0.0
        self.home_alt = 0.0
        self.has_home = False
        self.distance_to_home = 0.0 # meters
        self.bearing_to_home = 0.0  # degrees
        
        # RC & Links
        self.rssi = 0             # %
        self.rc_channels = [1500] * 8
        self.last_update = time.time()

    def add_message(self, text, severity=6):
        """Append a message with timestamp to the message log, keeping up to 1000 entries."""
        if not text:
            return
        clean_text = str(text).strip()
        if not clean_text:
            return
        ts = time.strftime("%H:%M:%S")
        self.mavlink_messages.append({"time": ts, "text": clean_text, "severity": int(severity)})
        if len(self.mavlink_messages) > 1000:
            self.mavlink_messages.pop(0)
        self.last_status_text = clean_text
        self.status_severity = int(severity)

    def update_distance_bearing(self):
        """Calculate distance and bearing to Home point."""
        if not self.has_home or abs(self.home_lat) < 0.0001:
            if abs(self.lat) > 0.0001:
                self.home_lat = self.lat
                self.home_lon = self.lon
                self.home_alt = self.alt_msl
                self.has_home = True
            return

        try:
            R = 6371000.0  # Earth radius in meters
            dlat = math.radians(self.lat - self.home_lat)
            dlon = math.radians(self.lon - self.home_lon)
            lat1 = math.radians(self.home_lat)
            lat2 = math.radians(self.lat)
            
            a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
            self.distance_to_home = round(R * c, 1)
            
            y = math.sin(-dlon) * math.cos(lat1)
            x = math.cos(lat2) * math.sin(lat1) - math.sin(lat2) * math.cos(lat1) * math.cos(-dlon)
            brg = math.degrees(math.atan2(y, x))
            self.bearing_to_home = round((brg + 360.0) % 360.0, 1)
        except Exception:
            pass

    def to_dict(self):
        """Export state to JSON dictionary."""
        with self.lock:
            now = time.time()
            is_alive = (now - self.last_packet_time) < 3.0
                
            return {
                "source": "mavlink",
                "connected": is_alive,
                "mavlink_online": is_alive,
                "timestamp": now,
                "attitude": {
                    "pitch": round(self.pitch, 2),
                    "roll": round(self.roll, 2),
                    "yaw": round(self.yaw, 2),
                    "pitch_rate": round(self.pitch_rate, 2),
                    "roll_rate": round(self.roll_rate, 2),
                    "yaw_rate": round(self.yaw_rate, 2)
                },
                "hud": {
                    "airspeed": round(self.airspeed, 1),
                    "groundspeed": round(self.groundspeed, 1),
                    "heading": round(self.heading, 1),
                    "throttle": int(self.throttle),
                    "alt_msl": round(self.alt_msl, 1),
                    "alt_rel": round(self.alt_rel, 1),
                    "climb_rate": round(self.climb_rate, 2)
                },
                "battery": {
                    "voltage": round(self.voltage, 2),
                    "current": round(self.current, 1),
                    "remaining_pct": int(self.battery_remaining),
                    "consumed_mah": int(self.consumed_mah),
                    "cell_count": self.cell_count,
                    "cell_voltage": round(self.cell_voltage, 2),
                    "temperature_c": round(self.battery_temp, 1),
                    "flight_time_min": round(self.est_flight_time_min, 1)
                },
                "gps": {
                    "fix_type": self.gps_fix_type,
                    "fix_label": self.gps_fix_label,
                    "satellites": self.satellites,
                    "hdop": round(self.hdop, 2),
                    "vdop": round(self.vdop, 2),
                    "lat": round(self.lat, 7),
                    "lon": round(self.lon, 7),
                    "alt_msl": round(self.alt_msl, 1),
                    "alt_rel": round(self.alt_rel, 1),
                    "home_lat": round(self.home_lat, 7),
                    "home_lon": round(self.home_lon, 7),
                    "home_alt": round(self.home_alt, 1),
                    "distance_to_home": self.distance_to_home,
                    "bearing_to_home": self.bearing_to_home
                },
                "status": {
                    "armed": self.armed,
                    "flight_mode": self.flight_mode,
                    "custom_mode": self.custom_mode,
                    "rssi_pct": int(self.rssi),
                    "packet_rate_hz": round(self.packet_rate, 1),
                    "vibe_clip": self.vibe_clip,
                    "status_text": self.last_status_text,
                    "severity": self.status_severity,
                    "messages": list(self.mavlink_messages),
                    "copter_endpoint": "%s:%d" % (self.copter_ip, self.copter_port)
                },
                "video": video_manager.get_status() if 'video_manager' in globals() else {"streaming_active": False, "kbps": 0.0}
            }


telemetry = TelemetryState()


class LiveMAVLinkClient(threading.Thread):
    """
    Active Bidirectional MAVLink UDP Client for 3DR Solo.
    1. Sends GCS heartbeats and stream requests to 10.1.1.10:14560 & 10.1.1.1:14550.
    2. Receives and decodes incoming MAVLink packets from Solo telem_forwarder.
    3. Sends flight mode changes and arm/disarm commands directly over UDP.
    """
    def __init__(self, state):
        threading.Thread.__init__(self)
        self.daemon = True
        self.state = state
        self.running = True
        self.sock = None
        self.seq = 0
        self.last_heartbeat_sent = 0.0
        self.last_stream_req_sent = 0.0
        self.pkt_counter = 0
        self.last_rate_time = time.time()
        
        # Target endpoints for 3DR Solo
        self.targets = [
            (SOLO_COPTER_IP, SOLO_COPTER_PORT),         # 10.1.1.10:14560 (Copter telem forwarder)
            (SOLO_CONTROLLER_IP, SOLO_CONTROLLER_PORT)  # 10.1.1.1:14550 (Controller telem forwarder)
        ]

    def get_socket(self):
        if self.sock:
            return self.sock
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            for port in [14560, 14550, 0]:
                try:
                    s.bind(('0.0.0.0', port))
                    break
                except Exception:
                    pass
            s.setblocking(0)
            self.sock = s
            return self.sock
        except Exception:
            return None

    def send_packet(self, pkt):
        """Broadcast packet to Solo endpoints."""
        s = self.get_socket()
        if not s:
            return
        for host, port in self.targets:
            try:
                s.sendto(pkt, (host, port))
            except Exception:
                pass

    def build_mavlink_v1_packet(self, msgid, payload, crc_extra):
        """Construct a standard MAVLink v1 packet with X.25 CRC."""
        self.seq = (self.seq + 1) & 0xff
        sysid = 254  # GCS system ID
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
        """Send MAV_CMD_COMPONENT_ARM_DISARM (400) via COMMAND_LONG (msg ID 76, CRC extra 152)."""
        param1 = 1.0 if arm else 0.0
        payload = struct.pack('<fffffffHBBB', param1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 400, 1, 1, 0)
        pkt = self.build_mavlink_v1_packet(76, payload, 152)
        self.send_packet(pkt)

    def send_set_mode_command(self, custom_mode):
        """Send SET_MODE (msg ID 11, CRC extra 89)."""
        payload = struct.pack('<IBB', int(custom_mode), 1, 1)
        pkt = self.build_mavlink_v1_packet(11, payload, 89)
        self.send_packet(pkt)

    def run(self):
        while self.running:
            s = self.get_socket()
            if not s:
                time.sleep(1.0)
                continue

            now = time.time()

            # Send heartbeat every 1 second to keep Solo telem_forwarder registered
            if now - self.last_heartbeat_sent >= 1.0:
                self.send_gcs_heartbeat()
                self.last_heartbeat_sent = now

            # Request data streams every 5 seconds
            if now - self.last_stream_req_sent >= 5.0:
                self.request_data_streams()
                self.last_stream_req_sent = now

            # Calculate packet rate
            if now - self.last_rate_time >= 1.0:
                dt = now - self.last_rate_time
                with self.state.lock:
                    self.state.packet_rate = round(self.pkt_counter / dt, 1)
                self.pkt_counter = 0
                self.last_rate_time = now

            # Listen for incoming packets with select
            try:
                rlist, _, _ = select.select([s], [], [], 0.05)
            except Exception:
                time.sleep(0.05)
                continue

            if not rlist:
                if now - self.state.last_packet_time > 3.0:
                    with self.state.lock:
                        self.state.mavlink_connected = False
                continue

            try:
                data, addr = s.recvfrom(2048)
                if data:
                    self.pkt_counter += 1
                    self.parse_mavlink_bytes(data)
            except Exception:
                pass

    def parse_mavlink_bytes(self, data):
        """Parse raw MAVLink stream bytes."""
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
                payload = data[idx+10:idx+10+plen]
                self.decode_payload(msgid, payload, sysid, compid)
                idx += total_len
            else:
                idx += 1

    def decode_payload(self, msgid, payload, sysid=1, compid=1):
        """Decode MAVLink fields into live telemetry state."""
        # Ignore our own GCS loopback packets (sysid 254)
        if sysid == 254:
            return

        now = time.time()
        with self.state.lock:
            # If newly connected, log event
            if not self.state.mavlink_connected:
                self.state.add_message("Connected to ArduPilot on %s:%d" % (self.state.copter_ip, self.state.copter_port), 6)
            
            self.state.last_packet_time = now
            self.state.mavlink_connected = True

            try:
                # HEARTBEAT (0)
                if msgid == 0 and len(payload) >= 9:
                    custom_mode, mtype, autopilot, base_mode, sys_status = struct.unpack('<IBBBB', payload[:8])
                    # Strictly filter for vehicle autopilot
                    if (autopilot == 3 or mtype in (1, 2, 3, 4, 12, 13, 14, 15)) and sysid not in (254, 255):
                        new_mode = ARDUCOPTER_MODES.get(custom_mode, "MODE %d" % custom_mode)
                        if self.state.flight_mode != new_mode:
                            self.state.add_message("Mode: %s" % new_mode, 6)
                            self.state.flight_mode = new_mode
                            self.state.custom_mode = custom_mode
                        
                        is_armed = bool(base_mode & 128)
                        if self.state.armed != is_armed:
                            self.state.add_message("MOTORS ARMED" if is_armed else "MOTORS DISARMED", 4 if is_armed else 6)
                            self.state.armed = is_armed
                        
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
                    res_map = {0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED", 3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS", 6: "CANCELLED"}
                    cmd_map = {400: "ARM/DISARM", 176: "DO_SET_MODE", 20: "NAV_TAKEOFF", 21: "NAV_LAND", 22: "NAV_RTL"}
                    cname = cmd_map.get(cmd, "CMD_%d" % cmd)
                    rname = res_map.get(result, "CODE_%d" % result)
                    self.state.add_message("ACK: %s -> %s" % (cname, rname), 4 if result != 0 else 6)

                # STATUSTEXT (253) - Accept any payload with len >= 2 to support MAVLink 2 zero-truncation
                elif msgid == 253 and len(payload) >= 2:
                    sev = ord(payload[0:1]) if sys.version_info[0] < 3 else payload[0]
                    raw_text = payload[1:min(len(payload), 51)].decode('ascii', errors='ignore').rstrip('\x00').strip()
                    if raw_text:
                        self.state.add_message(raw_text, sev)

                # RC_CHANNELS_RAW (35) or RC_CHANNELS (65)
                elif msgid == 35 and len(payload) >= 22:
                    rssi = ord(payload[21:22]) if sys.version_info[0] < 3 else payload[21]
                    self.state.rssi = int(rssi * 100.0 / 255.0)

            except Exception:
                pass


def parse_rtp_h264(data, fu_buffer):
    """RFC 6184 H.264 RTP depacketizer for raw Annex-B NAL units."""
    if len(data) < 12:
        return [], fu_buffer
    payload = data[12:]
    if not payload:
        return [], fu_buffer
    
    nals = []
    nal_header = ord(payload[0]) if isinstance(payload[0], str) else payload[0]
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
        fu_header = ord(payload[1]) if isinstance(payload[1], str) else payload[1]
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


class SoloVideoManager(threading.Thread):
    """
    Manages the 3DR Solo HDMI live video pipeline.
    1. Connects and holds a TCP handshake to 10.1.1.1:5502 (tells Sololink to route HDMI stream).
    2. Listens on UDP port 5600 for incoming RTP H.264 video packets from the copter i.MX6.
    3. Depacketizes RTP packets into Annex-B H.264 NAL units (SPS, PPS, IDR, slices).
    4. Caches SPS/PPS parameter sets and fans out live H.264 stream to browser clients.
    """
    def __init__(self, controller_ip=SOLO_CONTROLLER_IP, tcp_port=SOLO_VIDEO_TCP_PORT, udp_port=SOLO_VIDEO_UDP_PORT):
        super(SoloVideoManager, self).__init__()
        self.daemon = True
        self.controller_ip = controller_ip
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.running = False
        
        self.lock = threading.Lock()
        self.subscribers = []
        self.sps = None
        self.pps = None
        
        # Diagnostics
        self.handshake_connected = False
        self.streaming_active = False
        self.packet_count = 0
        self.bytes_received = 0
        self.last_packet_time = 0.0
        self.kbps = 0.0
        self._rate_calc_time = time.time()
        self._rate_calc_bytes = 0

    def subscribe(self):
        """Register a new HTTP/browser consumer for live H.264 NAL units."""
        q = queue.Queue(maxsize=80)
        with self.lock:
            if self.sps:
                q.put(self.sps)
            if self.pps:
                q.put(self.pps)
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        """Unregister an HTTP/browser consumer."""
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def broadcast_nal(self, nal_data):
        """Distribute a completed NAL unit to all active browser subscriber queues."""
        nal_header = ord(nal_data[4:5]) if isinstance(nal_data[4:5], str) else nal_data[4]
        nal_type = nal_header & 0x1F
        
        with self.lock:
            if nal_type == 7:
                self.sps = nal_data
            elif nal_type == 8:
                self.pps = nal_data

            for q in list(self.subscribers):
                try:
                    if q.full():
                        try:
                            q.get_nowait()
                        except queue.Empty:
                            pass
                    q.put_nowait(nal_data)
                except Exception:
                    pass

    def get_status(self):
        """Return video diagnostics dictionary."""
        now = time.time()
        is_active = (now - self.last_packet_time < 2.5) if self.last_packet_time > 0 else False
        return {
            "handshake_connected": self.handshake_connected,
            "streaming_active": is_active,
            "packet_count": self.packet_count,
            "kbps": round(self.kbps, 1),
            "subscribers": len(self.subscribers),
            "last_packet_sec_ago": round(now - self.last_packet_time, 1) if self.last_packet_time > 0 else None
        }

    def _handshake_worker(self):
        """Background worker keeping the TCP handshake alive to 10.1.1.1:5502."""
        while self.running:
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(4.0)
                sock.connect((self.controller_ip, self.tcp_port))
                self.handshake_connected = True
                last_ping = 0.0
                
                while self.running:
                    now = time.time()
                    if now - last_ping >= 2.0:
                        # app_server.py expects 4-byte length prefix.
                        # Sending length 4 (0 payload bytes) serves as an ideal non-intrusive heartbeat.
                        try:
                            sock.sendall(struct.pack('!I', 4))
                            last_ping = now
                        except Exception:
                            break

                    r, _, _ = select.select([sock], [], [], 1.0)
                    if r:
                        try:
                            data = sock.recv(1024)
                            if not data:
                                break
                        except Exception:
                            break
            except Exception:
                self.handshake_connected = False
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
                self.handshake_connected = False
            
            for _ in range(20):
                if not self.running:
                    break
                time.sleep(0.1)

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
        
        try:
            udp_sock.bind(('0.0.0.0', self.udp_port))
            udp_sock.settimeout(1.0)
        except Exception as e:
            print("[!] Could not bind UDP %d for video: %s" % (self.udp_port, e))
            return

        fu_buffer = b''

        while self.running:
            try:
                data, addr = udp_sock.recvfrom(65536)
            except socket.timeout:
                continue
            except Exception:
                break

            now = time.time()
            self.packet_count += 1
            self.bytes_received += len(data)
            self.last_packet_time = now
            self._rate_calc_bytes += len(data)

            if now - self._rate_calc_time >= 1.0:
                elapsed = now - self._rate_calc_time
                self.kbps = (self._rate_calc_bytes * 8.0) / (elapsed * 1000.0) if elapsed > 0 else 0.0
                self._rate_calc_bytes = 0
                self._rate_calc_time = now

            nals, fu_buffer = parse_rtp_h264(data, fu_buffer)
            for nal in nals:
                self.broadcast_nal(nal)

        try:
            udp_sock.close()
        except Exception:
            pass


video_manager = SoloVideoManager()


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):

    daemon_threads = True
    allow_reuse_address = True


class CockpitHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Cockpit web application."""

    def log_message(self, format, *args):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Telemetry JSON API
        if path == "/api/telemetry":
            self.send_json(telemetry.to_dict())
            return

        # Live H.264 Video Stream from Solo Copter i.MX6 HDMI
        if path == "/api/video/h264":
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4; codecs="avc1.42E01E"')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Connection', 'close')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            q = video_manager.subscribe()
            try:
                while True:
                    try:
                        chunk = q.get(timeout=2.0)
                    except queue.Empty:
                        continue
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (socket.error, Exception):
                pass
            finally:
                video_manager.unsubscribe(q)
            return

        # Video Diagnostics Status JSON API
        if path == "/api/video/status":
            self.send_json(video_manager.get_status())
            return

        # Server-Sent Events (SSE) Real-Time Stream (20 Hz)
        if path == "/api/telemetry/stream":
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            try:
                while True:
                    data = json.dumps(telemetry.to_dict())
                    msg = "data: {}\n\n".format(data)
                    self.wfile.write(msg.encode('utf-8'))
                    self.wfile.flush()
                    time.sleep(0.05)
            except Exception:
                return

        # Serve static assets
        if path == "/" or path == "/index.html":
            target = os.path.join(STATIC_DIR, "index.html")
        else:
            rel = path.lstrip("/")
            if rel.startswith("static/"):
                rel = rel[len("static/"):]
            target = os.path.join(STATIC_DIR, rel)

        if os.path.exists(target) and os.path.isfile(target):
            ext = os.path.splitext(target)[1].lower()
            mime_types = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
                ".json": "application/json",
                ".svg": "image/svg+xml",
                ".png": "image/png",
                ".ico": "image/x-icon",
            }
            content_type = mime_types.get(ext, "application/octet-stream")
            
            try:
                with open(target, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(content)
                return
            except Exception as e:
                self.send_error(500, "Error reading file: %s" % e)
                return

        self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        length = int(self.headers.get('content-length', 0))
        post_data = self.rfile.read(length) if length > 0 else b"{}"
        
        try:
            req = json.loads(post_data.decode('utf-8'))
        except Exception:
            req = {}

        # Real Copter MAVLink Action commands (Flight mode, Arm/Disarm)
        if path == "/api/action":
            action = req.get("action")
            mode = req.get("mode")
            
            with telemetry.lock:
                if action == "set_mode" and mode:
                    mode_name = mode.upper()
                    mode_id = MODE_NAME_TO_ID.get(mode_name, None)
                    if mode_id is not None:
                        mav_client.send_set_mode_command(mode_id)
                    telemetry.add_message("Command dispatched: Set Mode %s" % mode_name, 6)
                elif action == "arm":
                    mav_client.send_arm_command(True)
                    telemetry.add_message("Command dispatched: ARM MOTORS", 4)
                elif action == "disarm":
                    mav_client.send_arm_command(False)
                    telemetry.add_message("Command dispatched: DISARM MOTORS", 6)

            self.send_json({"status": "ok", "message": telemetry.last_status_text})
            return

        self.send_error(404, "Unknown API route")


mav_client = LiveMAVLinkClient(telemetry)


def start_server(port=DEFAULT_PORT):
    mav_client.start()
    video_manager.start()

    server_address = ('', port)
    try:
        httpd = ThreadedHTTPServer(server_address, CockpitHandler)
    except Exception as e:
        alt_port = port + 1
        print("[!] Port %d unavailable (%s), trying %d..." % (port, e, alt_port))
        httpd = ThreadedHTTPServer(('', alt_port), CockpitHandler)
        port = alt_port

    print("=" * 68)
    print("  3DR SOLO COCKPIT - LIVE MAVLINK TELEMETRY & HDMI VIDEO")
    print("  Phone Landscape Compact Edition")
    print("  Target Copter MAVLink: %s:%d" % (SOLO_COPTER_IP, SOLO_COPTER_PORT))
    print("  HDMI Video Receiver:   UDP 5600 (Handshake: %s:%d)" % (SOLO_CONTROLLER_IP, SOLO_VIDEO_TCP_PORT))
    print("  Cockpit Web Server running at: http://localhost:%d" % port)
    print("=" * 68)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Cockpit Server...")
        mav_client.running = False
        video_manager.running = False
        httpd.server_close()


if __name__ == '__main__':
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    start_server(port)
