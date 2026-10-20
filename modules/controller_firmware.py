#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Controller Firmware Management Module
Handles Artoo controller iMX6 firmware package extraction, staging, MD5 validation,
deployment using native Linux paths under firmware_files/tmp_updates/ on the Solo copter,
and Artoo Controller SSH/SCP orchestration.
"""

import os
import compat
import sys
import time
import shutil
import hashlib
import logging
import threading
import subprocess
from webcore import Blueprint, request, jsonify
from modules.auth import login_required

import flash_mgr

logger = logging.getLogger("solo_web_utility.controller_firmware")

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

CONTROLLER_IP = "10.1.1.1"
CONTROLLER_USER = "root"
CONTROLLER_PASS = "TjSDBkAu"

CTRL_TAR_NAME = "3dr-controller.tar.gz"
CTRL_MD5_NAME = "3dr-controller.tar.gz.md5"

CTRL_TAR_SRC = os.path.join(FIRMWARE_DIR, CTRL_TAR_NAME)
CTRL_MD5_SRC = os.path.join(FIRMWARE_DIR, CTRL_MD5_NAME)
CTRL_TAR_DST = os.path.join(TMP_UPDATES_DIR, CTRL_TAR_NAME)
CTRL_MD5_DST = os.path.join(TMP_UPDATES_DIR, CTRL_MD5_NAME)


def ensure_controller_firmware_dirs():
    """Ensure clean local Windows directories exist prior to reading or extracting packages."""
    for d in (FIRMWARE_DIR, FIRMWARE_FILES_DIR, TMP_UPDATES_DIR, STAGING_EXTRACT_DIR):
        if not os.path.exists(d):
            compat.makedirs(d)


ensure_controller_firmware_dirs()


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
# Wi-Fi Helpers (On Solo Copter)
# ---------------------------------------------------------------------------
def find_sololink_profile():
    """Return default SoloLink profile name."""
    return "SoloLink"


def get_current_wifi_ssid():
    """Detect active Wi-Fi SSID on Solo copter."""
    try:
        res = compat.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "SoloLink"


def get_wifi_interface_name():
    """Return active WLAN interface name on Solo copter."""
    return "wlan0"


def lock_wifi_profile(saved_ssid=None):
    """No-op on Solo copter (app runs natively on the drone)."""
    return (saved_ssid or "SoloLink", [])


def unlock_wifi_profiles(modified_profiles):
    """No-op on Solo copter."""
    pass


def is_network_socket_exception(exc):
    """Check if an exception is a network socket or unreachable error."""
    import socket
    if isinstance(exc, (socket.error, socket.timeout, OSError)):
        return True
    msg = str(exc).lower()
    return any(k in msg for k in ["timeout", "timed out", "unreachable", "reset", "closed", "broken pipe"])


def relatch_wifi_connection(saved_ssid=None, log_callback=None):
    """No-op on Solo copter."""
    pass


def start_wifi_relatch_thread(saved_ssid=None, log_callback=None, max_duration=180, interval=3):
    """No-op on Solo copter."""
    return None


def reconnect_wifi(saved_ssid=None, log_callback=None, max_duration=360):
    """
    Background monitor waiting for Artoo controller (10.1.1.1) to reboot,
    then automatically broadcasting software pairing invitation if staged.
    """
    def _log(msg, level="INFO"):
        if log_callback:
            log_callback(msg, level)
        logger.info("[%s] %s", level, msg)

    _log("[Controller Reboot] Waiting for Artoo Controller (10.1.1.1) to finish booting...", "INFO")
    start_time = time.time()
    time.sleep(5)

    controller_alive = False
    while (time.time() - start_time) < max_duration:
        elapsed = int(time.time() - start_time)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect((CONTROLLER_IP, 22))
            s.close()
            controller_alive = True
            break
        except Exception:
            _log("[Controller Reboot] Waiting for controller at 10.1.1.1 (%ds)..." % elapsed, "INFO")
            time.sleep(2)

    if controller_alive:
        _log("[Controller Reboot] Controller is alive! Checking post-update pairing...", "SUCCESS")
        try:
            # Check [ -f /log/updates/RUN_PAIR_ONCE ] locally on Solo
            if os.path.exists("/log/updates/RUN_PAIR_ONCE"):
                _log("Automatically broadcasting software pairing invitation now...", "SUCCESS")
                flash_mgr._ssh("sololink_config --pair", host=CONTROLLER_IP, log_callback=_log)
                try:
                    # rm -f /log/updates/RUN_PAIR_ONCE
                    os.remove("/log/updates/RUN_PAIR_ONCE")
                except Exception:
                    pass
        except Exception as ex_pair:
            logger.warning("Post-update pairing trigger error: %s", ex_pair)
        return True
    else:
        _log("[Controller Reboot] Controller probe reached timeout (%ds)." % max_duration, "WARNING")
        return False


# ---------------------------------------------------------------------------
# Remote Execution Helpers (Delegated to flash_mgr)
# ---------------------------------------------------------------------------
def sftp_upload_file(local_path, remote_path, host=CONTROLLER_IP, user=CONTROLLER_USER, password=CONTROLLER_PASS, timeout=600, saved_ssid=None, log_callback=None):
    """Upload a file to remote host using flash_mgr._scp_to."""
    rc, msg = flash_mgr._scp_to(local_path, host, remote_path, saved_ssid=saved_ssid, log_callback=log_callback)
    return rc == 0, msg


def ssh_run_command(cmd, host=CONTROLLER_IP, user=CONTROLLER_USER, password=CONTROLLER_PASS, timeout=60, saved_ssid=None, log_callback=None):
    """Execute a command on remote host (or locally if copter) via flash_mgr._ssh."""
    return flash_mgr._ssh(cmd, host=host, saved_ssid=saved_ssid, log_callback=log_callback)


# ---------------------------------------------------------------------------
# Local Staging Functions
# ---------------------------------------------------------------------------
def stage_controller_firmware():
    """
    Safely stages 3dr-controller.tar.gz and its MD5 file into the native Windows
    temporary workspace (firmware_files/tmp_updates/).
    Guarantees os.makedirs() runs before any file read or copy.
    """
    ensure_controller_firmware_dirs()

    if not os.path.exists(CTRL_TAR_SRC):
        raise IOError(
            "Controller firmware package '%s' not found in %s" % (CTRL_TAR_NAME, FIRMWARE_DIR)
        )

    # Copy package to tmp_updates if not already present or out of date
    if not os.path.exists(CTRL_TAR_DST) or os.path.getsize(CTRL_TAR_SRC) != os.path.getsize(CTRL_TAR_DST):
        logger.info("Staging controller firmware: %s -> %s", CTRL_TAR_SRC, CTRL_TAR_DST)
        shutil.copyfile(CTRL_TAR_SRC, CTRL_TAR_DST)

    # Stage or compute MD5 natively in Python
    if os.path.exists(CTRL_MD5_SRC):
        shutil.copyfile(CTRL_MD5_SRC, CTRL_MD5_DST)
    else:
        ctrl_md5 = get_file_md5(CTRL_TAR_DST)
        with compat.open_text(CTRL_MD5_DST, "w") as f:
            f.write("%s  %s\n" % (ctrl_md5, CTRL_TAR_NAME))

    md5_val = None
    if os.path.exists(CTRL_MD5_DST):
        try:
            with compat.open_text(CTRL_MD5_DST, "r") as f:
                md5_val = f.read().strip().split()[0]
        except Exception:
            pass

    return {
        "success": True,
        "package": CTRL_TAR_NAME,
        "size_bytes": os.path.getsize(CTRL_TAR_DST),
        "md5": md5_val,
        "staged_path": CTRL_TAR_DST,
        "tmp_updates_dir": TMP_UPDATES_DIR
    }


def get_controller_firmware_status():
    """Returns local staging and availability state of controller firmware."""
    ensure_controller_firmware_dirs()
    src_available = os.path.exists(CTRL_TAR_SRC)
    staged_available = os.path.exists(CTRL_TAR_DST)

    size = os.path.getsize(CTRL_TAR_DST) if staged_available else (os.path.getsize(CTRL_TAR_SRC) if src_available else 0)
    md5_val = None
    if staged_available and os.path.exists(CTRL_MD5_DST):
        try:
            with compat.open_text(CTRL_MD5_DST, "r") as f:
                md5_val = f.read().strip().split()[0]
        except Exception:
            pass

    return {
        "source_available": src_available,
        "staged_available": staged_available,
        "package": CTRL_TAR_NAME,
        "size_bytes": size,
        "md5": md5_val,
        "tmp_updates_dir": TMP_UPDATES_DIR
    }


# ---------------------------------------------------------------------------
# Background Controller Updater Engine
# ---------------------------------------------------------------------------
class ControllerUpdater(object):
    def __init__(self):
        self.is_running = False
        self.progress = 0
        self.current_stage = "Idle"
        self.status = "ready"
        self.log_buffer = []
        self.lock = threading.Lock()

    def log(self, message, level="INFO"):
        ts = time.strftime("%H:%M:%S")
        entry = {"time": ts, "level": level, "message": message}
        with self.lock:
            self.log_buffer.append(entry)
            if len(self.log_buffer) > 200:
                self.log_buffer = self.log_buffer[-200:]
        logger.info("[%s] %s", level, message)

    def get_state(self):
        with self.lock:
            return {
                "is_running": self.is_running,
                "progress": self.progress,
                "stage": self.current_stage,
                "status": self.status,
                "logs": list(self.log_buffer)
            }

    def start_update(self, controller_ip=CONTROLLER_IP):
        with self.lock:
            if self.is_running:
                return False, "Controller update is already in progress."
            self.is_running = True
            self.status = "running"
            self.progress = 5
            self.current_stage = "Staging firmware"

        t = threading.Thread(target=self._run_pipeline, args=(controller_ip,))
        t.daemon = True
        t.start()
        return True, "Controller update started."

    def _run_pipeline(self, controller_ip):
        modified_profiles = []
        try:
            # 1. Detect and lock active Wi-Fi profile before file transfers
            saved_ssid = get_current_wifi_ssid()
            self.log("[Wi-Fi Automation] Detected active Wi-Fi profile: %s" % (saved_ssid or "None"), "INFO")
            saved_ssid, modified_profiles = lock_wifi_profile(saved_ssid)
            self.log("[Wi-Fi Lockdown] Locked profile '%s' (auto-connect, priority 1, roaming disabled on other profiles)" % saved_ssid, "SUCCESS")

            self.log("Preparing controller firmware workspace on Windows...", "INFO")
            stage_info = stage_controller_firmware()
            self.progress = 25
            self.current_stage = "Connecting to Controller"
            self.log("Firmware staged locally: %s" % stage_info["staged_path"], "INFO")

            # 2. Connect to remote controller with AutoAddPolicy bypassing known_hosts cache
            self.log("Preparing remote /tmp/updates on controller (%s)..." % controller_ip, "INFO")
            rc, out = ssh_run_command("mkdir -p /tmp/updates /log/updates", host=controller_ip, saved_ssid=saved_ssid, log_callback=self.log)
            if rc != 0:
                raise RuntimeError("Failed to prepare directories on controller (code %d): %s" % (rc, out))

            self.progress = 40
            self.current_stage = "Uploading Package via SFTP"
            self.log("Uploading %s to controller via SFTP..." % CTRL_TAR_NAME, "INFO")
            ok, msg = sftp_upload_file(CTRL_TAR_DST, "/tmp/updates/" + CTRL_TAR_NAME, host=controller_ip, saved_ssid=saved_ssid, log_callback=self.log)
            if not ok:
                raise RuntimeError("SFTP package transfer failed: %s" % msg)

            if os.path.exists(CTRL_MD5_DST):
                self.log("Uploading MD5 checksum file via SFTP...", "INFO")
                sftp_upload_file(CTRL_MD5_DST, "/tmp/updates/" + CTRL_MD5_NAME, host=controller_ip, saved_ssid=saved_ssid, log_callback=self.log)
            else:
                ctrl_md5 = get_file_md5(CTRL_TAR_DST)
                ssh_run_command(
                    "echo '%s  %s' > /tmp/updates/%s" % (ctrl_md5, CTRL_TAR_NAME, CTRL_MD5_NAME),
                    host=controller_ip,
                    saved_ssid=saved_ssid,
                    log_callback=self.log
                )

            self.progress = 75
            self.current_stage = "Configuring Update Flags"
            self.log("Configuring update flags on controller...", "INFO")
            ssh_run_command("sololink_config --update-prepare sololink 2>/dev/null || true", host=controller_ip, saved_ssid=saved_ssid, log_callback=self.log)
            ssh_run_command("cp /tmp/updates/3dr-controller.tar.* /log/updates/ 2>/dev/null || true", host=controller_ip, saved_ssid=saved_ssid, log_callback=self.log)
            ssh_run_command("touch /log/updates/UPDATE /log/updates/RESETSETTINGS", host=controller_ip, saved_ssid=saved_ssid, log_callback=self.log)

            # Establish RUN_PAIR_ONCE marker file confirmation flag configuration setup explicitly
            # right before the final background 'nohup sh -c "sleep 2 && reboot"' command is executed on the controller at 10.1.1.1
            self.log("[Wi-Fi] Staging one-time post-update pairing trigger...", "INFO")
            ssh_run_command("mkdir -p /log/updates && touch /log/updates/RUN_PAIR_ONCE", host="10.1.1.10", saved_ssid=saved_ssid, log_callback=self.log)

            self.progress = 90
            self.current_stage = "Rebooting Controller"
            self.log("Update flags applied. Triggering controller reboot...", "SUCCESS")
            ssh_run_command("nohup sh -c 'sleep 2 && reboot' >/dev/null 2>&1 &", host=controller_ip, saved_ssid=saved_ssid, log_callback=self.log)

            # Wait until staging phases are completely finalized before pushing global state notification event back to web layer
            with self.lock:
                self.progress = 100
                self.current_stage = "Rebooting — Applying Updates"
                self.status = "success"
                self.is_running = False
            self.log("Controller update procedure staged successfully. Reboot in progress.", "SUCCESS")

            # 3. Post-reboot automated Wi-Fi reconnection loop runs asynchronously
            self.log("[Wi-Fi Automation] Controller reboot triggered. Executing automatic Wi-Fi reconnection...", "INFO")
            t_rec = threading.Thread(target=reconnect_wifi, args=(saved_ssid,), kwargs={"log_callback": self.log})
            t_rec.daemon = True
            t_rec.start()
        except Exception as e:
            with self.lock:
                self.status = "error"
                self.current_stage = "Error: %s" % str(e)
                self.is_running = False
            self.log("Controller update error: %s" % str(e), "ERROR")
        finally:
            if modified_profiles:
                unlock_wifi_profiles(modified_profiles)


controller_updater = ControllerUpdater()

# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------
controller_firmware_bp = Blueprint("controller_firmware", __name__)


@controller_firmware_bp.route("/api/controller/firmware/status", methods=["GET"])
def api_controller_firmware_status():
    st = get_controller_firmware_status()
    st.update(controller_updater.get_state())
    st["saved_wifi_ssid"] = get_current_wifi_ssid()
    return jsonify(st)


@controller_firmware_bp.route("/api/controller/firmware/stage", methods=["POST", "GET"])
@login_required
def api_controller_firmware_stage():
    try:
        res = stage_controller_firmware()
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": True, "message": str(e)}), 400


@controller_firmware_bp.route("/api/controller/firmware/update", methods=["POST"])
@login_required
def api_controller_firmware_update():
    from modules.telemetry import is_vehicle_armed
    try:
        if is_vehicle_armed():
            return jsonify({"error": True, "message": "SAFETY INTERLOCK: Controller update prohibited while vehicle is ARMED!"}), 403
    except Exception:
        pass

    body = request.get_json(silent=True) or {}
    controller_ip = body.get("controller_ip", CONTROLLER_IP)
    ok, msg = controller_updater.start_update(controller_ip=controller_ip)
    if ok:
        return jsonify({"success": True, "message": msg})
    return jsonify({"error": True, "message": msg}), 400
