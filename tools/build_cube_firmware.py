#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build the Solo's two Cube firmwares on this PC with the same code the Solo uses for "Check for updates".

  python tools\\build_cube_firmware.py [--version 4.7.3] [--out release\\cube_builds]

What it does (it uses arducopter_builder.py as it is, nothing is changed in the project):
  * asks custom.ardupilot.org for the stable Copter release (the newest one, or --version X.Y.Z)
  * builds CubeSolo and CubeGreen-solo with the features in firmware\\solocustombuild.yaml
  * embeds the Solo parameter defaults (firmware\\Solo.param / SoloGreen.param) with bin\\apj_tool.py
  * writes arducopter_CubeSolo_<ver>.apj and arducopter_CubeGreen_<ver>.apj and a short report (MD5 + size)
    into the output folder (default release\\cube_builds) and NOTHING else: the firmware\\ folder is not touched,
    no older build is moved into previous\\, and the Solo/Controller are never contacted.
custom.ardupilot.org allows 10 builds per hour; this uses 2.
Python 2.7 and 3 compatible (no f-strings).
"""
from __future__ import print_function

import hashlib
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    want = arg("--version")
    out = os.path.abspath(arg("--out", os.path.join(ROOT, "release", "cube_builds")))

    # The builder reaches for the Solo's Wi-Fi helper and the Controller's internet proxy; on a PC neither exists.
    stub = types.ModuleType("wifi_mgr")
    stub.ensure_copter_internet_route = lambda *a, **k: None
    sys.modules["wifi_mgr"] = stub

    import arducopter_builder as ab

    def no_controller(*a, **k):
        raise RuntimeError("no Controller proxy on a PC")
    ab._http_get_via_controller = no_controller
    ab._http_post_via_controller = no_controller
    ab.clean_old_firmware_files = lambda *a, **k: []      # keep the PC's firmware\ folder exactly as it is
    ab.update_version_references = lambda *a, **k: None

    if not os.path.isdir(out):
        os.makedirs(out)
    ab.FIRMWARE_DIR = out                                   # builds are written here, params/config are found in firmware\ and bin\

    print("=" * 66)
    print(" Building the Solo Cube firmware on this PC")
    print(" Output folder: %s" % out)
    print("=" * 66)

    if want:
        versions = ab._api_get_json("/api/v1/vehicles/copter/versions?type=stable")
        match = [v for v in versions if v.get("name") == want and v.get("type") == "stable"]
        if not match:
            names = sorted(set(str(v.get("name")) for v in versions if v.get("type") == "stable"))
            print("[!] custom.ardupilot.org has no stable Copter %s. It offers: %s" % (want, ", ".join(names)))
            return 1
        v = match[0]
        ver = {"id": ab._native(v.get("id")), "name": ab._native(v.get("name")), "type": "stable",
               "commit_ref": ab._native(v.get("commit_ref") or ""), "vehicle_id": "copter"}
    else:
        ver = ab.get_latest_stable_version()
    print("Copter version: %s" % ver["name"])

    config, cfg_path = ab.load_build_config()
    print("Features from: %s" % cfg_path)
    results = []
    for board in ab.BUILD_BOARDS:
        print("\n---- %s ----" % board)
        res = ab.build_and_stage_cube(board, ver, config=config)
        results.append(res)

    print("\n" + "=" * 66)
    lines = []
    for res in results:
        path = os.path.join(out, res["versioned_file"])
        with open(path, "rb") as f:
            md5 = hashlib.md5(f.read()).hexdigest()
        line = "%s  %d bytes  md5 %s" % (res["versioned_file"], os.path.getsize(path), md5)
        lines.append(line)
        print(line)
    with open(os.path.join(out, "build_report_%s.txt" % ver["name"]), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n[OK] Done. Tell Claude the build finished; the files are in:\n     %s" % out)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("\n[!] FAILED: %s" % e)
        sys.exit(1)
