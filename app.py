#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
 3DR Solo Web Utility - Modern Modular Server (Python 2.7 stdlib, no Flask)
Unified portal for 3DR Solo / Open Solo management:
- Real-time copter link status via live non-blocking heartbeat probe
- Firmware updates & ArduCopter custom build engine
- ArduPilot parameters editor & 1-click presets
- Artoo controller physical button mappings
- Cockpit glass flight deck with live MAVLink telemetry & HDMI video streaming
- Wi-Fi home networking & NAT bi-directional routing
- Isolated legacy TCP socket server thread for SoloLink mobile app connectivity
"""

import os
import sys
import time
import signal
import logging
import threading
from webcore import App, send_from_directory, render_template, redirect, jsonify, request, Response

# Add workspace directory to python path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Safe logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("solo_web_utility")

# Ignore SIGHUP and SIGPIPE on POSIX systems
try:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
except Exception:
    pass

try:
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
except Exception:
    pass

# Import modular blueprints and services
from modules.auth import auth_bp
from modules.version import version_bp, get_version_info
from modules.parameters import parameters_bp
from modules.controller import controller_bp
from modules.firmware import firmware_bp, get_local_firmware_version
from modules.controller_firmware import controller_firmware_bp
from modules.video import video_bp, start_video_service
from modules.telemetry import (
    telemetry_bp,
    start_telemetry_service,
    is_vehicle_armed,
    get_live_copter_status,
    start_connection_monitor
)
from modules.wifi import wifi_bp
from modules.pairing import pairing_bp
from modules.calibration import calibration_bp
from modules.legacy_server import start_legacy_server
import pair_mgr

# Initialize the standard-library web application (webcore.py - no Flask)
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

app = App(
    static_dir=STATIC_DIR,
    template_dir=TEMPLATES_DIR if os.path.exists(TEMPLATES_DIR) else STATIC_DIR
)

# Register modular blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(version_bp)
app.register_blueprint(parameters_bp)
app.register_blueprint(controller_bp)
app.register_blueprint(firmware_bp)
app.register_blueprint(controller_firmware_bp)
app.register_blueprint(video_bp)
app.register_blueprint(telemetry_bp)
app.register_blueprint(wifi_bp)
app.register_blueprint(pairing_bp)
app.register_blueprint(calibration_bp)

# Captive portal probe paths for mobile device automatic popups
CAPTIVE_PORTAL_PATHS = [
    '/hotspot-detect.html',
    '/library/test/success.html',
    '/captiveportal.html',
    '/success.html',
    '/bag',
    '/generate_204',
    '/gen_204',
    '/mobile/status.php',
    '/check_network_status.txt',
    '/ncsi.txt',
    '/connecttest.txt',
    '/msftconnecttest/redirect',
    '/msftncsi.txt',
    '/canonical.html',
    '/success.txt'
]


# ---------------------------------------------------------------------------
# Core Routes
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
@app.route("/index.html", methods=["GET"])
def serve_index():
    if os.path.exists(os.path.join(TEMPLATES_DIR, "index.html")):
        resp = Response(render_template("index.html"), mimetype="text/html")
    else:
        resp = send_from_directory(STATIC_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/updates", methods=["GET"])
@app.route("/updates.html", methods=["GET"])
def serve_updates():
    if os.path.exists(os.path.join(TEMPLATES_DIR, "updates.html")):
        resp = Response(render_template("updates.html"), mimetype="text/html")
    else:
        resp = send_from_directory(STATIC_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/status", methods=["GET"])
def api_status():
    """
    Non-blocking status route.
    Calls get_live_copter_status() directly to return true live physical connection state,
    and checks if Controller is paired/linked at 10.1.1.1.
    """
    live_online = get_live_copter_status()
    ver_info = get_version_info()
    ctrl_online = pair_mgr.is_controller_linked(timeout=0.6)

    resp = jsonify({
        "copter_online": live_online,
        "controller_online": ctrl_online,
        "paired": ctrl_online,
        "status_text": "Paired & Connected" if ctrl_online else "Not Connected",
        "pixhawk_version": "ArduCopter %s" % get_local_firmware_version(),
        "utility_version": ver_info.get("version", "1.0.1"),
        "iteration": ver_info.get("iteration", 1),
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "armed": is_vehicle_armed()
    })
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/ping", methods=["GET"])
def api_ping():
    """Lightweight uncached endpoint for quick connection availability probes."""
    resp = jsonify({"online": True, "pong": True, "time": time.time()})
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def uninstall_solo_web_utility(is_cli=False):
    """
    Completely removes SoloWebUtility2:
    1. Removes SoloWebUtility2 startup lines from /etc/rc.local.
    2. Deletes /usr/bin/SoloWebUtility2, /log/SoloWebUtility2, and /home/root/SoloWebUtility2.
    3. If called from CLI, exits immediately.
    4. If called from web server, schedules directory deletion and process termination after returning HTTP response.
    """
    logger.info("[UNINSTALL] SoloWebUtility2 uninstallation initiated (is_cli=%s)...", is_cli)

    # 1. Clean /etc/inittab (remove SWU respawn entry)
    inittab_path = "/etc/inittab"
    if os.path.exists(inittab_path):
        try:
            with open(inittab_path, "r") as f:
                lines = f.readlines()
            new_lines = [l for l in lines if not l.startswith("SWU:") and "SoloWebUtility" not in l and "solo_web_app" not in l]
            with open(inittab_path, "w") as f:
                f.writelines(new_lines)
            try:
                import subprocess
                subprocess.call("telinit q 2>/dev/null || true", shell=True)
            except Exception:
                pass
            logger.info("[UNINSTALL] Cleaned SWU respawn entries from /etc/inittab.")
        except Exception as e:
            logger.warning("[UNINSTALL] Error updating /etc/inittab: %s", e)

    # Clean legacy init scripts if any exist
    for legacy_init in ["/etc/init.d/solowebutility", "/etc/rc5.d/S99solowebutility"]:
        if os.path.exists(legacy_init):
            try:
                os.remove(legacy_init)
            except Exception:
                pass

    # Clean /etc/rc.local
    rc_local_path = "/etc/rc.local"
    if os.path.exists(rc_local_path):
        try:
            with open(rc_local_path, "r") as f:
                lines = f.readlines()
            new_lines = []
            for line in lines:
                if "SoloWebUtility" in line or "solo_web_app" in line:
                    continue
                new_lines.append(line)
            with open(rc_local_path, "w") as f:
                f.writelines(new_lines)
            try:
                os.chmod(rc_local_path, 0o755)
            except Exception:
                pass
            logger.info("[UNINSTALL] Cleaned auto-start entries from /etc/rc.local.")
        except Exception as e:
            logger.warning("[UNINSTALL] Error updating /etc/rc.local: %s", e)

    dirs_to_remove = [
        "/usr/bin/SoloWebUtility2",
        "/log/SoloWebUtility2",
        "/home/root/SoloWebUtility2"
    ]

    if is_cli:
        import subprocess
        for d in dirs_to_remove:
            if os.path.exists(d):
                try:
                    subprocess.call("rm -rf %s" % d, shell=True)
                except Exception:
                    pass
        print("[OK] SoloWebUtility2 successfully uninstalled from /etc/rc.local and disk.")
        return {"success": True, "message": "SoloWebUtility2 uninstalled successfully."}

    # For web endpoint: deferred cleanup and process termination after HTTP response is flushed
    def _delayed_teardown():
        time.sleep(1.2)
        import subprocess
        for d in dirs_to_remove:
            try:
                subprocess.call("rm -rf %s" % d, shell=True)
            except Exception:
                pass
        logger.info("[UNINSTALL] Application files removed. Terminating web server process.")
        try:
            os._exit(0)
        except Exception:
            sys.exit(0)

    t = threading.Thread(target=_delayed_teardown)
    t.daemon = True
    t.start()

    return {
        "success": True,
        "message": "SoloWebUtility2 uninstalled successfully. Removing files and terminating server."
    }


@app.route("/api/uninstall", methods=["POST", "GET"])
def api_uninstall():
    """Endpoint to completely remove/uninstall SoloWebUtility2 from the drone."""
    res = uninstall_solo_web_utility(is_cli=False)
    resp = jsonify(res)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/wifi/reconnect", methods=["POST"])
def api_wifi_reconnect_route():
    """Trigger automated background reconnection loop for host Wi-Fi and copter heartbeat."""
    from modules.controller_firmware import get_current_wifi_ssid, reconnect_wifi
    saved_ssid = get_current_wifi_ssid()
    t = threading.Thread(target=reconnect_wifi, args=(saved_ssid,))
    t.daemon = True
    t.start()
    return jsonify({"success": True, "message": "Reconnection loop started for '%s'." % saved_ssid})


# Captive portal redirection
for path in CAPTIVE_PORTAL_PATHS:
    endpoint_name = "captive_" + path.replace("/", "_").replace(".", "_").replace("-", "_")
    app.add_url_rule(path, endpoint_name, lambda: redirect("/", code=302), methods=["GET"])


# Fallback direct static asset route (e.g. /app.js, /style.css, /cockpit.js)
def serve_static_root(path):
    filename = path.lstrip("/")
    if filename.startswith("static/"):
        filename = filename[len("static/"):]
    file_path = os.path.join(STATIC_DIR, filename)
    if filename and os.path.isfile(file_path):
        resp = send_from_directory(STATIC_DIR, filename)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp
    if path.startswith("/api/"):
        return jsonify({"error": True, "message": "Not found."}), 404
    # Fallback to index.html for SPA client-side routes
    return serve_index()


app.fallback = serve_static_root


def init_background_services(flask_port=5000):
    """
    Launch background telemetry, HDMI video pipeline, connection monitor,
    and isolated legacy TCP socket server in dedicated background threads.
    """
    logger.info("Initializing background services...")

    # 1. Real-time background connection monitor for 10.1.1.10
    try:
        start_connection_monitor()
        logger.info("[OK] Real-time 0.5s connection heartbeat monitor started.")
    except Exception as e:
        logger.warning("Could not start connection monitor: %s", e)

    # 2. Telemetry MAVLink engine
    try:
        start_telemetry_service()
        logger.info("[OK] Live MAVLink telemetry client started.")
    except Exception as e:
        logger.warning("Could not start telemetry service: %s", e)

    # 3. HDMI live video pipeline
    try:
        start_video_service()
        logger.info("[OK] Solo live HDMI video streaming engine started.")
    except Exception as e:
        logger.warning("Could not start video service: %s", e)

    # 4. Safety: drop the Controller's home Wi-Fi link as soon as the vehicle arms
    try:
        import wifi_mgr
        wifi_mgr.start_arm_interlock(is_vehicle_armed)
        logger.info("[OK] Home Wi-Fi arm interlock started.")
    except Exception as e:
        logger.warning("Could not start Wi-Fi arm interlock: %s", e)

    # 5. Legacy TCP socket loop from app_server.py in an isolated background thread
    try:
        legacy_thread = start_legacy_server(flask_port=flask_port)
        logger.info("[OK] Legacy SoloLink TCP socket loop running in background thread on port %d.", legacy_thread.port)
    except Exception as e:
        logger.warning("Could not start legacy TCP server thread: %s", e)


def create_app():
    """Application factory for WSGI servers."""
    init_background_services(flask_port=5000)
    return app


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        uninstall_solo_web_utility(is_cli=True)
        sys.exit(0)

    port = 80  # Default to port 80 (standard HTTP origin on Solo 10.1.1.10)

    # Support CLI port override e.g. 'python app.py 8080' or 'python app.py 5000'
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    elif "PORT" in os.environ:
        try:
            port = int(os.environ["PORT"])
        except ValueError:
            pass

    # Start background threads before starting the web server
    init_background_services(flask_port=port)

    ver_info = get_version_info()
    print("=" * 65)
    print(" 3DR SOLO MODULAR WEB UTILITY (v%s)" % ver_info.get("version", "1.0.1"))
    print("=" * 65)
    print("  * Web Interface:      http://localhost:%d" % port)
    print("  * Solo Network Link:  http://10.1.1.10:%d" % port)
    print("  * Legacy TCP Socket:  Listening in background thread")
    print("=" * 65)
    sys.stdout.flush()

    app.run(host="0.0.0.0", port=port)
