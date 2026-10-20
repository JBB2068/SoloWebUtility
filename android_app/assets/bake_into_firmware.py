#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
bake_into_firmware.py -- Bake SoloWebUtility2 into 3DR Solo i.MX6 Firmware.
Injects the SoloWebUtility2 application files and /etc/rc.local startup entry
into firmware/3dr-solo.tar.gz and any Clean Install .zip files found in firmware/.

Runs on the PC during development and deployment so the i.MX6 CPU never has to
recompress the 76MB archive on the fly.
"""

import os
import sys
import time
import shutil
import hashlib
import tarfile
import zipfile
import tempfile

try:
    from StringIO import StringIO
except ImportError:
    from io import BytesIO as StringIO

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIRMWARE_DIR = os.path.join(BASE_DIR, "firmware")

TOP_FILES = [
    "app.py", "solo_web_app.py", "webcore.py", "compat.py", "param_fetch.py",
    "auth.py", "change_password.py", "presets.py", "param_mgr.py", "wifi_mgr.py",
    "flash_mgr.py", "pair_mgr.py", "calib_mgr.py", "controller_mgr.py",
    "cockpit_mgr.py", "version_mgr.py", "arducopter_builder.py", "version.json",
    "solo.param"
]
TREE_DIRS = ["modules", "templates", "static", "bin"]
SKIP_EXTS = (".pyc", ".pyo", ".apk", ".idsig")
SKIP_DIRS = ("__pycache__", ".git", "firmware", "firmware_files", "cache_staging", "tmp_updates", "build", "dist", "docs", "scratch")

INITD_SERVICE_CONTENT = (
    "#!/bin/sh\n"
    "### BEGIN INIT INFO\n"
    "# Provides:          solowebutility\n"
    "# Required-Start:    $network $local_fs\n"
    "# Required-Stop:\n"
    "# Default-Start:     5\n"
    "# Default-Stop:      0 1 6\n"
    "# Short-Description: 3DR Solo Web Utility\n"
    "### END INIT INFO\n"
    "\n"
    "case \"$1\" in\n"
    "    start)\n"
    "        echo \"Starting 3DR Solo Web Utility...\"\n"
    "        if fuser 80/tcp >/dev/null 2>&1 || ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep >/dev/null 2>&1; then\n"
    "            echo \"3DR Solo Web Utility is already running.\"\n"
    "            exit 0\n"
    "        fi\n"
    "        mkdir -p /log/firmware 2>/dev/null || true\n"
    "        if [ -d /usr/bin/SoloWebUtility2/firmware ]; then\n"
    "            for f in /usr/bin/SoloWebUtility2/firmware/*.apj; do\n"
    "                [ -f \"$f\" ] && [ ! -f \"/log/firmware/$(basename \"$f\")\" ] && cp \"$f\" /log/firmware/ 2>/dev/null || true\n"
    "            done\n"
    "        fi\n"
    "        APP_DIR=\"\"\n"
    "        if [ -f /usr/bin/SoloWebUtility2/solo_web_app.py ] || [ -f /usr/bin/SoloWebUtility2/app.py ]; then\n"
    "            APP_DIR=\"/usr/bin/SoloWebUtility2\"\n"
    "        fi\n"
    "        if [ -f /log/SoloWebUtility2/solo_web_app.py ] || [ -f /log/SoloWebUtility2/app.py ]; then\n"
    "            APP_DIR=\"/log/SoloWebUtility2\"\n"
    "        fi\n"
    "        if [ -z \"$APP_DIR\" ]; then\n"
    "            if [ -f /home/root/SoloWebUtility2/solo_web_app.py ] || [ -f /home/root/SoloWebUtility2/app.py ]; then\n"
    "                APP_DIR=\"/home/root/SoloWebUtility2\"\n"
    "            fi\n"
    "        fi\n"
    "        if [ -n \"$APP_DIR\" ]; then\n"
    "            ENTRY=\"\"\n"
    "            if [ -f \"$APP_DIR/solo_web_app.py\" ]; then\n"
    "                ENTRY=\"solo_web_app.py\"\n"
    "            elif [ -f \"$APP_DIR/app.py\" ]; then\n"
    "                ENTRY=\"app.py\"\n"
    "            fi\n"
    "            if [ -n \"$ENTRY\" ]; then\n"
    "                cd \"$APP_DIR\" && nohup /usr/bin/python \"$APP_DIR/$ENTRY\" 80 </dev/null >/tmp/solo_web.log 2>&1 &\n"
    "                echo \"3DR Solo Web Utility started from $APP_DIR/$ENTRY on port 80.\"\n"
    "            fi\n"
    "        else\n"
    "            echo \"Error: SoloWebUtility2 not found in /usr/bin, /log, or /home/root.\"\n"
    "        fi\n"
    "        ;;\n"
    "    stop)\n"
    "        echo \"Stopping 3DR Solo Web Utility...\"\n"
    "        fuser -k 80/tcp 2>/dev/null || true\n"
    "        for p in $(ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep | awk '{print $1}'); do\n"
    "            kill -9 $p 2>/dev/null || true\n"
    "        done\n"
    "        ;;\n"
    "    restart)\n"
    "        $0 stop\n"
    "        sleep 1\n"
    "        $0 start\n"
    "        ;;\n"
    "    status)\n"
    "        if ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep >/dev/null 2>&1; then\n"
    "            echo \"3DR Solo Web Utility is running.\"\n"
    "            exit 0\n"
    "        else\n"
    "            echo \"3DR Solo Web Utility is NOT running.\"\n"
    "            exit 1\n"
    "        fi\n"
    "        ;;\n"
    "    *)\n"
    "        echo \"Usage: $0 {start|stop|restart|status}\"\n"
    "        exit 1\n"
    "        ;;\n"
    "esac\n"
    "\n"
    "exit 0\n"
)

RC_LOCAL_CONTENT = (
    "#!/bin/sh -e\n"
    "#\n"
    "# rc.local\n"
    "#\n"
    "# This script is executed at the end of each multiuser runlevel.\n"
    "# Make sure that the script will \"exit 0\" on success or any other\n"
    "# value on error.\n"
    "\n"
    "# Start 3DR Solo Web Utility on port 80\n"
    "if [ -x /etc/init.d/solowebutility ]; then\n"
    "    /etc/init.d/solowebutility start || true\n"
    "elif [ -f /usr/bin/SoloWebUtility2/solo_web_app.py ]; then\n"
    "    cd /usr/bin/SoloWebUtility2 && nohup /usr/bin/python /usr/bin/SoloWebUtility2/solo_web_app.py 80 </dev/null >/tmp/solo_web.log 2>&1 &\n"
    "elif [ -f /log/SoloWebUtility2/solo_web_app.py ]; then\n"
    "    cd /log/SoloWebUtility2 && nohup /usr/bin/python /log/SoloWebUtility2/solo_web_app.py 80 </dev/null >/tmp/solo_web.log 2>&1 &\n"
    "elif [ -f /usr/bin/SoloWebUtility2/app.py ]; then\n"
    "    cd /usr/bin/SoloWebUtility2 && nohup /usr/bin/python /usr/bin/SoloWebUtility2/app.py 80 </dev/null >/tmp/solo_web.log 2>&1 &\n"
    "elif [ -f /log/SoloWebUtility2/app.py ]; then\n"
    "    cd /log/SoloWebUtility2 && nohup /usr/bin/python /log/SoloWebUtility2/app.py 80 </dev/null >/tmp/solo_web.log 2>&1 &\n"
    "fi\n"
    "\n"
    "exit 0\n"
)


def calc_file_md5(filepath):
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _is_skip_member(name):
    norm = name.replace("\\", "/").lstrip("./").lstrip("/")
    if norm in ("etc/rc.local", "rc.local"):
        return True
    if "solowebutility" in norm.lower():
        return True
    if "SoloWebUtility" in norm:
        return True
    if norm.endswith(("arducopter_solo.apj", "arducopter_green.apj")):
        return True
    return False


def bake_tarball(tar_path, app_base_dir=BASE_DIR, log_func=None):
    """Inject SoloWebUtility2 and /etc/rc.local into 3dr-solo.tar.gz and update .md5."""
    if not os.path.isfile(tar_path):
        if log_func:
            log_func("[!] Tarball does not exist: %s" % tar_path)
        return False, None

    if log_func:
        log_func("[*] Baking SoloWebUtility2 into %s..." % os.path.basename(tar_path))

    tmp_dir = tempfile.mkdtemp(prefix="solo_bake_")
    tmp_path = os.path.join(tmp_dir, "3dr-solo-baked.tar.gz")

    injected_count = 0
    created_dirs = set()

    def _ensure_dir(tout, dir_name):
        norm = dir_name.strip("/")
        if not norm or norm in created_dirs:
            return
        parts = norm.split("/")
        cur = ""
        for p in parts:
            cur = (cur + "/" + p) if cur else p
            if cur not in created_dirs:
                td = tarfile.TarInfo(name=cur)
                td.type = tarfile.DIRTYPE
                td.mode = 0o755
                td.uid = 0
                td.gid = 0
                td.uname = "root"
                td.gname = "root"
                td.mtime = int(time.time())
                tout.addfile(td)
                created_dirs.add(cur)

    def _add_file(tout, local_fp, arc_name):
        parent_dir = os.path.dirname(arc_name).replace("\\", "/")
        if parent_dir:
            _ensure_dir(tout, parent_dir)

        with open(local_fp, "rb") as fl:
            raw = fl.read()

        # Strict Unix LF line endings for all text files (zero \r)
        is_text = local_fp.endswith((".py", ".sh", ".json", ".html", ".css", ".js", ".txt", ".param", ".conf", ".md"))
        if is_text:
            raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

        ti = tarfile.TarInfo(name=arc_name)
        ti.size = len(raw)
        ti.mode = 0o755
        ti.uid = 0
        ti.gid = 0
        ti.uname = "root"
        ti.gname = "root"
        ti.mtime = int(time.time())
        ti.type = tarfile.REGTYPE
        tout.addfile(ti, StringIO(raw))

    try:
        with tarfile.open(tar_path, "r:gz") as tin:
            members = tin.getmembers()
            with tarfile.open(tmp_path, "w:gz") as tout:
                # Copy existing members (skip any old rc.local or SoloWebUtility entries)
                for m in members:
                    if _is_skip_member(m.name):
                        continue
                    if m.isreg():
                        tout.addfile(m, tin.extractfile(m))
                    else:
                        tout.addfile(m)

                # Ensure base directories exist
                _ensure_dir(tout, "etc")
                _ensure_dir(tout, "etc/init.d")
                _ensure_dir(tout, "etc/rc5.d")
                _ensure_dir(tout, "usr/bin/SoloWebUtility2")

                # Add etc/init.d/solowebutility (mode 0755, uid=0, gid=0, strict Unix LF)
                init_clean = INITD_SERVICE_CONTENT.replace("\r\n", "\n").replace("\r", "\n")
                init_bytes = init_clean.encode("utf-8") if sys.version_info[0] >= 3 else init_clean
                ti_init = tarfile.TarInfo(name="etc/init.d/solowebutility")
                ti_init.size = len(init_bytes)
                ti_init.mode = 0o755
                ti_init.uid = 0
                ti_init.gid = 0
                ti_init.uname = "root"
                ti_init.gname = "root"
                ti_init.mtime = int(time.time())
                ti_init.type = tarfile.REGTYPE
                tout.addfile(ti_init, StringIO(init_bytes))

                # Add etc/rc5.d/S99solowebutility symlink -> ../init.d/solowebutility (mode 0755, uid=0, gid=0)
                ti_sym = tarfile.TarInfo(name="etc/rc5.d/S99solowebutility")
                ti_sym.type = tarfile.SYMTYPE
                ti_sym.linkname = "../init.d/solowebutility"
                ti_sym.mode = 0o755
                ti_sym.uid = 0
                ti_sym.gid = 0
                ti_sym.uname = "root"
                ti_sym.gname = "root"
                ti_sym.mtime = int(time.time())
                tout.addfile(ti_sym)

                # Add etc/rc.local (executable mode 0755, uid=0, gid=0, strict Unix LF)
                rc_clean = RC_LOCAL_CONTENT.replace("\r\n", "\n").replace("\r", "\n")
                rc_bytes = rc_clean.encode("utf-8") if sys.version_info[0] >= 3 else rc_clean
                ti_rc = tarfile.TarInfo(name="etc/rc.local")
                ti_rc.size = len(rc_bytes)
                ti_rc.mode = 0o755
                ti_rc.uid = 0
                ti_rc.gid = 0
                ti_rc.uname = "root"
                ti_rc.gname = "root"
                ti_rc.mtime = int(time.time())
                ti_rc.type = tarfile.REGTYPE
                tout.addfile(ti_rc, StringIO(rc_bytes))

                # Add top-level SoloWebUtility2 files
                for fn in TOP_FILES:
                    fp = os.path.join(app_base_dir, fn)
                    if os.path.isfile(fp):
                        arcname = "usr/bin/SoloWebUtility2/%s" % fn
                        _add_file(tout, fp, arcname)
                        injected_count += 1

                # Add tree directories (modules, templates, static, bin)
                for td in TREE_DIRS:
                    dir_path = os.path.join(app_base_dir, td)
                    if os.path.isdir(dir_path):
                        for root, d_list, f_list in os.walk(dir_path):
                            d_list[:] = [d for d in d_list if d not in SKIP_DIRS]
                            for fn in f_list:
                                if fn.endswith(SKIP_EXTS):
                                    continue
                                fp = os.path.join(root, fn)
                                rel = os.path.relpath(fp, app_base_dir).replace("\\", "/")
                                arcname = "usr/bin/SoloWebUtility2/%s" % rel
                                _add_file(tout, fp, arcname)
                                injected_count += 1

                # Add firmware APJ and param files into usr/bin/SoloWebUtility2/firmware/
                local_fw = os.path.dirname(tar_path)
                if not os.path.isdir(local_fw) or not any(f.endswith(".apj") for f in os.listdir(local_fw)):
                    local_fw = os.path.join(app_base_dir, "firmware")
                if os.path.isdir(local_fw):
                    _ensure_dir(tout, "usr/bin/SoloWebUtility2/firmware")
                    for fw_fn in sorted(os.listdir(local_fw)):
                        if fw_fn.endswith((".apj", ".param")):
                            if fw_fn in ("arducopter_solo.apj", "arducopter_green.apj"):
                                continue
                            fw_fp = os.path.join(local_fw, fw_fn)
                            if os.path.isfile(fw_fp):
                                arcname = "usr/bin/SoloWebUtility2/firmware/%s" % fw_fn
                                _add_file(tout, fw_fp, arcname)
                                injected_count += 1

        # Atomically replace target file
        if os.path.exists(tar_path):
            os.remove(tar_path)
        shutil.move(tmp_path, tar_path)
        try:
            os.utime(tar_path, None)
        except Exception:
            pass

        # Regenerate MD5 file
        digest = calc_file_md5(tar_path)
        md5_file = tar_path + ".md5"
        base_name = os.path.basename(tar_path)
        with open(md5_file, "w") as f:
            f.write("%s  %s\n" % (digest, base_name))

        # Verify baked archive
        ok_verify, v_errors, swu_count = verify_tarball(tar_path)
        if not ok_verify:
            if log_func:
                log_func("[!] Verification failed for %s:" % base_name)
                for err in v_errors:
                    log_func("    - %s" % err)
            return False, None

        if log_func:
            log_func("[OK] Verified %s: 1 init.d service + 1 rc5.d symlink + 1 rc.local (all 0755, Unix LF), %d SoloWebUtility2 files, MD5: %s." % (
                base_name, swu_count, digest[:8]
            ))

        return True, digest
    finally:
        if os.path.exists(tmp_dir):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass


def verify_tarball(tar_path):
    """
    Verify baked tarball contains:
    - exactly one etc/rc.local (0755, uid=0, gid=0, LF)
    - exactly one etc/init.d/solowebutility (0755, uid=0, gid=0, LF)
    - exactly one etc/rc5.d/S99solowebutility (symlink to ../init.d/solowebutility)
    - versioned arducopter_CubeSolo_*.apj and arducopter_CubeGreen_*.apj
    - NO legacy unversioned arducopter_solo.apj or arducopter_green.apj
    - all scripts have mode 0755, uid=0, gid=0, and no CRLF.
    """
    rc_count = 0
    init_count = 0
    rc5_count = 0
    swu_files = 0
    apj_files = 0
    errors = []
    with tarfile.open(tar_path, "r:gz") as t:
        for m in t.getmembers():
            norm = m.name.replace("\\", "/").lstrip("./").lstrip("/")
            if norm == "etc/rc.local":
                rc_count += 1
                if m.mode != 0o755:
                    errors.append("etc/rc.local mode is %s, expected 0755" % oct(m.mode))
                if m.uid != 0 or m.gid != 0:
                    errors.append("etc/rc.local uid/gid is %d/%d, expected 0/0" % (m.uid, m.gid))
                f = t.extractfile(m)
                if f and b"\r" in f.read():
                    errors.append("etc/rc.local contains Windows CR (\\r) line endings!")
            elif norm == "etc/init.d/solowebutility":
                init_count += 1
                if m.mode != 0o755:
                    errors.append("etc/init.d/solowebutility mode is %s, expected 0755" % oct(m.mode))
                if m.uid != 0 or m.gid != 0:
                    errors.append("etc/init.d/solowebutility uid/gid is %d/%d, expected 0/0" % (m.uid, m.gid))
                f = t.extractfile(m)
                if f and b"\r" in f.read():
                    errors.append("etc/init.d/solowebutility contains Windows CR (\\r) line endings!")
            elif norm == "etc/rc5.d/S99solowebutility":
                rc5_count += 1
                if not (m.issym() or m.isreg()):
                    errors.append("etc/rc5.d/S99solowebutility is not a symlink or regular file!")
                if m.issym() and m.linkname not in ("../init.d/solowebutility", "/etc/init.d/solowebutility"):
                    errors.append("etc/rc5.d/S99solowebutility symlink target is %s, expected ../init.d/solowebutility" % m.linkname)
            elif "SoloWebUtility2" in norm and m.isreg():
                swu_files += 1
                if norm.endswith(".apj"):
                    if norm.endswith(("arducopter_solo.apj", "arducopter_green.apj")):
                        errors.append("Legacy unversioned APJ found in archive: %s" % norm)
                    else:
                        apj_files += 1
                if m.uid != 0 or m.gid != 0:
                    errors.append("%s uid/gid is %d/%d, expected 0/0" % (m.name, m.uid, m.gid))
                if m.mode != 0o755:
                    errors.append("%s mode is %s, expected 0755" % (m.name, oct(m.mode)))
                if norm.endswith((".py", ".sh", ".html", ".js", ".css", ".json", ".txt", ".param", ".conf", ".md")):
                    f = t.extractfile(m)
                    if f and b"\r" in f.read():
                        errors.append("%s contains Windows CR (\\r) line endings!" % m.name)

    if rc_count != 1:
        errors.append("Found %d instances of etc/rc.local, expected exactly 1" % rc_count)
    if init_count != 1:
        errors.append("Found %d instances of etc/init.d/solowebutility, expected exactly 1" % init_count)
    if rc5_count != 1:
        errors.append("Found %d instances of etc/rc5.d/S99solowebutility, expected exactly 1" % rc5_count)
    if swu_files == 0:
        errors.append("No SoloWebUtility2 files found in archive!")
    if apj_files == 0:
        errors.append("No ArduCopter firmware (.apj) files found in usr/bin/SoloWebUtility2/firmware/!")
    return len(errors) == 0, errors, swu_files


def get_source_files_to_check(app_base_dir=BASE_DIR, firmware_dir=FIRMWARE_DIR):
    """
    Collect all project source files whose changes require re-baking 3dr-solo.tar.gz.
    Includes:
    - Root app scripts and assets (.py, .json, .param, etc.)
    - Directories: modules/, templates/, static/, bin/
    - Firmware APJ files (*.apj) and parameter files (*.param) in firmware/
    Excludes:
    - 3dr-solo.tar.gz itself and .md5 checksums
    - 3dr-controller.tar.gz and .md5
    - .pyc, .pyo, cache directories, git, docs, scratch
    """
    sources = []

    # Root files in app_base_dir
    for fn in TOP_FILES:
        fp = os.path.join(app_base_dir, fn)
        if os.path.isfile(fp):
            sources.append(fp)

    # Extra root files that define baking/deployment logic
    extra_root = [
        "bake_into_firmware.py", "deploy_to_drone.py", "deploy_to_drone.bat", "install_to_solo.py"
    ]
    for fn in extra_root:
        fp = os.path.join(app_base_dir, fn)
        if os.path.isfile(fp) and fp not in sources:
            sources.append(fp)

    # Recursive directories
    for td in TREE_DIRS:
        dir_path = os.path.join(app_base_dir, td)
        if os.path.isdir(dir_path):
            for root, d_list, f_list in os.walk(dir_path):
                d_list[:] = [d for d in d_list if d not in SKIP_DIRS]
                for fn in f_list:
                    if fn.endswith(SKIP_EXTS):
                        continue
                    fp = os.path.join(root, fn)
                    sources.append(fp)

    # Firmware APJs and params that get baked into usr/bin/SoloWebUtility2/firmware
    if os.path.isdir(firmware_dir):
        for fn in os.listdir(firmware_dir):
            if fn.endswith((".apj", ".param")):
                if fn in ("arducopter_solo.apj", "arducopter_green.apj"):
                    continue
                fp = os.path.join(firmware_dir, fn)
                if os.path.isfile(fp) and fp not in sources:
                    sources.append(fp)

    return sources


def is_firmware_stale(tar_path=None, app_base_dir=BASE_DIR, firmware_dir=FIRMWARE_DIR):
    """
    Check if any project source file has a newer mtime than 3dr-solo.tar.gz.
    Returns (is_stale, list_of_newer_files).
    """
    if tar_path is None:
        tar_path = os.path.join(firmware_dir, "3dr-solo.tar.gz")
    if not os.path.isfile(tar_path):
        return True, ["(archive missing: %s)" % os.path.basename(tar_path)]

    tar_mtime = os.path.getmtime(tar_path)
    md5_path = tar_path + ".md5"
    if not os.path.isfile(md5_path):
        return True, ["(checksum missing: %s.md5)" % os.path.basename(tar_path)]

    sources = get_source_files_to_check(app_base_dir=app_base_dir, firmware_dir=firmware_dir)
    newer = []
    for fp in sources:
        try:
            m = os.path.getmtime(fp)
            if m > tar_mtime:
                rel = os.path.relpath(fp, app_base_dir).replace("\\", "/")
                newer.append((rel, m))
        except (OSError, IOError):
            pass

    if newer:
        newer.sort(key=lambda x: x[1], reverse=True)
        return True, [x[0] for x in newer]
    return False, []


def ensure_firmware_baked(tar_path=None, app_base_dir=BASE_DIR, firmware_dir=FIRMWARE_DIR, force=False, log_func=print):
    """
    Automatic build hook: verifies if 3dr-solo.tar.gz is stale against any source file.
    If stale or force=True, automatically re-bakes the firmware archive and regenerates .md5.
    Returns True if firmware is up to date and valid.
    """
    def _log(msg):
        if log_func:
            log_func(msg)

    # If running on the drone i.MX6 hardware, do NOT recompress 76MB archive locally
    if os.path.exists("/etc/solo-release") or (os.path.isdir("/log/firmware") and not os.path.exists("c:\\")):
        _log("[*] Solo Drone i.MX6 environment detected: using pre-baked firmware archive.")
        return True

    if tar_path is None:
        tar_path = os.path.join(firmware_dir, "3dr-solo.tar.gz")

    stale, newer_files = is_firmware_stale(tar_path=tar_path, app_base_dir=app_base_dir, firmware_dir=firmware_dir)
    if not stale and not force:
        _log("[OK] Pre-baked firmware archive %s is up to date." % os.path.basename(tar_path))
        return True

    _log("=" * 65)
    _log("[*] AUTO-BAKE TRIGGER: 3dr-solo.tar.gz is stale or requires update!")
    if newer_files:
        _log("[*] %d modified source file(s) detected since last bake:" % len(newer_files))
        for nf in newer_files[:8]:
            _log("    - %s" % nf)
        if len(newer_files) > 8:
            _log("    ... and %d more file(s)" % (len(newer_files) - 8))
    _log("[*] Programmatically executing bake_into_firmware...")
    _log("=" * 65)

    ok = bake_into_firmware(firmware_dir=firmware_dir, app_base_dir=app_base_dir, log_func=_log)
    if not ok:
        _log("[!] CRITICAL ERROR: Auto-baking failed for %s!" % tar_path)
        return False

    _log("[OK] Auto-bake successful! %s is synchronized and verified." % os.path.basename(tar_path))
    return True


def bake_into_firmware(firmware_dir=FIRMWARE_DIR, app_base_dir=BASE_DIR, log_func=print):
    """Main entrypoint to bake SoloWebUtility2 into all local firmware files."""
    if log_func:
        log_func("=" * 65)
        log_func("[*] SoloWebUtility2 Firmware Baker (PC / Local)")
        log_func("=" * 65)

    tar_path = os.path.join(firmware_dir, "3dr-solo.tar.gz")
    if not os.path.isfile(tar_path):
        if log_func:
            log_func("[!] Error: %s not found!" % tar_path)
        return False

    ok, digest = bake_tarball(tar_path, app_base_dir=app_base_dir, log_func=log_func)
    if not ok:
        if log_func:
            log_func("[!] Failed to bake tarball!")
        return False

    if log_func:
        log_func("=" * 65)
        log_func("[OK] Firmware baking complete! MD5: %s" % digest)
        log_func("=" * 65)
    return True


if __name__ == "__main__":
    if "--check" in sys.argv:
        stale, newer = is_firmware_stale()
        if stale:
            print("[!] Firmware archive is STALE. %d modified file(s) found." % len(newer))
            for f in newer[:5]:
                print("    - %s" % f)
            sys.exit(1)
        else:
            print("[OK] Firmware archive is up to date.")
            sys.exit(0)
    else:
        success = ensure_firmware_baked(force=("--force" in sys.argv or len(sys.argv) == 1))
        sys.exit(0 if success else 1)
