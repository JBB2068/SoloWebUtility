# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for "Solo Web Utility v1.2.3 Installer.exe"
# One self-contained Windows installer: deploy_to_drone.py + paramiko, the
# SoloWebUtility2 app files it installs on the Solo, and the firmware it stages.
# Build with build_exe.bat (run from this folder). Nothing else is packed.

import os
import glob

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
NAME = "Solo Web Utility v1.2.3 Installer"


def rel(*p):
    return os.path.join(ROOT, *p)


# --- App files: exactly the list deploy_to_drone.py uploads to the Solo ----------
TOP_LEVEL_FILES = [
    "app.py", "solo_web_app.py", "webcore.py", "compat.py", "param_fetch.py",
    "auth.py", "change_password.py", "presets.py", "param_mgr.py", "wifi_mgr.py",
    "flash_mgr.py", "pair_mgr.py", "calib_mgr.py", "controller_mgr.py",
    "cockpit_mgr.py", "version_mgr.py", "arducopter_builder.py", "version.json",
]
TREE_DIRS = ["modules", "templates", "static", "bin"]
SKIP_DIR_NAMES = ("__pycache__", "squashfs")          # bin/squashfs = Windows tools, never run on the Solo
SKIP_EXTS = (".pyc", ".pyo", ".apk", ".idsig", ".exe", ".dll")
SKIP_FILES = ("solo_icon.png", "solo_icon_512.png")  # not referenced by the web pages

datas = []
for f in TOP_LEVEL_FILES:
    datas.append((rel(f), "."))
for d in TREE_DIRS:
    for cur, dirs, files in os.walk(rel(d)):
        dirs[:] = [x for x in dirs if x not in SKIP_DIR_NAMES]
        for fn in files:
            if os.path.splitext(fn)[1].lower() in SKIP_EXTS or fn in SKIP_FILES:
                continue
            src = os.path.join(cur, fn)
            datas.append((src, os.path.relpath(cur, ROOT)))

# --- Firmware staged on the Solo (/log/firmware) ----------------------------------
FW = rel("firmware")
for fn in ["3dr-solo.tar.gz", "3dr-solo.tar.gz.md5",
           "3dr-controller.tar.gz", "3dr-controller.tar.gz.md5",
           "Solo.param", "SoloGreen.param", "solocustombuild.yaml"]:
    datas.append((os.path.join(FW, fn), "firmware"))
for p in sorted(glob.glob(os.path.join(FW, "arducopter_Cube*_*.apj"))):
    datas.append((p, "firmware"))
for p in sorted(glob.glob(os.path.join(FW, "previous", "arducopter_Cube*_*.apj"))):
    datas.append((p, os.path.join("firmware", "previous")))

missing = [s for s, _ in datas if not os.path.isfile(s)]
if missing:
    raise SystemExit("Missing files for the package:\n  " + "\n  ".join(missing))

a = Analysis(
    [rel("deploy_to_drone.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=["paramiko"],
    hookspath=[],
    runtime_hooks=[],
    # the packaged installer never re-bakes (no WSL/SquashFS on end-user PCs)
    excludes=["bake_into_firmware", "tkinter", "unittest", "pydoc", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=NAME,
    debug=False,
    strip=False,
    upx=False,
    console=True,
    version=os.path.join(SPECPATH, "version_info_1.2.3.txt"),
)
