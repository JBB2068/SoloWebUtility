#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Parameters & Presets Management Module
Handles reading, modifying, searching, saving, and exporting ArduPilot parameters,
preset configurations, and MAVLink parameter fetching.
"""

import os
import sys
import time
import json
import logging
from webcore import Blueprint, request, jsonify, make_response
from modules.auth import login_required

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
import param_mgr

fetch_all_params = param_mgr.fetch_all_params
fetch_via_mavftp = param_mgr.fetch_via_mavftp
fetch_via_param_messages = param_mgr.fetch_via_param_messages
fetch_progress = param_mgr.fetch_progress
commit_param_mavlink = param_mgr.commit_param_mavlink
commit_batch_mavlink = param_mgr.commit_batch_mavlink

try:
    from pymavlink import mavutil
except Exception:  # ImportError or any binding failure
    mavutil = None

DEFAULT_PARAM_FILE = os.path.join(BASE_DIR, "solo.param")
FALLBACK_PARAM_FILE = "/firmware/solo.param"

logger = logging.getLogger("parameters")

# Parameter descriptions metadata
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
    "SERVO10_MIN": "Servo 10 closed PWM pulse width (μs, 1100)",
    "SERVO10_MAX": "Servo 10 open PWM pulse width (μs, 1900)",
    "SERVO10_TRIM": "Servo 10 resting closed PWM pulse width (μs, 1100)",
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
    "ANGLE_MAX": "Maximum lean angle (centidegrees, 2000 = 20°, 3500 = 35°)",
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

PRESETS = {
    "here_gps": {
        "id": "here_gps",
        "name": "Here 1 / Here 2 GPS Compass",
        "category": "Navigation",
        "description": "Configures compass orientation for Here 1 and Here 2 external GPS units mounted upright facing forward (COMPASS_ORIENT = 0).",
        "icon": "🧭",
        "params": {
            "COMPASS_ORIENT": 0
        }
    },
    "stock_compass": {
        "id": "stock_compass",
        "name": "Stock Solo Leg Compass",
        "category": "Navigation",
        "description": "Restores factory compass orientation for the stock compass located in the Solo leg (COMPASS_ORIENT = 38).",
        "icon": "🧭",
        "params": {
            "COMPASS_ORIENT": 38
        }
    },
    "retract_gear": {
        "id": "retract_gear",
        "name": "Retractable Landing Gear",
        "category": "Airframe",
        "description": "Configures landing gear servo output on SERVO7, manual toggle on RC7, and automatic deployment in RTL and LAND modes.",
        "icon": "🛬",
        "params": {
            "LGR_ENABLE": 1,
            "LGR_OPTIONS": 3,
            "LGR_SERVO_DEPLOY": 1000,
            "LGR_SERVO_RTRACT": 2000,
            "SERVO7_FUNCTION": 29,
            "RC7_OPTION": 29
        }
    },
    "speed_sport": {
        "id": "speed_sport",
        "name": "Speed Control: Sport Mode (High Velocity)",
        "category": "Flight Performance",
        "description": "Unlocks maximum agility with 15 m/s waypoint cruise, 12 m/s loiter speed, faster vertical climb/descent rates, and 35° lean angle.",
        "icon": "⚡",
        "params": {
            "WPNAV_SPEED": 1500,
            "WPNAV_SPEED_UP": 400,
            "WPNAV_SPEED_DN": 300,
            "LOIT_SPEED": 1200,
            "LOIT_ACC_MAX": 350,
            "LOIT_BRK_ACCEL": 250,
            "ANGLE_MAX": 3500,
            "PILOT_SPEED_UP": 250,
            "PILOT_SPEED_DN": 200
        }
    },
    "speed_cinematic": {
        "id": "speed_cinematic",
        "name": "Speed Control: Cinematic Mode (Smooth)",
        "category": "Flight Performance",
        "description": "Smooth gentle accelerations and dampened braking optimized for ultra-smooth video recording and stabilized photography.",
        "icon": "🎬",
        "params": {
            "WPNAV_SPEED": 500,
            "WPNAV_SPEED_UP": 180,
            "WPNAV_SPEED_DN": 120,
            "LOIT_SPEED": 500,
            "LOIT_ACC_MAX": 150,
            "LOIT_BRK_ACCEL": 120,
            "LOIT_BRK_DELAY": 0.4,
            "ANGLE_MAX": 2000
        }
    },
    "bait_drop": {
        "id": "bait_drop",
        "name": "Bait Drop / Payload Release Gripper",
        "category": "Payload",
        "description": "Configures ArduPilot Gripper / Servo release on Pin 18 (PWM8 / Servo 8) with RC8 controller trigger.",
        "icon": "🎣",
        "params": {
            "GRIP_ENABLE": 1,
            "GRIP_TYPE": 1,
            "SERVO8_FUNCTION": 28,
            "RC8_OPTION": 19,
            "GRIP_GRAB": 1000,
            "GRIP_RELEASE": 2000
        }
    },
    "lipo_default": {
        "id": "lipo_default",
        "name": "Stock 4S LiPo Battery Parameters",
        "category": "Battery & Power",
        "description": "Standard factory 4S Lithium-Polymer failsafe thresholds (14.0V Low Failsafe RTL, 13.5V Critical Failsafe RTL, 5200mAh pack).",
        "icon": "🔋",
        "params": {
            "BATT_MONITOR": 5,
            "BATT_ARM_VOLT": 13.5,
            "BATT_LOW_VOLT": 14.0,
            "BATT_CRT_VOLT": 13.5,
            "BATT_LOW_MAH": 520,
            "BATT_CRT_MAH": 250,
            "BATT_LOW_TIMER": 10,
            "BATT_FS_LOW_ACT": 2,
            "BATT_FS_CRT_ACT": 2,
            "BATT_FS_VOLTSRC": 1,
            "MOT_BAT_VOLT_MIN": 13.2,
            "MOT_BAT_VOLT_MAX": 16.8,
            "MOT_BAT_CURR_MAX": 40.0
        }
    },
    "liion_default": {
        "id": "liion_default",
        "name": "4S Li-ion Battery Parameters (Madhacker / High-Cap)",
        "category": "Battery & Power",
        "description": "Optimized discharge curves for Lithium-Ion packs (18650 / 21700), lowering voltage cutoffs down to 11.6V Low / 11.3V Critical with Smart RTL.",
        "icon": "⚡",
        "params": {
            "BATT_MONITOR": 5,
            "BATT_ARM_VOLT": 13.8,
            "BATT_LOW_VOLT": 11.6,
            "BATT_CRT_VOLT": 11.3,
            "BATT_LOW_MAH": 0,
            "BATT_CRT_MAH": 0,
            "BATT_LOW_TIMER": 2,
            "BATT_FS_LOW_ACT": 3,
            "BATT_FS_CRT_ACT": 3,
            "BATT_FS_VOLTSRC": 1,
            "MOT_BAT_VOLT_MIN": 10.8,
            "MOT_BAT_VOLT_MAX": 16.8,
            "MOT_BAT_CURR_MAX": 40.0
        }
    }
}


def get_all_presets():
    return list(PRESETS.values())


def get_preset(preset_id):
    return PRESETS.get(preset_id)


class ParameterManager(object):
    def __init__(self):
        self.params = {}
        self.load_initial_params()

    def load_initial_params(self):
        target_file = DEFAULT_PARAM_FILE
        if not os.path.exists(target_file) and os.path.exists(FALLBACK_PARAM_FILE):
            target_file = FALLBACK_PARAM_FILE

        if os.path.exists(target_file):
            self.load_from_file(target_file)
        else:
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
                "GPS_TYPE": 1.0,
                "WPNAV_SPEED": 1000.0,
                "WPNAV_SPEED_UP": 250.0,
                "WPNAV_SPEED_DN": 150.0,
                "LOIT_SPEED": 800.0,
                "LOIT_ACC_MAX": 250.0,
                "LOIT_BRK_ACCEL": 200.0,
                "LOIT_BRK_DELAY": 0.5,
                "ANGLE_MAX": 3000.0,
                "PILOT_SPEED_UP": 250.0,
                "PILOT_SPEED_DN": 200.0,
                "BATT_MONITOR": 5.0,
                "BATT_ARM_VOLT": 13.5,
                "BATT_LOW_VOLT": 14.0,
                "BATT_CRT_VOLT": 13.5,
                "BATT_LOW_MAH": 520.0,
                "BATT_CRT_MAH": 250.0,
                "BATT_LOW_TIMER": 10.0,
                "BATT_FS_LOW_ACT": 2.0,
                "BATT_FS_CRT_ACT": 2.0,
                "RTL_ALT": 4500.0,
                "RTL_SPEED": 800.0
            }

    def load_from_file(self, filepath):
        if not os.path.exists(filepath):
            return False, "File %s does not exist." % filepath
        try:
            new_params = {}
            with open(filepath, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(None, 1) if " " in line or "\t" in line else line.split(",", 1)
                    if len(parts) >= 2:
                        k = parts[0].strip()
                        v_str = parts[1].strip()
                        try:
                            new_params[k] = float(v_str) if "." in v_str or "e" in v_str.lower() else int(v_str)
                        except ValueError:
                            new_params[k] = v_str
            self.params.update(new_params)
            return True, "Loaded %d parameters." % len(new_params)
        except Exception as e:
            return False, "Failed to load parameters: %s" % str(e)

    def save_to_file(self, filepath=DEFAULT_PARAM_FILE):
        try:
            with open(filepath, "w") as f:
                f.write("# 3DR Solo Parameter File\n")
                f.write("# Generated: %s\n\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
                for k in sorted(self.params.keys()):
                    f.write("%s\t%s\n" % (k, str(self.params[k])))
            return True, "Parameters saved to %s" % filepath
        except Exception as e:
            return False, "Failed to save parameter file: %s" % str(e)

    def search_params(self, query=""):
        q = (query or "").strip().lower()
        results = []
        for k in sorted(self.params.keys()):
            val = self.params[k]
            desc = PARAM_METADATA.get(k, "")
            if not q or q in k.lower() or q in desc.lower() or q in str(val).lower():
                results.append({
                    "name": k,
                    "value": val,
                    "description": desc
                })
        return results

    def set_param(self, name, value, send_mavlink=True):
        """Set a single parameter and commit to Pixhawk with PARAM_VALUE verification."""
        ok, msg = param_mgr.mgr.set_param(name, value, send_mavlink=send_mavlink)
        if ok or (name in param_mgr.mgr.params):
            self.params.update(param_mgr.mgr.params)
            self.save_to_file(DEFAULT_PARAM_FILE)
        return ok, msg

    def set_batch_params(self, param_dict, send_mavlink=True):
        """Set multiple parameters with PARAM_VALUE verification for every parameter."""
        ok, msg = param_mgr.mgr.set_batch_params(param_dict, send_mavlink=send_mavlink)
        if ok or param_mgr.mgr.params:
            self.params.update(param_mgr.mgr.params)
            self.save_to_file(DEFAULT_PARAM_FILE)
        return ok, msg

    def export_param_string(self):
        lines = [
            "# 3DR Solo Parameter File Export",
            "# Exported: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            ""
        ]
        for k in sorted(self.params.keys()):
            lines.append("%s\t%s" % (k, str(self.params[k])))
        return "\n".join(lines)

    def _get_mavlink_connection(self, timeout=4.0):
        """Open a local loopback MAVLink link to the Sololink telemetry forwarder (on-drone)."""
        if mavutil is None:
            return None
        # Loopback and local forwarder telemetry endpoints
        endpoints = [
            'udpin:127.0.0.1:14560',
            'udpin:0.0.0.0:14560',
            'udpout:127.0.0.1:14560',
            'udpout:10.1.1.10:14560',
            'udpin:127.0.0.1:14550',
            'udpin:0.0.0.0:14550',
            'udpout:127.0.0.1:14550',
            'udpout:10.1.1.10:14550',
        ]
        per_endpoint = max(1.5, min(timeout, 4.0))
        for ep in endpoints:
            mav = None
            try:
                # source_system=254 avoids colliding with the Solo Controller (system 255).
                # Do NOT pass source_component (incompatible with embedded pymavlink on Solo).
                mav = mavutil.mavlink_connection(ep, source_system=254)
                start_t = time.time()
                while time.time() - start_t < per_endpoint:
                    # GCS heartbeat so telem_forwarder registers this UDP socket as an active client
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
                try:
                    if mav is not None:
                        mav.close()
                except Exception:
                    pass
        return None

    def fetch_live_mavlink(self, timeout=30.0, progress_callback=None):
        """Fetch the full live parameter set from the flight controller over loopback MAVLink.

        Returns (ok, message). Never reads any local file; on failure nothing is faked.
        """
        def _cb(cur, tot=0, ph="running", msg=""):
            param_mgr.fetch_progress.update(cur, tot, ph, msg)
            if progress_callback:
                try:
                    progress_callback(cur, tot, ph, msg)
                except Exception:
                    pass

        param_mgr.fetch_progress.reset()
        param_mgr.fetch_progress.update(0, 0, "connecting", "Connecting to autopilot...")

        if mavutil is None:
            param_mgr.fetch_progress.update(0, 0, "error", "pymavlink is not installed.")
            return False, "pymavlink is not installed in this Python (%s)." % sys.executable

        mav = self._get_mavlink_connection(timeout=4.0)
        if not mav:
            param_mgr.fetch_progress.update(0, 0, "error", "Connection timeout waiting for heartbeat.")
            return False, ("Connection timeout: no MAVLink heartbeat from the autopilot on "
                           "127.0.0.1:14560/14550. Is the Sololink telemetry forwarder running?")

        try:
            result = param_mgr.fetch_all_params(mav, log=lambda m: logger.info("param fetch: %s", m), progress_callback=_cb)
        except Exception as e:
            try:
                mav.close()
            except Exception:
                pass
            param_mgr.fetch_progress.update(0, 0, "error", str(e))
            return False, "MAVLink error: %s" % str(e)
        try:
            mav.close()
        except Exception:
            pass

        if not result["ok"]:
            param_mgr.fetch_progress.update(len(result.get("params", {})), result.get("expected", 0), "error", result.get("message", "Fetch failed"))
            return False, result["message"]

        for name, val in result["params"].items():
            self.params[name] = val
        self.save_to_file(DEFAULT_PARAM_FILE)

        # Synchronize param_mgr singleton cache as well
        try:
            param_mgr.mgr.params.update(self.params)
            param_mgr.mgr.save_to_file(DEFAULT_PARAM_FILE)
        except Exception:
            pass

        param_mgr.fetch_progress.update(len(result["params"]), result.get("expected", len(result["params"])), "done", result["message"])

        return True, "Successfully fetched %d live parameters from the flight controller (%s)." % (
            len(result["params"]), "MAVFTP param.pck" if result.get("method") == "mavftp" else "PARAM_REQUEST_LIST")


# Global singleton
mgr = ParameterManager()

# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------
parameters_bp = Blueprint("parameters", __name__)


@parameters_bp.route("/api/params", methods=["GET"])
@login_required
def api_get_params():
    q = request.args.get("q", "")
    results = mgr.search_params(q)
    return jsonify({
        "count": len(results),
        "total": len(mgr.params),
        "params": results
    })


@parameters_bp.route("/api/params/presets", methods=["GET"])
def api_get_presets():
    return jsonify({
        "presets": get_all_presets()
    })


@parameters_bp.route("/api/params/export", methods=["GET"])
@login_required
def api_export_params():
    param_str = mgr.export_param_string()
    resp = make_response(param_str)
    resp.headers["Content-Type"] = "text/plain; charset=utf-8"
    resp.headers["Content-Disposition"] = 'attachment; filename="solo.param"'
    return resp


@parameters_bp.route("/api/params/save", methods=["POST"])
@login_required
def api_save_params():
    body = request.get_json(silent=True) or {}
    param_dict = body.get("params", {})
    if not param_dict:
        name = body.get("name")
        val = body.get("value")
        if name:
            param_dict[name] = val

    if not param_dict:
        return jsonify({"error": True, "message": "No parameters provided to save."}), 400

    ok, msg = mgr.set_batch_params(param_dict)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": True, "message": msg}), 400


@parameters_bp.route("/api/params/apply-preset", methods=["POST"])
@login_required
def api_apply_preset():
    body = request.get_json(silent=True) or {}
    preset_id = body.get("preset_id", "")
    preset = get_preset(preset_id)
    if not preset:
        return jsonify({"error": True, "message": "Preset '%s' not found." % preset_id}), 404

    ok, msg = mgr.set_batch_params(preset["params"])
    if ok:
        return jsonify({
            "success": True,
            "message": "Preset '%s': %s" % (preset["name"], msg),
            "preset_id": preset_id,
            "params": preset["params"]
        })
    return jsonify({"error": True, "message": msg}), 400


@parameters_bp.route("/api/params/import", methods=["POST"])
@login_required
def api_import_params():
    body = request.get_json(silent=True) or {}
    content = body.get("content", "")
    temp_file = os.path.join(BASE_DIR, "temp_import.param")
    try:
        with open(temp_file, "w") as f:
            f.write(content)
        ok, msg = mgr.load_from_file(temp_file)
        if os.path.exists(temp_file):
            os.remove(temp_file)
        if ok:
            return jsonify({"success": True, "message": msg})
        return jsonify({"error": True, "message": msg}), 400
    except Exception as e:
        return jsonify({"error": True, "message": str(e)}), 500


@parameters_bp.route("/api/params/fetch-live", methods=["POST"])
@login_required
def api_fetch_live_params():
    """Live MAVLink fetch only. No file fallback: failures are reported explicitly."""
    try:
        ok, msg = mgr.fetch_live_mavlink()
    except Exception as e:
        ok, msg = False, "MAVLink error: %s" % e
    if ok:
        return jsonify({"success": True, "source": "live", "message": msg})
    return jsonify({"error": True, "source": "none",
                    "message": "MAVLink fetch failed: " + msg}), 503


@parameters_bp.route("/api/parameters/fetch", methods=["GET", "POST"])
@login_required
def api_parameters_fetch():
    """Live MAVLink fetch returning [{name, value}, ...]. No file fallback."""
    try:
        ok, msg = mgr.fetch_live_mavlink()
    except Exception as e:
        ok, msg = False, "MAVLink error: %s" % e
    if not ok:
        return jsonify({"error": True, "source": "none",
                        "message": "MAVLink fetch failed: " + msg}), 503
    rows = [{"name": k, "value": mgr.params[k]} for k in sorted(mgr.params)]
    return jsonify({"success": True, "source": "live", "message": msg,
                    "count": len(rows), "params": rows})


@parameters_bp.route("/api/params/fetch-status", methods=["GET"])
def api_params_fetch_status():
    """Live MAVLink parameter download progress and status check."""
    return jsonify(param_mgr.fetch_progress.get_status())


@parameters_bp.route("/api/parameters/fetch-status", methods=["GET"])
def api_parameters_fetch_status():
    """Alias for /api/params/fetch-status."""
    return jsonify(param_mgr.fetch_progress.get_status())

