#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
bake_into_firmware.py -- keep the golden copter image firmware/3dr-solo.tar.gz up to date.

firmware/3dr-solo.tar.gz IS the golden image: Open Solo 4.0.0 with SoloWebUtility2
baked into its squashfs rootfs (/usr/bin/SoloWebUtility2 + one SWU respawn line in
/etc/inittab). Running this script after code changes replaces
/usr/bin/SoloWebUtility2 inside that image IN PLACE and rewrites the .md5.

All SquashFS work is done by tools/bake_squashfs.sh, run as root inside WSL on
WSL's own Linux filesystem (never on a Windows drive: NTFS loses execute/setuid
bits, device nodes and case-distinct names, and the copter then cannot boot).

Before the golden image is replaced, the new image is verified entry-by-entry
against tools/stock_reference (a record of the pristine Open Solo 4.0.0 image):
everything except the app folder and the inittab line must be exactly stock.
If verification fails, firmware/3dr-solo.tar.gz is left untouched.

firmware/3dr-solo.tar.gz.baked records what was baked (image MD5 + MD5 of every app
file); the release builds run `python bake_into_firmware.py --auto`, which re-bakes
whenever the current utility differs from that record.

firmware/3dr-solo.tar.gz.md5 is always "<md5>  3dr-solo.tar.gz\n" (two spaces, LF);
the Solo updater's BusyBox md5sum -c rejects anything else.
"""

import os
import re
import sys
import shutil
import hashlib
import tarfile
import tempfile
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FIRMWARE_DIR = os.path.join(BASE_DIR, "firmware")
BAKE_SCRIPT = os.path.join(BASE_DIR, "tools", "bake_squashfs.sh")
STOCK_REF_DIR = os.path.join(BASE_DIR, "tools", "stock_reference")

SQFS_NAME = "3dr-solo-imx6solo-3dr-1080p.squashfs"

TOP_FILES = [
    "app.py", "solo_web_app.py", "webcore.py", "compat.py", "param_fetch.py",
    "auth.py", "change_password.py", "presets.py", "param_mgr.py", "wifi_mgr.py",
    "flash_mgr.py", "pair_mgr.py", "calib_mgr.py", "controller_mgr.py",
    "cockpit_mgr.py", "version_mgr.py", "arducopter_builder.py", "version.json",
    "solo.param"
]
TREE_DIRS = ["modules", "templates", "static", "bin"]
SKIP_EXTS = (".pyc", ".pyo", ".apk", ".idsig", ".exe", ".dll")
SKIP_DIRS = (
    "__pycache__", ".git", "firmware", "firmware_files", "cache_staging",
    "tmp_updates", "build", "dist", "docs", "scratch", "squashfs"
)
TEXT_EXTS = (".py", ".sh", ".json", ".html", ".css", ".js", ".txt", ".param", ".conf", ".md", ".yaml",
             ".apj")   # .apj is JSON: Git on Windows may check it out with CRLF

# Must match SWU_LINE in tools/bake_squashfs.sh
INITTAB_RESPAWN_ENTRY = "SWU:345:respawn:/usr/bin/python /usr/bin/SoloWebUtility2/solo_web_app.py >>/log/solowebutility.log 2>&1\n"

# Deprecated legacy definitions kept for backward compatibility
INITD_SERVICE_CONTENT = ""
RC_LOCAL_CONTENT = ""
DEFAULT_INITTAB = ""


def calc_file_md5(filepath):
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def _is_windows():
    return os.name == "nt"


def _to_wsl_path(win_path):
    """Convert Windows path (C:\\foo\\bar) to WSL path (/mnt/c/foo/bar)."""
    drive, rest = os.path.splitdrive(os.path.abspath(win_path))
    drive_letter = drive.rstrip(":").lower()
    norm_rest = rest.replace("\\", "/")
    return "/mnt/%s%s" % (drive_letter, norm_rest)


def _makedirs(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def _copy_text_or_binary(src, dst):
    _makedirs(os.path.dirname(dst))
    with open(src, "rb") as fl:
        raw = fl.read()
    if src.lower().endswith(TEXT_EXTS):
        raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    with open(dst, "wb") as fl:
        fl.write(raw)


def _copy_swu_into_rootfs(app_base_dir, rootfs_swu_dir, local_firmware_dir=None):
    """Stage the app files (LF-normalised) into rootfs_swu_dir. Returns file count."""
    _makedirs(rootfs_swu_dir)
    injected_count = 0

    for fn in TOP_FILES:
        fp = os.path.join(app_base_dir, fn)
        if os.path.isfile(fp):
            _copy_text_or_binary(fp, os.path.join(rootfs_swu_dir, fn))
            injected_count += 1

    for td in TREE_DIRS:
        td_path = os.path.join(app_base_dir, td)
        if os.path.isdir(td_path):
            for root, d_list, f_list in os.walk(td_path):
                d_list[:] = [d for d in d_list if d not in SKIP_DIRS]
                for fn in f_list:
                    if fn.endswith(SKIP_EXTS):
                        continue
                    src = os.path.join(root, fn)
                    rel = os.path.relpath(src, app_base_dir)
                    _copy_text_or_binary(src, os.path.join(rootfs_swu_dir, rel))
                    injected_count += 1

    if local_firmware_dir is None:
        local_firmware_dir = os.path.join(app_base_dir, "firmware")
    if os.path.isdir(local_firmware_dir):
        fw_dst_dir = os.path.join(rootfs_swu_dir, "firmware")
        _makedirs(fw_dst_dir)
        for fn in sorted(os.listdir(local_firmware_dir)):
            if fn.endswith((".apj", ".param", ".yaml")):
                if fn in ("arducopter_solo.apj", "arducopter_green.apj"):
                    continue
                src = os.path.join(local_firmware_dir, fn)
                if os.path.isfile(src):
                    _copy_text_or_binary(src, os.path.join(fw_dst_dir, fn))
                    injected_count += 1
        # Older ArduCopter builds kept for reverting (firmware/previous/*.apj)
        prev_src_dir = os.path.join(local_firmware_dir, "previous")
        if os.path.isdir(prev_src_dir):
            prev_dst_dir = os.path.join(fw_dst_dir, "previous")
            _makedirs(prev_dst_dir)
            for fn in sorted(os.listdir(prev_src_dir)):
                src = os.path.join(prev_src_dir, fn)
                if fn.endswith(".apj") and os.path.isfile(src):
                    _copy_text_or_binary(src, os.path.join(prev_dst_dir, fn))
                    injected_count += 1

    return injected_count


def _run_bake_script(args, work_dir, log_func=None):
    """Run tools/bake_squashfs.sh as root on a Linux filesystem (WSL on Windows)."""
    if not os.path.isfile(BAKE_SCRIPT):
        raise RuntimeError("Missing %s" % BAKE_SCRIPT)
    # Always run an LF copy of the script (git/editors on Windows may add CRLF)
    with open(BAKE_SCRIPT, "rb") as f:
        script = f.read().replace(b"\r\n", b"\n")
    script_copy = os.path.join(work_dir, "bake_squashfs.sh")
    with open(script_copy, "wb") as f:
        f.write(script)

    if _is_windows():
        cmd = ["wsl", "-u", "root", "-e", "bash", _to_wsl_path(script_copy)]
        cmd += [a if not os.path.isabs(a) else _to_wsl_path(a) for a in args]
    else:
        cmd = ["bash", script_copy] + list(args)
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            cmd = ["sudo"] + cmd

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, _ = proc.communicate()
    text = _decode_wsl(out)
    if log_func:
        for line in text.splitlines():
            if line.strip():
                log_func(line.rstrip())
    return proc.returncode == 0, text


def _decode_wsl(raw):
    """wsl.exe writes its own messages in UTF-16; Linux programs inside it write UTF-8."""
    if isinstance(raw, bytes):
        if raw[1:2] == b"\x00" or raw.count(b"\x00") > len(raw) // 4:
            raw = raw.replace(b"\x00", b"")
        return raw.decode("utf-8", "replace")
    return raw


def _check_wsl(log_func=None):
    """On Windows, baking needs WSL with a Linux distribution and squashfs-tools. Returns True if ready."""
    if not _is_windows():
        return True
    problem = None
    try:
        proc = subprocess.Popen(["wsl", "-u", "root", "-e", "sh", "-c",
                                 "command -v mksquashfs >/dev/null && command -v unsquashfs >/dev/null && echo WSL_OK || echo NO_SQUASHFS"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
        text = _decode_wsl(out)
        if "WSL_OK" in text:
            return True
        if "NO_SQUASHFS" in text:
            problem = ("WSL is installed but squashfs-tools is not. Fix (one time):\n"
                       "        wsl -u root -e sh -c \"apt-get update && apt-get install -y squashfs-tools\"")
        else:
            problem = ("WSL has no Linux distribution installed. Fix (one time, admin PowerShell, then restart):\n"
                       "        wsl --install -d Ubuntu\n"
                       "    then:\n"
                       "        wsl -u root -e sh -c \"apt-get update && apt-get install -y squashfs-tools\"")
    except OSError:
        problem = ("WSL is not installed. Fix (one time, admin PowerShell, then restart):\n"
                   "        wsl --install -d Ubuntu\n"
                   "    then:\n"
                   "        wsl -u root -e sh -c \"apt-get update && apt-get install -y squashfs-tools\"")
    if log_func:
        log_func("[!] Cannot bake: " + problem)
    return False


def _check_tools():
    for f in [BAKE_SCRIPT] + [os.path.join(STOCK_REF_DIR, n) for n in ("rootfs.manifest", "inittab", "boot.md5", "members.txt")]:
        if not os.path.isfile(f):
            raise RuntimeError("Missing %s" % f)


def bake_tarball(tar_path, app_base_dir=BASE_DIR, log_func=None):
    """
    Update /usr/bin/SoloWebUtility2 inside tar_path (the golden image) in place,
    verify the result against the stock reference, then replace tar_path + .md5.
    Returns (ok, md5_hex_or_None). On failure tar_path is not modified.
    """
    base_name = os.path.basename(tar_path)
    if log_func:
        log_func("[*] Updating SoloWebUtility2 inside golden image %s..." % base_name)
    if not os.path.isfile(tar_path):
        if log_func:
            log_func("[!] Golden image not found: %s" % tar_path)
        return False, None
    try:
        _check_tools()
    except RuntimeError as e:
        if log_func:
            log_func("[!] %s" % e)
        return False, None
    if not _check_wsl(log_func):
        if log_func:
            log_func("[!] Firmware bake FAILED -- %s was NOT modified." % base_name)
        return False, None

    work_dir = tempfile.mkdtemp(prefix="solo_bake_")
    try:
        app_stage = os.path.join(work_dir, "app")
        local_fw = _bake_firmware_dir(tar_path, app_base_dir)
        count = _copy_swu_into_rootfs(app_base_dir, app_stage, local_firmware_dir=local_fw)
        if log_func:
            log_func("    Staged %d SoloWebUtility2 files." % count)

        ok, _ = _run_bake_script(["build", os.path.abspath(tar_path), app_stage, STOCK_REF_DIR], work_dir, log_func)
        if not ok:
            if log_func:
                log_func("[!] Firmware bake FAILED -- %s was NOT modified." % base_name)
            return False, None

        digest = calc_file_md5(tar_path)
        if log_func:
            log_func("[OK] %s updated in place and verified against stock. MD5: %s" % (base_name, digest))
        return True, digest
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def verify_tarball(tar_path, runner=None):
    """
    Verify tar_path against the stock reference (entry-by-entry rootfs comparison,
    boot files, inittab, .md5 format). Returns (ok, errors, swu_file_count).
    """
    try:
        _check_tools()
    except RuntimeError as e:
        return False, [str(e)], 0
    work_dir = tempfile.mkdtemp(prefix="solo_verify_")
    try:
        ok, text = _run_bake_script(["verify", os.path.abspath(tar_path), STOCK_REF_DIR], work_dir)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    errors = [] if ok else ([l for l in text.splitlines() if l.strip()][-40:] or ["verification failed"])
    swu_count = 0
    for line in text.splitlines():
        m = re.search(r"(\d+) SoloWebUtility2 entries", line)
        if m:
            swu_count = int(m.group(1))
    return ok, errors, swu_count


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
    - .pyc, .pyo, cache directories, git, docs, scratch, squashfs
    """
    sources = []

    # Root files in app_base_dir
    for fn in TOP_FILES:
        fp = os.path.join(app_base_dir, fn)
        if os.path.isfile(fp):
            sources.append(fp)

    # Extra root files that define baking/deployment logic
    extra_root = [
        "bake_into_firmware.py", "deploy_to_drone.py", "deploy_to_drone.bat", "install_to_solo.py",
        os.path.join("tools", "bake_squashfs.sh")
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
            if fn.endswith((".apj", ".param", ".yaml")):
                if fn in ("arducopter_solo.apj", "arducopter_green.apj"):
                    continue
                fp = os.path.join(firmware_dir, fn)
                if os.path.isfile(fp) and fp not in sources:
                    sources.append(fp)

    return sources


def _bake_firmware_dir(tar_path, app_base_dir):
    """Folder whose .apj/.param/.yaml files are baked next to the app (same rule as bake_tarball)."""
    local_fw = os.path.dirname(os.path.abspath(tar_path))
    if not os.path.isdir(local_fw) or not any(f.endswith(".apj") for f in os.listdir(local_fw)):
        local_fw = os.path.join(app_base_dir, "firmware")
    return local_fw


def _record_path(tar_path):
    """Record of exactly what is baked into tar_path: 'image <md5>' then '<md5>  <path>' lines."""
    return tar_path + ".baked"


def app_fingerprint(tar_path, app_base_dir=BASE_DIR):
    """{relative path: md5} of the files a bake would put in /usr/bin/SoloWebUtility2 right now
    (staged exactly like bake_tarball does, including the LF normalisation)."""
    work_dir = tempfile.mkdtemp(prefix="solo_fp_")
    try:
        _copy_swu_into_rootfs(app_base_dir, work_dir, local_firmware_dir=_bake_firmware_dir(tar_path, app_base_dir))
        fp = {}
        for root, _, files in os.walk(work_dir):
            for fn in files:
                full = os.path.join(root, fn)
                fp[os.path.relpath(full, work_dir).replace("\\", "/")] = calc_file_md5(full)
        # The bake recipe itself (e.g. which stock files are removed) is part of the image too
        if os.path.isfile(BAKE_SCRIPT):
            with open(BAKE_SCRIPT, "rb") as f:
                fp["(recipe) tools/bake_squashfs.sh"] = hashlib.md5(f.read().replace(b"\r\n", b"\n")).hexdigest()
        return fp
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def write_bake_record(tar_path, app_base_dir=BASE_DIR):
    """Remember what was just baked (called after every successful bake)."""
    lines = ["image %s" % calc_file_md5(tar_path)]
    for rel, digest in sorted(app_fingerprint(tar_path, app_base_dir).items()):
        lines.append("%s  %s" % (digest, rel))
    with open(_record_path(tar_path), "w") as f:
        f.write("\n".join(lines) + "\n")


def _read_bake_record(tar_path):
    """(image_md5, {path: md5}) from the record, or (None, None) when there is no usable record."""
    try:
        with open(_record_path(tar_path)) as f:
            rows = [l.rstrip("\r\n") for l in f if l.strip()]
    except (IOError, OSError):
        return None, None
    if not rows or not rows[0].startswith("image "):
        return None, None
    files = {}
    for row in rows[1:]:
        digest, _, rel = row.partition("  ")
        if rel:
            files[rel] = digest
    return rows[0][6:].strip(), files


def is_firmware_stale(tar_path=None, app_base_dir=BASE_DIR, firmware_dir=FIRMWARE_DIR):
    """
    Compare the CONTENT of what a bake would install now with the record of what is baked
    into 3dr-solo.tar.gz (timestamps are not used: copies and checkouts change them).
    Stale when the record is missing, the image changed since it was baked, or any app
    file was added, removed or changed. Returns (is_stale, list_of_changed_paths).
    """
    if tar_path is None:
        tar_path = os.path.join(firmware_dir, "3dr-solo.tar.gz")
    if not os.path.isfile(tar_path):
        return True, ["(archive missing: %s)" % os.path.basename(tar_path)]
    if not os.path.isfile(tar_path + ".md5"):
        return True, ["(checksum missing: %s.md5)" % os.path.basename(tar_path)]

    current = app_fingerprint(tar_path, app_base_dir)
    image_md5, baked = _read_bake_record(tar_path)
    if image_md5 is None:
        return True, ["(no record of what is baked in %s)" % os.path.basename(tar_path)] + sorted(current)
    if image_md5 != calc_file_md5(tar_path):
        return True, ["(%s changed since it was baked)" % os.path.basename(tar_path)] + sorted(current)
    changed = sorted(rel for rel in set(current) | set(baked) if current.get(rel) != baked.get(rel))
    return bool(changed), changed


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
        _log("[*] %d file(s) differ from what is baked into the image:" % len(newer_files))
        for nf in newer_files[:8]:
            _log("    - %s" % nf)
        if len(newer_files) > 8:
            _log("    ... and %d more file(s)" % (len(newer_files) - 8))
    _log("[*] Programmatically executing bake_into_firmware (SquashFS pipeline)...")
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
        log_func("[*] SoloWebUtility2 Firmware Baker (SquashFS Pipeline)")
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
    write_bake_record(tar_path, app_base_dir=app_base_dir)

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
    elif "--auto" in sys.argv:
        # Release builds: bake whenever the image does not hold the current utility.
        success = ensure_firmware_baked(force=False)
        if success:
            stale, newer = is_firmware_stale()
            if stale:
                print("[!] Image still differs from the current utility after baking: %s" % ", ".join(newer[:5]))
                success = False
        sys.exit(0 if success else 1)
    else:
        success = ensure_firmware_baked(force=("--force" in sys.argv or len(sys.argv) == 1))
        sys.exit(0 if success else 1)
