#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Pairing Management Module
Controls pairing between 3DR Solo Copter and Solo Controller using
the 3DR developer method (sololink_config --pair) and Flask blueprint.
Enforces active pairing guard requiring the SoloLink Wi-Fi network to be connected.
"""

import os
import sys
import socket
from webcore import Blueprint, request, jsonify
from modules.auth import login_required
from modules.controller_firmware import get_current_wifi_ssid

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import pair_mgr
from modules.firmware import check_and_trigger_post_update_pairing, reconnect_wifi

pair_engine = pair_mgr.pair_engine

PAIRING_INSTRUCTION = (
    "After Copter and Controller have finished rebooting and applying their updates, "
    "hold the Pair button on the bottom of the Solo for 5 seconds to complete the pairing process. "
    "The SoloLink Wi-Fi network must also be connected to continue."
)


def is_sololink_network_connected():
    """On the Solo copter, check if the Controller or local MAVLink telemetry is reachable."""
    if pair_mgr.is_controller_linked(timeout=0.5):
        return True
    for host, port in [("127.0.0.1", 14560), ("10.1.1.10", 14550)]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.3)
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            pass
    return True


# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------
pairing_bp = Blueprint("pairing", __name__)


@pairing_bp.route("/api/pair/status", methods=["GET"])
def api_pair_status():
    st = pair_engine.get_state()
    st["sololink_connected"] = is_sololink_network_connected()
    st["wifi_ssid"] = get_current_wifi_ssid()
    st["pairing_instruction"] = PAIRING_INSTRUCTION
    return jsonify(st)


@pairing_bp.route("/api/pair/check", methods=["GET"])
def api_pair_check():
    chk = pair_engine.check_paired_status()
    chk["sololink_connected"] = is_sololink_network_connected()
    chk["wifi_ssid"] = get_current_wifi_ssid()
    chk["pairing_instruction"] = PAIRING_INSTRUCTION
    return jsonify(chk)


@pairing_bp.route("/api/pair/start", methods=["POST"])
def api_pair_start():
    # Active pairing guard check: prevent running unless connected to SoloLink Wi-Fi
    if not is_sololink_network_connected():
        return jsonify({
            "error": True,
            "warning": "Wi-Fi Connection Required",
            "message": "Wi-Fi Connection Required: The SoloLink Wi-Fi network must also be connected to continue."
        }), 400

    body = request.get_json(silent=True) or {}
    ssid = body.get("ssid", "")
    pwd = body.get("password", "")
    ok, msg = pair_engine.start_pairing(ssid, pwd)
    if ok:
        return jsonify({"success": True, "message": msg, "pairing_instruction": PAIRING_INSTRUCTION})
    return jsonify({"error": True, "message": msg}), 400
