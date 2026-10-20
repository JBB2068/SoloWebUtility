#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Version Management for 3DR Solo Web Utility.
Tracks and increments version starting at 1.0.1 with 0.0.1 increments per iteration.
Compatible with Python 2.7 and Python 3.x.
"""

import os
import json
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
                {"version": INITIAL_VERSION, "date": time.strftime("%Y-%m-%d %H:%M:%S"), "description": "Initial Release of 3DR Solo Web Utility (Open Solo 4.7.2 & Flashing Manager)."}
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


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] in ["--inc", "-i", "inc"]:
        desc = sys.argv[2] if len(sys.argv) > 2 else "Manual version bump"
        v, it = increment_version(desc)
        print("[*] Version bumped to: %s (Iteration %d)" % (v, it))
    else:
        print("[*] Current Version: %s (Iteration %d)" % (get_version(), get_version_info().get("iteration", 1)))
