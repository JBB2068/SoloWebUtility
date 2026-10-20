#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Calibration Management Module
Manages the 3DR Solo post-flash sensor calibration sequence:
- IMU Accelerometer Level & 6-Axis Calibration
- Autopilot Reboot & EKF sensor reset
- Compass (Magnetometer) Sphere Calibration
"""

from __future__ import absolute_import

import os
import sys
from webcore import Blueprint, request, jsonify
from modules.auth import login_required

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import calib_mgr

calib_engine = calib_mgr.calib_engine

# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------
calibration_bp = Blueprint("calibration", __name__)


@calibration_bp.route("/api/calib/status", methods=["GET"])
@login_required
def api_calib_status():
    return jsonify(calib_engine.get_state())


@calibration_bp.route("/api/calib/start", methods=["POST"])
@login_required
def api_calib_start():
    body = request.get_json(silent=True) or {}
    cal_type = body.get("type", "")
    endpoint = body.get("endpoint", None)

    if cal_type == "imu":
        ok, msg = calib_engine.start_imu(endpoint=endpoint)
    elif cal_type == "compass":
        wait_comp = body.get("wait_completion", True)
        ok, msg = calib_engine.start_compass(wait_completion=wait_comp, endpoint=endpoint)
    elif cal_type == "reboot":
        ok, msg = calib_engine.start_reboot(endpoint=endpoint)
    else:
        ok, msg = calib_engine.start_sequence(endpoint=endpoint)

    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": True, "message": msg}), 400


@calibration_bp.route("/api/calib/imu/start", methods=["POST"])
@login_required
def api_calib_imu_start():
    body = request.get_json(silent=True) or {}
    endpoint = body.get("endpoint", None)
    ok, msg = calib_engine.start_imu(endpoint=endpoint)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": True, "message": msg}), 400


@calibration_bp.route("/api/calib/imu/continue", methods=["POST"])
@login_required
def api_calib_imu_continue():
    ok, msg = calib_engine.continue_imu()
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": True, "message": msg}), 400


@calibration_bp.route("/api/calib/compass/start", methods=["POST"])
@login_required
def api_calib_compass_start():
    body = request.get_json(silent=True) or {}
    wait_comp = body.get("wait_completion", True)
    endpoint = body.get("endpoint", None)
    ok, msg = calib_engine.start_compass(wait_completion=wait_comp, endpoint=endpoint)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": True, "message": msg}), 400


@calibration_bp.route("/api/calib/reboot", methods=["POST"])
@login_required
def api_calib_reboot():
    body = request.get_json(silent=True) or {}
    endpoint = body.get("endpoint", None)
    ok, msg = calib_engine.start_reboot(endpoint=endpoint)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": True, "message": msg}), 400


@calibration_bp.route("/api/calib/cancel", methods=["POST"])
@login_required
def api_calib_cancel():
    ok, msg = calib_engine.cancel()
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": True, "message": msg}), 400
