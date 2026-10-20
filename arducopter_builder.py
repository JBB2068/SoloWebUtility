#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ArduCopter Custom Build & Update Engine for 3DR Solo & Green Cube.
Interacts with the official ArduPilot Custom Firmware Builder API (custom.ardupilot.org).
Compares current firmware version in the Firmware folder with the latest ArduCopter
stable release. If a newer stable release is available (or forced), requests custom
builds for CubeSolo and CubeGreen-solo with exactly the feature set listed in
firmware/solocustombuild.yaml (a CustomBuild "Download config" file), validated
against the live feature list of each board.
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
BUILDER_BASE_URL = os.environ.get("SOLO_CUSTOMBUILD_URL", "https://custom.ardupilot.org").rstrip("/")
BUILD_INFO_FILE = os.path.join(FIRMWARE_DIR, "custom_build_info.json")

BOARD_MAP = {
    "solo": "CubeSolo",
    "cubesolo": "CubeSolo",
    "green": "CubeGreen-solo",
    "cubegreen": "CubeGreen-solo",
    "cubegreen-solo": "CubeGreen-solo"
}

import socket


# ---------------------------------------------------------------------------
# Build configuration: firmware/solocustombuild.yaml (CustomBuild config 0.0.1)
#
# The file is the "Download config" YAML from custom.ardupilot.org:
#   config_version, vehicle{id,name}, version{id,name,type,remote_name},
#   board{id,name}, selected_features[...], optional use_default_features.
# selected_features are CustomBuild feature LABELS (the API's feature "id").
# CustomBuild enables exactly the labels submitted and DISABLES every other
# feature, so this list is the complete feature set of the firmware.
#
# The Solo has no PyYAML, so the (flat, two-level) file is parsed by hand.
# ---------------------------------------------------------------------------
BUILD_CONFIG_NAME = "solocustombuild.yaml"
BUILD_CONFIG_SEARCH = [
    os.path.join(FIRMWARE_DIR, BUILD_CONFIG_NAME),             # /log/firmware (survives Full Install)
    os.path.join(BASE_DIR, "firmware", BUILD_CONFIG_NAME),      # baked into the golden image
]
# Built for both Solo Cubes with the feature list from the YAML
BUILD_BOARDS = ["CubeSolo", "CubeGreen-solo"]
# Needed so apj_tool can embed the Solo .param defaults after download
REQUIRED_FEATURES = ["APJ_TOOL_PARAMETERS"]
SUPPORTED_CONFIG_VERSIONS = ("0.0.1",)


def _native(s):
    """Python 2: unicode -> native byte str (UTF-8), so it can be mixed with the
    module's byte-string messages (several contain emoji) without UnicodeDecodeError."""
    if sys.version_info[0] < 3 and isinstance(s, unicode):  # noqa: F821 (py2 only)
        return s.encode("utf-8")
    return s


def _yaml_scalar(raw):
    """Parse a YAML scalar: strips comments/quotes, handles true/false/null/[]."""
    s = raw.strip()
    if not s:
        return ""
    if s[0] in ("'", '"'):
        q = s[0]
        end = s.find(q, 1)
        while q == "'" and end != -1 and end + 1 < len(s) and s[end + 1] == "'":
            end = s.find(q, end + 2)          # '' is an escaped quote in single-quoted YAML
        val = s[1:end] if end != -1 else s[1:]
        return _native(val.replace("''", "'") if q == "'" else val)
    hash_at = s.find(" #")
    if hash_at != -1:
        s = s[:hash_at].rstrip()
    low = s.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if s == "[]":
        return []
    return _native(s)


def parse_custombuild_yaml(text):
    """
    Parse a CustomBuild config YAML (schema 0.0.1) without PyYAML.
    Supports: top-level 'key: value', one level of nested mappings, block lists
    ('- item', indented or not), quoted scalars, comments, CRLF, BOM.
    Returns a dict.
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = text.lstrip(u"﻿")
    result = {}
    cur_key = None          # top-level key whose block we are in
    cur_kind = None         # "map" | "list" | None
    for lineno, line in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped == "---":
            continue
        indent = len(line) - len(line.lstrip(" "))
        if "\t" in line[:indent + 1]:
            raise ValueError("line %d: tabs are not allowed in YAML indentation" % lineno)

        if stripped.startswith("- ") or stripped == "-":
            if cur_key is None:
                raise ValueError("line %d: list item outside of a key" % lineno)
            if cur_kind is None:
                result[cur_key] = []
                cur_kind = "list"
            if cur_kind != "list":
                raise ValueError("line %d: list item inside mapping '%s'" % (lineno, cur_key))
            result[cur_key].append(_yaml_scalar(stripped[1:]))
            continue

        if ":" not in stripped:
            raise ValueError("line %d: expected 'key: value', got %r" % (lineno, stripped))
        key, _, rest = stripped.partition(":")
        key = key.strip().strip("'\"")
        value = rest.strip()

        if indent == 0:
            cur_key = key
            cur_kind = None
            if value in ("", "|", ">"):
                result[key] = None            # block follows (mapping or list)
            else:
                result[key] = _yaml_scalar(value)
                cur_key = None
        else:
            if cur_key is None:
                raise ValueError("line %d: indented key '%s' without a parent" % (lineno, key))
            if cur_kind is None:
                result[cur_key] = {}
                cur_kind = "map"
            if cur_kind != "map":
                raise ValueError("line %d: key '%s' inside list '%s'" % (lineno, key, cur_key))
            result[cur_key][key] = _yaml_scalar(value)
    return dict((_native(k), v) for k, v in result.items())


def validate_build_config(cfg):
    """Validate a parsed config against CustomBuild schema 0.0.1. Raises ValueError."""
    errors = []
    if str(cfg.get("config_version", "")) not in SUPPORTED_CONFIG_VERSIONS:
        errors.append("config_version must be one of %s (got %r)" % (SUPPORTED_CONFIG_VERSIONS, cfg.get("config_version")))
    for section, fields in (("vehicle", ("id", "name")),
                            ("version", ("id", "name", "type", "remote_name")),
                            ("board", ("id", "name"))):
        sec = cfg.get(section)
        if not isinstance(sec, dict):
            errors.append("'%s' must be a mapping" % section)
            continue
        for f in fields:
            if sec.get(f) in (None, ""):
                errors.append("'%s.%s' is required" % (section, f))
    feats = cfg.get("selected_features")
    if not isinstance(feats, list):
        errors.append("'selected_features' must be a list")
    elif not all(isinstance(x, ("".__class__, u"".__class__)) and x for x in feats):
        errors.append("'selected_features' must contain only non-empty strings")
    if "use_default_features" in cfg and not isinstance(cfg["use_default_features"], bool):
        errors.append("'use_default_features' must be true/false")
    allowed = set(["config_version", "vehicle", "version", "board", "selected_features", "use_default_features"])
    extra = [k for k in cfg if k not in allowed]
    if extra:
        errors.append("unknown top-level keys: %s" % ", ".join(sorted(extra)))
    if errors:
        raise ValueError("Invalid %s: %s" % (BUILD_CONFIG_NAME, "; ".join(errors)))
    return cfg


def find_build_config_path():
    for p in BUILD_CONFIG_SEARCH:
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
    return None


def load_build_config(path=None):
    """Load + validate solocustombuild.yaml. Returns (config_dict, path). Raises IOError/ValueError."""
    path = path or find_build_config_path()
    if not path:
        raise IOError("%s not found (looked in: %s)" % (BUILD_CONFIG_NAME, ", ".join(BUILD_CONFIG_SEARCH)))
    with open(path, "rb") as f:
        cfg = parse_custombuild_yaml(f.read())
    validate_build_config(cfg)
    # de-duplicate, keep order
    seen = set()
    feats = []
    for x in cfg["selected_features"]:
        if x not in seen:
            seen.add(x)
            feats.append(x)
    cfg["selected_features"] = feats
    return cfg, path


def resolve_selected_features(config, board_features, log_fn=None):
    """
    Turn the YAML feature list into the exact selected_features for one board,
    validated against GET .../boards/{board}/features:
      - labels the board/version does not offer are dropped (CustomBuild would
        silently ignore them) and reported
      - missing dependencies of selected features are added and reported
      - REQUIRED_FEATURES (APJ_TOOL_PARAMETERS) are always included
      - use_default_features: true -> board defaults are added as well
    Returns (selected_sorted, report_dict).
    """
    def _log(msg, level="INFO"):
        if log_fn:
            log_fn(msg, level)

    by_id = {}
    for f in board_features or []:
        fid = _native(f.get("id"))
        if f.get("dependencies"):
            f = dict(f)
            f["dependencies"] = [_native(d) for d in f["dependencies"]]
        if fid:
            by_id[fid] = f
    wanted = list(config.get("selected_features", []))
    report = {"requested": len(wanted), "unknown": [], "added_dependencies": [], "added_required": [], "added_defaults": 0}

    selected = set()
    for label in wanted:
        if label in by_id:
            selected.add(label)
        else:
            report["unknown"].append(label)

    if config.get("use_default_features"):
        before = len(selected)
        for fid, f in by_id.items():
            if (f.get("default") or {}).get("enabled"):
                selected.add(fid)
        report["added_defaults"] = len(selected) - before

    for req in REQUIRED_FEATURES:
        if req in by_id and req not in selected:
            selected.add(req)
            report["added_required"].append(req)

    # close over dependencies (labels), repeatedly
    changed = True
    while changed:
        changed = False
        for fid in sorted(selected):
            for dep in (by_id.get(fid, {}).get("dependencies") or []):
                if dep in by_id and dep not in selected:
                    selected.add(dep)
                    report["added_dependencies"].append("%s (needed by %s)" % (dep, fid))
                    changed = True

    if report["unknown"]:
        _log("[!] %d feature(s) in %s are not offered for this board/version and were skipped: %s" % (
            len(report["unknown"]), BUILD_CONFIG_NAME, ", ".join(report["unknown"])), "WARNING")
    if report["added_required"]:
        _log("[*] Added required feature(s): %s" % ", ".join(report["added_required"]), "INFO")
    if report["added_dependencies"]:
        _log("[*] Added missing dependencies: %s" % ", ".join(report["added_dependencies"]), "INFO")
    for req in REQUIRED_FEATURES:
        if req not in selected:
            raise ValueError("%s is not available for this board/version - cannot embed Solo parameters." % req)
    return sorted(selected), report


def compute_selected_features(features_list, config=None):
    """Back-compat wrapper: features for a board from solocustombuild.yaml."""
    if config is None:
        config, _ = load_build_config()
    selected, _ = resolve_selected_features(config, features_list)
    return selected




def _tcp_probe(host, port, timeout):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


# How the last successful check reached the internet: "direct", "controller" or None.
LAST_INTERNET_PATH = None


def check_internet_connection(host="8.8.8.8", port=53, timeout=1.2):
    """
    Internet gate for update checks and builds. Never hangs the app thread.

      1. Repair the copter's own route/DNS (default via 10.1.1.1, public DNS);
         a hostapd restart on the Controller silently drops them.
      2. Direct probe from the copter: host:port, then 1.1.1.1:53.
      3. Fallback: if the Controller itself is online, report True; every
         request then goes through _http_get_via_controller /
         _http_post_via_controller when the direct path fails.
    Sets LAST_INTERNET_PATH to "direct" or "controller".
    """
    global LAST_INTERNET_PATH
    wifi = None
    try:
        import wifi_mgr as wifi
        wifi.ensure_copter_internet_route()
    except Exception:
        wifi = None

    for h in (host, "1.1.1.1"):
        if _tcp_probe(h, port, timeout):
            LAST_INTERNET_PATH = "direct"
            return True

    if wifi is not None:
        try:
            if wifi.controller_has_internet():
                LAST_INTERNET_PATH = "controller"
                return True
        except Exception:
            pass
    LAST_INTERNET_PATH = None
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


class CustomBuildRateLimited(RuntimeError):
    """POST /api/v1/builds answered 429 (10 builds per hour per client)."""


class CustomBuildHTTPError(RuntimeError):
    """custom.ardupilot.org answered with an HTTP error (the request reached the server)."""
    def __init__(self, code, detail, url):
        RuntimeError.__init__(self, "HTTP %s from %s: %s" % (code, url, detail))
        self.code = code
        self.detail = detail


def _http_error_detail(err):
    try:
        body = err.read()
        if isinstance(body, bytes):
            body = body.decode("utf-8", "ignore")
        try:
            j = json.loads(body)
            if isinstance(j, dict) and j.get("detail"):
                return str(j["detail"])
        except Exception:
            pass
        return body.strip()[:300] or str(err)
    except Exception:
        return str(err)


def _http_get(url, timeout=25):
    """HTTP GET -> raw bytes. Falls back to the Controller proxy only when the
    server could not be reached at all (never after an HTTP error response)."""
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
    except urllib_error.HTTPError as http_err:
        raise CustomBuildHTTPError(http_err.code, _http_error_detail(http_err), url)
    except Exception as direct_err:
        try:
            return _http_get_via_controller(url, timeout=timeout)
        except Exception:
            raise direct_err


def _http_post_json(url, payload, timeout=30):
    """HTTP POST JSON -> parsed JSON. Never re-sent through the Controller proxy
    after the server answered (a second POST would queue a second build)."""
    try:
        import wifi_mgr
        wifi_mgr.ensure_copter_internet_route()
    except Exception:
        pass

    headers = {
        "User-Agent": "SoloWebUtility/1.0 (3DR Solo CustomBuild Client)",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    data = json.dumps(payload).encode("utf-8")
    try:
        req = urllib_request.Request(url, data=data, headers=headers)
        resp = urllib_request.urlopen(req, timeout=timeout)
        raw = resp.read()
        if sys.version_info[0] >= 3 and isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        return json.loads(raw)
    except urllib_error.HTTPError as http_err:
        raise CustomBuildHTTPError(http_err.code, _http_error_detail(http_err), url)
    except Exception as direct_err:
        # Only when the request could not be sent/connected at all
        try:
            return _http_post_via_controller(url, payload, timeout=timeout)
        except Exception:
            raise direct_err


def _api_get_json(path, timeout=25):
    raw = _http_get("%s%s" % (BUILDER_BASE_URL, path), timeout=timeout)
    if sys.version_info[0] >= 3 and isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    return json.loads(raw)


def parse_version_tuple(ver_str):
    """Convert version string like '4.7.2' or 'v4.7.2' into tuple (4, 7, 1) for comparison."""
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
    Defaults to 4.7.2 if no newer version tag is found.
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

    return "4.7.2"


# ---------------------------------------------------------------------------
# custom.ardupilot.org CustomBuild API v1 (https://custom.ardupilot.org/api/docs)
#   GET  /api/v1/vehicles/{vehicle}/versions?type=stable
#   GET  /api/v1/vehicles/{vehicle}/versions/{version_id}/boards/{board_id}
#   GET  /api/v1/vehicles/{vehicle}/versions/{version_id}/boards/{board_id}/features
#   POST /api/v1/builds  {vehicle_id, board_id, version_id, selected_features}  (10/hour)
#   GET  /api/v1/builds/{build_id}         progress.state: PENDING RUNNING SUCCESS
#                                          FAILURE ERROR TIMED_OUT; artifact_available
#   GET  /api/v1/builds/{build_id}/logs?tail=N
#   GET  /api/v1/builds/{build_id}/artifact   tar.gz: <folder>/arducopter.apj, build.log, ...
# ---------------------------------------------------------------------------
BUILD_OK_STATES = ("SUCCESS",)
BUILD_FAIL_STATES = ("FAILURE", "ERROR", "TIMED_OUT", "FAILED", "CANCELLED")


def get_latest_stable_version(vehicle_id="copter"):
    """
    Latest stable release from CustomBuild.
    Returns dict: {'id', 'name', 'type', 'commit_ref', 'remote', 'vehicle_id'}
    """
    versions = _api_get_json("/api/v1/vehicles/%s/versions?type=stable" % vehicle_id)
    stables = [v for v in versions if v.get("type") == "stable"]
    if not stables:
        raise RuntimeError("CustomBuild returned no stable %s versions." % vehicle_id)
    stables.sort(key=lambda v: parse_version_tuple(v.get("name", "0.0.0")))
    latest = stables[-1]
    return {
        "id": _native(latest.get("id")),
        "name": _native(latest.get("name")),
        "type": _native(latest.get("type")),
        "commit_ref": _native(latest.get("commit_ref") or ""),
        "remote": _native((latest.get("remote") or {}).get("name", "")),
        "vehicle_id": _native(vehicle_id),
    }


def board_available(version_id, board_id, vehicle_id="copter"):
    """True if CustomBuild can build board_id at version_id."""
    try:
        _api_get_json("/api/v1/vehicles/%s/versions/%s/boards/%s" % (vehicle_id, version_id, board_id))
        return True
    except CustomBuildHTTPError as e:
        if e.code == 404:
            return False
        raise


def get_board_features(version_id, board_id, vehicle_id="copter"):
    """Feature list (labels, defaults, dependencies) for a board at a version."""
    return _api_get_json("/api/v1/vehicles/%s/versions/%s/boards/%s/features" % (vehicle_id, version_id, board_id))


def submit_build(board_id, version_id, selected_features, vehicle_id="copter"):
    """POST /api/v1/builds. Returns build_id."""
    payload = {
        "vehicle_id": vehicle_id,
        "board_id": board_id,
        "version_id": version_id,
        "selected_features": list(selected_features),
    }
    try:
        resp = _http_post_json("%s/api/v1/builds" % BUILDER_BASE_URL, payload)
    except CustomBuildHTTPError as e:
        if e.code == 429:
            raise CustomBuildRateLimited("custom.ardupilot.org rate limit reached (10 builds per hour). Try again later.")
        raise RuntimeError("Build request rejected by custom.ardupilot.org (HTTP %s): %s" % (e.code, e.detail))
    build_id = resp.get("build_id")
    if not build_id:
        raise RuntimeError("custom.ardupilot.org did not return a build_id: %r" % resp)
    return build_id


def get_build_log_tail(build_id, lines=25):
    try:
        raw = _http_get("%s/api/v1/builds/%s/logs?tail=%d" % (BUILDER_BASE_URL, build_id, lines), timeout=20)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "ignore")
        return raw.strip()
    except Exception:
        return ""


def poll_build(build_id, progress_callback=None, max_wait=2400, poll_interval=5):
    """
    Poll GET /api/v1/builds/{id} until SUCCESS (and artifact_available), a failure
    state, or max_wait. Calls progress_callback(percent, state, msg). Returns True.
    """
    url = "%s/api/v1/builds/%s" % (BUILDER_BASE_URL, build_id)
    start_time = time.time()
    errors = 0
    last_state = None
    while time.time() - start_time < max_wait:
        try:
            raw = _http_get(url, timeout=20)
            if sys.version_info[0] >= 3 and isinstance(raw, bytes):
                raw = raw.decode("utf-8", "ignore")
            res = json.loads(raw)
            errors = 0
        except CustomBuildHTTPError as e:
            if e.code == 404:
                raise RuntimeError("Build %s no longer exists on the server." % build_id)
            errors += 1
            res = None
        except Exception as e:
            errors += 1
            res = None
            if progress_callback:
                progress_callback(-1, "POLL", "Polling error (%d): %s" % (errors, e))
        if res is None:
            if errors >= 20:
                raise RuntimeError("Lost contact with custom.ardupilot.org while polling build %s." % build_id)
            time.sleep(poll_interval)
            continue

        prog = res.get("progress") or {}
        state = str(prog.get("state") or "UNKNOWN").upper()
        try:
            percent = int(prog.get("percent") or 0)
        except Exception:
            percent = 0
        if progress_callback:
            progress_callback(percent, state, "Server build progress: %d%% (%s)" % (percent, state))
        last_state = state

        if state in BUILD_OK_STATES:
            if res.get("artifact_available", True):
                return True
        elif state in BUILD_FAIL_STATES:
            tail = get_build_log_tail(build_id)
            last = [l for l in tail.splitlines() if l.strip()][-6:]
            raise RuntimeError("Build %s ended in state %s on custom.ardupilot.org.%s" % (
                build_id, state, (" Last log lines: " + " | ".join(last)) if last else ""))
        time.sleep(poll_interval)

    raise RuntimeError("Custom build %s timed out after %d seconds." % (build_id, max_wait))


def download_artifact_apj(build_id, target_apj_path):
    """Download the build archive (tar.gz) and extract arducopter.apj to target_apj_path."""
    raw_tar = _http_get("%s/api/v1/builds/%s/artifact" % (BUILDER_BASE_URL, build_id), timeout=120)
    try:
        tar = tarfile.open(fileobj=BytesIO(raw_tar), mode="r:gz")
    except Exception as e:
        raise IOError("Build artifact is not a valid tar.gz archive (%d bytes): %s" % (len(raw_tar or b""), e))
    try:
        apjs = [m for m in tar.getmembers() if m.isfile() and m.name.lower().endswith(".apj")]
        apj_member = None
        for m in apjs:
            if os.path.basename(m.name) == "arducopter.apj":
                apj_member = m
                break
        if apj_member is None and apjs:
            apj_member = apjs[0]
        if apj_member is None:
            raise IOError("No .apj inside build artifact (contents: %s)" % ", ".join(m.name for m in tar.getmembers()[:12]))
        apj_bytes = tar.extractfile(apj_member).read()
    finally:
        tar.close()

    try:
        text = apj_bytes.decode("utf-8") if isinstance(apj_bytes, bytes) else apj_bytes
        apj = json.loads(text)
        if not apj.get("image") or "board_id" not in apj:
            raise ValueError("missing image/board_id")
    except Exception as e:
        raise IOError("Downloaded %s is not a valid APJ firmware file: %s" % (os.path.basename(apj_member.name), e))

    dest_dir = os.path.dirname(target_apj_path)
    if dest_dir and not os.path.exists(dest_dir):
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


PREVIOUS_DIRNAME = "previous"     # <firmware dir>/previous: older builds kept for reverting
MAX_PREVIOUS_VERSIONS = 3


def _apj_version_key(fn):
    m = re.search(r"(\d+(?:\.\d+)+)", fn)
    if not m:
        return (0,)
    return tuple(int(p) for p in m.group(1).split(".") if p.isdigit())


def prune_previous_firmware(board_id, keep=MAX_PREVIOUS_VERSIONS, firmware_dir=None):
    """Keep only the `keep` newest archived versions of this board in <firmware>/previous."""
    prefixes = ["arducopter_CubeGreen_", "arducoptercubegreen-solo-"] if board_id == "CubeGreen-solo" else ["arducopter_CubeSolo_", "arducoptercubesolo-"]
    prev_dir = os.path.join(firmware_dir or FIRMWARE_DIR, PREVIOUS_DIRNAME)
    if not os.path.isdir(prev_dir):
        return []
    mine = [fn for fn in os.listdir(prev_dir)
            if fn.lower().endswith(".apj") and any(fn.startswith(p) for p in prefixes)]
    mine.sort(key=_apj_version_key, reverse=True)
    dropped = []
    for fn in mine[keep:]:
        try:
            os.remove(os.path.join(prev_dir, fn))
            dropped.append(fn)
        except Exception:
            pass
    return dropped


def clean_old_firmware_files(board_id, new_version):
    """
    Move previous versioned APJ files of this board into <firmware>/previous (kept for
    reverting; the 3 newest per board are retained) and delete legacy unversioned
    duplicates. Example: arducopter_CubeSolo_4.7.2.apj -> previous/ when 4.7.3 is staged.
    flash_mgr.get_latest_apj() never scans previous/, so "latest" stays the new build.
    Returns the names moved out of the firmware folder (archived or deleted).
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

        prev_dir = os.path.join(d, PREVIOUS_DIRNAME)
        for fn in os.listdir(d):
            if not fn.lower().endswith(".apj") or fn == kept_fn:
                continue
            if any(fn.startswith(p) for p in prefixes):
                old_path = os.path.join(d, fn)
                try:
                    if not os.path.isdir(prev_dir):
                        os.makedirs(prev_dir)
                    dst = os.path.join(prev_dir, fn)
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.rename(old_path, dst)
                    removed.append(fn)
                except Exception:
                    pass
        prune_previous_firmware(board_id, firmware_dir=d)
    return removed


def build_and_stage_cube(board_id, ver_info, progress_callback=None, log_fn=None, config=None):
    """
    Build one Cube with the features from solocustombuild.yaml and stage it as
    /log/firmware/arducopter_<CubeSolo|CubeGreen>_<version>.apj:
      features -> POST /api/v1/builds -> poll -> download archive -> extract .apj
      -> embed Solo .param defaults with apj_tool -> atomically replace -> remove
      older versions of this board.
    The previous firmware is left untouched if any step fails.
    """
    def _log(msg, level="INFO"):
        if log_fn:
            log_fn(msg, level)
        else:
            print("[%s] %s" % (level, msg))

    if config is None:
        config, _ = load_build_config()

    vehicle_id = (config.get("vehicle") or {}).get("id", "copter")
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
    # Work in a hidden sub-folder under the real .apj name: apj_tool picks the file
    # format from the extension, and flash_mgr only scans FIRMWARE_DIR itself, so a
    # half-built file can never be selected for flashing.
    staging_dir = os.path.join(FIRMWARE_DIR, ".staging")
    if not os.path.isdir(staging_dir):
        os.makedirs(staging_dir)
    temp_path = os.path.join(staging_dir, versioned_name)

    if not board_available(version_id, board_id, vehicle_id=vehicle_id):
        raise RuntimeError("custom.ardupilot.org cannot build %s for %s %s." % (board_id, vehicle_id, version_name))

    _log("[*] Fetching %s feature list for %s %s..." % (board_id, vehicle_id, version_name), "INFO")
    features = get_board_features(version_id, board_id, vehicle_id=vehicle_id)
    selected_features, report = resolve_selected_features(config, features, log_fn=_log)
    off = len(features) - len(selected_features)
    _log("[*] %s: %d features enabled from %s, %d disabled (of %d offered)." % (
        board_id, len(selected_features), BUILD_CONFIG_NAME, off, len(features)), "INFO")

    _log("[*] Submitting custom build request to %s..." % BUILDER_BASE_URL, "INFO")
    build_id = submit_build(board_id, version_id, selected_features, vehicle_id=vehicle_id)
    _log("[*] Build %s queued: %s/api/v1/builds/%s" % (build_id, BUILDER_BASE_URL, build_id), "INFO")

    last_logged = [None]
    def _on_poll(pct, state, msg):
        if progress_callback:
            progress_callback(max(pct, 0), state, msg)
        key = (pct // 10 if pct >= 0 else -1, state)
        if key != last_logged[0]:
            last_logged[0] = key
            if pct >= 0:
                _log("[*] Server build progress (%s): %d%% (%s)" % (cube_name, pct, state), "INFO")
            else:
                _log("[!] Polling notice (%s): %s" % (cube_name, msg), "WARNING")

    poll_build(build_id, progress_callback=_on_poll)
    _log("[OK] Build %s compiled successfully on server!" % build_id, "SUCCESS")

    try:
        _log("[*] Downloading build archive and extracting arducopter.apj...", "INFO")
        download_artifact_apj(build_id, temp_path)
        _log("[OK] Downloaded firmware (%d bytes)." % os.path.getsize(temp_path), "SUCCESS")

        ok_params, msg_params = apply_param_defaults_to_apj(temp_path, cube_type=cube_type_key, log_fn=_log)
        if not ok_params:
            raise RuntimeError("Could not embed Solo parameter defaults (%s); keeping the previous firmware." % msg_params)

        if os.path.exists(versioned_path):
            os.remove(versioned_path)
        os.rename(temp_path, versioned_path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
    file_sz = os.path.getsize(versioned_path)
    _log("[OK] Staged %s (%d bytes) with Solo parameter defaults." % (versioned_name, file_sz), "SUCCESS")

    removed = clean_old_firmware_files(board_id, version_name)
    if removed:
        _log("[*] Archived previous version files for reverting (%s/%s, newest %d kept): %s" % (
            FIRMWARE_DIR, PREVIOUS_DIRNAME, MAX_PREVIOUS_VERSIONS, ", ".join(removed)), "INFO")

    return {
        "board": board_id,
        "cube_name": cube_name,
        "version": version_name,
        "version_id": version_id,
        "build_id": build_id,
        "versioned_file": versioned_name,
        "active_file": versioned_name,
        "file_size": file_sz,
        "features_enabled": len(selected_features),
        "features_skipped": report["unknown"],
        "dependencies_added": report["added_dependencies"],
        "config_file": BUILD_CONFIG_NAME,
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
                    "description": "Updated ArduCopter firmware to stable release %s via custom.ardupilot.org with the feature set from solocustombuild.yaml." % new_version
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
        self.local_version = "4.7.2"
        self.online_version = "4.7.2"
        self.update_available = False
        self.logs = []
        self._refresh_local_version()

    def _refresh_local_version(self):
        try:
            self.local_version = get_local_firmware_version()
        except Exception:
            self.local_version = "4.7.2"

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
                "build_config": self._config_summary(),
                "logs": list(self.logs)
            }

    def _config_summary(self):
        try:
            cfg, path = load_build_config()
            return {"path": path, "features": len(cfg["selected_features"]),
                    "created_for": "%s %s" % (cfg["version"]["name"], cfg["board"]["id"])}
        except Exception as e:
            return {"error": str(e)}

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

            # Feature set for the custom builds
            try:
                build_cfg, cfg_path = load_build_config()
            except Exception as ce:
                with self._lock:
                    self.status = "error"
                    self.stage = "Build configuration error"
                    self.message = str(ce)
                    self.is_running = False
                self.log("[!] %s" % ce, "ERROR")
                return
            self.log("[*] Build features: %s (%d features; created for %s %s / %s)" % (
                cfg_path, len(build_cfg["selected_features"]), build_cfg["vehicle"]["name"],
                build_cfg["version"]["name"], build_cfg["board"]["id"]), "INFO")

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
                self.log("[!] No internet: copter route test (8.8.8.8/1.1.1.1) and Controller fallback both failed", "WARNING")
                return
            if LAST_INTERNET_PATH == "controller":
                self.log("[*] Copter has no direct route; downloading through the Controller (10.1.1.1)", "INFO")

            vehicle_id = build_cfg["vehicle"]["id"]
            self.log("[*] Contacting %s/api/v1/vehicles/%s/versions?type=stable..." % (BUILDER_BASE_URL, vehicle_id), "INFO")
            online_info = get_latest_stable_version(vehicle_id)
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

            # Build & stage each Cube with the YAML feature set
            results = {}
            failures = {}
            n = len(BUILD_BOARDS)
            for i, board in enumerate(BUILD_BOARDS):
                lo = 15 + int(80.0 * i / n)
                hi = 15 + int(80.0 * (i + 1) / n)
                with self._lock:
                    self.status = "building"
                    self.stage = "Building %s firmware" % board
                    self.progress = lo
                    self.message = "Building ArduCopter v%s for %s..." % (online_ver, board)
                self.log("\n[%d/%d] Building %s firmware (v%s)..." % (i + 1, n, board, online_ver), "STAGE")

                def _prog(pct, state, msg, lo=lo, hi=hi, board=board):
                    with self._lock:
                        self.progress = min(hi - 1, lo + int((hi - lo) * max(pct, 0) / 100.0))
                        self.message = "%s: %s" % (board, msg)
                try:
                    results[board] = build_and_stage_cube(board, online_info, progress_callback=_prog,
                                                          log_fn=self.log, config=build_cfg)
                except CustomBuildRateLimited as rl:
                    for rest in BUILD_BOARDS[i:]:
                        failures[rest] = str(rl)
                    self.log("[!] %s - not submitting further builds now." % rl, "ERROR")
                    break
                except Exception as be:
                    failures[board] = str(be)
                    self.log("[!] %s build failed: %s" % (board, be), "ERROR")

            if not results:
                raise RuntimeError("; ".join("%s: %s" % kv for kv in failures.items()))

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
            for board, res in results.items():
                build_db["latest_%s" % board] = res
            save_build_info(build_db)

            self._refresh_local_version()

            staged = ", ".join(r["versioned_file"] for r in results.values())
            with self._lock:
                self.local_version = online_ver
                self.update_available = False
                self.progress = 100
                self.is_running = False
                if failures:
                    self.status = "error"
                    self.stage = "Partially built"
                    self.message = "Staged %s, but %s failed: %s" % (
                        staged, ", ".join(failures), "; ".join(failures.values()))
                else:
                    self.status = "update_ready"
                    self.stage = "Update Ready"
                    self.message = "Update ready! ArduCopter v%s staged: %s." % (online_ver, staged)

            self.log("\n" + "=" * 60, "HEADER")
            if failures:
                self.log("[!] PARTIAL: staged %s; failed: %s" % (staged, ", ".join(failures)), "WARNING")
            else:
                self.log("🎉 UPDATE READY! ArduCopter v%s is staged and ready for deployment." % online_ver, "SUCCESS")
            for r in results.values():
                self.log("%s: %s (%d features enabled%s)" % (
                    r["board"], r["versioned_file"], r["features_enabled"],
                    ", skipped: %s" % ", ".join(r["features_skipped"]) if r["features_skipped"] else ""), "INFO")
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
