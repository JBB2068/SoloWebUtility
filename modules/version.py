#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Version Management Module
Tracks application release version and iteration count with persistent JSON storage.
"""

import os
import json
import time
from webcore import Blueprint, request, jsonify

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_FILE = os.path.join(BASE_DIR, "version.json")
INITIAL_VERSION = "1.0.28"


def parse_version(ver_str):
    """Parse 'major.minor.patch' into a tuple of ints."""
    try:
        parts = [int(p) for p in ver_str.strip().lstrip("v").split(".")]
        while len(parts) < 3:
            parts.append(0)
        return parts[0], parts[1], parts[2]
    except Exception:
        return 1, 0, 1


def format_version(major, minor, patch):
    return "%d.%d.%d" % (major, minor, patch)


def get_version_info():
    """Retrieve current version details."""
    if not os.path.exists(VERSION_FILE):
        info = {
            "version": INITIAL_VERSION,
            "iteration": 1,
            "updated_at": int(time.time()),
            "changelog": [
                {
                    "version": INITIAL_VERSION,
                    "date": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "description": "Initial Release of 3DR Solo Web Utility."
                }
            ]
        }
        save_version_info(info)
        return info

    try:
        with open(VERSION_FILE, "r") as f:
            return json.load(f)
    except Exception:
        info = {
            "version": INITIAL_VERSION,
            "iteration": 1,
            "updated_at": int(time.time()),
            "changelog": []
        }
        save_version_info(info)
        return info


def save_version_info(info):
    """Persist version info to version.json."""
    with open(VERSION_FILE, "w") as f:
        json.dump(info, f, indent=2)


def get_version():
    """Get current version string."""
    return get_version_info().get("version", INITIAL_VERSION)


def increment_version(description="Iteration update"):
    """Increment version by 0.0.1 (patch increment)."""
    info = get_version_info()
    cur_ver = info.get("version", INITIAL_VERSION)
    major, minor, patch = parse_version(cur_ver)

    new_patch = patch + 1
    new_ver = format_version(major, minor, new_patch)
    iteration = info.get("iteration", 1) + 1

    info["version"] = new_ver
    info["iteration"] = iteration
    info["updated_at"] = int(time.time())

    changelog = info.get("changelog", [])
    changelog.append({
        "version": new_ver,
        "iteration": iteration,
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "description": description
    })
    info["changelog"] = changelog

    save_version_info(info)
    return new_ver, iteration


# ---------------------------------------------------------------------------
# Flask Blueprint
# ---------------------------------------------------------------------------
version_bp = Blueprint("version", __name__)


@version_bp.route("/api/version", methods=["GET"])
def api_get_version():
    return jsonify(get_version_info())


@version_bp.route("/api/version/increment", methods=["POST"])
def api_increment_version():
    from modules.auth import login_required

    data = request.get_json(silent=True) or {}
    desc = data.get("description", "User iteration increment")
    new_v, it = increment_version(desc)
    return jsonify({
        "success": True,
        "version": new_v,
        "iteration": it,
        "message": "Version incremented to %s (Iteration %d)" % (new_v, it)
    })
