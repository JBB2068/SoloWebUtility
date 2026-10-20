#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
3DR Solo Web Utility 2 -- deploy_to_drone.py (same method as SoloWebUtility install_to_solo.py)
Uploads the web server and firmware to Solo Copter (10.1.1.10)
and configures automatic startup on boot via /etc/rc.local.

Key Design Points for 3DR Solo:
  1. The Web App is uploaded, configured, and STARTED FIRST so the server
     is immediately live on port 80.
  2. Large firmware tarballs go to /log/firmware (which has 6.5 GB free space)
     with a symlink to /home/root/SoloWebUtility2/firmware.
  3. Uses unpipelined synchronous SFTP writes (fr.set_pipelined(False)) so
     the Solo's embedded OpenSSH server buffer never overflows on Wi-Fi.
  4. Automatic resume: if a transfer was previously interrupted, it resumes
     from the exact byte offset instead of starting over.
  5. Copter-to-Controller SSH key exchange is performed so the Copter can
     control the Controller without permission errors.
"""

import os
import sys
import time
import socket

if sys.version_info[0] >= 3:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

SOLO_IP    = "10.1.1.10"
PORT       = 22
USER       = "root"
PASSWORD   = "TjSDBkAu"
REMOTE_DIR = "/log/SoloWebUtility2"
LOG_FW_DIR = "/log/firmware"
if getattr(sys, 'frozen', False):
    LOCAL_DIR = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(sys.executable)))
    EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))
    EXE_DIR = LOCAL_DIR


def get_local_path(loc_rel):
    p2 = os.path.join(EXE_DIR, loc_rel)
    if os.path.exists(p2) and p2 != os.path.join(LOCAL_DIR, loc_rel):
        return p2
    p1 = os.path.join(LOCAL_DIR, loc_rel)
    if os.path.exists(p1):
        return p1
    return p1


def get_installer_version():
    try:
        import json
        vpath = get_local_path("version.json")
        if os.path.exists(vpath):
            with open(vpath, "r") as f:
                return json.load(f).get("version", "1.2.0")
    except Exception:
        pass
    return "1.2.0"


def format_size(n):
    if n >= 1048576:
        return "%.1f MB" % (n / 1048576.0)
    if n >= 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%d B" % n


def test_connection(ip, port=22, timeout=3.0, retries=5):
    for attempt in range(1, retries + 1):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((ip, port))
            s.close()
            return True
        except Exception:
            if attempt < retries:
                time.sleep(1.0)
    return False


def ssh_exec(client, cmd, timeout=None):
    """Run a command on the Solo and return (returncode, stdout_str)."""
    try:
        full_cmd = "cd / && " + cmd if not cmd.strip().startswith("cd ") else cmd
        if timeout is not None:
            transport = client.get_transport()
            if transport is None:
                return -1, "No transport"
            chan = transport.open_session()
            chan.exec_command(full_cmd)
            chan.status_event.wait(timeout)
            if not chan.status_event.is_set():
                try:
                    chan.close()
                except Exception:
                    pass
                return 0, ""
            rc = chan.recv_exit_status()
            out_chunks = []
            while chan.recv_ready():
                chunk = chan.recv(4096)
                if not chunk:
                    break
                out_chunks.append(chunk)
            try:
                chan.close()
            except Exception:
                pass
            out = b"".join(out_chunks)
            if sys.version_info[0] >= 3 and isinstance(out, bytes):
                out = out.decode("utf-8", "ignore")
            return rc, out
        else:
            _, stdout, stderr = client.exec_command(full_cmd)
            rc = stdout.channel.recv_exit_status()
            out = stdout.read()
            if sys.version_info[0] >= 3 and isinstance(out, bytes):
                out = out.decode("utf-8", "ignore")
            return rc, out
    except Exception as e:
        return -1, str(e)


INITTAB_RESPAWN_ENTRY = "SWU:345:respawn:/usr/bin/python /usr/bin/SoloWebUtility2/solo_web_app.py >>/log/solowebutility.log 2>&1\n"

# Deprecated legacy definitions kept for compatibility
INITD_SERVICE_CONTENT = ""
RC_LOCAL_CONTENT = ""


def diagnose_post_update_state(client):
    """
    Check if /usr/bin/SoloWebUtility2 and /tmp/solo_web*.log exist on the drone
    after a firmware update and print a 1-line summary.
    """
    print("\n[*] Diagnosing post-update environment on Solo Copter...")
    rc_swu, count_str = ssh_exec(
        client,
        "if [ -d /usr/bin/SoloWebUtility2 ]; then ls -1 /usr/bin/SoloWebUtility2 2>/dev/null | wc -l; else echo 0; fi"
    )
    swu_count = 0
    try:
        swu_count = int(count_str.strip())
    except Exception:
        swu_count = 0

    rc_log, log_out = ssh_exec(
        client,
        "cat /log/solowebutility.log /tmp/solo_web*.log /log/solo_web*.log 2>/dev/null || true"
    )
    log_text = log_out.strip()

    rc_ps, ps_out = ssh_exec(
        client,
        "ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep || true"
    )
    is_running = bool(ps_out.strip())

    if is_running:
        summary = "[DIAGNOSTIC] Server ALREADY RUNNING on drone (process: %s)" % ps_out.strip().splitlines()[0]
    elif swu_count > 0:
        if log_text:
            err_lines = [l.strip() for l in log_text.splitlines() if "error" in l.lower() or "traceback" in l.lower() or "exception" in l.lower()]
            err_info = err_lines[-1] if err_lines else log_text.splitlines()[-1].strip()
            summary = "[DIAGNOSTIC] Baked /usr/bin/SoloWebUtility2 FOUND (%d files), but server failed: %s" % (swu_count, err_info)
        else:
            summary = "[DIAGNOSTIC] Baked /usr/bin/SoloWebUtility2 FOUND (%d files), but service did not launch on boot (no startup log found)" % swu_count
    else:
        summary = "[DIAGNOSTIC] Baked /usr/bin/SoloWebUtility2 NOT FOUND on drone (firmware update used unbaked archive)"

    print("  --> %s" % summary)
    return summary


def calc_file_md5(filepath):
    import hashlib
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def safe_sftp_put(sftp, local_path, remote_path, resume=True, chunk_size=32768, force=False, client=None):
    """
    Robust SFTP upload using synchronous writes (unpipelined).
    Prevents overflowing the embedded OpenSSH daemon buffer on Solo.
    Automatically resumes partial transfers if found.
    Checks MD5 checksum if client is provided to avoid stale or modified file skips.
    """
    file_size = os.path.getsize(local_path)
    remote_size = 0
    if not force:
        try:
            rstat = sftp.stat(remote_path)
            remote_size = rstat.st_size
            if remote_size == file_size:
                if client is not None:
                    # Compare MD5 checksums to guarantee identical content
                    loc_md5 = calc_file_md5(local_path)
                    rc, out = ssh_exec(client, "md5sum %s 2>/dev/null || true" % remote_path)
                    rem_md5 = out.strip().split()[0].lower() if (rc == 0 and out.strip()) else ""
                    if rem_md5 and rem_md5 == loc_md5:
                        print("    [ALREADY UPLOADED] Matches local MD5 (%s: %s). Skipping." % (format_size(file_size), loc_md5[:8]))
                        return True
                    elif rem_md5:
                        print("    [!] MD5 mismatch (remote %s vs local %s). Re-uploading %s..." % (
                            rem_md5[:8], loc_md5[:8], os.path.basename(remote_path)
                        ))
                        force = True
                        remote_size = 0
                    else:
                        print("    [ALREADY UPLOADED] Matches local size (%s). Skipping." % format_size(file_size))
                        return True
                else:
                    print("    [ALREADY UPLOADED] Matches local size (%s). Skipping." % format_size(file_size))
                    return True
        except Exception:
            remote_size = 0
    else:
        try:
            sftp.remove(remote_path)
        except Exception:
            pass

    if force:
        try:
            sftp.remove(remote_path)
        except Exception:
            pass
        remote_size = 0

    mode = "wb"
    start_offset = 0
    if resume and 0 < remote_size < file_size:
        print("    [RESUMING] From %s (%d%%)..." % (
            format_size(remote_size), int(remote_size * 100 / file_size)
        ))
        mode = "ab"
        start_offset = remote_size
    elif remote_size > file_size:
        # Remote file is larger than local file (stale or corrupted); start fresh
        try:
            sftp.remove(remote_path)
        except Exception:
            pass
        remote_size = 0
        mode = "wb"
        start_offset = 0

    remote_dir = os.path.dirname(remote_path).replace("\\", "/")
    if remote_dir and client is not None:
        try:
            sftp.stat(remote_dir)
        except Exception:
            ssh_exec(client, "mkdir -p %s 2>/dev/null || true" % remote_dir)

    try:
        with open(local_path, "rb") as fl:
            if start_offset:
                fl.seek(start_offset)
            fr = sftp.open(remote_path, mode)
            # CRITICAL: Disable pipelining to avoid buffer exhaustion on Solo's sshd
            fr.set_pipelined(False)

            transferred = start_offset
            last_pct = -1

            while transferred < file_size:
                data = fl.read(chunk_size)
                if not data:
                    break
                fr.write(data)
                transferred += len(data)
                pct = int(transferred * 100 / file_size)
                if pct != last_pct:
                    last_pct = pct
                    bars = "=" * (pct // 5) + "-" * (20 - (pct // 5))
                    sys.stdout.write("\r    [%s] %3d%% (%s / %s)" % (
                        bars, pct, format_size(transferred), format_size(file_size)
                    ))
                    sys.stdout.flush()

            fr.close()

        sys.stdout.write("\r    [====================] 100%% (%s) Complete! \n" % format_size(file_size))
        sys.stdout.flush()
        return True
    except Exception as e:
        sys.stdout.write("\n")
        print("    [!] Upload error: %s" % str(e))
        return False


def upload_app_file(client, sftp, local_path, remote_path):
    """
    Robust upload of application files (<1MB) with multi-layer fallback:
    1. Tries SFTP write after removing existing destination file.
    2. If SFTP fails or errors, streams base64 via SSH (base64 -d > file) and verifies remote byte count.
    3. Fallback to direct stdin pipe stream (cat > file).
    """
    file_size = os.path.getsize(local_path)
    remote_dir = os.path.dirname(remote_path).replace("\\", "/")
    ssh_exec(client, "mkdir -p %s 2>/dev/null && rm -f %s 2>/dev/null || true" % (remote_dir, remote_path))

    with open(local_path, "rb") as fl:
        content = fl.read()

    # Attempt 1: SFTP
    sftp_ok = False
    if sftp is not None:
        try:
            fr = sftp.open(remote_path, "wb")
            fr.set_pipelined(False)
            fr.write(content)
            fr.close()
            sftp_ok = True
        except Exception:
            sftp_ok = False

    if sftp_ok:
        _, sz_str = ssh_exec(client, "wc -c < %s 2>/dev/null || true" % remote_path)
        try:
            if int(sz_str.strip()) == file_size:
                print("    [OK] SFTP complete (%s)" % format_size(file_size))
                return True
        except ValueError:
            pass

    # Attempt 2: base64 stream over SSH
    import base64
    b64_str = base64.b64encode(content).decode('ascii')
    rc, err = ssh_exec(client, "base64 -d > %s << 'EOF_B64'\n%s\nEOF_B64\n" % (remote_path, b64_str))

    # Attempt 3: cat stream over SSH if base64 failed
    if rc != 0:
        stdin, stdout, stderr = client.exec_command("cat > %s" % remote_path)
        stdin.write(content)
        stdin.flush()
        stdin.channel.shutdown_write()
        rc = stdout.channel.recv_exit_status()
        err = stderr.read()
        if sys.version_info[0] >= 3 and isinstance(err, bytes):
            err = err.decode('utf-8', 'ignore')

    # Verify size
    _, sz_str = ssh_exec(client, "wc -c < %s 2>/dev/null || true" % remote_path)
    try:
        sz = int(sz_str.strip())
        if sz == file_size:
            print("    [OK] Upload complete (%s)" % format_size(file_size))
            return True
        else:
            print("    [!] Size mismatch: got %d bytes, expected %d. %s" % (sz, file_size, err.strip() if err else ""))
            return False
    except Exception as e:
        print("    [!] Upload failed (%s): %s" % (remote_path, str(e)))
        return False


TOP_LEVEL_FILES = [
    "app.py", "solo_web_app.py", "webcore.py", "compat.py", "param_fetch.py",
    "auth.py", "change_password.py", "presets.py", "param_mgr.py", "wifi_mgr.py",
    "flash_mgr.py", "pair_mgr.py", "calib_mgr.py", "controller_mgr.py",
    "cockpit_mgr.py", "version_mgr.py", "arducopter_builder.py", "version.json",
]
TREE_DIRS = ["modules", "templates", "static", "bin"]
SKIP_DIR_NAMES = ("__pycache__", "squashfs")   # bin/squashfs holds Windows-only tools
SKIP_EXTS = (".pyc", ".pyo", ".apk", ".idsig", ".exe", ".dll")


def build_app_file_list(target_dir):
    """(local_rel, remote_path) for every file of the complete SoloWebUtility2 app."""
    items = [(f, "%s/%s" % (target_dir, f)) for f in TOP_LEVEL_FILES]
    for d in TREE_DIRS:
        root = get_local_path(d)
        for cur, dirs, files in os.walk(root):
            dirs[:] = sorted([x for x in dirs if x not in SKIP_DIR_NAMES])
            for fn in sorted(files):
                if os.path.splitext(fn)[1].lower() in SKIP_EXTS:
                    continue
                rel = os.path.relpath(os.path.join(cur, fn), LOCAL_DIR).replace("\\", "/")
                items.append((rel, "%s/%s" % (target_dir, rel)))
    return items


def ensure_writable_target(client):
    """Ensure the target installation directory is fully writable."""
    print("\n[*] Checking filesystem write permissions...")
    rc, _ = ssh_exec(client, "touch /log/.rw_test 2>/dev/null && rm -f /log/.rw_test 2>/dev/null")
    if rc == 0:
        print("  [OK] /log partition is writable.")
        target_dir = "/log/SoloWebUtility2"
    else:
        ssh_exec(client, "mount -o remount,rw /dev/mmcblk0p4 /log 2>/dev/null || mount -o remount,rw /log 2>/dev/null || true")
        rc, _ = ssh_exec(client, "touch /log/.rw_test 2>/dev/null && rm -f /log/.rw_test 2>/dev/null")
        if rc == 0:
            print("  [OK] /log partition remounted read-write.")
            target_dir = "/log/SoloWebUtility2"
        else:
            print("  [*] Using /home/root/SoloWebUtility2 on rootfs...")
            target_dir = "/home/root/SoloWebUtility2"

    if target_dir == "/log/SoloWebUtility2":
        ssh_exec(client, "mkdir -p /log/SoloWebUtility2/bin /log/SoloWebUtility2/static /log/SoloWebUtility2/templates /log/SoloWebUtility2/modules /log/firmware 2>/dev/null || true")
        ssh_exec(client, "chmod -R 755 /log/SoloWebUtility2 2>/dev/null || true")
        ssh_exec(client, "ln -sfn /log/firmware /log/SoloWebUtility2/firmware 2>/dev/null || true")
        ssh_exec(client, "rm -rf /home/root/SoloWebUtility2 2>/dev/null; ln -sfn /log/SoloWebUtility2 /home/root/SoloWebUtility2 2>/dev/null || true")
        ssh_exec(client, "rm -rf /usr/bin/SoloWebUtility2 2>/dev/null; ln -sfn /log/SoloWebUtility2 /usr/bin/SoloWebUtility2 2>/dev/null || true")
    else:
        ssh_exec(client, "rm -rf /home/root/SoloWebUtility2 2>/dev/null")
        ssh_exec(client, "mkdir -p /home/root/SoloWebUtility2/bin /home/root/SoloWebUtility2/static /home/root/SoloWebUtility2/templates /home/root/SoloWebUtility2/modules /home/root/SoloWebUtility2/firmware 2>/dev/null || true")
        ssh_exec(client, "chmod -R 755 /home/root/SoloWebUtility2 2>/dev/null || true")
        ssh_exec(client, "ln -sfn /log/firmware /home/root/SoloWebUtility2/firmware 2>/dev/null || true")

    return target_dir


def firmware_archive_ready(log_func=print):
    """True when firmware/3dr-solo.tar.gz may be uploaded.

    Packaged installer (.exe): the golden image was baked and verified before it was
    packed, so only its integrity is checked (MD5 against 3dr-solo.tar.gz.md5).
    Project folder: re-bake if any app source is newer (bake_into_firmware)."""
    if getattr(sys, 'frozen', False):
        tar_path = get_local_path("firmware/3dr-solo.tar.gz")
        md5_path = tar_path + ".md5"
        if not (os.path.isfile(tar_path) and os.path.isfile(md5_path)):
            log_func("[!] Packaged golden image or its .md5 is missing.")
            return False
        with open(md5_path, "r") as f:
            want = (f.read().split() or [""])[0].lower()
        got = calc_file_md5(tar_path)
        if got != want:
            log_func("[!] Packaged golden image is damaged (MD5 %s, expected %s)." % (got, want))
            return False
        log_func("[OK] Packaged golden image verified (MD5 %s)." % got)
        return True
    import bake_into_firmware
    return bake_into_firmware.ensure_firmware_baked(
        firmware_dir=get_local_path("firmware"),
        app_base_dir=LOCAL_DIR,
        log_func=log_func
    )


def main():
    cur_ver = get_installer_version()
    print("=" * 65)
    print("[*] 3DR SOLO WEB UTILITY -- SoloWebUtility2 DEPLOY (app.py on port 80) v%s" % cur_ver)
    print("=" * 65)
    print("Uploads web server to Solo Copter (%s)" % SOLO_IP)
    print("\n[*] Checking firmware archive synchronization...")
    try:
        if not firmware_archive_ready(log_func=print):
            print("\n[CRITICAL ERROR] Cannot deploy: firmware archive 3dr-solo.tar.gz could not be synchronized!")
            return
    except Exception as e:
        # Never deploy a tarball that was not freshly baked and verified against stock.
        print("\n[CRITICAL ERROR] Firmware bake failed: %s" % str(e))
        print("[CRITICAL ERROR] Deploy aborted so an unverified 3dr-solo.tar.gz is not uploaded.")
        return

    print("\n[*] Checking Wi-Fi link to Solo Copter (%s)..." % SOLO_IP)
    if not test_connection(SOLO_IP, PORT):
        print("\n[!] Cannot reach Solo at %s." % SOLO_IP)
        print("[!] Connect PC to SoloLink Wi-Fi and retry.")
        return
    print("[OK] Solo Copter is reachable.")

    try:
        import paramiko
    except ImportError:
        print("[!] paramiko is required. Run: pip install paramiko")
        return

    print("[*] Connecting via SSH...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connected = False
    for attempt in range(1, 4):
        try:
            client.connect(
                SOLO_IP, port=PORT, username=USER, password=PASSWORD,
                timeout=15.0, look_for_keys=False, allow_agent=False
            )
            connected = True
            break
        except Exception as e:
            if attempt < 3:
                time.sleep(1.5)
            else:
                print("[!] SSH connection failed: %s" % str(e))
                return

    client.get_transport().set_keepalive(15)

    # 1. Post-Update Diagnostic Check (runs before modifying any files or logs)
    diagnose_post_update_state(client)

    # Display Solo disk space
    print("\n[*] Solo Copter partition status:")
    _, df_out = ssh_exec(client, "df -h / /log 2>/dev/null || df -h")
    for line in df_out.strip().splitlines():
        print("    " + line)

    # Terminate running solo_web_app and ensure read-write filesystem prior to file updates
    print("\n[*] Stopping running processes and verifying filesystem state...")
    ssh_exec(client, "mount -o remount,rw / 2>/dev/null || true")
    # Clean stale updates directories to avoid disk space exhaustion or file collisions
    ssh_exec(client, "rm -rf /tmp/updates /log/updates 2>/dev/null || true")
    ssh_exec(client, "fuser -k 80/tcp 2>/dev/null || true")
    ssh_exec(client, "for p in $(ps | grep -E 'solo_web_app|SoloWebUtility2/app.py' | grep -v grep | awk '{print $1}'); do kill -9 $p 2>/dev/null; done")
    ssh_exec(client, "sleep 1")

    # Determine and verify writable installation target directory
    target_dir = ensure_writable_target(client)
    print("  [*] Active installation directory: %s" % target_dir)

    sftp = client.open_sftp()

    # =========================================================================
    # PHASE 1: Upload Web Application Core Files
    # =========================================================================
    app_files = build_app_file_list(target_dir)

    print("\n[*] Phase 1: Uploading Web Server Application files to %s..." % target_dir)
    for idx, (loc_rel, rem_path) in enumerate(app_files, 1):
        loc = get_local_path(loc_rel)
        if not os.path.exists(loc):
            print("  [%d/%d] [SKIP] %s" % (idx, len(app_files), loc_rel))
            continue
        size = os.path.getsize(loc)
        print("  [%d/%d] %-44s %s" % (idx, len(app_files), loc_rel, format_size(size)))
        upload_app_file(client, sftp, loc, rem_path)

    # Set executable permissions
    print("\n[*] Setting permissions and authorizing SSH keys...")
    ssh_exec(client, "chmod -R 755 %s 2>/dev/null || true" % target_dir)
    ssh_exec(client, "chmod +x %s/*.py %s/bin/*.py 2>/dev/null || true" % (target_dir, target_dir))

    # Authorize Copter's SSH key on Controller (10.1.1.1)
    ssh_exec(client, 'test -f /home/root/.ssh/id_rsa || ssh-keygen -t rsa -N "" -f /home/root/.ssh/id_rsa')
    rc, pubkey = ssh_exec(client, "cat /home/root/.ssh/id_rsa.pub 2>/dev/null")
    if rc == 0 and "ssh-rsa" in pubkey:
        clean_key = pubkey.strip()
        if test_connection("10.1.1.1", 22, timeout=2.0):
            try:
                ctrl_client = paramiko.SSHClient()
                ctrl_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ctrl_client.connect("10.1.1.1", port=22, username="root", password=PASSWORD,
                                    timeout=5.0, look_for_keys=False, allow_agent=False)
                ctrl_client.exec_command("mkdir -p /home/root/.ssh /root/.ssh && chmod 700 /home/root/.ssh /root/.ssh")
                ctrl_client.exec_command(
                    "grep -q '%s' /home/root/.ssh/authorized_keys 2>/dev/null || echo '%s' >> /home/root/.ssh/authorized_keys"
                    % (clean_key, clean_key)
                )
                ctrl_client.exec_command(
                    "grep -q '%s' /root/.ssh/authorized_keys 2>/dev/null || echo '%s' >> /root/.ssh/authorized_keys"
                    % (clean_key, clean_key)
                )
                ctrl_client.exec_command("chmod 600 /home/root/.ssh/authorized_keys /root/.ssh/authorized_keys 2>/dev/null || true")
                ctrl_client.close()
                print("  [OK] Copter SSH key authorized on Controller!")
            except Exception as e:
                print("  [!] Could not connect to Controller: %s" % str(e))
        else:
            print("  [*] Controller (10.1.1.1) not online yet -- PTY auto-password will be used.")

    # =========================================================================
    # PHASE 2: Upload Firmware Packages to /log/firmware (BEFORE Starting Server)
    # =========================================================================
    print("\n" + "=" * 65)
    print("[*] PHASE 2: Baking & Uploading Firmware Packages to Solo Copter")
    print("=" * 65)
    print("[*] Updating staged 3dr-solo.tar.gz with latest SoloWebUtility2 files & SysVinit service...")
    try:
        bake_ok = firmware_archive_ready(log_func=lambda msg: print("    " + msg))
    except Exception as e:
        print("    [!] Firmware bake raised: %s" % str(e))
        bake_ok = False
    if not bake_ok:
        print("\n[CRITICAL ERROR] 3dr-solo.tar.gz could not be baked/verified. Not uploading firmware.")
        return

    # Check MD5 of 3dr-solo.tar.gz on drone at paths flash_mgr.py uses
    local_solo_tar = get_local_path("firmware/3dr-solo.tar.gz")
    local_solo_md5_file = get_local_path("firmware/3dr-solo.tar.gz.md5")
    if os.path.exists(local_solo_tar):
        local_md5 = calc_file_md5(local_solo_tar)
        print("\n[*] Verifying 3dr-solo.tar.gz MD5 on drone (/log/firmware and /log/updates)...")
        # Clean any stale /log/updates or /tmp/updates tarball
        for stale_dir in ["/log/updates", "/tmp/updates"]:
            rc_st, out_st = ssh_exec(client, "md5sum %s/3dr-solo.tar.gz 2>/dev/null || true" % stale_dir)
            stale_md5 = out_st.strip().split()[0].lower() if rc_st == 0 and out_st.strip() else ""
            if stale_md5:
                if stale_md5 != local_md5:
                    print("    [!] Stale unbaked archive found at %s/3dr-solo.tar.gz (%s). Removing..." % (stale_dir, stale_md5[:8]))
                    ssh_exec(client, "rm -rf %s 2>/dev/null || true" % stale_dir)
                else:
                    print("    [*] %s/3dr-solo.tar.gz matches current baked MD5." % stale_dir)

        # Check /log/firmware/3dr-solo.tar.gz
        rem_fw_tar = "%s/3dr-solo.tar.gz" % LOG_FW_DIR
        rc_fw, fw_out = ssh_exec(client, "md5sum %s 2>/dev/null || true" % rem_fw_tar)
        rem_fw_md5 = fw_out.strip().split()[0].lower() if rc_fw == 0 and fw_out.strip() else ""

        if not rem_fw_md5:
            print("    [*] %s not found on drone. Will upload." % rem_fw_tar)
        elif rem_fw_md5 != local_md5:
            print("    [!] MD5 MISMATCH on drone %s:" % rem_fw_tar)
            print("        Drone MD5: %s" % rem_fw_md5)
            print("        Local MD5: %s (baked with SysVinit + SWU2)" % local_md5)
            print("        Drone will be updated with the newly baked archive!")
            ssh_exec(client, "rm -f %s %s.md5 2>/dev/null || true" % (rem_fw_tar, rem_fw_tar))
        else:
            print("    [OK] Drone %s already matches baked MD5 (%s)." % (rem_fw_tar, rem_fw_md5[:8]))

    # Clean any legacy unversioned APJ files from drone firmware directory
    ssh_exec(client, "rm -f %s/arducopter_solo.apj %s/arducopter_green.apj /firmware/arducopter_solo.apj /firmware/arducopter_green.apj 2>/dev/null || true" % (LOG_FW_DIR, LOG_FW_DIR))

    local_fw_dir = get_local_path("firmware")
    firmware_files = []
    if os.path.isdir(local_fw_dir):
        for fn in sorted(os.listdir(local_fw_dir)):
            if fn.endswith((".apj", ".tar.gz", ".tar.gz.md5", ".md5", ".param", ".json", ".yaml")):
                if fn in ("arducopter_solo.apj", "arducopter_green.apj"):
                    continue
                firmware_files.append(("firmware/%s" % fn, "%s/%s" % (LOG_FW_DIR, fn)))
    # Older ArduCopter builds kept for reverting (firmware/previous/*.apj)
    local_prev_dir = os.path.join(local_fw_dir, "previous")
    if os.path.isdir(local_prev_dir):
        for fn in sorted(os.listdir(local_prev_dir)):
            if fn.endswith(".apj"):
                firmware_files.append(("firmware/previous/%s" % fn, "%s/previous/%s" % (LOG_FW_DIR, fn)))
    if not firmware_files:
        firmware_files = [
            ("firmware/arducopter_CubeSolo_4.7.2.apj",  "%s/arducopter_CubeSolo_4.7.2.apj" % LOG_FW_DIR),
            ("firmware/arducopter_CubeGreen_4.7.2.apj", "%s/arducopter_CubeGreen_4.7.2.apj" % LOG_FW_DIR),
            ("firmware/3dr-solo.tar.gz",               "%s/3dr-solo.tar.gz" % LOG_FW_DIR),
            ("firmware/3dr-solo.tar.gz.md5",           "%s/3dr-solo.tar.gz.md5" % LOG_FW_DIR),
            ("firmware/3dr-controller.tar.gz",         "%s/3dr-controller.tar.gz" % LOG_FW_DIR),
            ("firmware/3dr-controller.tar.gz.md5",     "%s/3dr-controller.tar.gz.md5" % LOG_FW_DIR),
            ("firmware/Solo.param",                    "%s/Solo.param" % LOG_FW_DIR),
            ("firmware/SoloGreen.param",               "%s/SoloGreen.param" % LOG_FW_DIR),
            ("firmware/solocustombuild.yaml",          "%s/solocustombuild.yaml" % LOG_FW_DIR),
        ]

    ssh_exec(client, "mkdir -p %s %s/previous /log/updates /tmp/updates 2>/dev/null || true" % (LOG_FW_DIR, LOG_FW_DIR))
    print("\n[*] Uploading Firmware Files to %s..." % LOG_FW_DIR)
    for idx, (loc_rel, rem_path) in enumerate(firmware_files, 1):
        loc = get_local_path(loc_rel)
        if not os.path.exists(loc):
            print("  [%d/%d] [SKIP] %s" % (idx, len(firmware_files), loc_rel))
            continue
        size = os.path.getsize(loc)
        print("  [%d/%d] %-44s %s" % (idx, len(firmware_files), loc_rel, format_size(size)))
        safe_sftp_put(sftp, loc, rem_path, client=client)

    # Post-upload verification of 3dr-solo.tar.gz
    if os.path.exists(local_solo_tar):
        rc_final, final_out = ssh_exec(client, "md5sum /log/firmware/3dr-solo.tar.gz 2>/dev/null || true")
        final_rem_md5 = final_out.strip().split()[0].lower() if rc_final == 0 and final_out.strip() else ""
        if final_rem_md5 == local_md5:
            print("  [OK] VERIFIED: /log/firmware/3dr-solo.tar.gz matches baked MD5 (%s)." % local_md5[:8])
        else:
            print("  [!] WARNING: Final MD5 check mismatch (%s vs %s)" % (final_rem_md5[:8], local_md5[:8]))

    # Post-upload verification of all required firmware files on /log/firmware
    print("\n[*] Verifying required firmware files on Solo Copter (/log/firmware)...")
    # Dynamic detection of latest APJ files in local firmware directory
    latest_solo_apj = "arducopter_CubeSolo_4.7.2.apj"
    latest_green_apj = "arducopter_CubeGreen_4.7.2.apj"
    if os.path.isdir(local_fw_dir):
        for fn in os.listdir(local_fw_dir):
            if fn.startswith("arducopter_CubeSolo_") and fn.endswith(".apj"):
                latest_solo_apj = fn
            elif fn.startswith("arducopter_CubeGreen_") and fn.endswith(".apj"):
                latest_green_apj = fn

    REQUIRED_FW = [
        (latest_solo_apj, "CubeSolo ArduCopter firmware", 100000),
        (latest_green_apj, "CubeGreen ArduCopter firmware", 100000),
        ("3dr-solo.tar.gz", "Solo Copter system firmware", 10000000),
        ("3dr-solo.tar.gz.md5", "Solo Copter checksum", 10),
        ("3dr-controller.tar.gz", "Controller system firmware", 10000000),
        ("3dr-controller.tar.gz.md5", "Controller checksum", 10),
    ]
    for req_fn, desc, min_sz in REQUIRED_FW:
        req_rem = "%s/%s" % (LOG_FW_DIR, req_fn)
        rc_sz, sz_out = ssh_exec(client, "wc -c < %s 2>/dev/null || echo 0" % req_rem)
        try:
            r_sz = int(sz_out.strip())
        except Exception:
            r_sz = 0
        if r_sz >= min_sz:
            print("  [OK] %-34s %10s  (%s)" % (req_fn, format_size(r_sz), desc))
        else:
            print("  [!] %-34s MISSING or incomplete (%d bytes) on drone!" % (req_fn, r_sz))

    # Check directory listing of /log/firmware/*.apj
    rc_ls, ls_out = ssh_exec(client, "ls -lh /log/firmware/*.apj 2>/dev/null || true")
    if ls_out.strip():
        print("\n[*] Confirmed ArduCopter (.apj) files installed in /log/firmware:")
        for line in ls_out.strip().splitlines():
            print("    " + line)

    # Link /usr/bin/SoloWebUtility2/firmware, /log/SoloWebUtility2/firmware, /home/root/SoloWebUtility2/firmware to /log/firmware
    ssh_exec(client, "mkdir -p /usr/bin/SoloWebUtility2 /log/SoloWebUtility2 /home/root/SoloWebUtility2 2>/dev/null || true")
    ssh_exec(client, "ln -sfn /log/firmware /usr/bin/SoloWebUtility2/firmware 2>/dev/null || true")
    ssh_exec(client, "ln -sfn /log/firmware /log/SoloWebUtility2/firmware 2>/dev/null || true")
    ssh_exec(client, "ln -sfn /log/firmware /home/root/SoloWebUtility2/firmware 2>/dev/null || true")

    # =========================================================================
    # PHASE 3: Configure SysVinit (/etc/inittab) & Start Web Server
    # =========================================================================
    print("\n" + "=" * 65)
    print("[*] PHASE 3: Configuring SysVinit (/etc/inittab) & Starting Web Server")
    print("=" * 65)
    print("[*] Cleaning legacy init scripts and rc.local hooks on drone...")
    ssh_exec(client, "rm -f /etc/init.d/solowebutility /etc/rc5.d/S99solowebutility 2>/dev/null || true")
    rc_tab, cur_rclocal = ssh_exec(client, "cat /etc/rc.local 2>/dev/null || true")
    if "SoloWebUtility" in cur_rclocal or "solo_web_app" in cur_rclocal:
        new_rc = "\n".join(l for l in cur_rclocal.splitlines() if "SoloWebUtility" not in l and "solo_web_app" not in l) + "\n"
        try:
            rc_f = sftp.open("/etc/rc.local", "w")
            rc_f.write(new_rc.encode("utf-8") if sys.version_info[0] >= 3 else new_rc)
            rc_f.close()
            ssh_exec(client, "chmod 755 /etc/rc.local")
            print("  [OK] Cleaned legacy hooks from /etc/rc.local.")
        except Exception:
            pass

    print("\n[*] Configuring SysVinit continuous supervision (/etc/inittab)...")
    rc_tab, cur_inittab = ssh_exec(client, "cat /etc/inittab 2>/dev/null || true")
    clean_respawn = INITTAB_RESPAWN_ENTRY.strip()
    if "SWU:345:respawn" not in cur_inittab and "SoloWebUtility" not in cur_inittab:
        updated_inittab = cur_inittab.rstrip() + "\n\n# SoloWebUtility2 continuous supervision\n" + INITTAB_RESPAWN_ENTRY
        try:
            tab_f = sftp.open("/etc/inittab", "w")
            tab_f.write(updated_inittab.encode("utf-8") if sys.version_info[0] >= 3 else updated_inittab)
            tab_f.close()
            ssh_exec(client, "chmod 644 /etc/inittab 2>/dev/null || true")
            print("  [OK] Appended respawn entry to /etc/inittab:")
            print("       %s" % clean_respawn)
        except Exception as e:
            print("  [!] Failed to write /etc/inittab via SFTP: %s" % str(e))
    else:
        print("  [OK] /etc/inittab already contains SoloWebUtility respawn entry.")

    # Tell init to re-read inittab immediately
    ssh_exec(client, "telinit q 2>/dev/null || kill -HUP 1 2>/dev/null || true")

    print("\n[*] Starting Solo Web Server on port 80...")
    # 1. Kill any existing process on port 80 and existing solo_web_app instances
    ssh_exec(client, "fuser -k 80/tcp 2>/dev/null || true")
    ssh_exec(client, "for p in $(ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep | awk '{print $1}'); do kill -9 $p 2>/dev/null; done")
    ssh_exec(client, "sleep 1")

    # Ensure log directory and symlinks
    ssh_exec(client, "mkdir -p /log /tmp 2>/dev/null || true")
    ssh_exec(client, "touch /log/solowebutility.log 2>/dev/null || true")
    ssh_exec(client, "ln -sfn /log/solowebutility.log /tmp/solo_web.log 2>/dev/null || true")
    ssh_exec(client, "ln -sfn /log/solowebutility.log /log/solo_web.log 2>/dev/null || true")
    ssh_exec(client, "ln -sfn /log/solowebutility.log /tmp/solo_web_server.log 2>/dev/null || true")
    ssh_exec(client, "ln -sfn /log/solowebutility.log /log/solo_web_server.log 2>/dev/null || true")

    # Trigger init reload and fallback launch if not immediately spawned
    app_entry = "%s/solo_web_app.py" % target_dir
    ssh_exec(client, "telinit q 2>/dev/null || true")
    time.sleep(1.5)
    _, ps_check = ssh_exec(client, "ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep || true")
    if not ps_check.strip():
        ssh_exec(client, "nohup /usr/bin/python %s 80 </dev/null >>/log/solowebutility.log 2>&1 &" % app_entry, timeout=5.0)
        time.sleep(2.0)

    # Check process and startup log
    _, ps_out = ssh_exec(client, "ps | grep -E 'solo_web_app|SoloWebUtility2' | grep -v grep || true")
    if ps_out.strip():
        print("  [OK] Web server process is running: %s" % ps_out.strip().splitlines()[0])
    else:
        print("  [!] WARNING: Web server process not detected in ps list!")

    _, log_out = ssh_exec(client, "cat /tmp/solo_web*.log 2>/dev/null || true")
    if log_out.strip():
        print("  [*] Server log output:\n" + "\n".join("      " + l for l in log_out.strip().splitlines()[-10:]))

    # Test server response and verify live version endpoint
    _, ver_test = ssh_exec(client, "curl -s --connect-timeout 2 --max-time 4 http://127.0.0.1/api/version 2>/dev/null || true")
    if ver_test.strip():
        print("  [OK] Verified active server on port 80: %s" % ver_test.strip())
    else:
        _, curl_test = ssh_exec(client, "curl -s -I --connect-timeout 2 --max-time 4 http://127.0.0.1/ | head -n 1 2>/dev/null || true")
        print("  [OK] Server response: %s" % curl_test.strip())

    try:
        sftp.close()
    except Exception:
        pass
    client.close()

    print("\n" + "=" * 65)
    print("*** SOLO WEB UTILITY v%s INSTALLED & READY!" % cur_ver)
    print("=" * 65)
    print("Open your browser and navigate to:")
    print("  --> http://10.1.1.10")
    print("\n[NOTE] If you previously had the page open, press")
    print("       Ctrl+Shift+R (or Ctrl+F5) to refresh your browser cache.")
    print("=" * 65)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("\n[FATAL ERROR] An unexpected error occurred: %s" % str(exc))
    finally:
        if getattr(sys, 'frozen', False):
            try:
                try:
                    raw_input("\nPress Enter to exit...")
                except NameError:
                    input("\nPress Enter to exit...")
            except Exception:
                pass
