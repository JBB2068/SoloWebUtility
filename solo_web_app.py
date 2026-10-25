#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
🛸 3DR Solo Web Utility Server
Self-contained, lightweight Python web server and REST API for 3DR Solo & Open Solo.
Zero external dependencies - runs on Python 2.7 & Python 3.x standard library.
"""

import os
import sys
import json
import time
import socket
import signal
import shutil
import mimetypes

# /log/solowebutility.log is this process's stdout (inittab / deploy redirect).
# Python 2 block-buffers a redirected stdout, so log lines sat in memory for
# hours and never reached the file. Make both streams unbuffered.
try:
    if sys.version_info[0] < 3:
        sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)
        sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', 0)
except Exception:
    pass

# Compatible BaseHTTP / urllib imports for Python 2 & 3
if sys.version_info[0] >= 3:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from socketserver import ThreadingMixIn
    from urllib.parse import parse_qs, urlparse
else:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    from SocketServer import ThreadingMixIn
    from urlparse import parse_qs, urlparse

# Ignore SIGHUP so process keeps running when parent SSH session exits without nohup
try:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
except Exception:
    pass

try:
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)
except Exception:
    pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

import auth
import presets
import param_mgr
import wifi_mgr
import flash_mgr
import calib_mgr
import version_mgr
import controller_mgr
import cockpit_mgr
import arducopter_builder
import pair_mgr

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
VIEWER_APK_URL = "/sololink_viewer.apk"
VIEWER_APK_FILE = os.path.join(STATIC_DIR, "sololink_viewer.apk.bin")

PORT = 80  # Default HTTP port (falls back to 8080 if 80 is occupied or restricted)

# Captive portal detection probe endpoints
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


def get_system_status():
    """Gather Solo system, version, and network status."""
    ctrl_online = pair_mgr.is_controller_linked(timeout=0.6)
    solo_online = True

    # Disk Space Check
    disk_free_mb = 0
    try:
        st = os.statvfs("/")
        disk_free_mb = int((st.f_bavail * st.f_frsize) / (1024 * 1024))
    except Exception:
        pass

    # Version check: what the Cube is actually running (not the newest stored .apj)
    pix_version = cockpit_mgr.get_cube_version()

    ver_info = version_mgr.get_version_info()
    armed = False
    try:
        armed = cockpit_mgr.is_vehicle_armed()
    except Exception:
        pass

    return {
        "copter_online": solo_online,
        "controller_online": ctrl_online,
        "paired": ctrl_online,
        "status_text": "Paired & Connected" if ctrl_online else "Not Connected",
        "pixhawk_version": pix_version,
        "utility_version": ver_info.get("version", "1.0.1"),
        "iteration": ver_info.get("iteration", 1),
        "disk_free_mb": disk_free_mb,
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "armed": armed
    }


class SoloApiHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        """Suppress default verbose request logging."""
        pass

    def _send_json(self, data, status_code=200, cookie=None):
        body = json.dumps(data)
        if sys.version_info[0] >= 3:
            body = body.encode('utf-8')
        
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Auth-Token, Authorization')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message, status_code=400):
        self._send_json({"error": True, "message": message}, status_code=status_code)

    def _get_auth_token(self):
        token = None
        # 1. Check query string first (?token=...)
        if hasattr(self, 'path') and 'token=' in self.path:
            try:
                parsed = urlparse(self.path)
                q = parse_qs(parsed.query)
                t_list = q.get('token', [])
                if t_list and t_list[0].strip():
                    token = t_list[0].strip()
            except Exception:
                pass

        # 2. Check HTTP headers (X-Auth-Token, Authorization)
        if not token and hasattr(self, 'headers'):
            if hasattr(self.headers, 'getheader'):
                token = self.headers.getheader('X-Auth-Token') or self.headers.getheader('Authorization')
            if not token and hasattr(self.headers, 'get'):
                token = (self.headers.get('X-Auth-Token') or 
                         self.headers.get('x-auth-token') or 
                         self.headers.get('Authorization') or 
                         self.headers.get('authorization'))
            if not token and hasattr(self.headers, 'headers'):
                for h in self.headers.headers:
                    h_clean = h.strip()
                    if h_clean.lower().startswith('x-auth-token:'):
                        token = h_clean.split(':', 1)[1].strip()
                        break
                    elif h_clean.lower().startswith('authorization:'):
                        token = h_clean.split(':', 1)[1].strip()
                        break

        if token and token.lower().startswith('bearer '):
            token = token[7:].strip()

        # 3. Check Cookie (solo_session=...)
        if not token and hasattr(self, 'headers'):
            cookie_str = None
            if hasattr(self.headers, 'getheader'):
                cookie_str = self.headers.getheader('Cookie') or self.headers.getheader('cookie')
            if not cookie_str and hasattr(self.headers, 'get'):
                cookie_str = self.headers.get('Cookie') or self.headers.get('cookie')
            if cookie_str:
                for c in cookie_str.split(';'):
                    c = c.strip()
                    if c.startswith('solo_session='):
                        token = c.split('=', 1)[1].strip()
                        break

        return token.strip() if token else None

    def _is_authenticated(self):
        token = self._get_auth_token()
        return auth.validate_session(token)

    def _read_json_body(self):
        try:
            cl = self.headers.get('Content-Length') or self.headers.get('content-length')
            if not cl and hasattr(self.headers, 'getheader'):
                cl = self.headers.getheader('Content-Length') or self.headers.getheader('content-length')
            content_length = int(cl or 0)
            if content_length > 0:
                raw = self.rfile.read(content_length)
                if sys.version_info[0] >= 3 and isinstance(raw, bytes):
                    raw = raw.decode('utf-8', 'ignore')
                return json.loads(raw)
        except Exception:
            pass
        return {}

    def _handle_captive_portal(self, path):
        """Redirect client devices to the main utility portal when joining Wi-Fi."""
        host = self.headers.get('Host', '10.1.1.10')
        target_url = "http://%s/" % host
        redirect_html = """<!DOCTYPE html>
<html>
<head>
<meta http-equiv="refresh" content="0; url=%s">
<title>3DR Solo Web Utility</title>
</head>
<body>
<p>Redirecting to 3DR Solo Web Utility portal... <a href="%s">Click here</a></p>
<script>window.location.href="%s";</script>
</body>
</html>""" % (target_url, target_url, target_url)

        body = redirect_html.encode('utf-8') if sys.version_info[0] >= 3 else redirect_html
        self.send_response(302)
        self.send_header('Location', target_url)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Auth-Token, Authorization')
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Captive portal automatic browser detection
        if path.lower() in CAPTIVE_PORTAL_PATHS:
            self._handle_captive_portal(path)
            return

        # SoloLink Viewer app download. Stored as static/sololink_viewer.apk.bin because
        # every packaging step (image bake, installers, git) deliberately skips *.apk files.
        if path == VIEWER_APK_URL:
            self._serve_viewer_apk()
            return

        # 1. Static Files
        if not path.startswith('/api/'):
            self._serve_static(path)
            return

        # 2. Public API Status & Version
        if path == '/api/status':
            self._send_json(get_system_status())
            return

        if path == '/api/pair/status':
            self._send_json(pair_mgr.pair_engine.get_state())
            return

        if path == '/api/pair/check':
            self._send_json(pair_mgr.pair_engine.check_paired_status())
            return

        if path == '/api/version':
            self._send_json(version_mgr.get_version_info())
            return

        if path == '/api/auth/check':
            self._send_json({"authenticated": self._is_authenticated()})
            return

        # Firmware Update & APJ Builder API
        if path == '/api/firmware/check':
            try:
                wifi_mgr.ensure_copter_internet_route()
            except Exception:
                pass
            res = arducopter_builder.update_engine.check_version_quick()
            self._send_json(res)
            return

        if path == '/api/firmware/status':
            self._send_json(arducopter_builder.update_engine.get_state())
            return

        # Cockpit Telemetry & Live Video API
        if path == '/api/telemetry':
            self._send_json(cockpit_mgr.get_telemetry_dict())
            return

        if path == '/api/video/status':
            self._send_json(cockpit_mgr.get_video_status())
            return

        if path == '/api/video/h264':
            q = cockpit_mgr.video_manager.subscribe()
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4; codecs="avc1.42E01E"')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Connection', 'close')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            try:
                while True:
                    try:
                        chunk = q.get(timeout=2.0)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except Exception:
                        break
            except Exception:
                pass
            finally:
                cockpit_mgr.video_manager.unsubscribe(q)
            return

        # Require Auth for all other API endpoints
        if not self._is_authenticated():
            self._send_error("Authentication required.", 401)
            return

        # 3. Flashing State & Logs
        if path == '/api/flash/status':
            self._send_json(flash_mgr.flash_engine.get_state())
            return

        # Firmware choices for the flash page: latest, up to 3 previous, custom uploads
        if path == '/api/flash/firmware':
            cube_type = (query.get('cube_type') or ['solo'])[0]
            try:
                data = flash_mgr.list_firmware_choices(cube_type)
                data["success"] = True
                self._send_json(data)
            except Exception as e:
                self._send_error("Could not list firmware files: %s" % e, 500)
            return

        # Calibration State & Logs
        if path == '/api/calib/status':
            self._send_json(calib_mgr.calib_engine.get_state())
            return

        # 4. Wi-Fi Status & Scan
        if path == '/api/wifi/status':
            self._send_json(wifi_mgr.get_wifi_status())
            return

        if path == '/api/wifi/scan':
            self._send_json(wifi_mgr.scan_wifi_networks())
            return

        # 5. Parameters API
        if path == '/api/params':
            q = query.get('q', [''])[0]
            results = param_mgr.mgr.search_params(q)
            self._send_json({
                "count": len(results),
                "total": len(param_mgr.mgr.params),
                "params": results
            })
            return

        if path == '/api/params/presets':
            self._send_json({
                "presets": presets.get_all_presets()
            })
            return

        if path in ('/api/params/fetch-status', '/api/parameters/fetch-status'):
            self._send_json(param_mgr.fetch_progress.get_status())
            return

        if path == '/api/params/export':
            param_str = param_mgr.mgr.export_param_string()
            if sys.version_info[0] >= 3:
                body = param_str.encode('utf-8')
            else:
                body = param_str
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Content-Disposition', 'attachment; filename="solo.param"')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        if path == '/api/controller/definitions':
            self._send_json(controller_mgr.get_definitions())
            return

        if path == '/api/controller/config':
            self._send_json(controller_mgr.fetch_config())
            return

        self._send_error("Endpoint not found", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_json_body()

        # 1. Login Endpoint (Public)
        if path == '/api/auth/login':
            pwd = body.get('password', '')
            if auth.verify_password(pwd):
                token = auth.create_session()
                self._send_json({
                    "success": True,
                    "token": token,
                    "message": "Login successful."
                }, cookie='solo_session=%s; Path=/; Max-Age=86400' % token)
            else:
                self._send_error("Invalid password.", 401)
            return

        if path == '/api/pair/start':
            ssid = body.get('ssid', '')
            pwd = body.get('password', '')
            ok, msg = pair_mgr.pair_engine.start_pairing(ssid, pwd)
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        # Cockpit Action (Mode Change / Disarm / Arm)
        
        if path == '/api/video/restart':
            setattr(cockpit_mgr, 'force_reconnect_flag', True)
            self._send_json({"success": True, "message": "Video stream reconnecting..."})
            return

        if path == '/api/action':
            action = body.get('action', '')
            mode = body.get('mode', body.get('custom_mode', None))
            res = cockpit_mgr.handle_action(action, mode)
            self._send_json(res)
            return

        # Protected Endpoints Check
        if not self._is_authenticated():
            self._send_error("Authentication required.", 401)
            return

        # 2. Auth Endpoints
        if path == '/api/auth/logout':
            token = self._get_auth_token()
            auth.invalidate_session(token)
            self._send_json({"success": True, "message": "Logged out."})
            return

        if path == '/api/auth/change-password':
            old_p = body.get('old_password', '')
            new_p = body.get('new_password', '')
            ok, msg = auth.change_password(old_p, new_p)
            if ok:
                new_token = auth.create_session()
                self._send_json({"success": True, "token": new_token, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        # 3. Flashing Endpoints
        if path == '/api/flash/start':
            try:
                if cockpit_mgr.is_vehicle_armed():
                    self._send_error("SAFETY INTERLOCK: Firmware flashing is strictly prohibited while vehicle is ARMED!", 403)
                    return
            except Exception:
                pass
            cube_type = body.get('cube_type', 'solo')
            install_mode = body.get('install_mode', 'full')
            firmware_path = body.get('firmware') or None   # None = latest version
            ok, msg = flash_mgr.flash_engine.start_flash(cube_type, install_mode, firmware_path)
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        if path == '/api/flash/upload-apj':
            if flash_mgr.flash_engine.is_flashing:
                self._send_error("Flashing in progress; upload the file after it finishes.", 409)
                return
            try:
                choice = flash_mgr.save_custom_apj(body.get('filename', ''), body.get('content') or '')
                print("[flash] custom firmware uploaded: %s (board_id %s)" % (choice.get('file'), choice.get('board_id')))
                self._send_json({"success": True, "firmware": choice,
                                 "message": "Uploaded %s" % choice.get('file')})
            except ValueError as e:
                self._send_error("Not a usable firmware file: %s" % e, 400)
            except Exception as e:
                self._send_error("Upload failed: %s" % e, 500)
            return

        if path == '/api/flash/reset':
            flash_mgr.flash_engine.reset_state()
            self._send_json({"success": True, "message": "Flashing state reset."})
            return

        if path == '/api/firmware/update':
            try:
                if cockpit_mgr.is_vehicle_armed():
                    self._send_error("SAFETY INTERLOCK: Firmware update routine prohibited while vehicle is ARMED!", 403)
                    return
            except Exception:
                pass
            try:
                wifi_mgr.ensure_copter_internet_route()
            except Exception:
                pass
            force = bool(body.get('force', False))
            ok, msg = arducopter_builder.update_engine.start_update_routine(force=force)
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        # Calibration Endpoints
        if path == '/api/calib/imu/start':
            endpoint = body.get('endpoint', None)
            ok, msg = calib_mgr.calib_engine.start_imu(endpoint=endpoint)
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        if path == '/api/calib/imu/continue':
            ok, msg = calib_mgr.calib_engine.continue_imu()
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        if path == '/api/calib/compass/start':
            wait_comp = body.get('wait_completion', True)
            endpoint = body.get('endpoint', None)
            ok, msg = calib_mgr.calib_engine.start_compass(
                wait_completion=wait_comp,
                endpoint=endpoint
            )
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        if path == '/api/calib/reboot':
            endpoint = body.get('endpoint', None)
            ok, msg = calib_mgr.calib_engine.start_reboot(endpoint=endpoint)
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        if path == '/api/calib/start':
            cal_type = body.get('type', '')
            if cal_type == 'imu':
                imu_type = body.get('imu_type', 'simple')
                endpoint = body.get('endpoint', None)
                ok, msg = calib_mgr.calib_engine.start_imu(imu_type=imu_type, endpoint=endpoint)
            elif cal_type == 'compass':
                wait_comp = body.get('wait_completion', True)
                endpoint = body.get('endpoint', None)
                ok, msg = calib_mgr.calib_engine.start_compass(wait_completion=wait_comp, endpoint=endpoint)
            elif cal_type == 'reboot':
                endpoint = body.get('endpoint', None)
                ok, msg = calib_mgr.calib_engine.start_reboot(endpoint=endpoint)
            else:
                imu_type = body.get('imu_type', 'simple')
                wait_comp = body.get('wait_for_compass', False)
                endpoint = body.get('endpoint', None)
                ok, msg = calib_mgr.calib_engine.start_sequence(
                    imu_type=imu_type,
                    wait_for_compass=wait_comp,
                    endpoint=endpoint
                )
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        if path == '/api/calib/cancel':
            ok, msg = calib_mgr.calib_engine.cancel()
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        # 4. Wi-Fi Endpoints
        if path == '/api/wifi/connect':
            try:
                if cockpit_mgr.is_vehicle_armed():
                    wifi_mgr._log("connect refused: telemetry reports the vehicle ARMED")
                    self._send_error("SAFETY INTERLOCK: Connecting to home Wi-Fi is prohibited while vehicle is ARMED!", 403)
                    return
            except Exception:
                pass
            ssid = body.get('ssid', '').strip()
            pwd = body.get('password', '').strip()
            ok, msg = wifi_mgr.connect_home_wifi(ssid, pwd, body.get('security'))
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        if path == '/api/wifi/bidirectional':
            enable = body.get('enable', True)
            ok, msg = wifi_mgr.set_bidirectional(enable)
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        if path == '/api/wifi/reset-default':
            ok, msg = wifi_mgr.reset_to_default_ap()
            self._send_json({"success": True, "message": msg})
            return

        # 5. Versioning Endpoints
        if path == '/api/version/increment':
            desc = body.get('description', 'User iteration increment')
            new_v, it = version_mgr.increment_version(desc)
            self._send_json({
                "success": True,
                "version": new_v,
                "iteration": it,
                "message": "Version incremented to %s (Iteration %d)" % (new_v, it)
            })
            return

        # 7. Parameters Endpoints
        if path == '/api/params/save':
            param_dict = body.get('params', {})
            if not param_dict:
                name = body.get('name')
                val = body.get('value')
                if name:
                    param_dict[name] = val
            
            if not param_dict:
                self._send_error("No parameters provided to save.", 400)
                return

            ok, msg = param_mgr.mgr.set_batch_params(param_dict)
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error(msg, 400)
            return

        if path == '/api/params/apply-preset':
            preset_id = body.get('preset_id', '')
            preset = presets.get_preset(preset_id)
            if not preset:
                self._send_error("Preset '%s' not found." % preset_id, 404)
                return

            ok, msg = param_mgr.mgr.set_batch_params(preset['params'])
            result = getattr(param_mgr.mgr, "last_batch_result", None) or {}
            if ok:
                self._send_json({
                    "success": True,
                    "message": "Preset '%s': %s" % (preset['name'], msg),
                    "preset_id": preset_id,
                    # the values the Cube confirmed, under the names it actually has
                    "params": result.get("confirmed", preset['params'])
                })
            else:
                self._send_json({
                    "error": True,
                    "message": "Preset '%s': %s" % (preset['name'], msg),
                    "preset_id": preset_id,
                    "params": result.get("confirmed", {}),
                    "failed": result.get("failed", {})
                }, status_code=400)
            return

        if path == '/api/params/import':
            content = body.get('content', '')
            temp_file = os.path.join(BASE_DIR, "temp_import.param")
            try:
                with open(temp_file, "w") as f:
                    f.write(content)
                ok, msg = param_mgr.mgr.load_from_file(temp_file)
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                if ok:
                    self._send_json({"success": True, "message": msg})
                else:
                    self._send_error(msg, 400)
            except Exception as e:
                self._send_error(str(e), 500)
            return

        if path == '/api/params/fetch-live':
            ok, msg = param_mgr.mgr.fetch_live_mavlink()
            if ok:
                self._send_json({"success": True, "message": msg})
            else:
                self._send_error("MAVLink fetch failed: " + msg, 400)
        # 6. Controller Setup Endpoints
        if path == '/api/controller/config':
            raw_text = body.get('raw_text')
            mappings = body.get('mappings')
            time_offset = body.get('timeOffsetHrs', 'None')
            res = controller_mgr.save_config(raw_text=raw_text, mappings=mappings, time_offset=time_offset)
            self._send_json(res)
            return

        if path == '/api/controller/reset':
            res = controller_mgr.reset_to_default()
            self._send_json(res)
            return

        self._send_error("Endpoint not found", 404)

    def _serve_viewer_apk(self):
        try:
            with open(VIEWER_APK_FILE, 'rb') as f:
                content = f.read()
        except Exception:
            self._send_error("SoloLink Viewer app is not included in this installation.", 404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/vnd.android.package-archive')
        self.send_header('Content-Disposition', 'attachment; filename="sololink_viewer.apk"')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(content)

    def _serve_static(self, path):
        if path in ['/', '']:
            path = '/index.html'

        clean_path = path.lstrip('/')
        if clean_path.startswith('static/'):
            clean_path = clean_path[7:]
        filepath = os.path.join(STATIC_DIR, clean_path)

        if not os.path.exists(filepath) or os.path.isdir(filepath):
            filepath = os.path.join(STATIC_DIR, "index.html")

        mime_type, _ = mimetypes.guess_type(filepath)
        if not mime_type:
            mime_type = 'application/octet-stream'

        try:
            with open(filepath, 'rb') as f:
                content = f.read()

            self.send_response(200)
            self.send_header('Content-Type', mime_type)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self._send_error("Failed to read file: " + str(e), 500)


def run_server(port=PORT):
    chosen_port = port
    server = None
    bind_host = '0.0.0.0'
    max_retries = 60
    retry_delay = 2
    
    # Strictly bind to '0.0.0.0' (never explicitly 10.1.1.10) with retry loop for boot timing
    for attempt in range(max_retries):
        try:
            server = ThreadedHTTPServer((bind_host, chosen_port), SoloApiHandler)
            break
        except (socket.error, Exception) as e:
            print("[!] Port %d busy or network not ready (%s), retrying in %ds (%d/%d)..." % (
                chosen_port, str(e), retry_delay, attempt + 1, max_retries
            ))
            try:
                os.system("fuser -k %d/tcp 2>/dev/null || true" % chosen_port)
            except Exception:
                pass
            time.sleep(retry_delay)

    if not server:
        print("[CRITICAL] Could not bind to port %d after %d retries." % (chosen_port, max_retries))
        sys.exit(1)

    # Start background telemetry and video services
    try:
        cockpit_mgr.start_services()
    except Exception as e:
        print("[!] Warning: Could not start cockpit services: %s" % e)

    # Safety: drop the Controller's home Wi-Fi link as soon as the vehicle arms
    try:
        wifi_mgr.start_arm_interlock(cockpit_mgr.is_vehicle_armed)
    except Exception as e:
        print("[!] Warning: Could not start Wi-Fi arm interlock: %s" % e)

    cur_ver = version_mgr.get_version()
    print("=" * 65)
    print("[*] 3DR SOLO WEB UTILITY SERVER STARTED (v%s)" % cur_ver)
    print("=" * 65)
    print("  * Web Interface: http://localhost:%d" % chosen_port)
    print("  * Solo Network:  http://10.1.1.10:%d" % chosen_port)
    print("=" * 65)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Stopping web server...")
        server.server_close()


if __name__ == '__main__':
    p = PORT
    if len(sys.argv) > 1:
        try:
            p = int(sys.argv[1])
        except ValueError:
            pass
    run_server(p)
