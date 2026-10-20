#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Firmware Management Module
Handles Pixhawk Cube autopilot & iMX6 clean install flasher pipeline,
native package extraction/staging on Solo, and the ArduCopter online
custom firmware builder engine.
"""

import os
import compat
import sys
import time
import shutil
import zipfile
import hashlib
import logging
import socket
import subprocess
from webcore import Blueprint, request, jsonify
from modules.auth import login_required

logger = logging.getLogger("solo_web_utility.firmware")

# ---------------------------------------------------------------------------
# Cross-Platform Directory Setup
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

FIRMWARE_DIR = os.path.join(BASE_DIR, "firmware")
FIRMWARE_FILES_DIR = os.path.join(BASE_DIR, "firmware_files")
TMP_UPDATES_DIR = os.path.join(FIRMWARE_FILES_DIR, "tmp_updates")
STAGING_EXTRACT_DIR = os.path.join(FIRMWARE_FILES_DIR, "staging_extract")

SOLO_IP = "10.1.1.10"
SOLO_USER = "root"
SOLO_PASS = "TjSDBkAu"
CONTROLLER_IP = "10.1.1.1"


def ensure_firmware_dirs():
    """Ensure clean local Windows directories exist prior to reading or extracting packages."""
    for d in (FIRMWARE_DIR, FIRMWARE_FILES_DIR, TMP_UPDATES_DIR, STAGING_EXTRACT_DIR):
        if not os.path.exists(d):
            compat.makedirs(d)


ensure_firmware_dirs()

import flash_mgr
import arducopter_builder

# Export singleton engines and Wi-Fi automation functions
flash_engine = flash_mgr.flash_engine
update_engine = arducopter_builder.update_engine
get_local_firmware_version = arducopter_builder.get_local_firmware_version
get_latest_apj = flash_mgr.get_latest_apj
from modules.controller_firmware import (
    get_current_wifi_ssid,
    lock_wifi_profile,
    unlock_wifi_profiles,
    relatch_wifi_connection,
    start_wifi_relatch_thread,
    is_network_socket_exception,
)


def get_file_md5(filepath):
    """Calculate MD5 checksum of a file in 64KB chunks."""
    if not os.path.exists(filepath):
        return None
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Direct Copter Execution & Staging Helpers
# ---------------------------------------------------------------------------
def sftp_upload_to_copter(local_path, remote_path, host=SOLO_IP, timeout=600, saved_ssid=None, log_callback=None, progress_callback=None):
    """Upload a file to the copter via flash_mgr._scp_to (local copy on Solo)."""
    rc, msg = flash_mgr._scp_to(local_path, host, remote_path, saved_ssid=saved_ssid, log_callback=log_callback, progress_callback=progress_callback)
    return rc == 0, msg


def ssh_run_on_copter(cmd, host=SOLO_IP, timeout=60, saved_ssid=None, log_callback=None):
    """Execute a command locally on the copter (or via flash_mgr._ssh if remote host)."""
    return flash_mgr._ssh(cmd, host=host, saved_ssid=saved_ssid, log_callback=log_callback)


def reboot_copter(host=SOLO_IP, saved_ssid=None, log_callback=None):
    """
    Trigger a clean reboot on the copter's iMX6 computer.
    Touches /log/updates/RUN_PAIR_ONCE right before reboot so pairing can trigger on boot.
    Uses 'nohup sh -c 'sleep 2 && reboot' >/dev/null 2>&1 &' so the command survives server shutdown.
    """
    try:
        from modules.telemetry import set_copter_boot_in_progress, set_live_copter_status
        set_copter_boot_in_progress(True)
        set_live_copter_status(False)
    except Exception:
        pass

    msg_pair = "[Wi-Fi] Staging one-time post-update pairing trigger..."
    logger.info(msg_pair)
    if log_callback:
        log_callback(msg_pair, "INFO")
    ssh_run_on_copter("mkdir -p /log/updates && touch /log/updates/RUN_PAIR_ONCE", host=host, saved_ssid=saved_ssid, log_callback=log_callback)

    cmd = "nohup sh -c 'sleep 2 && reboot' >/dev/null 2>&1 &"
    msg = "[AUTO-REBOOT] Triggering copter reboot: %s" % cmd
    logger.info(msg)
    if log_callback:
        log_callback(msg, "HEADER")
    rc, out = ssh_run_on_copter(cmd, host=host, saved_ssid=saved_ssid, log_callback=log_callback)
    if log_callback:
        log_callback("[✓] Copter reboot triggered cleanly.", "SUCCESS")
    return rc == 0


def _probe_copter_awake(host=SOLO_IP, timeout=0.8):
    """On-drone check: copter is awake since this process runs on it."""
    return True


def check_and_trigger_post_update_pairing(log_callback=None):
    """
    Check if /log/updates/RUN_PAIR_ONCE exists on copter.
    If present, immediately executes controller software pairing command: 'sololink_config --pair',
    deletes /log/updates/RUN_PAIR_ONCE, and logs to stream.
    """
    def _log(msg, lvl="INFO"):
        logger.info(msg)
        if log_callback:
            log_callback(msg, lvl)

    try:
        if os.path.exists("/log/updates/RUN_PAIR_ONCE"):
            _log("[AUTOMATION] Copter update complete! Automatically broadcasting software pairing invitation now...", "SUCCESS")
            from modules.controller_firmware import ssh_run_command
            ssh_run_command("sololink_config --pair", host=CONTROLLER_IP, timeout=15, log_callback=log_callback)
            try:
                # rm -f /log/updates/RUN_PAIR_ONCE
                os.remove("/log/updates/RUN_PAIR_ONCE")
            except Exception:
                pass
            return True
    except Exception as e:
        logger.warning("Error checking RUN_PAIR_ONCE marker: %s", e)
    return False


def reconnect_wifi(saved_ssid=None, log_callback=None, max_duration=360):
    """Delegate to modules.controller_firmware.reconnect_wifi."""
    from modules.controller_firmware import reconnect_wifi as cf_reconnect
    return cf_reconnect(saved_ssid=saved_ssid, log_callback=log_callback, max_duration=max_duration)


# ---------------------------------------------------------------------------
# Native Directory Preparation & Package Staging (Zero Local Shell Runners)
# ---------------------------------------------------------------------------
def stage_copter_firmware(cube_type="solo", install_mode="full"):
    """
    Safely stage copter firmware packages into the temporary workspace
    (firmware_files/tmp_updates/) using pure Python 'os' and 'shutil' commands.
    Uses pre-staged uncompressed files and selects the latest versioned APJ.
    Never executes 'preinstall.sh' or 'postinstall.sh' locally using 'sh'.
    """
    ensure_firmware_dirs()

    apj_src = flash_mgr.get_latest_apj(cube_type)
    apj_dst = os.path.join(TMP_UPDATES_DIR, "arducopter.apj")

    solo_tar_src = os.path.join(FIRMWARE_DIR, "3dr-solo.tar.gz")
    solo_md5_src = os.path.join(FIRMWARE_DIR, "3dr-solo.tar.gz.md5")
    solo_tar_dst = os.path.join(TMP_UPDATES_DIR, "3dr-solo.tar.gz")
    solo_md5_dst = os.path.join(TMP_UPDATES_DIR, "3dr-solo.tar.gz.md5")

    logger.info("Preparing local staging environment natively in Python...")
    compat.makedirs(TMP_UPDATES_DIR)

    # Stage 3dr-solo.tar.gz directly from FIRMWARE_DIR (the single golden image).
    # Refresh the staged copy whenever it differs from the golden image, so a stale
    # or unbaked tarball can never be left behind here.
    if os.path.exists(solo_tar_src):
        stale = (not os.path.exists(solo_tar_dst)
                 or os.path.getsize(solo_tar_dst) != os.path.getsize(solo_tar_src)
                 or os.path.getmtime(solo_tar_dst) < os.path.getmtime(solo_tar_src))
        if stale:
            logger.info("Copying %s -> %s", solo_tar_src, solo_tar_dst)
            shutil.copyfile(solo_tar_src, solo_tar_dst)
            if os.path.exists(solo_md5_dst):
                os.remove(solo_md5_dst)

    if not os.path.exists(solo_md5_dst):
        if os.path.exists(solo_md5_src):
            shutil.copyfile(solo_md5_src, solo_md5_dst)
        elif os.path.exists(solo_tar_dst):
            computed_md5 = get_file_md5(solo_tar_dst)
            with compat.open_text(solo_md5_dst, "w") as f:
                f.write("%s  3dr-solo.tar.gz\n" % computed_md5)

    # Stage APJ file
    if apj_src and os.path.exists(apj_src):
        logger.info("Copying APJ: %s -> %s", apj_src, apj_dst)
        shutil.copyfile(apj_src, apj_dst)

    # Validate package MD5 natively in Python (replaces postinstall.sh md5sum)
    md5_valid = False
    if os.path.exists(solo_tar_dst):
        computed = get_file_md5(solo_tar_dst)
        if os.path.exists(solo_md5_dst):
            try:
                with compat.open_text(solo_md5_dst, "r") as f:
                    expected = f.read().strip().split()[0]
                md5_valid = (computed.lower() == expected.lower())
                logger.info("Package MD5 check: computed=%s, expected=%s, match=%s", computed, expected, md5_valid)
            except Exception:
                md5_valid = True
        else:
            md5_valid = True

    return {
        "success": os.path.exists(solo_tar_dst) or (install_mode == "cube_only" and os.path.exists(apj_dst)),
        "tmp_updates_dir": TMP_UPDATES_DIR,
        "staged_files": os.listdir(TMP_UPDATES_DIR) if os.path.exists(TMP_UPDATES_DIR) else [],
        "extracted_from_zip": False,
        "md5_valid": md5_valid
    }


# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------
firmware_bp = Blueprint("firmware", __name__)


@firmware_bp.route("/api/flash/status", methods=["GET"])
@login_required
def api_flash_status():
    return jsonify(flash_engine.get_state())


@firmware_bp.route("/api/flash/start", methods=["POST"])
@login_required
def api_flash_start():
    from modules.telemetry import is_vehicle_armed
    try:
        if is_vehicle_armed():
            return jsonify({"error": True, "message": "SAFETY INTERLOCK: Flashing prohibited while vehicle is ARMED!"}), 403
    except Exception:
        pass

    body = request.get_json(silent=True) or {}
    cube_type = body.get("cube_type", "solo")
    install_mode = body.get("install_mode", "full")

    # Detect and lock down active Wi-Fi profile prior to starting flashing
    saved_ssid = get_current_wifi_ssid()
    if saved_ssid:
        logger.info("[Wi-Fi Automation] Pre-flash active Wi-Fi profile stored: %s", saved_ssid)
        lock_wifi_profile(saved_ssid)

    # Pre-stage files natively on Windows using Python os/shutil (no local sh runner)
    try:
        stage_copter_firmware(cube_type=cube_type, install_mode=install_mode)
    except Exception as e:
        logger.error("Pre-staging copter firmware failed: %s", e)
        return jsonify({"error": True, "message": "Failed to prepare local staging workspace: %s" % str(e)}), 500

    ok, msg = flash_engine.start_flash(cube_type, install_mode)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": True, "message": msg}), 400


@firmware_bp.route("/api/flash/reset", methods=["POST"])
@login_required
def api_flash_reset():
    flash_engine.reset_state()
    return jsonify({"success": True, "message": "Flashing state reset."})


@firmware_bp.route("/api/firmware/stage", methods=["POST", "GET"])
@login_required
def api_firmware_stage():
    """Explicit endpoint to verify or perform package staging into tmp_updates."""
    body = request.get_json(silent=True) or {} if request.method == "POST" else {}
    cube_type = body.get("cube_type", "solo")
    install_mode = body.get("install_mode", "full")
    result = stage_copter_firmware(cube_type=cube_type, install_mode=install_mode)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Internet Verification & Firmware Update Handlers
# ---------------------------------------------------------------------------
def check_internet_connection(host="8.8.8.8", port=53, timeout=1.2):
    """
    Quick low-timeout connection test to public DNS provider (8.8.8.8:53).
    Returns True if an external internet route is active, False otherwise.
    Never hangs or stalls the application thread.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def check_for_updates():
    """
    Check for firmware updates.
    Before making an external network request to custom.ardupilot.org, wraps it in
    a quick, low-timeout connection test to public DNS (8.8.8.8).
    If the test fails or times out, immediately yields 'NO_INTERNET_CONNECTION'.
    Does not let the thread hang.
    """
    if not check_internet_connection():
        logger.warning("[Firmware Check] No public internet route available (8.8.8.8 unreachable). Yielding NO_INTERNET_CONNECTION.")
        return {
            "success": False,
            "status": "NO_INTERNET_CONNECTION",
            "error": "NO_INTERNET_CONNECTION",
            "message": "NO_INTERNET_CONNECTION",
            "local_version": get_local_firmware_version(),
            "online_version": None,
            "update_available": False
        }

    try:
        from modules.wifi import ensure_copter_internet_route
        ensure_copter_internet_route()
    except Exception:
        pass

    return update_engine.check_version_quick()


@firmware_bp.route("/api/firmware/check", methods=["GET"])
@firmware_bp.route("/api/firmware/check-updates", methods=["GET"])
def api_firmware_check():
    return jsonify(check_for_updates())


@firmware_bp.route("/api/firmware/status", methods=["GET"])
def api_firmware_status():
    return jsonify(update_engine.get_state())


@firmware_bp.route("/api/firmware/update", methods=["POST"])
@login_required
def api_firmware_update():
    from modules.telemetry import is_vehicle_armed
    try:
        if is_vehicle_armed():
            return jsonify({"error": True, "message": "SAFETY INTERLOCK: Firmware update routine prohibited while vehicle is ARMED!"}), 403
    except Exception:
        pass

    if not check_internet_connection():
        return jsonify({
            "error": True,
            "status": "NO_INTERNET_CONNECTION",
            "message": "NO_INTERNET_CONNECTION"
        }), 400

    try:
        from modules.wifi import ensure_copter_internet_route
        ensure_copter_internet_route()
    except Exception:
        pass

    body = request.get_json(silent=True) or {}
    force = bool(body.get("force", False))
    ok, msg = update_engine.start_update_routine(force=force)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": True, "message": msg}), 400

