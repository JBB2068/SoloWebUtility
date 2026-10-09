#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ArduPilot Parameter Manager for 3DR Solo / Open Solo.
Reads, modifies, searches, and saves parameters over MAVLink or parameter files.
Compatible with Python 2.7 and Python 3.x.
"""

import os
import sys
import time
import json
import logging

try:
    from pymavlink import mavutil
except ImportError:
    mavutil = None

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
                f.write("# 3DR Solo Parameter File - Open Solo 4.7.1\n")
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
            'udpout:10.1.1.10:14560',
            'udpin:0.0.0.0:14560',
            'udpin:127.0.0.1:14560',
            'udpout:127.0.0.1:14560',
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
                    if hb:
                        mav.target_system = hb.get_srcSystem()
                        mav.target_component = hb.get_srcComponent()
                        return mav
                mav.close()
            except Exception:
                pass
        return None

    def fetch_live_mavlink(self, timeout=20.0):
        """Attempt to fetch live parameters from Pixhawk over safe UDP MAVLink."""
        if mavutil is None:
            return False, "pymavlink not installed."
        
        mav = self._get_mavlink_connection(timeout=4.0)
        if not mav:
            return False, "Could not connect to Pixhawk MAVLink (timeout waiting for heartbeat)."

        try:
            mav.mav.param_request_list_send(mav.target_system, mav.target_component)
            deadline = time.time() + timeout
            count = 0
            total_expected = None
            while time.time() < deadline:
                msg = mav.recv_match(type='PARAM_VALUE', blocking=True, timeout=1.0)
                if msg:
                    param_id = msg.param_id
                    if isinstance(param_id, bytes):
                        param_id = param_id.decode('ascii', 'ignore')
                    param_id = param_id.rstrip('\x00').strip()
                    val = msg.param_value
                    if isinstance(val, float) and val.is_integer():
                        val = int(val)
                    self.params[param_id] = val
                    count += 1
                    if hasattr(msg, 'param_count') and msg.param_count > 0:
                        total_expected = msg.param_count
                    if total_expected and len(self.params) >= total_expected:
                        break
                else:
                    if count > 0:
                        break
            mav.close()
            if count > 0:
                self.save_to_file(DEFAULT_PARAM_FILE)
                return True, "Successfully fetched %d live parameters from Pixhawk." % count
            return False, "Connected to Pixhawk, but no parameter values received."
        except Exception as e:
            try:
                mav.close()
            except Exception:
                pass
            return False, str(e)

    def set_param(self, name, value, send_mavlink=True):
        """Set a single parameter and commit to Pixhawk."""
        try:
            val_num = float(value)
            if val_num.is_integer():
                val_num = int(val_num)
        except Exception:
            val_num = value
        
        self.params[name] = val_num
        self.save_to_file(DEFAULT_PARAM_FILE)
        
        mav_status = ""
        # If on Solo, sync to Pixhawk over safe UDP MAVLink
        if send_mavlink and mavutil is not None:
            mav = self._get_mavlink_connection(timeout=3.0)
            if mav:
                try:
                    val_f = float(value)
                    pname = name.encode('ascii') if sys.version_info[0] >= 3 else str(name)
                    target_sys = getattr(mav, 'target_system', 1) or 1
                    target_comp = getattr(mav, 'target_component', 1) or 1
                    mav.mav.param_set_send(
                        target_sys,
                        target_comp,
                        pname,
                        val_f,
                        mavutil.mavlink.MAV_PARAM_TYPE_REAL32
                    )
                    ack = None
                    for _ in range(5):
                        msg = mav.recv_match(type='PARAM_VALUE', blocking=True, timeout=1.0)
                        if msg:
                            ack_id = msg.param_id
                            if isinstance(ack_id, bytes):
                                ack_id = ack_id.decode('ascii', 'ignore')
                            ack_id = ack_id.rstrip('\x00').strip()
                            if ack_id == name:
                                ack = msg
                                break
                    if ack:
                        mav_status = " (Committed & ACKed by Pixhawk)"
                    else:
                        mav_status = " (Sent to Pixhawk)"
                except Exception as ex:
                    mav_status = " (Autopilot sync error: %s)" % str(ex)
                finally:
                    try:
                        mav.close()
                    except Exception:
                        pass
            else:
                mav_status = " (Saved locally, MAVLink unreachable)"
        
        return True, "Parameter %s set to %s%s" % (name, str(val_num), mav_status)

    def set_batch_params(self, param_dict, send_mavlink=True):
        """Set multiple parameters at once in a single safe MAVLink session with ACK verification."""
        updated = 0
        for k, v in param_dict.items():
            try:
                val_num = float(v)
                if val_num.is_integer():
                    val_num = int(val_num)
            except Exception:
                val_num = v
            self.params[k] = val_num
            updated += 1
        
        # Save to local file once
        self.save_to_file(DEFAULT_PARAM_FILE)
        
        mav_acked = 0
        mav_err = ""
        # Send over single UDP MAVLink session
        if send_mavlink and mavutil is not None:
            mav = self._get_mavlink_connection(timeout=3.5)
            if mav:
                try:
                    target_sys = getattr(mav, 'target_system', 1) or 1
                    target_comp = getattr(mav, 'target_component', 1) or 1
                    for k, v in param_dict.items():
                        try:
                            val_f = float(v)
                            pname = k.encode('ascii') if sys.version_info[0] >= 3 else str(k)
                            mav.mav.param_set_send(
                                target_sys,
                                target_comp,
                                pname,
                                val_f,
                                mavutil.mavlink.MAV_PARAM_TYPE_REAL32
                            )
                            for _ in range(5):
                                ack = mav.recv_match(type='PARAM_VALUE', blocking=True, timeout=1.0)
                                if ack:
                                    ack_id = ack.param_id
                                    if isinstance(ack_id, bytes):
                                        ack_id = ack_id.decode('ascii', 'ignore')
                                    ack_id = ack_id.rstrip('\x00').strip()
                                    if ack_id == k:
                                        mav_acked += 1
                                        break
                        except Exception as p_ex:
                            mav_err = str(p_ex)
                finally:
                    try:
                        mav.close()
                    except Exception:
                        pass
            else:
                mav_err = "Autopilot MAVLink not reachable (timeout waiting for heartbeat)"
        
        if send_mavlink and mav_acked > 0:
            return True, "Committed %d parameters to Pixhawk (%d ACKed by flight controller)." % (updated, mav_acked)
        elif send_mavlink and mav_err:
            return True, "Saved %d parameters locally (Autopilot sync: %s)." % (updated, mav_err)
        return True, "Saved %d parameters." % updated

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
        lines = ["# 3DR Solo Parameter Export - Open Solo 4.7.1"]
        for k in sorted(self.params.keys()):
            val = self.params[k]
            if isinstance(val, float) and val.is_integer():
                val = int(val)
            lines.append("%s %s" % (k, str(val)))
        return "\n".join(lines)


# Singleton instance
mgr = ParameterManager()
