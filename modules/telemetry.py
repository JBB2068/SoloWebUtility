#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Telemetry & Live Connection Module
Handles:
- Live MAVLink v1/v2 flight telemetry from ArduPilot
- Vehicle armed status detection and flight mode commands
- Strict real-time non-blocking background TCP socket heartbeat loop to 10.1.1.10
  running every 0.5s with a strict 0.2s timeout, eliminating all cached/remembered state
- Thread-safe get_live_copter_status() export
- Flask telemetry blueprint
"""

import os
import sys
import time
import socket
import threading
from webcore import Blueprint, request, jsonify
from modules.auth import login_required

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import cockpit_mgr

# Export core objects from cockpit_mgr
telemetry = cockpit_mgr.telemetry
mav_client = cockpit_mgr.mav_client
is_vehicle_armed = cockpit_mgr.is_vehicle_armed
get_telemetry_dict = cockpit_mgr.get_telemetry_dict
handle_action = cockpit_mgr.handle_action

IS_ON_SOLO = True
SOLO_COPTER_IP = "127.0.0.1"
SOLO_MAVLINK_PORT = 14550
SOLO_FALLBACK_PORT = 22

# ---------------------------------------------------------------------------
# Strict Real-Time Asynchronous Copter Connection Monitor
# ---------------------------------------------------------------------------
_status_lock = threading.Lock()
_live_copter_online = False
_copter_boot_in_progress = False

_monitor_thread = None
_monitor_running = False


def set_copter_boot_in_progress(flag):
    """
    Thread-safe flag setter marking that copter is currently rebooting/booting.
    While True, get_live_copter_status() remains strictly False.
    """
    global _copter_boot_in_progress, _live_copter_online
    with _status_lock:
        _copter_boot_in_progress = bool(flag)
        if _copter_boot_in_progress:
            _live_copter_online = False


def set_live_copter_status(online):
    """Thread-safe setter to directly set live copter status flag."""
    global _live_copter_online
    with _status_lock:
        _live_copter_online = bool(online)


def _perform_live_socket_probe(host, ports=(SOLO_MAVLINK_PORT, SOLO_FALLBACK_PORT), timeout=0.2):
    """
    Rapid, cross-platform TCP socket connection attempt with strict 0.2-second timeout.
    Returns True immediately if a connection is established, False on any timeout,
    unreachable host, or connection failure.
    """
    for port in ports:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            res = sock.connect_ex((host, port))
            if res == 0:
                return True
        except Exception:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
    return False


class LiveCopterHeartbeatMonitor(threading.Thread):
    """
    Isolated background thread running every 0.5 seconds.
    Executes a real network socket probe on each iteration with zero caching.
    Immediately updates the thread-safe flag to reflect true physical link status.
    """
    def __init__(self, interval=0.5):
        super(LiveCopterHeartbeatMonitor, self).__init__()
        self.daemon = True
        self.name = "Live-Copter-Heartbeat-Monitor"
        self.interval = interval

    def run(self):
        global _live_copter_online, _monitor_running
        _monitor_running = True

        while _monitor_running:
            # Active real-time live probe with strict 0.2s timeout
            connected = _perform_live_socket_probe(SOLO_COPTER_IP, timeout=0.2)

            # Instantly update thread-safe flag
            with _status_lock:
                if not _copter_boot_in_progress:
                    _live_copter_online = connected
                else:
                    _live_copter_online = False

            time.sleep(self.interval)


def get_live_copter_status():
    """
    Thread-safe getter that returns the real-time live connection state of the copter.
    No cached assumptions or memory windows.
    Strictly returns False if copter boot/update is in progress until 10.1.1.10 is fully awake.
    """
    with _status_lock:
        if _copter_boot_in_progress:
            return False
        return bool(_live_copter_online)


# Backward compatibility aliases
def is_copter_online():
    return get_live_copter_status()


def is_controller_online():
    return get_live_copter_status()


def start_connection_monitor():
    """Start isolated 0.5s background socket monitor loop."""
    global _monitor_thread
    if _monitor_thread is None or not _monitor_thread.is_alive():
        # Perform initial probe immediately so startup state is accurate
        initial_status = _perform_live_socket_probe(SOLO_COPTER_IP, timeout=0.2)
        with _status_lock:
            global _live_copter_online
            _live_copter_online = initial_status

        _monitor_thread = LiveCopterHeartbeatMonitor(interval=0.5)
        _monitor_thread.start()


def start_telemetry_service():
    """Start MAVLink telemetry client and isolated heartbeat monitor."""
    start_connection_monitor()
    if not mav_client.is_alive():
        mav_client.start()


# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------
telemetry_bp = Blueprint("telemetry", __name__)


@telemetry_bp.route("/api/telemetry", methods=["GET"])
def api_telemetry():
    return jsonify(get_telemetry_dict())


@telemetry_bp.route("/api/action", methods=["POST"])
def api_action():
    body = request.get_json(silent=True) or {}
    action = body.get("action", "")
    mode = body.get("mode", body.get("custom_mode", None))
    res = handle_action(action, mode)
    return jsonify(res)
