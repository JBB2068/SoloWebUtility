#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
🎮 3DR Solo Controller Manager
Handles reading, parsing, modifying, and saving /usr/bin/extSettings.conf
for Open Solo Artoo Controller hardware button customization.

Compatible with Python 2.7 & 3.x.
Operates directly when deployed on Solo copter (10.1.1.10)
or via SSH/SFTP when running locally on PC.
"""

import os
import sys
import json
import time
import socket
import datetime

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False

SOLO_IP = "10.1.1.10"
SOLO_PORT = 22
SOLO_USER = "root"
SOLO_PASS = "TjSDBkAu"
REMOTE_CONF_PATH = "/usr/bin/extSettings.conf"
REMOTE_BACKUP_PATH = "/usr/bin/extSettings.conf.backup"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_BACKUP_DIR = os.path.join(BASE_DIR, "backups")

if not os.path.exists(LOCAL_BACKUP_DIR):
    try:
        os.makedirs(LOCAL_BACKUP_DIR)
    except Exception:
        pass

DEFAULT_EXT_SETTINGS_CONF = """[extFunctions]
4,7=-1, 0 ,-1
4,5=-1, 3, -1
5,7=-1, 11, -1
5,5=-1, 13, -1
6,5=-1, -1, -1
7,5=-1, -1, -1
8,7=-1, -1, -1
8,5=-1, -1, 3
timeOffsetHrs=None


[USAGE]
USAGE: {button},{event} = {shot}, {mode}, {func}


[USAGE-BUTTONS]
ButtonPower = 0
ButtonFly = 1
ButtonRTL = 2
ButtonLoiter = 3
ButtonA = 4
ButtonB = 5
ButtonPreset1 = 6
ButtonPreset2 = 7
ButtonCameraClick = 8


[USAGE-EVENTS]
Short hold: 7
Long hold: 5


[USAGE-SHOTS]
APP_SHOT_NONE = -1
APP_SHOT_SELFIE = 0
APP_SHOT_ORBIT = 1
APP_SHOT_CABLECAM = 2
APP_SHOT_ZIPLINE = 3
APP_SHOT_RECORD = 4
APP_SHOT_FOLLOW = 5
APP_SHOT_MULTIPOINT = 6
APP_SHOT_PANO = 7
APP_SHOT_REWIND = 8
APP_SHOT_TRANSECT = 9
APP_SHOT_RTL = 10


[USAGE-MODES]
-1 = None
0 = Stabilize
1 = Acro
2 = FLY: Manual (Alt Hold)
3 = Autonomous
4 = Takeoff
5 = FLY (Loiter)
6 = RTL
7 = Circle
8 = Position (Do not use!!)
9 = Land
10 = OF_LOITER (Do not use!!)
11 = Drift
13 = Sport
14 = Flip
15 = Auto-tune
16 = Position Hold
17 = Brake
18 = Throw (ArduCopter 3.5+)
19 = ADS-B Avoid (ArduCopter 3.5+ and not a user mode)
20 = Guided No GPS (ArduCopter 3.5+)
21 = SmartRTL (ArduCopter 3.6+ Only)
22 = Optical Flow Hold (ArduCopter 3.6+)
23 = Follow (ArduCopter 3.6+)
24 = ZIGZAG (ArduCopter 3.7+)


[USAGE-FUNCTIONS]
-1 = none
1 = Landing gear up     (Not available in ArduCopter yet)
2 = Landing gear down   (Not available in ArduCopter yet)
3 = Gimbal lock toggle
4 = Servo 6 low
5 = Servo 6 med
6 = Servo 6 high
7 = Servo 7 low
8 = Servo 7 med
9 = Servo 7 high
10 = Servo 8 low
11 = Servo 8 med
12 = Servo 8 high
13 = Gripper Close      (ArduCopter 3.5+ Only)
14 = Gripper Open       (ArduCopter 3.5+ Only)
"""

DEFINITION_DATA = {
    "buttons": [
        {"id": 4, "name": "Button A", "code": "ButtonA", "description": "Face Left button (next to Button B)", "supported_events": [7, 5]},
        {"id": 5, "name": "Button B", "code": "ButtonB", "description": "Face Left button (next to Button A)", "supported_events": [7, 5]},
        {"id": 6, "name": "Preset 1", "code": "ButtonPreset1", "description": "Top Right camera preset 1", "supported_events": [5]},
        {"id": 7, "name": "Preset 2", "code": "ButtonPreset2", "description": "Top Right camera preset 2", "supported_events": [5]},
        {"id": 8, "name": "Camera Trigger", "code": "ButtonCameraClick", "description": "Top Left shoulder trigger button", "supported_events": [7, 5]}
    ],
    "events": [
        {"id": 7, "name": "Short Hold (Press & Hold ~0.5s)", "code": "HoldRelease", "short_label": "Short Hold"},
        {"id": 5, "name": "Long Hold (Hold ~2.0s)", "code": "LongHold", "short_label": "Long Hold"}
    ],
    "shots": [
        {"id": -1, "name": "None", "badge": "off"},
        {"id": 0, "name": "Selfie", "badge": "shot"},
        {"id": 1, "name": "Orbit", "badge": "shot"},
        {"id": 2, "name": "Cablecam", "badge": "shot"},
        {"id": 3, "name": "Zipline", "badge": "shot"},
        {"id": 4, "name": "Record", "badge": "shot"},
        {"id": 5, "name": "Follow", "badge": "shot"},
        {"id": 6, "name": "Multipoint Cablecam", "badge": "shot"},
        {"id": 7, "name": "Panorama", "badge": "shot"},
        {"id": 8, "name": "Rewind", "badge": "shot"},
        {"id": 9, "name": "Transect", "badge": "shot"},
        {"id": 10, "name": "Smart RTL", "badge": "shot"}
    ],
    "modes": [
        {"id": -1, "name": "None", "badge": "off"},
        {"id": 0, "name": "Stabilize (Manual levelling)", "badge": "mode"},
        {"id": 1, "name": "Acro (Rate mode, manual flips)", "badge": "mode"},
        {"id": 2, "name": "FLY: Manual (Alt Hold - Barometer)", "badge": "mode"},
        {"id": 3, "name": "Autonomous (Auto Mission / Waypoints)", "badge": "mode"},
        {"id": 4, "name": "Takeoff (Auto takeoff routine)", "badge": "mode"},
        {"id": 5, "name": "FLY: Loiter (GPS position & altitude)", "badge": "mode"},
        {"id": 6, "name": "RTL (Return to Launch)", "badge": "mode"},
        {"id": 7, "name": "Circle (Circle around point)", "badge": "mode"},
        {"id": 8, "name": "Position (DO NOT USE)", "badge": "warning", "disabled": True},
        {"id": 9, "name": "Land (Auto land in place)", "badge": "mode"},
        {"id": 10, "name": "OF_LOITER (DO NOT USE)", "badge": "warning", "disabled": True},
        {"id": 11, "name": "Drift (Car-like steering)", "badge": "mode"},
        {"id": 13, "name": "Sport (Fast acrobatic leveling)", "badge": "mode"},
        {"id": 14, "name": "Flip (Auto acrobatic flip)", "badge": "mode"},
        {"id": 15, "name": "Auto-tune (In-flight tuning)", "badge": "mode"},
        {"id": 16, "name": "Position Hold (PosHold)", "badge": "mode"},
        {"id": 17, "name": "Brake (Immediate halt in place)", "badge": "mode"},
        {"id": 18, "name": "Throw (ArduCopter 3.5+)", "badge": "mode"},
        {"id": 20, "name": "Guided No GPS (ArduCopter 3.5+)", "badge": "mode"},
        {"id": 21, "name": "SmartRTL (ArduCopter 3.6+ Retrace)", "badge": "mode"},
        {"id": 22, "name": "Optical Flow Hold (ArduCopter 3.6+)", "badge": "mode"},
        {"id": 23, "name": "Follow (ArduCopter 3.6+)", "badge": "mode"},
        {"id": 24, "name": "ZigZag (ArduCopter 3.7+)", "badge": "mode"}
    ],
    "functions": [
        {"id": -1, "name": "None", "badge": "off"},
        {"id": 1, "name": "Landing Gear Up (Not implemented)", "badge": "disabled", "disabled": True},
        {"id": 2, "name": "Landing Gear Down (Not implemented)", "badge": "disabled", "disabled": True},
        {"id": 3, "name": "Gimbal Lock Toggle (Retract/RC Target)", "badge": "func"},
        {"id": 4, "name": "Servo 6 LOW (PWM 1000µs)", "badge": "func"},
        {"id": 5, "name": "Servo 6 MID (PWM 1500µs)", "badge": "func"},
        {"id": 6, "name": "Servo 6 HIGH (PWM 2000µs)", "badge": "func"},
        {"id": 7, "name": "Servo 7 LOW (PWM 1000µs)", "badge": "func"},
        {"id": 8, "name": "Servo 7 MID (PWM 1500µs)", "badge": "func"},
        {"id": 9, "name": "Servo 7 HIGH (PWM 2000µs)", "badge": "func"},
        {"id": 10, "name": "Servo 8 LOW (PWM 1000µs)", "badge": "func"},
        {"id": 11, "name": "Servo 8 MID (PWM 1500µs)", "badge": "func"},
        {"id": 12, "name": "Servo 8 HIGH (PWM 2000µs)", "badge": "func"},
        {"id": 13, "name": "Gripper CLOSE (ArduCopter 3.5+)", "badge": "func"},
        {"id": 14, "name": "Gripper OPEN (ArduCopter 3.5+)", "badge": "func"}
    ]
}


def is_running_on_solo():
    """True if executing natively on the Solo copter."""
    return os.path.exists(REMOTE_CONF_PATH) and os.path.isfile(REMOTE_CONF_PATH)


def check_ssh_reachability(host=SOLO_IP, port=SOLO_PORT, timeout=1.5):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        err = s.connect_ex((host, port))
        s.close()
        return (err == 0)
    except Exception:
        return False


def get_definitions():
    return DEFINITION_DATA


def parse_ext_settings(raw_text):
    mappings = {}
    time_offset = "None"
    lines = raw_text.splitlines()
    in_ext_functions = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if stripped.startswith("["):
            section_name = stripped.strip("[]").strip()
            in_ext_functions = (section_name.lower() == "extfunctions")
            continue

        if in_ext_functions and "=" in stripped:
            k, v = stripped.split("=", 1)
            k = k.strip()
            v = v.strip()

            if k.lower() == "timeoffsethrs":
                time_offset = v
            else:
                parts = [p.strip() for p in v.split(",")]
                shot_val = int(parts[0]) if len(parts) > 0 and parts[0] else -1
                mode_val = int(parts[1]) if len(parts) > 1 and parts[1] else -1
                func_val = int(parts[2]) if len(parts) > 2 and parts[2] else -1

                mappings[k] = {
                    "shot": shot_val,
                    "mode": mode_val,
                    "func": func_val,
                    "raw_val": v
                }

    return {
        "mappings": mappings,
        "timeOffsetHrs": time_offset,
        "raw_text": raw_text
    }


def fetch_config():
    raw_content = ""
    is_live = False

    # 1. Native on Solo Copter
    if is_running_on_solo():
        try:
            with open(REMOTE_CONF_PATH, "r") as f:
                raw_content = f.read()
            is_live = True
        except Exception as e:
            sys.stderr.write("Local read error: %s\n" % str(e))

    # 2. Remote from PC via SSH
    elif HAS_PARAMIKO and check_ssh_reachability(SOLO_IP, SOLO_PORT):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(SOLO_IP, port=SOLO_PORT, username=SOLO_USER, password=SOLO_PASS, timeout=4)
            stdin, stdout, stderr = client.exec_command("cat %s 2>/dev/null" % REMOTE_CONF_PATH)
            remote_txt = stdout.read().decode("utf-8", errors="replace")
            client.close()
            if remote_txt.strip():
                raw_content = remote_txt
                is_live = True
        except Exception as e:
            sys.stderr.write("SSH read error: %s\n" % str(e))

    # 3. Fallback to local cache or stock template
    if not raw_content:
        local_cache = os.path.join(BASE_DIR, "extSettings.local.conf")
        if os.path.exists(local_cache):
            with open(local_cache, "r") as f:
                raw_content = f.read()
        else:
            raw_content = DEFAULT_EXT_SETTINGS_CONF

    parsed = parse_ext_settings(raw_content)
    parsed["is_live"] = is_live
    parsed["is_native"] = is_running_on_solo()
    parsed["solo_connected"] = is_running_on_solo() or check_ssh_reachability(SOLO_IP, SOLO_PORT)
    return parsed


def build_config_text(mappings, time_offset="None", reference_text=None):
    out = ["[extFunctions]"]

    def sort_key(k):
        try:
            b, e = k.split(",")
            return (int(b), -int(e))
        except Exception:
            return (999, k)

    for k in sorted(mappings.keys(), key=sort_key):
        item = mappings[k]
        s = item.get("shot", -1)
        m = item.get("mode", -1)
        f = item.get("func", -1)
        out.append("%s=%d, %d, %d" % (k, s, m, f))

    offset_str = str(time_offset).strip()
    if not offset_str:
        offset_str = "None"
    out.append("timeOffsetHrs=%s" % offset_str)
    out.append("")
    out.append("")

    default_pos = DEFAULT_EXT_SETTINGS_CONF.find("[USAGE]")
    out.append(DEFAULT_EXT_SETTINGS_CONF[default_pos:].rstrip())
    out.append("")
    return "\n".join(out)


def save_config(raw_text=None, mappings=None, time_offset="None"):
    if raw_text:
        content_to_save = raw_text
    elif mappings:
        content_to_save = build_config_text(mappings, time_offset)
    else:
        return {"success": False, "error": "No configuration data provided."}

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    local_saved = False

    # Save to local backups if directory exists
    try:
        local_bak = os.path.join(LOCAL_BACKUP_DIR, "extSettings_%s.conf" % timestamp)
        with open(local_bak, "w") as f:
            f.write(content_to_save)
        local_saved = True
    except Exception:
        pass

    # Save to local cache
    try:
        local_cache = os.path.join(BASE_DIR, "extSettings.local.conf")
        with open(local_cache, "w") as f:
            f.write(content_to_save)
    except Exception:
        pass

    # Case A: Running natively on Solo copter
    if is_running_on_solo():
        try:
            # Backups
            os.system("cp %s %s 2>/dev/null" % (REMOTE_CONF_PATH, REMOTE_BACKUP_PATH))
            os.system("cp %s %s.%s 2>/dev/null" % (REMOTE_CONF_PATH, REMOTE_CONF_PATH, timestamp))
            tmp_path = "/tmp/extSettings.conf.tmp"
            with open(tmp_path, "w") as f:
                f.write(content_to_save)
            os.system("mv %s %s && chmod 755 %s && sync" % (tmp_path, REMOTE_CONF_PATH, REMOTE_CONF_PATH))
            return {
                "success": True,
                "remote_saved": True,
                "is_native": True,
                "message": "Updated /usr/bin/extSettings.conf locally on Solo. Takes effect immediately!"
            }
        except Exception as e:
            return {"success": False, "error": "Native file write error: %s" % str(e)}

    # Case B: Running on PC via SSH to Solo copter
    if HAS_PARAMIKO and check_ssh_reachability(SOLO_IP, SOLO_PORT):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(SOLO_IP, port=SOLO_PORT, username=SOLO_USER, password=SOLO_PASS, timeout=4)

            # Backup
            bak_cmd = "cp %s %s 2>/dev/null; cp %s %s.%s 2>/dev/null" % (
                REMOTE_CONF_PATH, REMOTE_BACKUP_PATH,
                REMOTE_CONF_PATH, REMOTE_CONF_PATH, timestamp
            )
            client.exec_command(bak_cmd)

            sftp = client.open_sftp()
            temp_path = "/tmp/extSettings.conf.tmp"
            with sftp.open(temp_path, "w") as rf:
                rf.write(content_to_save)
            sftp.close()

            move_cmd = "mv /tmp/extSettings.conf.tmp %s && chmod 755 %s && sync" % (
                REMOTE_CONF_PATH, REMOTE_CONF_PATH
            )
            stdin, stdout, stderr = client.exec_command(move_cmd)
            exit_code = stdout.channel.recv_exit_status()
            client.close()

            if exit_code == 0:
                return {
                    "success": True,
                    "remote_saved": True,
                    "is_native": False,
                    "message": "Updated /usr/bin/extSettings.conf on Solo (10.1.1.10). Takes effect immediately!"
                }
            else:
                return {"success": False, "error": "Remote move failed with exit code %d" % exit_code}
        except Exception as e:
            return {"success": False, "error": "SSH write error: %s" % str(e)}

    # Case C: Solo offline, saved locally
    return {
        "success": True,
        "remote_saved": False,
        "is_native": False,
        "message": "Saved to local cache (Solo copter is offline)."
    }


def reset_to_default():
    return save_config(raw_text=DEFAULT_EXT_SETTINGS_CONF)
