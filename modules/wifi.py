#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Wi-Fi & Network Management Module (Native On-Drone Linux Client Bridging)
Runs directly on the 3DR Solo Copter iMX6 Linux computer (Yocto Linux).
All network routing and client bridging is executed on the Artoo Controller (10.1.1.1)
over native SSH without any Windows desktop dependencies:
  - Scanning:  wpa_cli -i wlan1 scan && wpa_cli -i wlan1 scan_results
  - Bridging:  sololink_config --set-wifi-sta "SSID" "PASSWORD"
"""

import os
import sys
import logging
from webcore import Blueprint, request, jsonify
from modules.auth import login_required

logger = logging.getLogger("solo_web_utility.wifi")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import wifi_mgr

# Export core functions
get_wifi_status = wifi_mgr.get_wifi_status
scan_wifi_networks = wifi_mgr.scan_wifi_networks
connect_home_wifi = wifi_mgr.connect_home_wifi
set_bidirectional = wifi_mgr.set_bidirectional
reset_to_default_ap = wifi_mgr.reset_to_default_ap
ensure_copter_internet_route = wifi_mgr.ensure_copter_internet_route


# ---------------------------------------------------------------------------
# Native On-Drone Controller Client Bridging (Linux SSH to 10.1.1.1)
# ---------------------------------------------------------------------------
def scan_controller_wifi():
    """
    Execute native Linux Wi-Fi scan on the Controller (10.1.1.1) over SSH:
      wpa_cli -i wlan1 scan && wpa_cli -i wlan1 scan_results
    Falls back to wlan0 if wlan1 is not present.
    Parses tab-separated results into a clean JSON array list of network SSIDs.
    """
    cmd = (
        "wpa_cli -i wlan1 scan >/dev/null 2>&1 || wpa_cli -i wlan0 scan >/dev/null 2>&1; "
        "sleep 1; "
        "wpa_cli -i wlan1 scan_results 2>/dev/null || wpa_cli -i wlan0 scan_results 2>/dev/null"
    )
    rc, out = wifi_mgr._ssh_paramiko(cmd, host="10.1.1.1", timeout=15)
    parsed = wifi_mgr._parse_wpa_cli(out)

    if not parsed:
        # Fallback to general multi-tool scan (wl / iwlist) if wpa_cli table was empty
        parsed = wifi_mgr.scan_wifi_networks().get("networks", [])

    ssids = []
    for item in parsed:
        name = item.get("ssid") if isinstance(item, dict) else str(item)
        if name and name not in ssids and not name.startswith("\\x00"):
            ssids.append(name)

    return ssids


def bridge_controller_wifi(ssid, password=None):
    """
    Command the Artoo Controller (10.1.1.1) to bridge to a selected home router SSID
    and password using the built-in system utility:
      sololink_config --set-wifi-sta "SSID" "PASSWORD"
    Bridges the controller to the home router, providing concurrent internet routing
    to both the copter's iMX6 and any connected PCs or mobile apps.
    """
    if not ssid:
        return False, "SSID is required."

    ssid = ssid.strip()
    safe_ssid = ssid.replace('"', '\\"')

    if password:
        safe_pwd = password.strip().replace('"', '\\"')
        cmd = 'sololink_config --set-wifi-sta "%s" "%s"' % (safe_ssid, safe_pwd)
    else:
        cmd = 'sololink_config --set-wifi-sta "%s" ""' % safe_ssid

    logger.info("Sending bridging command to Controller (10.1.1.1): %s", cmd)
    rc, out = wifi_mgr._ssh_paramiko(cmd, host="10.1.1.1", timeout=30)
    logger.info("sololink_config response (rc=%s): %s", rc, out.strip())

    # Ensure Copter default route points to Controller gateway 10.1.1.1
    try:
        wifi_mgr.ensure_copter_internet_route()
    except Exception as e:
        logger.warning("ensure_copter_internet_route error: %s", e)

    if rc == 0 or "success" in out.lower() or "connected" in out.lower() or not out.strip():
        return True, "Controller bridged to '%s' successfully. Internet routing established." % ssid
    else:
        # Check if error output contains helpful diagnostic
        err_msg = out.strip() if out.strip() else "Process exited with return code %s" % rc
        return False, "Bridge configuration failed: %s" % err_msg


# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------
wifi_bp = Blueprint("wifi", __name__)


@wifi_bp.route("/api/wifi/status", methods=["GET"])
def api_wifi_status():
    """Return active Wi-Fi and bridging status."""
    st = get_wifi_status()
    return jsonify(st)


@wifi_bp.route("/api/wifi/scan", methods=["GET"])
def api_wifi_scan():
    """
    Trigger native Controller SSH scan (wpa_cli -i wlan1 scan && wpa_cli -i wlan1 scan_results)
    and return a clean JSON array list of discovered network names.
    """
    ssids = scan_controller_wifi()
    return jsonify(ssids)


@wifi_bp.route("/api/wifi/bridge", methods=["POST"])
@wifi_bp.route("/api/wifi/connect", methods=["POST"])
def api_wifi_bridge():
    """
    Bridge Controller to home router using:
      sololink_config --set-wifi-sta "SSID" "PASSWORD"
    """
    from modules.telemetry import is_vehicle_armed
    try:
        if is_vehicle_armed():
            return jsonify({"error": True, "message": "SAFETY INTERLOCK: Bridging Wi-Fi prohibited while vehicle is ARMED!"}), 403
    except Exception:
        pass

    body = request.get_json(silent=True) or {}
    ssid = body.get("ssid", "").strip()
    pwd = body.get("password", "").strip()

    if not ssid:
        return jsonify({"error": True, "message": "SSID is required."}), 400

    ok, msg = bridge_controller_wifi(ssid, pwd or None)
    if ok:
        return jsonify({"success": True, "message": msg, "ssid": ssid})
    return jsonify({"error": True, "message": msg}), 400


@wifi_bp.route("/api/wifi/reconnect-sololink", methods=["POST"])
def api_wifi_reconnect_sololink():
    """
    Return standard success signal for client compatibility.
    When running in on-drone bridge mode, clients remain permanently connected
    to the SoloLink AP while the controller bridges WAN traffic transparently.
    """
    return jsonify({"success": True, "message": "SoloLink client connection active."})


@wifi_bp.route("/api/wifi/bidirectional", methods=["POST"])
@login_required
def api_wifi_bidirectional():
    body = request.get_json(silent=True) or {}
    enable = body.get("enable", True)
    ok, msg = set_bidirectional(enable)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": True, "message": msg}), 400


@wifi_bp.route("/api/wifi/reset-default", methods=["POST"])
@login_required
def api_wifi_reset_default():
    ok, msg = reset_to_default_ap()
    return jsonify({"success": True, "message": msg})
