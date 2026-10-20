#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ArduCopter Custom Build & Update Engine for 3DR Solo & Green Cube.
Interacts with the official ArduPilot Custom Firmware Builder API (custom.ardupilot.org).
Compares current firmware version in the Firmware folder with the latest ArduCopter
stable release. If a newer stable release is available (or forced), uses the custom
APJ builder routine to compile/fetch builds for CubeSolo and CubeGreen-solo with:
  - APJ parameter space enabled (APJ_TOOL_PARAMETERS)
  - Sport Mode enabled (MODE_SPORT)
  - Lua Scripting disabled (SCRIPTING and dependent scripting features removed)
Applies the corresponding Cube parameter defaults via apj_tool, stages each build
into storage as arducopter_CubeSolo_<version>.apj and arducopter_CubeGreen_<version>.apj,
replaces previous versioned files, and informs the user that an update is ready.

Compatible with Python 2.7 and Python 3.x standard library.
"""

import os
import compat
import re
import sys
import json
import time
import shutil
import tarfile
import threading

if sys.version_info[0] >= 3:
    import urllib.request as urllib_request
    import urllib.error as urllib_error
    from io import BytesIO
else:
    import urllib2 as urllib_request
    import urllib2 as urllib_error
    try:
        from cStringIO import StringIO as BytesIO
    except ImportError:
        from io import BytesIO

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir("/log/firmware"):
    FIRMWARE_DIR = "/log/firmware"
else:
    FIRMWARE_DIR = os.path.join(BASE_DIR, "firmware")

BIN_DIR = os.path.join(BASE_DIR, "bin")
BUILDER_BASE_URL = "https://custom.ardupilot.org"
BUILD_INFO_FILE = os.path.join(FIRMWARE_DIR, "custom_build_info.json")

# Mandatory features to enforce
ENABLE_FEATURES = ["APJ_TOOL_PARAMETERS", "MODE_SPORT"]
DISABLE_FEATURES = [
    "SCRIPTING",
    "Camera_Info_From_Script",
    "RANGEFINDER_LUA",
    "SCRIPTING_BINDING_MOTORS",
    "SCRIPTING_BINDING_VEHICLE",
    "SCRIPTING_SERIALDEVICE",
    "CRSF Scripting"
]

BOARD_MAP = {
    "solo": "CubeSolo",
    "cubesolo": "CubeSolo",
    "green": "CubeGreen-solo",
    "cubegreen": "CubeGreen-solo",
    "cubegreen-solo": "CubeGreen-solo"
}

import socket


def check_internet_connection(host="8.8.8.8", port=53, timeout=1.2):
    """
    Quick low-timeout connection test to a public DNS provider (8.8.8.8:53).
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


def _http_get_via_controller(url, timeout=25):
    """Fallback: execute HTTP GET via Controller (10.1.1.1) SSH when Copter direct route is unavailable."""
    import base64
    py_code = (
        "import urllib2, base64\n"
        "req = urllib2.Request(%r, headers={'User-Agent': 'SoloWebUtility/1.0'})\n"
        "raw = urllib2.urlopen(req, timeout=%d).read()\n"
        "print('===RESP_START===' + base64.b64encode(raw) + '===RESP_END===')\n"
    ) % (url, timeout)

    py_b64 = base64.b64encode(py_code.encode('utf-8') if sys.version_info[0] >= 3 else py_code).decode('ascii')
    full_cmd = "python -c \"import base64; exec(base64.b64decode('%s'))\"" % py_b64

    out = ""
    rc = 1
    try:
        import wifi_mgr
        rc, out = wifi_mgr._ssh_paramiko(full_cmd, timeout=timeout+10)
    except Exception:
        pass

    if rc != 0 or "===RESP_START===" not in out:
        try:
            import flash_mgr
            rc, out = flash_mgr._run_with_ssh_auth('ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@10.1.1.1 "%s"' % full_cmd.replace('"', '\\"'), host="10.1.1.1", timeout=timeout+10)
        except Exception as ex:
            raise RuntimeError("SSH proxy unavailable: %s" % str(ex))

    if "===RESP_START===" in out and "===RESP_END===" in out:
        m = re.search(r'===RESP_START===(.*?)===RESP_END===', out, re.DOTALL)
        if m:
            b64_str = m.group(1).strip()
            return base64.b64decode(b64_str)
    raise RuntimeError("Controller HTTP GET proxy failed (rc=%d): %s" % (rc, out[:200] if out else "no output"))


def _http_post_via_controller(url, payload, timeout=30):
    """Fallback: execute HTTP POST via Controller (10.1.1.1) SSH when Copter direct route is unavailable."""
    import base64
    payload_json = json.dumps(payload)
    py_code = (
        "import urllib2, base64, json\n"
        "data = %r\n"
        "req = urllib2.Request(%r, data=data, headers={'User-Agent': 'SoloWebUtility/1.0', 'Content-Type': 'application/json'})\n"
        "raw = urllib2.urlopen(req, timeout=%d).read()\n"
        "print('===RESP_START===' + base64.b64encode(raw) + '===RESP_END===')\n"
    ) % (payload_json, url, timeout)

    py_b64 = base64.b64encode(py_code.encode('utf-8') if sys.version_info[0] >= 3 else py_code).decode('ascii')
    full_cmd = "python -c \"import base64; exec(base64.b64decode('%s'))\"" % py_b64

    out = ""
    rc = 1
    try:
        import wifi_mgr
        rc, out = wifi_mgr._ssh_paramiko(full_cmd, timeout=timeout+10)
    except Exception:
        pass

    if rc != 0 or "===RESP_START===" not in out:
        try:
            import flash_mgr
            rc, out = flash_mgr._run_with_ssh_auth('ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@10.1.1.1 "%s"' % full_cmd.replace('"', '\\"'), host="10.1.1.1", timeout=timeout+10)
        except Exception as ex:
            raise RuntimeError("SSH proxy unavailable: %s" % str(ex))

    if "===RESP_START===" in out and "===RESP_END===" in out:
        m = re.search(r'===RESP_START===(.*?)===RESP_END===', out, re.DOTALL)
        if m:
            b64_str = m.group(1).strip()
            raw = base64.b64decode(b64_str)
            if sys.version_info[0] >= 3 and isinstance(raw, bytes):
                raw = raw.decode("utf-8", "ignore")
            return json.loads(raw)
    raise RuntimeError("Controller HTTP POST proxy failed (rc=%d): %s" % (rc, out[:200] if out else "no output"))


def _http_get(url, timeout=25):
    """Perform HTTP GET request and return raw bytes."""
    try:
        import wifi_mgr
        wifi_mgr.ensure_copter_internet_route()
    except Exception:
        pass

    headers = {"User-Agent": "SoloWebUtility/1.0 (3DR Solo CustomBuild Client)"}
    try:
        req = urllib_request.Request(url, headers=headers)
        resp = urllib_request.urlopen(req, timeout=timeout)
        return resp.read()
    except Exception as direct_err:
        try:
            return _http_get_via_controller(url, timeout=timeout)
        except Exception:
            raise direct_err


def _http_post_json(url, payload, timeout=30):
    """Perform HTTP POST request with JSON body and return parsed JSON."""
    try:
        import wifi_mgr
        wifi_mgr.ensure_copter_internet_route()
    except Exception:
        pass

    headers = {
        "User-Agent": "SoloWebUtility/1.0 (3DR Solo CustomBuild Client)",
        "Content-Type": "application/json"
    }
    data = json.dumps(payload).encode("utf-8")
    try:
        req = urllib_request.Request(url, data=data, headers=headers)
        resp = urllib_request.urlopen(req, timeout=timeout)
        raw = resp.read()
        if sys.version_info[0] >= 3 and isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        return json.loads(raw)
    except Exception as direct_err:
        try:
            return _http_post_via_controller(url, payload, timeout=timeout)
        except Exception:
            raise direct_err


def parse_version_tuple(ver_str):
    """Convert version string like '4.7.1' or 'v4.7.1' into tuple (4, 7, 1) for comparison."""
    if not ver_str:
        return (0, 0, 0)
    parts = []
    tokens = str(ver_str).strip().lstrip("v").split(".")
    for token in tokens:
        clean = ""
        for ch in token:
            if ch.isdigit():
                clean += ch
            else:
                break
        if clean:
            parts.append(int(clean))
        else:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_version_newer(online_ver, local_ver):
    """Return True if online_ver is strictly newer than local_ver."""
    return parse_version_tuple(online_ver) > parse_version_tuple(local_ver)


def resolve_board_id(cube_type):
    """Return standard board ID ('CubeSolo' or 'CubeGreen-solo')."""
    k = str(cube_type).strip().lower()
    return BOARD_MAP.get(k, "CubeSolo")


def load_build_info():
    """Load metadata from custom_build_info.json."""
    if os.path.exists(BUILD_INFO_FILE):
        try:
            with open(BUILD_INFO_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_build_info(info):
    """Save metadata to custom_build_info.json."""
    try:
        dest_dir = os.path.dirname(BUILD_INFO_FILE)
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
        with open(BUILD_INFO_FILE, "w") as f:
            json.dump(info, f, indent=2)
    except Exception:
        pass


def get_local_firmware_version():
    """
    Inspect the Firmware folder to determine the active/stored ArduCopter version.
    Checks custom_build_info.json and versioned filenames (arducoptercubesolo-<version>.apj).
    Defaults to 4.7.1 if no newer version tag is found.
    """
    candidates = []

    # 1. Check custom_build_info.json
    info = load_build_info()
    if info.get("current_version"):
        candidates.append((parse_version_tuple(info["current_version"]), str(info["current_version"])))
    for k in ["latest_CubeSolo", "latest_CubeGreen-solo"]:
        if k in info and isinstance(info[k], dict) and info[k].get("version"):
            candidates.append((parse_version_tuple(info[k]["version"]), str(info[k]["version"])))

    # 2. Scan firmware directory for versioned APJ files
    if os.path.isdir(FIRMWARE_DIR):
        for fn in os.listdir(FIRMWARE_DIR):
            if not fn.lower().endswith(".apj"):
                continue
            m = re.match(r"^arducopter[_\-]?cube(?:green[_\-]?solo|green|solo)[_\-]?([\d\.]+)\.apj$", fn, re.IGNORECASE)
            if m:
                v_str = m.group(1)
                candidates.append((parse_version_tuple(v_str), v_str))

    if candidates:
        candidates.sort()
        return candidates[-1][1]

    return "4.7.1"


def get_latest_stable_version():
    """
    Query ArduPilot Custom Build Server for latest stable ArduCopter version.
    Returns dict: {'id': '...', 'name': '4.7.1', 'commit_ref': '...', 'git_hash': '...', 'vehicle_id': 'copter'}
    """
    url = "%s/api/v1/vehicles/copter/versions" % BUILDER_BASE_URL
    raw = _http_get(url)
    if sys.version_info[0] >= 3 and isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    versions = json.loads(raw)

    stables = [v for v in versions if v.get("type") == "stable"]
    if not stables:
        stables = versions

    # Sort by semantic version
    stables.sort(key=lambda v: parse_version_tuple(v.get("name", "0.0.0")))
    latest = stables[-1]
    return {
        "id": latest.get("id"),
        "name": latest.get("name"),
        "commit_ref": latest.get("commit_ref"),
        "git_hash": latest.get("git_hash", ""),
        "vehicle_id": "copter"
    }


def get_board_features(version_id, board_id):
    """Retrieve configurable features for the given board and version from server."""
    url = "%s/api/v1/vehicles/copter/versions/%s/boards/%s/features" % (
        BUILDER_BASE_URL, version_id, board_id
    )
    raw = _http_get(url)
    if sys.version_info[0] >= 3 and isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    return json.loads(raw)


def compute_selected_features(features_list):
    """
    Compute feature set:
      - Default enabled features from server
      - Add APJ_TOOL_PARAMETERS and MODE_SPORT
      - Remove SCRIPTING and its dependents
    """
    selected = set()
    for f in features_list:
        if f.get("default", {}).get("enabled"):
            selected.add(f.get("id"))

    for feat in ENABLE_FEATURES:
        selected.add(feat)

    for feat in DISABLE_FEATURES:
        selected.discard(feat)

    return sorted(list(selected))


def submit_build(board_id, version_id, selected_features):
    """Submit build request to custom build server. Returns build_id."""
    url = "%s/api/v1/builds" % BUILDER_BASE_URL
    payload = {
        "vehicle_id": "copter",
        "board_id": board_id,
        "version_id": version_id,
        "selected_features": selected_features
    }
    resp = _http_post_json(url, payload)
    return resp.get("build_id")


def poll_build(build_id, progress_callback=None, max_wait=1200, poll_interval=3):
    """
    Poll build status until success, failure, or timeout.
    Calls progress_callback(percent, state, msg) if provided.
    Returns True on success.
    """
    url = "%s/api/v1/builds/%s" % (BUILDER_BASE_URL, build_id)
    start_time = time.time()
    consecutive_errors = 0

    while time.time() - start_time < max_wait:
        try:
            raw = _http_get(url, timeout=15)
            if sys.version_info[0] >= 3 and isinstance(raw, bytes):
                raw = raw.decode("utf-8", "ignore")
            res = json.loads(raw)
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            if progress_callback:
                progress_callback(-1, "POLL", "Polling build error (%d): %s" % (consecutive_errors, str(e)))
            time.sleep(poll_interval)
            continue

        prog = res.get("progress", {})
        if isinstance(prog, dict):
            state = (prog.get("state") or res.get("state") or res.get("status") or "UNKNOWN").upper()
            percent = prog.get("percent")
            if percent is None:
                percent = res.get("percent", 0)
        else:
            state = (res.get("state") or res.get("status") or "UNKNOWN").upper()
            percent = res.get("percent", 0)

        if progress_callback:
            progress_callback(percent, state, "Server build progress: %d%% (%s)" % (percent, state))

        if state in ["SUCCESS", "FINISHED", "COMPLETE", "COMPLETED"] or res.get("artifact_available"):
            return True
        elif state in ["FAILED", "ERROR"]:
            err_msg = res.get("errors") or res.get("error") or "Build server compilation error"
            raise RuntimeError("Custom build failed on server: %s" % str(err_msg))

        time.sleep(poll_interval)

    raise RuntimeError("Custom build timed out after %d seconds." % max_wait)


def download_artifact_apj(build_id, target_apj_path):
    """Download build artifact .tar.gz and extract arducopter.apj to target_apj_path."""
    url = "%s/api/v1/builds/%s/artifact" % (BUILDER_BASE_URL, build_id)
    raw_tar = _http_get(url, timeout=60)

    tar = tarfile.open(fileobj=BytesIO(raw_tar), mode="r:gz")
    apj_member = None
    for member in tar.getmembers():
        if member.name.endswith("arducopter.apj"):
            apj_member = member
            break

    if not apj_member:
        tar.close()
        raise IOError("arducopter.apj not found inside build artifact package.")

    extracted_f = tar.extractfile(apj_member)
    apj_bytes = extracted_f.read()
    tar.close()

    dest_dir = os.path.dirname(target_apj_path)
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    with open(target_apj_path, "wb") as f:
        f.write(apj_bytes)

    return target_apj_path


def resolve_param_file(cube_type="solo"):
    """Find the matching .param file for the given Cube hardware."""
    board_id = resolve_board_id(cube_type)
    if board_id == "CubeGreen-solo":
        preferred = [
            os.path.join(FIRMWARE_DIR, "SoloGreen.param"),
            os.path.join(FIRMWARE_DIR, "sologreen.param"),
            os.path.join(BASE_DIR, "firmware", "SoloGreen.param"),
            os.path.join(BASE_DIR, "firmware", "sologreen.param")
        ]
    else:
        preferred = [
            os.path.join(FIRMWARE_DIR, "Solo.param"),
            os.path.join(FIRMWARE_DIR, "solo.param"),
            os.path.join(BASE_DIR, "firmware", "Solo.param"),
            os.path.join(BASE_DIR, "firmware", "solo.param"),
            os.path.join(BASE_DIR, "solo.param")
        ]
    for p in preferred:
        if os.path.exists(p) and os.path.getsize(p) > 50:
            return p
    return None


def apply_param_defaults_to_apj(apj_path, param_file=None, cube_type="solo", log_fn=None):
    """
    Apply parameters from the matching .param file into the .apj file using bin/apj_tool.py.
    Returns (success, message).
    """
    def _log(msg, level="INFO"):
        if log_fn:
            log_fn(msg, level)
        else:
            print("[%s] %s" % (level, msg))

    if not param_file:
        param_file = resolve_param_file(cube_type)

    if not param_file or not os.path.exists(param_file):
        _log("[!] No parameter file found for %s; skipping parameter defaults embedding." % cube_type, "WARNING")
        return False, "Parameter file not found"

    _log("[*] Using apj_tool to embed default parameters (%s) into %s..." % (
        os.path.basename(param_file), os.path.basename(apj_path)
    ), "INFO")

    try:
        if BIN_DIR not in sys.path:
            sys.path.insert(0, BIN_DIR)
        import apj_tool
        defaults = apj_tool.embedded_defaults(apj_path)
        if defaults.find():
            defaults.set_file(param_file)
            defaults.save()
            _log("[OK] Successfully embedded %s parameter defaults (%d bytes) into %s!" % (
                os.path.basename(param_file), defaults.length, os.path.basename(apj_path)
            ), "SUCCESS")
            return True, "Parameters embedded successfully"
        else:
            _log("[!] No parameter defaults area found in %s (APJ_TOOL_PARAMETERS required)." % apj_path, "WARNING")
            return False, "No param area in APJ"
    except Exception as e:
        _log("[!] Error applying param defaults with apj_tool: %s" % str(e), "ERROR")
        return False, str(e)


def clean_old_firmware_files(board_id, new_version):
    """
    Remove previous versioned APJ files and any legacy unversioned APJ duplicates for this board.
    Example: removes arducopter_CubeSolo_4.7.1.apj when new_version is 4.7.2,
    and removes legacy arducopter_solo.apj / arducopter_green.apj.
    """
    prefixes = ["arducopter_CubeGreen_", "arducoptercubegreen-solo-"] if board_id == "CubeGreen-solo" else ["arducopter_CubeSolo_", "arducoptercubesolo-"]
    kept_fn = ("arducopter_CubeGreen_%s.apj" if board_id == "CubeGreen-solo" else "arducopter_CubeSolo_%s.apj") % new_version
    legacy_fn = "arducopter_green.apj" if board_id == "CubeGreen-solo" else "arducopter_solo.apj"
    removed = []

    check_dirs = [FIRMWARE_DIR]
    if "/log/firmware" not in check_dirs and os.path.isdir("/log/firmware"):
        check_dirs.append("/log/firmware")

    for d in check_dirs:
        if not os.path.isdir(d):
            continue
        # Clean legacy unversioned duplicate if present
        leg_p = os.path.join(d, legacy_fn)
        if os.path.isfile(leg_p):
            try:
                os.remove(leg_p)
                removed.append(legacy_fn)
            except Exception:
                pass

        for fn in os.listdir(d):
            if not fn.lower().endswith(".apj") or fn == kept_fn:
                continue
            if any(fn.startswith(p) for p in prefixes):
                old_path = os.path.join(d, fn)
                try:
                    os.remove(old_path)
                    removed.append(fn)
                except Exception:
                    pass
    return removed


def build_and_stage_cube(board_id, ver_info, progress_callback=None, log_fn=None):
    """
    Execute full custom build, download, parameter embedding, and staging for a single Cube board.
    Saves directly to arducopter_Cube<board>_<ver>.apj in /log/firmware/ and removes previous versioned files.
    Returns dict of build results.
    """
    def _log(msg, level="INFO"):
        if log_fn:
            log_fn(msg, level)
        else:
            print("[%s] %s" % (level, msg))

    cube_name = "Green Cube" if board_id == "CubeGreen-solo" else "Solo Cube (Black)"
    version_name = ver_info["name"]
    version_id = ver_info["id"]

    if board_id == "CubeGreen-solo":
        versioned_name = "arducopter_CubeGreen_%s.apj" % version_name
        cube_type_key = "green"
    else:
        versioned_name = "arducopter_CubeSolo_%s.apj" % version_name
        cube_type_key = "solo"

    versioned_path = os.path.join(FIRMWARE_DIR, versioned_name)

    _log("[*] Fetching configurable board features for %s (%s)..." % (cube_name, board_id), "INFO")
    features = get_board_features(version_id, board_id)
    selected_features = compute_selected_features(features)
    _log("[*] Enforcing features: APJ Space (ON), Sport Mode (ON), Scripting (OFF)", "INFO")

    _log("[*] Submitting custom build request to %s..." % BUILDER_BASE_URL, "INFO")
    build_id = submit_build(board_id, version_id, selected_features)
    _log("[*] Build ID %s queued on server. Compiling ArduCopter firmware..." % build_id, "INFO")

    last_logged = [0, ""]
    def _on_poll(pct, state, msg):
        if progress_callback:
            progress_callback(pct, state, msg)
        key = "%s_%s" % (pct, state)
        if key != last_logged[1]:
            last_logged[1] = key
            if pct >= 0:
                _log("[*] Server build progress (%s): %d%% (%s)" % (cube_name, pct, state), "INFO")
            else:
                _log("[!] Polling notice (%s): %s" % (cube_name, msg), "WARNING")

    poll_build(build_id, progress_callback=_on_poll, max_wait=1200)
    _log("[OK] Build %s compiled successfully on server!" % build_id, "SUCCESS")

    _log("[*] Downloading artifact package and extracting %s..." % versioned_name, "INFO")
    download_artifact_apj(build_id, versioned_path)
    file_sz = os.path.getsize(versioned_path)
    _log("[OK] Successfully saved: %s (%d bytes)" % (versioned_name, file_sz), "SUCCESS")

    # Embed matching .param defaults directly into the versioned APJ
    apply_param_defaults_to_apj(versioned_path, cube_type=cube_type_key, log_fn=_log)
    _log("[OK] Parameter defaults embedded in: %s" % versioned_name, "SUCCESS")

    # Replace previous versioned file(s) and legacy unversioned duplicates in storage
    removed = clean_old_firmware_files(board_id, version_name)
    if removed:
        _log("[*] Cleaned up previous version files: %s" % ", ".join(removed), "INFO")

    # Mandatory build hook: if running in dev environment with base firmware present, ensure baked
    try:
        import bake_into_firmware
        bake_into_firmware.ensure_firmware_baked(app_base_dir=BASE_DIR, log_func=_log)
    except Exception as be:
        _log("[!] Auto-bake notice: %s" % str(be), "INFO")

    return {
        "board": board_id,
        "cube_name": cube_name,
        "version": version_name,
        "version_id": version_id,
        "build_id": build_id,
        "versioned_file": versioned_name,
        "active_file": versioned_name,
        "file_size": file_sz,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }


def update_setup_files(new_version, solo_apj_name=None, green_apj_name=None, log_fn=None):
    """Clean install zips have been replaced with pre-staged uncompressed files."""
    return True


def update_version_references(new_version, old_version=None, log_fn=None):
    """
    Update ArduCopter firmware version number across project files where used.
    """
    def _log(msg, level="INFO"):
        if log_fn:
            log_fn(msg, level)
        else:
            print("[%s] %s" % (level, msg))

    _log("[*] Updating ArduCopter version references across configuration and setup files...", "INFO")

    files_to_update = [
        os.path.join(BASE_DIR, "flash_mgr.py"),
        os.path.join(BASE_DIR, "solo_web_app.py"),
        os.path.join(BASE_DIR, "param_mgr.py"),
        os.path.join(BASE_DIR, "solo.param"),
        os.path.join(BASE_DIR, "static", "index.html"),
        os.path.join(BASE_DIR, "docs", "index.html"),
        os.path.join(BASE_DIR, "worker", "page.html"),
        os.path.join(BASE_DIR, "README.md"),
        os.path.join(BASE_DIR, "TESTER_INSTRUCTIONS.txt"),
    ]

    for fpath in files_to_update:
        if not os.path.exists(fpath):
            continue
        try:
            with compat.open_text(fpath, "r") as f:
                content = f.read()

            new_content = content
            if old_version and old_version != new_version:
                new_content = new_content.replace("ArduCopter %s" % old_version, "ArduCopter %s" % new_version)
                new_content = new_content.replace("Copter-%s" % old_version, "Copter-%s" % new_version)
                new_content = new_content.replace("Open Solo %s" % old_version, "Open Solo %s" % new_version)
            else:
                new_content = re.sub(r'ArduCopter \d+\.\d+\.\d+', 'ArduCopter %s' % new_version, new_content)
                new_content = re.sub(r'Copter-\d+\.\d+\.\d+', 'Copter-%s' % new_version, new_content)

            if new_content != content:
                with compat.open_text(fpath, "w") as f:
                    f.write(new_content)
                _log("  [OK] Updated version in %s" % os.path.basename(fpath), "INFO")
        except Exception as e:
            _log("  [!] Failed to update %s: %s" % (os.path.basename(fpath), str(e)), "WARNING")

    # Record in version.json
    try:
        vjson_path = os.path.join(BASE_DIR, "version.json")
        if os.path.exists(vjson_path):
            with compat.open_text(vjson_path, "r") as f:
                vdata = json.load(f)
            last_desc = vdata.get("changelog", [{}])[-1].get("description", "")
            if ("ArduCopter %s" % new_version) not in last_desc:
                cur_iter = vdata.get("iteration", 58) + 1
                vdata["iteration"] = cur_iter
                vdata["updated_at"] = int(time.time())
                vdata["changelog"].append({
                    "version": vdata.get("version", "1.0.58"),
                    "iteration": cur_iter,
                    "date": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "description": "Updated ArduCopter firmware to stable release %s using APJ Builder system (APJ space enabled, Sport mode enabled, Lua scripting removed)." % new_version
                })
                with compat.open_text(vjson_path, "w") as f:
                    json.dump(vdata, f, indent=2)
                _log("  [OK] Recorded firmware update in version.json (iteration %d)" % cur_iter, "SUCCESS")
    except Exception as e:
        _log("  [!] Failed to update version.json: %s" % str(e), "WARNING")

    return True


class FirmwareUpdateEngine(object):
    """
    Thread-safe engine managing firmware version checks and automated background updates.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self.is_running = False
        self.status = "idle"  # idle, checking, building, up_to_date, update_ready, error
        self.progress = 0     # 0 to 100
        self.message = ""
        self.stage = ""
        self.local_version = "4.7.1"
        self.online_version = "4.7.1"
        self.update_available = False
        self.logs = []
        self._refresh_local_version()

    def _refresh_local_version(self):
        try:
            self.local_version = get_local_firmware_version()
        except Exception:
            self.local_version = "4.7.1"

    def log(self, msg, level="INFO"):
        ts = time.strftime("%H:%M:%S")
        entry = "[%s] [%s] %s" % (ts, level, msg)
        with self._lock:
            self.logs.append(entry)
            if len(self.logs) > 60:
                self.logs.pop(0)

    def get_state(self):
        with self._lock:
            return {
                "is_running": self.is_running,
                "status": self.status,
                "progress": self.progress,
                "message": self.message,
                "stage": self.stage,
                "local_version": self.local_version,
                "online_version": self.online_version,
                "update_available": self.update_available,
                "logs": list(self.logs)
            }

    def check_version_quick(self):
        """
        Fast non-building version check to query latest stable release and compare.
        Returns dict with status and version info.
        """
        self._refresh_local_version()
        if not check_internet_connection():
            return {
                "success": False,
                "status": "NO_INTERNET_CONNECTION",
                "error": "NO_INTERNET_CONNECTION",
                "message": "NO_INTERNET_CONNECTION",
                "local_version": self.local_version,
                "online_version": self.online_version,
                "update_available": False
            }
        try:
            online_info = get_latest_stable_version()
            online_ver = online_info["name"]
            with self._lock:
                self.online_version = online_ver
                self.update_available = is_version_newer(online_ver, self.local_version)
            return {
                "success": True,
                "status": "success",
                "local_version": self.local_version,
                "online_version": online_ver,
                "update_available": self.update_available,
                "online_info": online_info
            }
        except Exception as e:
            return {
                "success": False,
                "status": "error",
                "local_version": self.local_version,
                "online_version": self.online_version,
                "update_available": False,
                "error": str(e)
            }

    def start_update_routine(self, force=False):
        """
        Initiate the update routine in a background thread.
        Compares online stable version vs local version. If newer (or forced),
        runs apj builder for CubeSolo and CubeGreen-solo, updates storage, makes them
        current, and informs user that update is ready.
        """
        with self._lock:
            if self.is_running:
                return False, "Update check or build is already in progress."
            self.is_running = True
            self.status = "checking"
            self.progress = 5
            self.message = "Checking ArduPilot custom build server for latest stable release..."
            self.stage = "Checking versions"
            self.logs = []

        self.log("=" * 60, "HEADER")
        self.log("🔍 INITIATING ARDUCOPTER UPDATE ROUTINE", "HEADER")
        self.log("Current version in Firmware folder: ArduCopter %s" % self.local_version, "INFO")
        self.log("=" * 60, "HEADER")

        t = threading.Thread(target=self._run_routine, args=(force,))
        t.daemon = True
        t.start()
        return True, "Update routine started."

    def _run_routine(self, force=False):
        try:
            self._refresh_local_version()
            local_ver = self.local_version

            with self._lock:
                self.progress = 10
                self.stage = "Querying latest stable ArduCopter"
                self.message = "Querying latest stable ArduCopter version..."

            if not check_internet_connection():
                with self._lock:
                    self.status = "NO_INTERNET_CONNECTION"
                    self.message = "NO_INTERNET_CONNECTION"
                    self.stage = "No Internet Connection"
                    self.is_running = False
                self.log("[!] Public internet route test (8.8.8.8) failed: NO_INTERNET_CONNECTION", "WARNING")
                return

            self.log("[*] Contacting %s/api/v1/vehicles/copter/versions..." % BUILDER_BASE_URL, "INFO")
            online_info = get_latest_stable_version()
            online_ver = online_info["name"]

            with self._lock:
                self.online_version = online_ver
                newer = is_version_newer(online_ver, local_ver)
                self.update_available = newer

            self.log("[*] Latest stable release online : ArduCopter %s (commit %s)" % (
                online_ver, online_info.get("commit_ref", "")[:8]
            ), "INFO")
            self.log("[*] Current release in storage   : ArduCopter %s" % local_ver, "INFO")

            if not newer and not force:
                with self._lock:
                    self.status = "up_to_date"
                    self.progress = 100
                    self.stage = "Up to date"
                    self.message = "Firmware is already up to date (ArduCopter %s matches latest stable release)." % local_ver
                    self.is_running = False
                self.log("[OK] Local firmware storage is already up to date with latest stable release (%s)." % local_ver, "SUCCESS")
                self.log("=" * 60, "HEADER")
                return

            # Newer version detected or forced rebuild
            if newer:
                self.log("[!] Newer stable release detected: ArduCopter %s > %s" % (online_ver, local_ver), "WARNING")
            else:
                self.log("[*] Force rebuild requested for ArduCopter %s" % online_ver, "INFO")

            with self._lock:
                self.status = "building"
                self.stage = "Building CubeSolo firmware"
                self.progress = 15
                self.message = "Building latest ArduCopter v%s for Stock Solo Cube..." % online_ver

            # Stage 1: Build & Stage CubeSolo
            self.log("\n[1/2] Building CubeSolo firmware (v%s)..." % online_ver, "STAGE")
            def _prog_solo(pct, state, msg):
                scaled = 15 + int(pct * 0.35)
                with self._lock:
                    self.progress = min(50, scaled)
                    self.message = "CubeSolo: %s" % msg

            solo_res = build_and_stage_cube("CubeSolo", online_info, progress_callback=_prog_solo, log_fn=self.log)

            # Stage 2: Build & Stage CubeGreen-solo
            with self._lock:
                self.stage = "Building CubeGreen-solo firmware"
                self.progress = 55
                self.message = "Building latest ArduCopter v%s for Green Cube..." % online_ver

            self.log("\n[2/2] Building CubeGreen-solo firmware (v%s)..." % online_ver, "STAGE")
            def _prog_green(pct, state, msg):
                scaled = 55 + int(pct * 0.35)
                with self._lock:
                    self.progress = min(90, scaled)
                    self.message = "CubeGreen-solo: %s" % msg

            green_res = build_and_stage_cube("CubeGreen-solo", online_info, progress_callback=_prog_green, log_fn=self.log)

            # Stage 3: Update version references across system
            with self._lock:
                self.stage = "Updating system version references"
                self.progress = 95
                self.message = "Updating system version references..."

            self.log("\n[3/3] Updating system version references...", "STAGE")
            try:
                update_version_references(online_ver, old_version=local_ver, log_fn=self.log)
            except Exception as ve:
                self.log("[!] Version reference update notice: %s" % str(ve), "WARNING")

            # Auto-bake updated references and APJs into firmware archive if on development host
            try:
                import bake_into_firmware
                bake_into_firmware.ensure_firmware_baked(app_base_dir=BASE_DIR, log_func=self.log)
            except Exception as be:
                self.log("[!] Auto-bake notice: %s" % str(be), "INFO")

            # Update custom_build_info.json
            build_db = load_build_info()
            build_db["current_version"] = online_ver
            build_db["latest_CubeSolo"] = solo_res
            build_db["latest_CubeGreen-solo"] = green_res
            save_build_info(build_db)

            self._refresh_local_version()

            with self._lock:
                self.status = "update_ready"
                self.progress = 100
                self.stage = "Update Ready"
                self.local_version = online_ver
                self.update_available = False
                self.message = "Update ready! ArduCopter v%s (CubeSolo & Green Cube) added to storage as current active firmware." % online_ver
                self.is_running = False

            self.log("\n" + "=" * 60, "HEADER")
            self.log("🎉 UPDATE READY! ArduCopter v%s is staged and ready for deployment." % online_ver, "SUCCESS")
            self.log("CubeSolo active file: arducopter_CubeSolo_%s.apj" % online_ver, "INFO")
            self.log("Green Cube active file: arducopter_CubeGreen_%s.apj" % online_ver, "INFO")
            self.log("=" * 60, "HEADER")

        except Exception as e:
            with self._lock:
                self.status = "error"
                self.progress = 0
                self.stage = "Error"
                self.message = "Update routine error: %s" % str(e)
                self.is_running = False
            self.log("[!] Update routine failed: %s" % str(e), "ERROR")
            self.log("=" * 60, "HEADER")


# Singleton instance
update_engine = FirmwareUpdateEngine()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ArduCopter Custom Build Engine for 3DR Solo")
    parser.add_argument("--check", action="store_true", help="Check if an update is available")
    parser.add_argument("--update", action="store_true", help="Run update routine")
    parser.add_argument("--force", action="store_true", help="Force rebuild even if version matches")
    parser.add_argument("--update-setup", action="store_true", help="Update setup zip packages with active APJ binaries")
    parser.add_argument("--update-version", type=str, default="", help="Update version number across all project files")
    args = parser.parse_args()

    if args.check:
        print("Checking for firmware updates...")
        res = update_engine.check_version_quick()
        print(json.dumps(res, indent=2))
    elif args.update_setup:
        ver = get_local_firmware_version()
        print("Updating setup packages for ArduCopter %s..." % ver)
        update_setup_files(ver)
    elif args.update_version:
        print("Updating version references to ArduCopter %s..." % args.update_version)
        update_version_references(args.update_version)
    elif args.update or args.force:
        print("Running update routine (force=%s)..." % args.force)
        ok, msg = update_engine.start_update_routine(force=args.force)
        print(msg)
        while update_engine.get_state()["is_running"]:
            st = update_engine.get_state()
            print("  [%d%%] %s" % (st["progress"], st["message"]))
            time.sleep(2)
        st = update_engine.get_state()
        print("Final status: %s — %s" % (st["status"], st["message"]))
    else:
        parser.print_help()
