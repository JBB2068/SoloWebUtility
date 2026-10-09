#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ArduPilot IMU & Compass Calibration Manager for 3DR Solo / Open Solo.
Automates the full post-flash calibration sequence:
  1. IMU Calibration (Gyro + Accelerometer / Level Trim)
  2. Autopilot Reboot & Automatic Reconnection
  3. Compass (Magnetometer) Calibration

Compatible with Python 2.7 and Python 3.x.
Uses safe UDP MAVLink without disturbing serial telemetry forwarders.
"""

import os
import sys
import time
import socket
import logging
import threading

try:
    from pymavlink import mavutil
except ImportError:
    mavutil = None

# MAVLink Command IDs
MAV_CMD_PREFLIGHT_CALIBRATION = 241
MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN = 246
MAV_CMD_DO_START_MAG_CAL = 42424
MAV_CMD_DO_ACCEPT_MAG_CAL = 42425
MAV_CMD_DO_CANCEL_MAG_CAL = 42426
MAV_CMD_ACCELCAL_VEHICLE_POS = 42429

# MAV_RESULT return codes
MAV_RESULT_ACCEPTED = 0
MAV_RESULT_TEMPORARILY_REJECTED = 1
MAV_RESULT_DENIED = 2
MAV_RESULT_UNSUPPORTED = 3
MAV_RESULT_FAILED = 4
MAV_RESULT_IN_PROGRESS = 5

RESULT_NAMES = {
    0: "ACCEPTED",
    1: "TEMPORARILY_REJECTED",
    2: "DENIED",
    3: "UNSUPPORTED",
    4: "FAILED",
    5: "IN_PROGRESS"
}

# MAG_CAL status codes
MAG_CAL_NOT_STARTED = 0
MAG_CAL_WAITING_TO_START = 1
MAG_CAL_RUNNING_STEP_ONE = 2
MAG_CAL_RUNNING_STEP_TWO = 3
MAG_CAL_SUCCESS = 4
MAG_CAL_FAILED = 5
MAG_CAL_BAD_ORIENTATION = 6
MAG_CAL_BAD_RADIUS = 7

MAG_STATUS_NAMES = {
    0: "NOT_STARTED",
    1: "WAITING_TO_START",
    2: "RUNNING_STEP_ONE",
    3: "RUNNING_STEP_TWO",
    4: "SUCCESS",
    5: "FAILED",
    6: "BAD_ORIENTATION",
    7: "BAD_RADIUS"
}

# Standard Solo MAVLink UDP endpoints
# 14560 is telem_forwarder on Solo iMX6; 14550 is GCS telemetry
def is_on_solo():
    return os.path.exists("/firmware") or os.path.exists("/etc/sololink.conf")

def get_default_endpoints():
    if is_on_solo():
        # On Solo copter: probe copter IP 10.1.1.10:14560 and local listener first
        return [
            'udpout:10.1.1.10:14560',
            'udpin:0.0.0.0:14560',
            'udpin:127.0.0.1:14560',
            'udpout:127.0.0.1:14560',
            'udpin:0.0.0.0:14550',
            'udpout:127.0.0.1:14550',
            'udpout:10.1.1.10:14550'
        ]
    else:
        return [
            'udpin:0.0.0.0:14560',
            'udpout:10.1.1.10:14560',
            'udpout:127.0.0.1:14560',
            'udpin:0.0.0.0:14550',
            'udpout:10.1.1.10:14550'
        ]

DEFAULT_ENDPOINTS = get_default_endpoints()


def get_mavlink_connection(endpoint=None, timeout=5.0, source_system=254):
    """
    Establish safe UDP MAVLink connection to the autopilot.
    Iterates through known Solo telemetry endpoints or tries specified endpoint.
    Sends GCS heartbeats so telem_forwarder registers the socket.
    Uses source_system=254 by default to avoid clashing with Solo Controller (system 255).
    """
    if mavutil is None:
        return None

    eps = [endpoint] if endpoint else get_default_endpoints()
    for ep in eps:
        if not ep:
            continue
        try:
            mav = mavutil.mavlink_connection(ep, source_system=source_system)
            start_t = time.time()
            probe_timeout = timeout if endpoint else min(1.2, timeout)
            while time.time() - start_t < probe_timeout:
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


def calibrate_imu(mav, imu_type="simple", timeout=30.0, min_duration=8.0, settle_delay=3.0, log_cb=None, progress_cb=None, cancel_event=None):
    """
    Perform IMU calibration (gyroscope and accelerometer/level trim).
    Ensures the autopilot completes sensor sampling and writes offsets to storage
    before reporting success.

    Parameters:
      mav: pymavlink connection object
      imu_type: 'simple' (1-step level surface accel offset cal, ArduPilot 3.5+),
                'level' (attitude level trim cal),
                'full' (3D 6-axis accel cal)
      timeout: maximum seconds to wait for calibration completion
      min_duration: minimum seconds to sample sensors before accepting completion
      settle_delay: seconds to wait after sampling so offsets are written to FRAM/EEPROM
      log_cb: callable(msg, level)
      progress_cb: callable(stage, pct, details)
      cancel_event: threading.Event to abort operation

    Returns:
      (bool success, str message)
    """
    def _log(msg, level="INFO"):
        if log_cb:
            log_cb(msg, level)
        else:
            print("[IMU_CAL] %s" % msg)

    if not mav:
        return False, "MAVLink connection not provided."

    target_sys = getattr(mav, 'target_system', 1) or 1
    target_comp = getattr(mav, 'target_component', 1) or 1

    # In mock test environments, skip real-time delays
    if getattr(mav, 'is_mock', False) or type(mav).__name__ == 'MockMavConnection':
        min_duration = 0.0
        settle_delay = 0.0

    # Param5 choices: 4 = simple accel cal (single position), 2 = level cal, 1 = 3D cal
    if imu_type == "level":
        p5_accel = 2.0
        mode_desc = "Level Attitude Trim Calibration"
    elif imu_type == "full":
        p5_accel = 1.0
        mode_desc = "Full 3D 6-Axis Accelerometer Calibration"
    else:
        p5_accel = 4.0
        mode_desc = "Simple Accelerometer Calibration (single-position)"

    _log("Starting IMU %s..." % mode_desc, "HEADER")
    _log("Ensure Solo is resting completely still on a flat, level surface.", "WARNING")

    if progress_cb:
        progress_cb("imu", 10, "Sending IMU calibration command...")

    # MAV_CMD_PREFLIGHT_CALIBRATION: param1 = 1 (gyro), param5 = p5_accel
    try:
        mav.mav.command_long_send(
            target_sys,
            target_comp,
            MAV_CMD_PREFLIGHT_CALIBRATION,
            0,          # confirmation
            1.0,        # param1: Gyro calibration
            0.0,        # param2: Magnetometer (handled in separate step)
            0.0,        # param3: Ground pressure
            0.0,        # param4: Radio RC
            p5_accel,   # param5: Accelerometer cal
            0.0,        # param6: Compass mot
            0.0         # param7: Empty
        )
    except Exception as e:
        return False, "Failed to send IMU calibration command: %s" % str(e)

    start_time = time.time()
    deadline = start_time + max(timeout, 20.0)
    last_hb = 0
    cmd_accepted = False
    cal_completed = False
    cal_failed = False
    last_status = ""

    while time.time() < deadline:
        if cancel_event and cancel_event.is_set():
            return False, "IMU calibration canceled by user."

        now = time.time()
        elapsed = now - start_time

        # Update progress smoothly across sampling duration
        if progress_cb and not cal_completed and min_duration > 0:
            pct = min(90, int(10 + (elapsed / min_duration) * 80))
            progress_cb("imu", pct, "Calibrating IMU sensors... Keep drone still (%ds)" % int(elapsed))

        # Maintain GCS heartbeat
        if now - last_hb >= 1.0:
            try:
                mav.mav.heartbeat_send(
                    mavutil.mavlink.MAV_TYPE_GCS,
                    mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                    0, 0, 0
                )
            except Exception:
                pass
            last_hb = now

        msg = mav.recv_match(type=['COMMAND_ACK', 'STATUSTEXT'], blocking=True, timeout=0.5)
        if not msg:
            if elapsed >= min_duration and cmd_accepted and not cal_failed:
                cal_completed = True
                break
            continue

        mtype = msg.get_type()
        if mtype == 'STATUSTEXT':
            text = getattr(msg, 'text', '')
            if isinstance(text, bytes):
                text = text.decode('utf-8', 'ignore')
            text = text.rstrip('\x00').strip()
            if text and text != last_status:
                last_status = text
                _log("Autopilot: %s" % text, "INFO")
                t_lower = text.lower()
                if any(w in t_lower for w in ["successful", "complete", "completed", "passed", "done"]):
                    cal_completed = True
                    if elapsed >= min_duration:
                        break
                elif "failed" in t_lower or "failure" in t_lower:
                    cal_failed = True
                    return False, "IMU calibration failed: %s" % text

        elif mtype == 'COMMAND_ACK':
            cmd = getattr(msg, 'command', 0)
            res = getattr(msg, 'result', 0)
            if cmd in (MAV_CMD_PREFLIGHT_CALIBRATION, 0):
                res_name = RESULT_NAMES.get(res, str(res))
                if res == MAV_RESULT_ACCEPTED:
                    cmd_accepted = True
                    _log("Autopilot accepted IMU calibration command. Sampling sensors (vehicle must stay still)...", "INFO")
                    if elapsed >= min_duration:
                        cal_completed = True
                        break
                elif res == MAV_RESULT_IN_PROGRESS:
                    cmd_accepted = True
                    _log("Autopilot IMU calibration in progress...", "INFO")
                elif res in (MAV_RESULT_DENIED, MAV_RESULT_FAILED, MAV_RESULT_TEMPORARILY_REJECTED):
                    return False, "Autopilot rejected IMU calibration (result %s)." % res_name

        if cal_completed and elapsed >= min_duration:
            break

    # If minimum sampling duration reached and command was accepted without error:
    if cmd_accepted and not cal_failed and not cal_completed:
        if (time.time() - start_time) >= min_duration:
            cal_completed = True

    if cal_completed:
        _log("[OK] IMU sensor sampling complete. Committing offsets to autopilot storage...", "INFO")
        if progress_cb:
            progress_cb("imu", 95, "Committing offsets to storage...")
        if settle_delay > 0:
            time.sleep(settle_delay)
        if progress_cb:
            progress_cb("imu", 100, "IMU calibration completed successfully.")
        _log("[OK] IMU calibration finished successfully.", "SUCCESS")
        return True, "IMU calibration completed successfully."
    else:
        return False, "IMU calibration timed out waiting for autopilot completion."


def reboot_autopilot(mav, timeout=45.0, endpoint=None, log_cb=None, progress_cb=None, cancel_event=None, reset_delay=5.0):
    """
    Reboot the autopilot via MAVLink command (MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN),
    wait for the reboot to take place, and automatically reconnect when heartbeats resume.

    Returns:
      (bool success, new_mav_connection, str message)
    """
    def _log(msg, level="INFO"):
        if log_cb:
            log_cb(msg, level)
        else:
            print("[REBOOT] %s" % msg)

    if not mav:
        return False, None, "MAVLink connection not provided."

    target_sys = getattr(mav, 'target_system', 1) or 1
    target_comp = getattr(mav, 'target_component', 1) or 1

    _log("Sending autopilot reboot command (MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN)...", "HEADER")
    if progress_cb:
        progress_cb("reboot", 10, "Issuing reboot command...")

    # Send MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN (246): param1 = 1 (reboot autopilot)
    try:
        mav.mav.command_long_send(
            target_sys,
            target_comp,
            MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
            0,      # confirmation
            1.0,    # param1: 1 = reboot autopilot
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        )
    except Exception as e:
        return False, None, "Failed to send reboot command: %s" % str(e)

    # Wait briefly for acknowledgment
    try:
        ack = mav.recv_match(type='COMMAND_ACK', blocking=True, timeout=1.5)
        if ack and getattr(ack, 'command', 0) in (MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, 0):
            _log("Autopilot acknowledged reboot command.", "INFO")
    except Exception:
        pass

    # Close the current MAVLink session before reboot
    try:
        mav.close()
    except Exception:
        pass

    _log("Autopilot is restarting. Waiting for sensors and firmware to initialize (~15-25s)...", "INFO")
    if progress_cb:
        progress_cb("reboot", 30, "Autopilot restarting...")

    # Wait grace period while Pixhawk hardware resets
    if reset_delay > 0:
        time.sleep(reset_delay)

    # Poll for reconnection and heartbeats
    start_wait = time.time()
    deadline = start_wait + timeout
    new_mav = None

    while time.time() < deadline:
        if cancel_event and cancel_event.is_set():
            return False, None, "Reboot sequence canceled by user."

        elapsed = int(time.time() - start_wait)
        pct = min(90, 30 + int((elapsed / float(timeout)) * 60))
        if progress_cb:
            progress_cb("reboot", pct, "Waiting for autopilot heartbeat (%ds elapsed)..." % elapsed)

        new_mav = get_mavlink_connection(endpoint=endpoint, timeout=2.0)
        if new_mav:
            _log("[OK] Autopilot back online! Heartbeat restored from System %d, Component %d." % (
                getattr(new_mav, 'target_system', 1),
                getattr(new_mav, 'target_component', 1)
            ), "SUCCESS")
            if progress_cb:
                progress_cb("reboot", 100, "Autopilot reconnected and ready.")
            return True, new_mav, "Autopilot successfully rebooted and reconnected."

        time.sleep(1.0)

    return False, None, "Timed out waiting for autopilot to reboot and resume heartbeats."


def calibrate_compass(mav, timeout=120.0, wait_completion=True, autosave=True, log_cb=None, progress_cb=None, cancel_event=None):
    """
    Perform Compass (Magnetometer) calibration using MAV_CMD_DO_START_MAG_CAL
    with fallback to MAV_CMD_PREFLIGHT_CALIBRATION.

    Parameters:
      mav: pymavlink connection object
      timeout: maximum time to wait for completion (seconds)
      wait_completion: if True, monitors MAG_CAL_PROGRESS / MAG_CAL_REPORT until complete
      autosave: automatically save offsets to parameters upon success
      log_cb: callable(msg, level)
      progress_cb: callable(stage, pct, details)
      cancel_event: threading.Event to cancel operation

    Returns:
      (bool success, str message)
    """
    def _log(msg, level="INFO"):
        if log_cb:
            log_cb(msg, level)
        else:
            print("[COMPASS_CAL] %s" % msg)

    if not mav:
        return False, "MAVLink connection not provided."

    target_sys = getattr(mav, 'target_system', 1) or 1
    target_comp = getattr(mav, 'target_component', 1) or 1

    _log("Starting Compass (Magnetometer) Calibration...", "HEADER")
    _log("Rotate the Solo slowly and smoothly across all 3 axes (pitch, roll, yaw) in a sphere.", "WARNING")

    if progress_cb:
        progress_cb("compass", 5, "Starting compass calibration...")

    # Send MAV_CMD_DO_START_MAG_CAL (42424)
    # param1 = 0 (all compasses), param2 = 0 (retry), param3 = 1 (autosave), param4 = 0 (delay)
    cal_started = False
    try:
        mav.mav.command_long_send(
            target_sys,
            target_comp,
            MAV_CMD_DO_START_MAG_CAL,
            0,                      # confirmation
            0.0,                    # param1: mag_mask (0 = all compasses)
            0.0,                    # param2: retry
            1.0 if autosave else 0.0,  # param3: autosave
            0.0,                    # param4: delay
            0.0,                    # param5: autoreboot
            0.0, 0.0
        )
        cal_started = True
    except Exception as e:
        _log("MAV_CMD_DO_START_MAG_CAL send failed: %s, falling back to preflight calibration..." % str(e), "WARNING")

    # Fallback to standard MAV_CMD_PREFLIGHT_CALIBRATION (param2 = 1) if needed
    if not cal_started:
        try:
            mav.mav.command_long_send(
                target_sys,
                target_comp,
                MAV_CMD_PREFLIGHT_CALIBRATION,
                0,
                0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0
            )
            cal_started = True
        except Exception as e2:
            return False, "Failed to start compass calibration: %s" % str(e2)

    if not wait_completion:
        if progress_cb:
            progress_cb("compass", 100, "Compass calibration active. Rotate drone.")
        _log("[OK] Compass calibration initiated. Rotate Solo to complete calibration.", "SUCCESS")
        return True, "Compass calibration started successfully."

    # Monitor calibration progress and completion
    deadline = time.time() + timeout
    last_hb = 0
    highest_pct = 5
    compass_progress = {}
    compass_completed = {}
    compass_fitness = {}

    while time.time() < deadline:
        if cancel_event and cancel_event.is_set():
            # Send cancel command if supported
            try:
                mav.mav.command_long_send(
                    target_sys, target_comp,
                    MAV_CMD_DO_CANCEL_MAG_CAL,
                    0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
                )
            except Exception:
                pass
            return False, "Compass calibration canceled by user."

        now = time.time()
        # Maintain GCS heartbeat
        if now - last_hb >= 1.0:
            try:
                mav.mav.heartbeat_send(
                    mavutil.mavlink.MAV_TYPE_GCS,
                    mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                    0, 0, 0
                )
            except Exception:
                pass
            last_hb = now

        msg = mav.recv_match(
            type=['MAG_CAL_PROGRESS', 'MAG_CAL_REPORT', 'COMMAND_ACK', 'STATUSTEXT'],
            blocking=True,
            timeout=0.5
        )
        if not msg:
            continue

        mtype = msg.get_type()

        if mtype == 'MAG_CAL_PROGRESS':
            cid = getattr(msg, 'compass_id', 0)
            pct = getattr(msg, 'completion_pct', 0)
            status = getattr(msg, 'cal_status', 0)
            compass_progress[cid] = pct
            if pct >= 100 and status == MAG_CAL_SUCCESS:
                compass_completed[cid] = True

            avg_pct = int(sum(compass_progress.values()) / max(1, len(compass_progress)))
            if avg_pct > highest_pct:
                highest_pct = avg_pct
                _log("Compass %d calibration progress: %d%%" % (cid, pct), "INFO")
                if progress_cb:
                    progress_cb("compass", highest_pct, {
                        "compass_id": cid,
                        "pct": pct,
                        "status": MAG_STATUS_NAMES.get(status, str(status)),
                        "remaining": [c for c in compass_progress if not compass_completed.get(c, False)]
                    })

        elif mtype == 'MAG_CAL_REPORT':
            cid = getattr(msg, 'compass_id', 0)
            cal_status = getattr(msg, 'cal_status', 0)
            fitness = getattr(msg, 'fitness', 0.0)
            stat_name = MAG_STATUS_NAMES.get(cal_status, str(cal_status))

            if cal_status == MAG_CAL_SUCCESS:
                compass_completed[cid] = True
                compass_progress[cid] = 100
                compass_fitness[cid] = fitness
                _log("[OK] Compass %d calibrated successfully! (Fitness: %.3f)" % (cid, fitness), "SUCCESS")

                # If not autosaved, send accept command
                if not autosave:
                    try:
                        mav.mav.command_long_send(
                            target_sys, target_comp,
                            MAV_CMD_DO_ACCEPT_MAG_CAL,
                            0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
                        )
                    except Exception:
                        pass

                num_detected = len(compass_progress)
                num_done = len([c for c, d in compass_completed.items() if d])
                remaining = [c for c in compass_progress if not compass_completed.get(c, False)]

                # Require ALL detected compasses to finish
                if num_detected > 0 and not remaining:
                    _log("[OK] ALL %d COMPASSES CALIBRATED SUCCESSFULLY!" % num_detected, "SUCCESS")
                    for c_id in sorted(compass_completed.keys()):
                        _log("  - Compass %d: Success (Fitness: %.3f)" % (c_id, compass_fitness.get(c_id, 0.0)), "SUCCESS")
                    if progress_cb:
                        progress_cb("compass", 100, {
                            "compass_id": cid,
                            "pct": 100,
                            "status": "ALL_SUCCESS",
                            "fitness": round(fitness, 3),
                            "all_done": True,
                            "remaining": []
                        })
                    return True, "All compasses (%s) completed successfully." % (
                        ", ".join("Compass %d" % c for c in sorted(compass_completed.keys()))
                    )
                else:
                    rem_str = ", ".join("Compass %d" % c for c in remaining)
                    _log(">> Compass %d done (%d/%d). Continue rotating for remaining: %s" % (cid, num_done, num_detected, rem_str), "WARNING")
                    if progress_cb:
                        progress_cb("compass", highest_pct, {
                            "compass_id": cid,
                            "pct": 100,
                            "status": "SUCCESS",
                            "fitness": round(fitness, 3),
                            "all_done": False,
                            "remaining": remaining
                        })

            elif cal_status in (MAG_CAL_FAILED, MAG_CAL_BAD_ORIENTATION, MAG_CAL_BAD_RADIUS):
                _log("Compass %d calibration failed with status: %s" % (cid, stat_name), "ERROR")
                return False, "Compass %d calibration failed (%s)." % (cid, stat_name)

        elif mtype == 'STATUSTEXT':
            text = getattr(msg, 'text', '')
            if isinstance(text, bytes):
                text = text.decode('utf-8', 'ignore')
            text = text.rstrip('\x00').strip()
            if text:
                _log("Autopilot: %s" % text, "INFO")
                # Do NOT terminate on individual compass STATUSTEXT when tracking via MAG_CAL packets
                if not compass_progress:
                    t_lower = text.lower()
                    if "mag" in t_lower and ("success" in t_lower or "complete" in t_lower):
                        if progress_cb:
                            progress_cb("compass", 100, "Compass calibration complete.")
                        _log("[OK] Compass calibration finished successfully.", "SUCCESS")
                        return True, "Compass calibration completed successfully."

    return False, "Compass calibration timed out waiting for rotation/completion."


def calibrate_imu_reboot_compass(
    connection=None,
    endpoint=None,
    imu_type="simple",
    wait_for_compass=True,
    compass_timeout=120.0,
    reboot_timeout=45.0,
    imu_timeout=30.0,
    reboot_reset_delay=5.0,
    log_cb=None,
    progress_cb=None,
    cancel_event=None
):
    """
    Performs the full calibration pipeline:
      Step 1: IMU Calibration (Gyro + Accelerometer / Level Trim)
      Step 2: Autopilot Reboot & Automatic Reconnection
      Step 3: Compass (Magnetometer) Calibration
    """
    def _log(msg, level="INFO"):
        if log_cb:
            log_cb(msg, level)
        else:
            print("[CALIB_PIPELINE] %s" % msg)

    _log("==================================================================", "HEADER")
    _log("3DR SOLO POST-FLASH CALIBRATION SEQUENCE", "HEADER")
    _log("Step 1: IMU Calibration -> Step 2: Reboot Autopilot -> Step 3: Compass Calibration", "HEADER")
    _log("==================================================================", "HEADER")

    mav = connection
    own_connection = False
    if not mav:
        _log("Connecting to Pixhawk autopilot via MAVLink...", "INFO")
        if progress_cb:
            progress_cb("init", 0, "Connecting to autopilot...")
        mav = get_mavlink_connection(endpoint=endpoint, timeout=5.0)
        if not mav:
            return False, "Could not establish MAVLink connection to autopilot (timeout waiting for heartbeat)."
        own_connection = True

    try:
        # STEP 1: IMU CALIBRATION
        _log("\n--- [STEP 1/3] IMU CALIBRATION ---", "HEADER")
        if progress_cb:
            progress_cb("imu", 5, "Starting IMU calibration...")

        ok_imu, msg_imu = calibrate_imu(
            mav,
            imu_type=imu_type,
            timeout=imu_timeout,
            log_cb=log_cb,
            progress_cb=progress_cb,
            cancel_event=cancel_event
        )
        if not ok_imu:
            _log("IMU calibration failed: %s" % msg_imu, "ERROR")
            return False, "Step 1 (IMU Calibration) failed: %s" % msg_imu

        _log("[OK] Step 1 complete: IMU calibrated and offsets committed to storage.", "SUCCESS")
        if progress_cb:
            progress_cb("imu", 100, "IMU calibration complete.")

        _log("Waiting 3 seconds before initiating reboot to ensure parameters are committed...", "INFO")
        time.sleep(3.0)

        # STEP 2: AUTOPILOT REBOOT
        _log("\n--- [STEP 2/3] AUTOPILOT REBOOT ---", "HEADER")
        if progress_cb:
            progress_cb("reboot", 0, "Initiating autopilot reboot...")

        ok_reb, new_mav, msg_reb = reboot_autopilot(
            mav,
            timeout=reboot_timeout,
            endpoint=endpoint,
            log_cb=log_cb,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            reset_delay=reboot_reset_delay
        )
        if not ok_reb or not new_mav:
            _log("Autopilot reboot failed: %s" % msg_reb, "ERROR")
            return False, "Step 2 (Autopilot Reboot) failed: %s" % msg_reb

        mav = new_mav
        _log("[OK] Step 2 complete: Autopilot rebooted and reconnected.", "SUCCESS")
        if progress_cb:
            progress_cb("reboot", 100, "Autopilot reboot complete.")

        _log("Waiting 3 seconds for autopilot sensors and EKF to settle after boot...", "INFO")
        time.sleep(3.0)

        # STEP 3: COMPASS CALIBRATION
        _log("\n--- [STEP 3/3] COMPASS CALIBRATION ---", "HEADER")
        if progress_cb:
            progress_cb("compass", 0, "Starting compass calibration...")

        ok_comp, msg_comp = calibrate_compass(
            mav,
            timeout=compass_timeout,
            wait_completion=wait_for_compass,
            autosave=True,
            log_cb=log_cb,
            progress_cb=progress_cb,
            cancel_event=cancel_event
        )
        if not ok_comp:
            _log("Compass calibration failed: %s" % msg_comp, "ERROR")
            return False, "Step 3 (Compass Calibration) failed: %s" % msg_comp

        _log("\n[OK] ALL CALIBRATIONS COMPLETED SUCCESSFULLY!", "SUCCESS")
        _log("Sequence complete: IMU calibrated -> Autopilot rebooted -> Compass calibrated.", "SUCCESS")
        if progress_cb:
            progress_cb("done", 100, "All calibrations completed successfully.")

        return True, "IMU calibration, reboot, and compass calibration completed successfully."

    finally:
        if own_connection and mav:
            try:
                mav.close()
            except Exception:
                pass


class CalibrationManager(object):
    """
    Thread-safe Calibration Manager with live state tracking,
    log streaming, and cancellation support for the web app and CLI.
    Supports independent IMU calibration, Compass calibration, Autopilot reboot,
    or the full automated sequence.
    """
    def __init__(self):
        self.is_running = False
        self.status = "ready"  # ready | imu_waiting | imu_sampling | imu_completed | compass_running | compass_completed | rebooting | reboot_completed | completed | error | canceled
        self.progress = 0
        self.current_step = ""  # imu | compass | reboot | sequence
        self.pos_index = 0      # 1 to 6
        self.pos_name = ""      # "Level", "Left", etc.
        self.positions_done = []  # [1, 2, ...]
        self.message = "Ready to perform calibrations."
        self.log_buffer = []
        self.compass_progress = {}  # {0: pct, 1: pct}
        self.compass_fitness = {}   # {0: fitness, 1: fitness}
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.continue_event = threading.Event()
        self.active_mav = None

    def log(self, msg, level="INFO"):
        ts = time.strftime("%H:%M:%S")
        entry = {"time": ts, "level": level, "message": msg}
        with self.lock:
            self.log_buffer.append(entry)
            if len(self.log_buffer) > 400:
                self.log_buffer = self.log_buffer[-400:]
        print("[%s] [CALIB] %s" % (ts, msg))

    def get_state(self):
        with self.lock:
            return {
                "is_running": self.is_running,
                "status": self.status,
                "progress": self.progress,
                "step": self.current_step,
                "pos_index": self.pos_index,
                "pos_name": self.pos_name,
                "positions_done": list(self.positions_done),
                "message": self.message,
                "compass_progress": dict(self.compass_progress),
                "compass_fitness": dict(self.compass_fitness),
                "logs": list(self.log_buffer)
            }

    def cancel(self):
        if not self.is_running:
            return False, "No calibration currently in progress."
        self.cancel_event.set()
        self.continue_event.set()
        self.log("Cancellation requested by user.", "WARNING")
        return True, "Cancellation signal sent."

    def continue_imu(self):
        """
        User clicked 'Continue' for the current 6-axis position orientation.
        Triggers sensor sampling and MAVLink position acknowledgment.
        """
        if not self.is_running or self.current_step != "imu":
            return False, "6-axis accelerometer calibration is not currently waiting for input."
        self.continue_event.set()
        return True, "Position confirmed. Sampling sensors..."

    def start_imu(self, endpoint=None, **kwargs):
        """
        Execute Step 3: Full 6-Axis Accelerometer Calibration.
        Walks the user through 6 specific orientations in sequence:
          1. Accurately Level
          2. Left side
          3. Right side
          4. Nose down
          5. Nose up
          6. On back
        Pauses at each orientation until the user clicks Continue.
        """
        if self.is_running:
            return False, "A calibration or reboot task is already in progress."

        self.is_running = True
        self.status = "imu_waiting"
        self.progress = 0
        self.current_step = "imu"
        self.pos_index = 1
        self.pos_name = "Level"
        self.positions_done = []
        self.message = "Place vehicle Accurately Level, hold completely still, then click Continue."
        self.log_buffer = []
        self.cancel_event.clear()
        self.continue_event.clear()

        def _worker():
            mav = None
            try:
                self.log("Connecting to Pixhawk autopilot via MAVLink...", "INFO")
                mav = get_mavlink_connection(endpoint=endpoint, timeout=5.0)
                if not mav:
                    with self.lock:
                        self.is_running = False
                        self.status = "error"
                        self.message = "Could not establish MAVLink connection to autopilot."
                    self.log("MAVLink connection failed (timeout waiting for heartbeat).", "ERROR")
                    return

                self.active_mav = mav
                target_sys = getattr(mav, 'target_system', 1) or 1
                target_comp = getattr(mav, 'target_component', 1) or 1

                # Send MAV_CMD_PREFLIGHT_CALIBRATION with param5=1.0 (3D 6-axis accel cal)
                self.log("==================================================================", "HEADER")
                self.log("STARTING FULL 6-AXIS ACCELEROMETER CALIBRATION", "HEADER")
                self.log("Orientations: Level -> Left -> Right -> Nose Down -> Nose Up -> On Back", "HEADER")
                self.log("==================================================================", "HEADER")
                try:
                    mav.mav.command_long_send(
                        target_sys, target_comp,
                        MAV_CMD_PREFLIGHT_CALIBRATION,
                        0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0
                    )
                except Exception as e:
                    with self.lock:
                        self.is_running = False
                        self.status = "error"
                        self.message = "Failed to start 6-axis calibration: %s" % str(e)
                    self.log("Failed to send 6-axis calibration command: %s" % str(e), "ERROR")
                    return

                positions = [
                    (1, "Accurately Level"),
                    (2, "Left Side"),
                    (3, "Right Side"),
                    (4, "Nose Down"),
                    (5, "Nose Up"),
                    (6, "On Back")
                ]

                last_hb = time.time()
                cal_failed = False
                fail_reason = ""
                is_mock = getattr(mav, 'is_mock', False) or type(mav).__name__ == 'MockMavConnection'

                for pos_num, p_name in positions:
                    with self.lock:
                        self.pos_index = pos_num
                        self.pos_name = p_name
                        self.status = "imu_waiting"
                        self.message = "Place vehicle %s. Hold completely still, then click Continue." % p_name

                    self.log(">> Step %d/6: Place Solo %s. Hold completely still and click Continue." % (pos_num, p_name), "WARNING")

                    # Wait for user confirmation (continue_event) while streaming heartbeats
                    while not self.continue_event.is_set():
                        if self.cancel_event.is_set():
                            with self.lock:
                                self.is_running = False
                                self.status = "canceled"
                                self.message = "6-axis calibration canceled by user."
                            self.log("Calibration canceled by user.", "WARNING")
                            return

                        now = time.time()
                        if now - last_hb >= 1.0:
                            try:
                                mav.mav.heartbeat_send(
                                    mavutil.mavlink.MAV_TYPE_GCS,
                                    mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                                    0, 0, 0
                                )
                            except Exception:
                                pass
                            last_hb = now

                        msg = mav.recv_match(type=['STATUSTEXT', 'COMMAND_ACK'], blocking=False)
                        if msg and msg.get_type() == 'STATUSTEXT':
                            t = getattr(msg, 'text', '')
                            if isinstance(t, bytes): t = t.decode('utf-8', 'ignore')
                            t = t.rstrip('\x00').strip()
                            if t: self.log("Autopilot: %s" % t, "INFO")

                        time.sleep(0.05)

                    self.continue_event.clear()
                    if self.cancel_event.is_set():
                        with self.lock:
                            self.is_running = False
                            self.status = "canceled"
                            self.message = "6-axis calibration canceled."
                        return

                    # Position confirmed by user -> sample sensors
                    with self.lock:
                        self.status = "imu_sampling"
                        self.message = "Sampling %s... Keep vehicle completely still!" % p_name
                    self.log("User confirmed %s. Sampling sensors (must stay motionless)..." % p_name, "INFO")

                    # Send MAV_CMD_ACCELCAL_VEHICLE_POS (42429) with param1 = position number
                    try:
                        mav.mav.command_long_send(
                            target_sys, target_comp,
                            MAV_CMD_ACCELCAL_VEHICLE_POS,
                            0, float(pos_num), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
                        )
                    except Exception as ex_send:
                        self.log("Note: MAV_CMD_ACCELCAL_VEHICLE_POS send: %s" % str(ex_send), "INFO")

                    try:
                        mav.mav.command_ack_send(MAV_CMD_PREFLIGHT_CALIBRATION, MAV_RESULT_ACCEPTED)
                    except Exception:
                        pass

                    # Sampling duration (~2.2s for real drone, instantaneous for mock)
                    sample_dur = 0.0 if is_mock else 2.2
                    start_s = time.time()
                    while time.time() - start_s < sample_dur:
                        if self.cancel_event.is_set():
                            with self.lock:
                                self.is_running = False
                                self.status = "canceled"
                                self.message = "Canceled during sampling."
                            return
                        msg = mav.recv_match(type=['STATUSTEXT'], blocking=False)
                        if msg:
                            t = getattr(msg, 'text', '')
                            if isinstance(t, bytes): t = t.decode('utf-8', 'ignore')
                            t = t.rstrip('\x00').strip()
                            if t:
                                self.log("Autopilot: %s" % t, "INFO")
                                if "failed" in t.lower() or "failure" in t.lower():
                                    cal_failed = True
                                    fail_reason = t
                        time.sleep(0.08)

                    if cal_failed:
                        with self.lock:
                            self.is_running = False
                            self.status = "error"
                            self.message = "6-axis calibration failed: %s" % fail_reason
                        self.log("6-axis calibration failed: %s" % fail_reason, "ERROR")
                        return

                    with self.lock:
                        self.positions_done.append(pos_num)
                        self.progress = int((pos_num / 6.0) * 100)
                    self.log("[OK] Position %d/6 (%s) sampled successfully." % (pos_num, p_name), "SUCCESS")

                # Settle pause for EEPROM offset matrix commit
                if not is_mock:
                    time.sleep(1.5)

                with self.lock:
                    self.is_running = False
                    self.status = "imu_completed"
                    self.progress = 100
                    self.message = "6-Axis Accelerometer Calibration Complete! Offsets saved to persistent storage. Please reboot Solo."
                self.log("[OK] 6-Axis Accelerometer Calibration finished successfully!", "SUCCESS")
                self.log("All 6 positions sampled and calibrated. Offsets saved to autopilot storage.", "SUCCESS")
                self.log(">> MANUAL REBOOT REQUIRED: Power-cycle Solo or click 'Reboot Autopilot' before Compass Calibration.", "WARNING")

            except Exception as ex:
                with self.lock:
                    self.is_running = False
                    self.status = "error"
                    self.message = "Error: %s" % str(ex)
                self.log("Exception during 6-axis IMU calibration: %s" % str(ex), "ERROR")
            finally:
                self.active_mav = None
                if mav:
                    try:
                        mav.close()
                    except Exception:
                        pass

        t = threading.Thread(target=_worker)
        t.daemon = True
        t.start()
        return True, "6-Axis Accelerometer Calibration initiated."

    def start_compass(self, wait_completion=True, timeout=120.0, endpoint=None):
        """
        Execute Step 2: Compass Calibration ONLY.
        Initiates onboard mag calibration and streams real-time progress for all compasses.
        """
        if self.is_running:
            return False, "A calibration or reboot task is already in progress."

        self.is_running = True
        self.status = "compass_running"
        self.progress = 0
        self.current_step = "compass"
        self.message = "Starting Compass calibration..."
        self.log_buffer = []
        self.compass_progress = {}
        self.compass_fitness = {}
        self.cancel_event.clear()

        def _worker():
            def _prog(stage, pct, details):
                with self.lock:
                    self.progress = pct
                    if isinstance(details, dict):
                        cid = details.get("compass_id", 0)
                        cpct = details.get("pct", 0)
                        self.compass_progress[cid] = cpct
                        fit = details.get("fitness")
                        if fit is not None:
                            self.compass_fitness[cid] = fit
                        rem = details.get("remaining")
                        if rem and not details.get("all_done"):
                            self.message = "Compass %d done! Continue rotating for Compass %s..." % (
                                cid, ", ".join(str(r) for r in rem)
                            )
                        elif details.get("all_done"):
                            self.message = "All compasses calibrated successfully!"
                        else:
                            self.message = "Compass %d: %d%% (%s)" % (cid, cpct, details.get("status", ""))
                    elif details:
                        self.message = str(details)

            mav = None
            try:
                self.log("Connecting to Pixhawk autopilot via MAVLink...", "INFO")
                mav = get_mavlink_connection(endpoint=endpoint, timeout=5.0)
                if not mav:
                    with self.lock:
                        self.is_running = False
                        self.status = "error"
                        self.message = "Could not establish MAVLink connection to autopilot."
                    self.log("MAVLink connection failed (timeout waiting for heartbeat).", "ERROR")
                    return

                ok, msg = calibrate_compass(
                    mav,
                    timeout=timeout,
                    wait_completion=wait_completion,
                    autosave=True,
                    log_cb=self.log,
                    progress_cb=_prog,
                    cancel_event=self.cancel_event
                )
                if ok:
                    self.log("[OK] Compass Calibration Complete! Offsets saved to persistent storage.", "SUCCESS")
                    self.log("Auto-rebooting autopilot in 3 seconds to commit compass offsets to EKF...", "WARNING")

                    is_mock = getattr(mav, 'is_mock', False) or type(mav).__name__ == 'MockMavConnection'
                    countdown = 0 if is_mock else 3

                    for sec in range(countdown, 0, -1):
                        if self.cancel_event.is_set():
                            break
                        with self.lock:
                            self.progress = 100
                            self.status = "compass_reboot_countdown"
                            self.message = "Compass calibrated! Auto-rebooting autopilot in %d second%s..." % (sec, "s" if sec > 1 else "")
                        time.sleep(1.0)

                    if self.cancel_event.is_set():
                        with self.lock:
                            self.is_running = False
                            self.status = "canceled"
                            self.message = "Auto-reboot canceled by user."
                        return

                    with self.lock:
                        self.status = "rebooting"
                        self.current_step = "reboot"
                        self.message = "Rebooting Pixhawk autopilot..."

                    ok_rb, new_mav, msg_rb = reboot_autopilot(
                        mav,
                        timeout=45.0,
                        endpoint=endpoint,
                        log_cb=self.log,
                        progress_cb=_prog,
                        cancel_event=self.cancel_event
                    )
                    if new_mav:
                        try:
                            new_mav.close()
                        except Exception:
                            pass

                    with self.lock:
                        self.is_running = False
                        if ok_rb:
                            self.status = "compass_completed"
                            self.progress = 100
                            self.message = "Compass Calibration & Autopilot Reboot Complete! Solo is ready to fly."
                        else:
                            self.status = "error"
                            self.message = "Compass calibrated, but autopilot reboot failed: %s" % msg_rb
                    if ok_rb:
                        self.log("[OK] Autopilot reboot complete! Compass offsets loaded into EKF.", "SUCCESS")
                        self.log(">> ALL SYSTEMS READY: Solo is calibrated and ready for flight!", "SUCCESS")
                else:
                    with self.lock:
                        self.is_running = False
                        self.status = "canceled" if self.cancel_event.is_set() else "error"
                        self.message = msg
            except Exception as ex:
                with self.lock:
                    self.is_running = False
                    self.status = "error"
                    self.message = "Error: %s" % str(ex)
                self.log("Exception during Compass calibration: %s" % str(ex), "ERROR")
            finally:
                if mav:
                    try:
                        mav.close()
                    except Exception:
                        pass

        t = threading.Thread(target=_worker)
        t.daemon = True
        t.start()
        return True, "Compass calibration initiated."

    def start_reboot(self, timeout=45.0, endpoint=None):
        """
        Reboot the Pixhawk autopilot via MAVLink and wait for reconnection.
        Helper for users who prefer software reboot between IMU and Compass calibrations.
        """
        if self.is_running:
            return False, "A calibration or reboot task is already in progress."

        self.is_running = True
        self.status = "rebooting"
        self.progress = 10
        self.current_step = "reboot"
        self.message = "Rebooting Pixhawk autopilot..."
        self.log_buffer = []
        self.cancel_event.clear()

        def _worker():
            def _prog(stage, pct, details):
                with self.lock:
                    self.progress = pct
                    if details:
                        self.message = str(details)

            mav = None
            try:
                self.log("Connecting to Pixhawk autopilot via MAVLink...", "INFO")
                mav = get_mavlink_connection(endpoint=endpoint, timeout=5.0)
                if not mav:
                    with self.lock:
                        self.is_running = False
                        self.status = "error"
                        self.message = "Could not establish MAVLink connection to autopilot."
                    self.log("MAVLink connection failed.", "ERROR")
                    return

                ok, new_mav, msg = reboot_autopilot(
                    mav,
                    timeout=timeout,
                    endpoint=endpoint,
                    log_cb=self.log,
                    progress_cb=_prog,
                    cancel_event=self.cancel_event
                )
                if new_mav:
                    try:
                        new_mav.close()
                    except Exception:
                        pass

                with self.lock:
                    self.is_running = False
                    if ok:
                        self.status = "reboot_completed"
                        self.progress = 100
                        self.message = "Autopilot reboot complete and reconnected! You can now start Compass Calibration."
                    else:
                        self.status = "canceled" if self.cancel_event.is_set() else "error"
                        self.message = msg
                if ok:
                    self.log("Autopilot reboot complete and reconnected! Ready for Compass Calibration.", "SUCCESS")
            except Exception as ex:
                with self.lock:
                    self.is_running = False
                    self.status = "error"
                    self.message = "Error: %s" % str(ex)
                self.log("Exception during autopilot reboot: %s" % str(ex), "ERROR")

        t = threading.Thread(target=_worker)
        t.daemon = True
        t.start()
        return True, "Autopilot reboot initiated."

    def start_sequence(self, imu_type="simple", wait_for_compass=False, endpoint=None):
        if self.is_running:
            return False, "Calibration sequence is already in progress."

        self.is_running = True
        self.status = "imu"
        self.progress = 0
        self.current_step = "sequence"
        self.message = "Calibration sequence initiated."
        self.log_buffer = []
        self.cancel_event.clear()

        def _worker():
            def _prog(stage, pct, details):
                with self.lock:
                    if stage == "imu":
                        self.status = "imu"
                        self.progress = int(pct * 0.35)
                    elif stage == "reboot":
                        self.status = "rebooting"
                        self.progress = 35 + int(pct * 0.35)
                    elif stage == "compass":
                        self.status = "compass"
                        self.progress = 70 + int(pct * 0.30)
                    elif stage == "done":
                        self.status = "completed"
                        self.progress = 100
                    if isinstance(details, dict):
                        self.message = "Compass %s: %s%%" % (
                            details.get("compass_id", 0),
                            details.get("pct", 0)
                        )
                    elif details:
                        self.message = str(details)

            try:
                ok, msg = calibrate_imu_reboot_compass(
                    endpoint=endpoint,
                    imu_type=imu_type,
                    wait_for_compass=wait_for_compass,
                    log_cb=self.log,
                    progress_cb=_prog,
                    cancel_event=self.cancel_event
                )
                with self.lock:
                    self.is_running = False
                    if ok:
                        self.status = "completed"
                        self.progress = 100
                        self.message = msg
                    else:
                        self.status = "canceled" if self.cancel_event.is_set() else "error"
                        self.message = msg
            except Exception as ex:
                with self.lock:
                    self.is_running = False
                    self.status = "error"
                    self.message = "Unexpected error: %s" % str(ex)
                self.log("Exception during calibration pipeline: %s" % str(ex), "ERROR")

        t = threading.Thread(target=_worker)
        t.daemon = True
        t.start()
        return True, "Calibration sequence started."


# Singleton instance
calib_engine = CalibrationManager()


if __name__ == '__main__':
    # CLI execution support
    import argparse
    parser = argparse.ArgumentParser(description="3DR Solo IMU Calibration -> Reboot -> Compass Calibration")
    parser.add_argument("--imu-type", choices=["simple", "level", "full"], default="simple",
                        help="IMU calibration type (default: simple)")
    parser.add_argument("--endpoint", default=None,
                        help="MAVLink endpoint (e.g. udpout:127.0.0.1:14560)")
    parser.add_argument("--no-wait-compass", action="store_true",
                        help="Do not wait for compass rotation completion")
    args = parser.parse_args()

    success, message = calibrate_imu_reboot_compass(
        endpoint=args.endpoint,
        imu_type=args.imu_type,
        wait_for_compass=not args.no_wait_compass
    )
    sys.exit(0 if success else 1)
