#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Raise the Solo Web Utility version by 0.0.1 (or to --to X.Y.Z) everywhere it is written.

The version is part of the files baked into the golden image (version.json, the web page), so run
this BEFORE bake_into_firmware.py, never after: otherwise build_exe/build_apk would find the image
out of date and try to re-bake it.

  python tools/bump_version.py "What changed in this release"       1.2.0 -> 1.2.1
  python tools/bump_version.py --to 1.3.0 "What changed"
  python tools/bump_version.py --check                               every file shows version.json's version

What it changes:
  * version.json: version, iteration + 1, updated_at, a changelog entry
  * the version in every file of VERSION_FILES (page header, cache-busters, manuals, installer
    names, build scripts, the website), including the Windows file-version tuple and the APK versionCode
  * renames release/Solo Web Utility vX.spec and release/version_info_X.txt
Line endings and everything else in the files are left exactly as they are.
"""
from __future__ import print_function

import io
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files that carry the current version (paths relative to ROOT; {v} = the version)
VERSION_FILES = [
    "static/index.html", "templates/index.html",
    "static/manual.html", "static/solo_drone_manual.html",
    "docs/index.html", "docs/manual.html", "docs/solo_drone_manual.html",
    "deploy_to_drone.py", "install_to_solo.py", "version_info.txt", "README.md",
    "release/Solo Web Utility v{v}.spec", "release/version_info_{v}.txt",
    "release/build_exe.bat", "release/build_apk.bat", "release/build_mac.bat",
    "android_installer/build.ps1", "android_installer/stage_assets.py", "android_installer/README.md",
    "mac_installer/build_mac.py", "mac_installer/install_solo_web_utility.command", "mac_installer/README.txt",
    "worker/index.js", "worker/page.html",
]
RENAMED = ["release/Solo Web Utility v{v}.spec", "release/version_info_{v}.txt"]
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def parse(v):
    if not VERSION_RE.match(v):
        raise SystemExit("Not a version (X.Y.Z): %r" % v)
    return tuple(int(x) for x in v.split("."))


def version_code(v):
    """Android versionCode: always increases (1.2.1 -> 10201)."""
    a, b, c = parse(v)
    return a * 10000 + b * 100 + c


def read_version_json():
    with io.open(os.path.join(ROOT, "version.json"), encoding="utf-8") as f:
        return json.load(f)


def _replace_in(path, old, new):
    with open(path, "rb") as f:
        data = f.read()
    oa, ob, oc = parse(old)
    na, nb, nc = parse(new)
    out = data.replace(old.encode("ascii"), new.encode("ascii"))
    # Windows file version tuple: filevers=(1, 2, 0, 0)
    out = re.sub(br"\(\s*%d,\s*%d,\s*%d,\s*0\s*\)" % (oa, ob, oc), b"(%d, %d, %d, 0)" % (na, nb, nc), out)
    # APK versionCode in build.ps1
    out = re.sub(br'(\$VersionCode\s*=\s*")\d+(")', lambda m: m.group(1) + str(version_code(new)).encode("ascii") + m.group(2), out)
    if out != data:
        with open(path, "wb") as f:
            f.write(out)
        return True
    return False


def check(version):
    """Files of VERSION_FILES that are missing or do not mention version."""
    bad = []
    for rel in VERSION_FILES:
        path = os.path.join(ROOT, rel.format(v=version))
        if not os.path.isfile(path):
            bad.append(rel.format(v=version) + " (missing)")
            continue
        with open(path, "rb") as f:
            if version.encode("ascii") not in f.read():
                bad.append(rel.format(v=version))
    return bad


def bump(description, new=None):
    info = read_version_json()
    old = info["version"]
    a, b, c = parse(old)
    new = new or "%d.%d.%d" % (a, b, c + 1)
    if parse(new) <= parse(old):
        raise SystemExit("The new version %s must be higher than %s" % (new, old))
    missing = [r for r in check(old) if r.endswith("(missing)")]
    if missing:
        raise SystemExit("Missing files, nothing changed:\n  " + "\n  ".join(missing))
    for rel in RENAMED:
        os.rename(os.path.join(ROOT, rel.format(v=old)), os.path.join(ROOT, rel.format(v=new)))
    changed = []
    for rel in VERSION_FILES:
        if _replace_in(os.path.join(ROOT, rel.format(v=new)), old, new):
            changed.append(rel.format(v=new))
    info["version"] = new
    info["iteration"] = int(info.get("iteration", 0)) + 1
    info["updated_at"] = int(time.time())
    info.setdefault("changelog", []).append({
        "version": new, "iteration": info["iteration"],
        "date": time.strftime("%Y-%m-%d %H:%M:%S"), "description": description})
    with io.open(os.path.join(ROOT, "version.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(info, indent=2, ensure_ascii=False))
    left = check(new)
    print("Version %s -> %s (iteration %d), %d files updated" % (old, new, info["iteration"], len(changed) + 1))
    if left:
        raise SystemExit("These files do not show %s:\n  %s" % (new, "\n  ".join(left)))
    return old, new


def main(argv):
    if "--check" in argv:
        v = read_version_json()["version"]
        bad = check(v)
        if bad:
            print("Not showing version %s:\n  %s" % (v, "\n  ".join(bad)))
            return 1
        print("[OK] Every file shows version %s" % v)
        return 0
    new = None
    if "--to" in argv:
        i = argv.index("--to")
        new = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    words = [a for a in argv if not a.startswith("--")]
    if not words:
        raise SystemExit(__doc__)
    bump(" ".join(words), new)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
