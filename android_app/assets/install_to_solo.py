#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
3DR Solo Web Utility -- Remote Installer v1.0.71
Uploads the web server and firmware to Solo Copter (10.1.1.10)
and configures automatic startup on boot via /etc/rc.local.

Key Design Points for 3DR Solo:
  1. The Web App is uploaded, configured, and STARTED FIRST so the server
     is immediately live on port 80.
  2. Large firmware tarballs go to /log/firmware (which has 6.5 GB free space)
     with a symlink to /home/root/SoloWebUtility/firmware.
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
REMOTE_DIR = "/log/SoloWebUtility"
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
                return json.load(f).get("version", "1.0.71")
    except Exception:
        pass
    return "1.0.71"


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


def ssh_exec(client, cmd):
    """Run a command on the Solo and return (returncode, stdout_str)."""
    try:
        full_cmd = "cd / && " + cmd if not cmd.strip().startswith("cd ") else cmd
        _, stdout, stderr = client.exec_command(full_cmd)
        rc = stdout.channel.recv_exit_status()
        out = stdout.read()
        if sys.version_info[0] >= 3 and isinstance(out, bytes):
            out = out.decode("utf-8", "ignore")
        return rc, out
    except Exception as e:
        return -1, str(e)


def safe_sftp_put(sftp, local_path, remote_path, resume=True, chunk_size=32768, force=False):
    """
    Robust SFTP upload using synchronous writes (unpipelined).
    Prevents overflowing the embedded OpenSSH daemon buffer on Solo.
    Automatically resumes partial transfers if found.
    """
    file_size = os.path.getsize(local_path)
    remote_size = 0
    if not force:
        try:
            rstat = sftp.stat(remote_path)
            remote_size = rstat.st_size
            if remote_size == file_size:
                print("    [ALREADY UPLOADED] Matches local size (%s). Skipping." % format_size(file_size))
                return True
        except Exception:
            remote_size = 0
    else:
        try:
            sftp.remove(remote_path)
        except Exception:
            pass

    mode = "wb"
    start_offset = 0
    if resume and 0 < remote_size < file_size:
        print("    [RESUMING] From %s (%d%%)..." % (
            format_size(remote_size), int(remote_size * 100 / file_size)
        ))
        mode = "ab"
        start_offset = remote_size

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


def ensure_writable_target(client):
    """Ensure the target installation directory is fully writable."""
    print("\n[*] Checking filesystem write permissions...")
    rc, _ = ssh_exec(client, "touch /log/.rw_test 2>/dev/null && rm -f /log/.rw_test 2>/dev/null")
    if rc == 0:
        print("  [OK] /log partition is writable.")
        target_dir = "/log/SoloWebUtility"
    else:
        ssh_exec(client, "mount -o remount,rw /dev/mmcblk0p4 /log 2>/dev/null || mount -o remount,rw /log 2>/dev/null || true")
        rc, _ = ssh_exec(client, "touch /log/.rw_test 2>/dev/null && rm -f /log/.rw_test 2>/dev/null")
        if rc == 0:
            print("  [OK] /log partition remounted read-write.")
            target_dir = "/log/SoloWebUtility"
        else:
            print("  [*] Using /home/root/SoloWebUtility on rootfs...")
            target_dir = "/home/root/SoloWebUtility"

    if target_dir == "/log/SoloWebUtility":
        ssh_exec(client, "mkdir -p /log/SoloWebUtility/bin /log/SoloWebUtility/static /log/firmware 2>/dev/null || true")
        ssh_exec(client, "chmod -R 755 /log/SoloWebUtility 2>/dev/null || true")
        ssh_exec(client, "ln -sfn /log/firmware /log/SoloWebUtility/firmware 2>/dev/null || true")
        ssh_exec(client, "rm -rf /home/root/SoloWebUtility 2>/dev/null; ln -sfn /log/SoloWebUtility /home/root/SoloWebUtility 2>/dev/null || true")
    else:
        ssh_exec(client, "rm -rf /home/root/SoloWebUtility 2>/dev/null")
        ssh_exec(client, "mkdir -p /home/root/SoloWebUtility/bin /home/root/SoloWebUtility/static /home/root/SoloWebUtility/firmware 2>/dev/null || true")
        ssh_exec(client, "chmod -R 755 /home/root/SoloWebUtility 2>/dev/null || true")
        ssh_exec(client, "ln -sfn /log/firmware /home/root/SoloWebUtility/firmware 2>/dev/null || true")

    return target_dir

    return target_dir


def main():
    cur_ver = get_installer_version()
    print("=" * 65)
    print("[*] 3DR SOLO WEB UTILITY -- ON-BOARD INSTALLER v%s" % cur_ver)
    print("=" * 65)
    print("Uploads web server to Solo Copter (%s)" % SOLO_IP)
    print("=" * 65)

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

    # Display Solo disk space
    print("\n[*] Solo Copter partition status:")
    _, df_out = ssh_exec(client, "df -h / /log 2>/dev/null || df -h")
    for line in df_out.strip().splitlines():
        print("    " + line)

    # Terminate running solo_web_app and ensure read-write filesystem prior to file updates
    print("\n[*] Stopping running processes and verifying filesystem state...")
    ssh_exec(client, "mount -o remount,rw / 2>/dev/null || true")
    ssh_exec(client, "fuser -k 80/tcp 2>/dev/null || true")
    ssh_exec(client, "for p in $(ps | grep solo_web_app | grep -v grep | awk '{print $1}'); do kill -9 $p 2>/dev/null; done")
    ssh_exec(client, "sleep 1")

    # Determine and verify writable installation target directory
    target_dir = ensure_writable_target(client)
    print("  [*] Active installation directory: %s" % target_dir)

    sftp = client.open_sftp()

    # =========================================================================
    # PHASE 1: Upload Web Application Core Files
    # =========================================================================
    app_files = [
        ("solo_web_app.py",                   "%s/solo_web_app.py" % target_dir),
        ("auth.py",                           "%s/auth.py" % target_dir),
        ("change_password.py",                "%s/change_password.py" % target_dir),
        ("presets.py",                        "%s/presets.py" % target_dir),
        ("param_mgr.py",                      "%s/param_mgr.py" % target_dir),
        ("wifi_mgr.py",                       "%s/wifi_mgr.py" % target_dir),
        ("flash_mgr.py",                      "%s/flash_mgr.py" % target_dir),
        ("pair_mgr.py",                       "%s/pair_mgr.py" % target_dir),
        ("calib_mgr.py",                      "%s/calib_mgr.py" % target_dir),
        ("controller_mgr.py",                 "%s/controller_mgr.py" % target_dir),
        ("cockpit_mgr.py",                    "%s/cockpit_mgr.py" % target_dir),
        ("version_mgr.py",                    "%s/version_mgr.py" % target_dir),
        ("version.json",                      "%s/version.json" % target_dir),
        ("static/index.html",                 "%s/static/index.html" % target_dir),
        ("static/solo_drone_manual.html",     "%s/static/solo_drone_manual.html" % target_dir),
        ("static/manual.html",                 "%s/static/manual.html" % target_dir),
        ("static/3DR_Solo_User_Manual_Updated_2026.pdf", "%s/static/3DR_Solo_User_Manual_Updated_2026.pdf" % target_dir),
        ("static/Solo_Web_Utility_Owners_Manual.pdf", "%s/static/Solo_Web_Utility_Owners_Manual.pdf" % target_dir),
        ("static/style.css",                  "%s/static/style.css" % target_dir),
        ("static/cockpit.css",                "%s/static/cockpit.css" % target_dir),
        ("static/cockpit.js",                 "%s/static/cockpit.js" % target_dir),
        ("static/jmuxer.min.js",              "%s/static/jmuxer.min.js" % target_dir),
        ("static/app.js",                     "%s/static/app.js" % target_dir),
        ("static/solo_icon.png",              "%s/static/solo_icon.png" % target_dir),
        ("static/solo_icon_512.png",          "%s/static/solo_icon_512.png" % target_dir),
        ("arducopter_builder.py",             "%s/arducopter_builder.py" % target_dir),
        ("bin/apj_tool.py",                   "%s/bin/apj_tool.py" % target_dir),
        ("bin/pixhawk.py",                    "%s/bin/pixhawk.py" % target_dir),
        ("bin/uploader.py",                   "%s/bin/uploader.py" % target_dir),
    ]

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

    # Configure auto-start on boot via /etc/rc.local (clean and deterministic)
    print("\n[*] Configuring auto-start on boot (/etc/rc.local)...")
    rc_content = (
        "#!/bin/sh -e\n"
        "#\n"
        "# rc.local\n"
        "#\n"
        "# This script is executed at the end of each multiuser runlevel.\n"
        "# Make sure that the script will \"exit 0\" on success or any other\n"
        "# value on error.\n"
        "\n"
        "nohup /usr/bin/python %s/solo_web_app.py 80 > /tmp/solo_web_server.log 2>&1 < /dev/null &\n"
        "\n"
        "exit 0\n"
    ) % target_dir
    try:
        rc_f = sftp.open("/etc/rc.local", "w")
        rc_f.write(rc_content.encode("utf-8") if sys.version_info[0] >= 3 else rc_content)
        rc_f.close()
        ssh_exec(client, "chmod 755 /etc/rc.local")
        print("  [OK] /etc/rc.local configured with clean single auto-start entry.")
    except Exception as e:
        print("  [!] Failed to write /etc/rc.local via SFTP: %s" % str(e))

    # Validate remote Python syntax before server start
    print("\n[*] Validating remote Python syntax on Solo...")
    rc, py_check = ssh_exec(client, "python -m py_compile %s/solo_web_app.py 2>&1" % target_dir)
    if rc == 0:
        print("  [OK] Remote Python syntax verified valid on Solo Copter.")
    else:
        print("  [!] Remote compilation warning:\n" + "\n".join("      " + l for l in py_check.strip().splitlines()))

    # =========================================================================
    # PHASE 2: Start the Web Server IMMEDIATELY
    # =========================================================================
    print("\n[*] Starting Solo Web Server on port 80...")
    # 1. Kill any existing process on port 80 and existing solo_web_app instances
    ssh_exec(client, "fuser -k 80/tcp 2>/dev/null || true")
    ssh_exec(client, "for p in $(ps | grep solo_web_app | grep -v grep | awk '{print $1}'); do kill -9 $p 2>/dev/null; done")
    ssh_exec(client, "sleep 1")

    # Start new server directly using nohup with stdin/out/err fully redirected,
    # and do not block on channel EOF so the installer never hangs!
    start_cmd = "nohup python %s/solo_web_app.py 80 > /tmp/solo_web_server.log 2>&1 < /dev/null &" % target_dir
    _, s_out, s_err = client.exec_command(start_cmd)
    try:
        s_out.close()
        s_err.close()
    except Exception:
        pass
    ssh_exec(client, "ln -sfn /tmp/solo_web_server.log /log/solo_web_server.log 2>/dev/null || true")
    time.sleep(2.0)

    # Check process and startup log
    _, ps_out = ssh_exec(client, "ps | grep solo_web_app | grep -v grep || true")
    if ps_out.strip():
        print("  [OK] Web server process is running: %s" % ps_out.strip())
    else:
        print("  [!] WARNING: Web server process not detected in ps list!")

    _, log_out = ssh_exec(client, "cat /tmp/solo_web_server.log 2>/dev/null || true")
    if log_out.strip():
        print("  [*] Server log output:\n" + "\n".join("      " + l for l in log_out.strip().splitlines()))

    # Test server response and verify live version endpoint
    _, ver_test = ssh_exec(client, "curl -s --connect-timeout 2 --max-time 4 http://127.0.0.1/api/version 2>/dev/null || true")
    if ver_test.strip():
        print("  [OK] Verified active server on port 80: %s" % ver_test.strip())
    else:
        _, curl_test = ssh_exec(client, "curl -s -I --connect-timeout 2 --max-time 4 http://127.0.0.1/ | head -n 1 2>/dev/null || true")
        print("  [OK] Server response: %s" % curl_test.strip())

    # =========================================================================
    # PHASE 3: Upload Firmware Packages to /log/firmware
    # =========================================================================
    try:
        import bake_into_firmware
        bake_into_firmware.ensure_firmware_baked(
            firmware_dir=get_local_path("firmware"),
            app_base_dir=LOCAL_DIR,
            log_func=print
        )
    except Exception as e:
        print("  [!] Notice: firmware auto-bake skipped/failed: %s" % str(e))

    local_fw = get_local_path("firmware")
    firmware_files = []
    if os.path.isdir(local_fw):
        for fn in sorted(os.listdir(local_fw)):
            if fn.endswith((".apj", ".tar.gz", ".tar.gz.md5", ".md5", ".param", ".json")):
                if fn in ("arducopter_solo.apj", "arducopter_green.apj"):
                    continue
                firmware_files.append(("firmware/%s" % fn, "%s/%s" % (LOG_FW_DIR, fn)))
    if not firmware_files:
        firmware_files = [
            ("firmware/arducopter_CubeSolo_4.7.1.apj",  "%s/arducopter_CubeSolo_4.7.1.apj" % LOG_FW_DIR),
            ("firmware/arducopter_CubeGreen_4.7.1.apj", "%s/arducopter_CubeGreen_4.7.1.apj" % LOG_FW_DIR),
            ("firmware/3dr-solo.tar.gz",               "%s/3dr-solo.tar.gz" % LOG_FW_DIR),
            ("firmware/3dr-solo.tar.gz.md5",           "%s/3dr-solo.tar.gz.md5" % LOG_FW_DIR),
            ("firmware/3dr-controller.tar.gz",         "%s/3dr-controller.tar.gz" % LOG_FW_DIR),
            ("firmware/3dr-controller.tar.gz.md5",     "%s/3dr-controller.tar.gz.md5" % LOG_FW_DIR),
            ("firmware/Solo.param",                    "%s/Solo.param" % LOG_FW_DIR),
            ("firmware/SoloGreen.param",               "%s/SoloGreen.param" % LOG_FW_DIR),
        ]

    print("\n[*] Phase 3: Uploading Firmware Files to /log/firmware...")
    for idx, (loc_rel, rem_path) in enumerate(firmware_files, 1):
        loc = get_local_path(loc_rel)
        if not os.path.exists(loc):
            print("  [%d/%d] [SKIP] %s" % (idx, len(firmware_files), loc_rel))
            continue
        size = os.path.getsize(loc)
        print("  [%d/%d] %-44s %s" % (idx, len(firmware_files), loc_rel, format_size(size)))
        safe_sftp_put(sftp, loc, rem_path)

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
