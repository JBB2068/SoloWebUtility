#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
🎮 3DR Solo Controller Setup GUI Server (Port 8089)
Standalone configuration utility for /usr/bin/extSettings.conf
Allows customizing Artoo controller physical button mappings (Smart Shots, Flight Modes, Aux Servos/Gimbal/Gripper).

Runs independently from SoloWebUtility until user approves integration.
Compatible with Python 2.7 and Python 3.x.
"""

import os
import sys
import json
import time
import socket
import datetime

if sys.version_info[0] >= 3:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    from socketserver import ThreadingMixIn
    from urllib.parse import parse_qs, urlparse
else:
    from BaseHTTPServer import HTTPServer
    from SimpleHTTPServer import SimpleHTTPRequestHandler
    from SocketServer import ThreadingMixIn
    from urlparse import parse_qs, urlparse

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
DEFAULT_LOCAL_PORT = 8089

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
LOCAL_BACKUP_DIR = os.path.join(BASE_DIR, "backups")

if not os.path.exists(LOCAL_BACKUP_DIR):
    os.makedirs(LOCAL_BACKUP_DIR)

# Default template when remote file is missing or restored
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
        {"id": 4, "name": "Button A", "code": "ButtonA", "description": "Left face button (next to Button B)", "supported_events": [7, 5]},
        {"id": 5, "name": "Button B", "code": "ButtonB", "description": "Left face button (next to Button A)", "supported_events": [7, 5]},
        {"id": 6, "name": "Preset 1", "code": "ButtonPreset1", "description": "Top right preset button 1", "supported_events": [5]},
        {"id": 7, "name": "Preset 2", "code": "ButtonPreset2", "description": "Top right preset button 2", "supported_events": [5]},
        {"id": 8, "name": "Camera Trigger", "code": "ButtonCameraClick", "description": "Top left shoulder camera trigger", "supported_events": [7, 5]}
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
        {"id": 3, "name": "Autonomous (Mission / Waypoints)", "badge": "mode"},
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
        {"id": 16, "name": "Position Hold (Braking PosHold)", "badge": "mode"},
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


def check_ssh_reachability(host, port=22, timeout=1.5):
    """Test TCP port availability."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        err = s.connect_ex((host, port))
        s.close()
        return (err == 0)
    except Exception:
        return False


def get_ssh_client():
    if not HAS_PARAMIKO:
        raise RuntimeError("paramiko library is not installed in Python environment.")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SOLO_IP, port=SOLO_PORT, username=SOLO_USER, password=SOLO_PASS, timeout=4)
    return client


def fetch_remote_config():
    """Read /usr/bin/extSettings.conf from Solo via SSH, or return local fallback."""
    raw_content = ""
    is_live = False

    if HAS_PARAMIKO and check_ssh_reachability(SOLO_IP, SOLO_PORT):
        try:
            client = get_ssh_client()
            stdin, stdout, stderr = client.exec_command("cat %s 2>/dev/null" % REMOTE_CONF_PATH)
            remote_txt = stdout.read().decode("utf-8", errors="replace")
            client.close()
            if remote_txt.strip():
                raw_content = remote_txt
                is_live = True
        except Exception as e:
            sys.stderr.write("SSH read error: %s\n" % str(e))

    if not raw_content:
        # Check local cache or use default
        local_cache = os.path.join(BASE_DIR, "extSettings.local.conf")
        if os.path.exists(local_cache):
            with open(local_cache, "r") as f:
                raw_content = f.read()
        else:
            raw_content = DEFAULT_EXT_SETTINGS_CONF

    return raw_content, is_live


def parse_ext_settings(raw_text):
    """Parse extSettings.conf content into structured dictionary."""
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

        if in_ext_functions:
            if "=" in stripped:
                k, v = stripped.split("=", 1)
                k = k.strip()
                v = v.strip()

                if k.lower() == "timeoffsethrs":
                    time_offset = v
                else:
                    # Expect key format: button,event
                    # Expect val format: shot, mode, func
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


def build_config_text(mappings, time_offset="None", reference_text=None):
    """Generate the full extSettings.conf file format."""
    out = ["[extFunctions]"]
    
    # Sort keys for consistent ordering
    # Standard keys: 4,7; 4,5; 5,7; 5,5; 6,5; 7,5; 8,7; 8,5
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

    # Append standard documentation usage sections
    if reference_text:
        # Extract USAGE sections from reference
        pos = reference_text.find("[USAGE]")
        if pos != -1:
            out.append(reference_text[pos:].rstrip())
        else:
            default_pos = DEFAULT_EXT_SETTINGS_CONF.find("[USAGE]")
            out.append(DEFAULT_EXT_SETTINGS_CONF[default_pos:].rstrip())
    else:
        default_pos = DEFAULT_EXT_SETTINGS_CONF.find("[USAGE]")
        out.append(DEFAULT_EXT_SETTINGS_CONF[default_pos:].rstrip())

    out.append("")
    return "\n".join(out)


def save_ext_settings(new_content):
    """Save extSettings.conf to Solo copter with automatic backup, or save locally."""
    solo_online = check_ssh_reachability(SOLO_IP, SOLO_PORT)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Save local copy in backups
    local_bak = os.path.join(LOCAL_BACKUP_DIR, "extSettings_%s.conf" % timestamp)
    with open(local_bak, "w") as f:
        f.write(new_content)

    # Save to local cache
    local_cache = os.path.join(BASE_DIR, "extSettings.local.conf")
    with open(local_cache, "w") as f:
        f.write(new_content)

    remote_saved = False
    error_msg = None

    if solo_online and HAS_PARAMIKO:
        try:
            client = get_ssh_client()
            # Backup current config on Solo
            bak_cmd = "cp %s %s 2>/dev/null; cp %s %s.%s 2>/dev/null" % (
                REMOTE_CONF_PATH, REMOTE_BACKUP_PATH,
                REMOTE_CONF_PATH, REMOTE_CONF_PATH, timestamp
            )
            client.exec_command(bak_cmd)

            # Write temp file and move atomically
            sftp = client.open_sftp()
            temp_path = "/tmp/extSettings.conf.tmp"
            with sftp.open(temp_path, "w") as rf:
                rf.write(new_content)
            sftp.close()

            # Move and sync
            move_cmd = "mv /tmp/extSettings.conf.tmp %s && chmod 755 %s && sync" % (
                REMOTE_CONF_PATH, REMOTE_CONF_PATH
            )
            stdin, stdout, stderr = client.exec_command(move_cmd)
            exit_status = stdout.channel.recv_exit_status()
            client.close()

            if exit_status == 0:
                remote_saved = True
            else:
                error_msg = "Remote move returned exit code %d" % exit_status
        except Exception as e:
            error_msg = str(e)
    else:
        error_msg = "Solo copter 10.1.1.10 not reachable via SSH (saved locally only)"

    return {
        "success": remote_saved or (not solo_online),
        "remote_saved": remote_saved,
        "solo_online": solo_online,
        "local_backup": os.path.basename(local_bak),
        "error": error_msg
    }


class ControllerSetupHandler(SimpleHTTPRequestHandler):
    """HTTP Request Handler for Controller Setup API & Static Assets."""

    def __init__(self, *args, **kwargs):
        # Point SimpleHTTPRequestHandler to our static folder
        kwargs['directory'] = STATIC_DIR if sys.version_info[0] >= 3 else STATIC_DIR
        SimpleHTTPRequestHandler.__init__(self, *args, **kwargs)

    def send_json_response(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/status":
            solo_ok = check_ssh_reachability(SOLO_IP, 22)
            ctrl_ok = check_ssh_reachability("10.1.1.1", 22)
            self.send_json_response({
                "solo_ip": SOLO_IP,
                "solo_connected": solo_ok,
                "controller_ip": "10.1.1.1",
                "controller_connected": ctrl_ok,
                "has_paramiko": HAS_PARAMIKO
            })
            return

        elif path == "/api/definitions":
            self.send_json_response(DEFINITION_DATA)
            return

        elif path == "/api/config":
            raw_text, is_live = fetch_remote_config()
            parsed_data = parse_ext_settings(raw_text)
            parsed_data["is_live"] = is_live
            parsed_data["solo_connected"] = check_ssh_reachability(SOLO_IP, 22)
            self.send_json_response(parsed_data)
            return

        elif path == "/api/backups":
            baks = []
            if os.path.exists(LOCAL_BACKUP_DIR):
                for f in sorted(os.listdir(LOCAL_BACKUP_DIR), reverse=True):
                    if f.endswith(".conf"):
                        baks.append(f)
            self.send_json_response({"backups": baks})
            return

        # Serve static assets or default to index.html
        if path == "/" or not os.path.exists(os.path.join(STATIC_DIR, path.lstrip("/"))):
            self.path = "/index.html"

        if sys.version_info[0] >= 3:
            return SimpleHTTPRequestHandler.do_GET(self)
        else:
            # Python 2 fallback: change dir temporarily or serve file directly
            rel_path = self.path.lstrip("/")
            file_path = os.path.join(STATIC_DIR, rel_path)
            if os.path.exists(file_path) and os.path.isfile(file_path):
                self.send_response(200)
                if file_path.endswith(".html"):
                    self.send_header("Content-Type", "text/html")
                elif file_path.endswith(".css"):
                    self.send_header("Content-Type", "text/css")
                elif file_path.endswith(".js"):
                    self.send_header("Content-Type", "application/javascript")
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "File not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length).decode("utf-8", errors="replace")

        try:
            payload = json.loads(raw_body) if raw_body else {}
        except Exception:
            payload = {}

        if path == "/api/config":
            # Save configuration
            raw_text = payload.get("raw_text")
            mappings = payload.get("mappings")
            time_offset = payload.get("timeOffsetHrs", "None")

            if raw_text:
                conf_to_write = raw_text
            elif mappings:
                conf_to_write = build_config_text(mappings, time_offset)
            else:
                self.send_json_response({"error": "No config data provided"}, 400)
                return

            res = save_ext_settings(conf_to_write)
            self.send_json_response(res)
            return

        elif path == "/api/reset_default":
            res = save_ext_settings(DEFAULT_EXT_SETTINGS_CONF)
            self.send_json_response(res)
            return

        elif path == "/api/restore_backup":
            filename = payload.get("filename")
            if not filename or ".." in filename:
                self.send_json_response({"error": "Invalid backup filename"}, 400)
                return
            target_bak = os.path.join(LOCAL_BACKUP_DIR, filename)
            if not os.path.exists(target_bak):
                self.send_json_response({"error": "Backup file not found"}, 404)
                return
            with open(target_bak, "r") as f:
                content = f.read()
            res = save_ext_settings(content)
            self.send_json_response(res)
            return

        self.send_json_response({"error": "Endpoint not found"}, 404)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def run_server(port=DEFAULT_LOCAL_PORT):
    # Ensure static dir exists
    if not os.path.exists(STATIC_DIR):
        os.makedirs(STATIC_DIR)

    server_address = ("", port)
    try:
        httpd = ThreadedHTTPServer(server_address, ControllerSetupHandler)
    except socket.error as e:
        sys.stderr.write("Port %d busy or unavailable, trying %d...\n" % (port, port + 1))
        port += 1
        server_address = ("", port)
        httpd = ThreadedHTTPServer(server_address, ControllerSetupHandler)

    print("=" * 65)
    print("  🎮 3DR SOLO CONTROLLER SETUP GUI SERVER")
    print("  Standalone testing server running at: http://localhost:%d" % port)
    print("  Target Solo Copter: %s:22 (%s)" % (SOLO_IP, REMOTE_CONF_PATH))
    print("=" * 65)
    sys.stdout.flush()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Controller Setup Server...")
        httpd.server_close()


if __name__ == "__main__":
    p = DEFAULT_LOCAL_PORT
    if len(sys.argv) > 1:
        try:
            p = int(sys.argv[1])
        except ValueError:
            pass
    run_server(p)
