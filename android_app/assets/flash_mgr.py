#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
3DR Solo Firmware Flashing Engine - v1.0.4
Uses the exact built-in scripts from the Open Solo installation packages:
  - preinstall.sh  : Clears staging dirs, prepares /tmp/updates and /firmware
  - postinstall.sh : setPermissions -> packageValid -> prepCopter -> installArduCopter
  Controller iMX6 is staged via SSH using the same sololink_config method.
Compatible with Python 2.7 and Python 3.x. No external dependencies.
"""

import os
import re
import compat
import sys
import time
import shutil
import hashlib
import subprocess
import threading



if sys.version_info[0] >= 3:
    import zipfile
else:
    import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# On Solo, /log has multi-GB persistent storage while / (rootfs) is tiny.
if os.path.isdir("/log/firmware"):
    FIRMWARE_DIR = "/log/firmware"
else:
    FIRMWARE_DIR = os.path.join(BASE_DIR, "firmware")
BIN_DIR      = os.path.join(BASE_DIR, "bin")
TMP_UPDATES_DIR = "/tmp/updates" if os.path.isdir("/tmp") else os.path.join(BASE_DIR, "firmware_files", "tmp_updates")

# Controller tarball (unpacked, pre-staged in firmware directory)
CTRL_TAR      = os.path.join(FIRMWARE_DIR, "3dr-controller.tar.gz")
CTRL_TAR_MD5  = os.path.join(FIRMWARE_DIR, "3dr-controller.tar.gz.md5")

CONTROLLER_IP = "10.1.1.1"
SOLO_IP       = "10.1.1.10"
SSH_USER      = "root"
SSH_OPTS      = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o GlobalKnownHostsFile=/dev/null -o ConnectTimeout=15"

# pixhawk.py: prefer system-installed /usr/bin/pixhawk.py, fall back to bundled
PIXHAWK_BINS = ["/usr/bin/pixhawk.py", os.path.join(BIN_DIR, "pixhawk.py")]


def _fmt_size(n):
    if n >= 1048576:
        return "%.1f MB" % (n / 1048576.0)
    if n >= 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%d B" % n


def parse_apj_version(filename):
    """
    Extract semantic version tuple from APJ filename.
    Examples:
      'arducopter_CubeSolo_4.7.1.apj' -> (4, 7, 1)
      'arducopter_CubeGreen_4.7.1.apj' -> (4, 7, 1)
      'arducopter_CubeSolo_4.7.2.apj' -> (4, 7, 2)
      'arducoptercubesolo-4.7.2.apj' -> (4, 7, 2)
    """
    m = re.search(r'(\d+(?:\.\d+)+)', filename)
    if m:
        parts = []
        for p in m.group(1).split('.'):
            if p.isdigit():
                parts.append(int(p))
        if parts:
            return tuple(parts)
    return (0, 0, 0)


def normalize_cube_type(cube_type):
    """
    Normalize any user or frontend cube representation to either 'CubeSolo' or 'CubeGreen'.
    Handles: 'solo', 'black', 'CubeSolo', 'Solo Cube (Black)', 'cubesolo', 'green', 'CubeGreen', etc.
    """
    if not cube_type:
        return "CubeSolo"
    s = str(cube_type).strip().lower()
    if "green" in s:
        return "CubeGreen"
    return "CubeSolo"


def get_latest_apj(cube_type="solo", search_dirs=None, log_fn=None):
    """
    Scan firmware directories for all .apj files matching the selected Cube type,
    parse semantic version numbers from filenames, log all found candidates,
    and automatically select the highest/latest semantic version.
    Cube types:
      - 'solo' / 'black' / 'cubesolo' / 'CubeSolo' -> CubeSolo
      - 'green' / 'cubegreen' / 'CubeGreen' -> CubeGreen
    """
    def _log(msg, lvl="INFO"):
        if log_fn:
            try:
                log_fn(msg, lvl)
            except TypeError:
                log_fn(msg)

    norm_cube = normalize_cube_type(cube_type)
    is_green = (norm_cube == "CubeGreen")

    if search_dirs is None:
        candidate_dirs = [
            "/log/firmware",
            "/log/updates",
            "/usr/bin/SoloWebUtility2/firmware",
            "/log/SoloWebUtility2/firmware",
            "/home/root/SoloWebUtility2/firmware",
            FIRMWARE_DIR,
            os.path.join(BASE_DIR, "firmware"),
            "/firmware",
            "/tmp/updates",
            TMP_UPDATES_DIR
        ]
        search_dirs = []
        for d in candidate_dirs:
            if d and d not in search_dirs:
                search_dirs.append(d)

    candidates = []
    seen_paths = set()

    for d in search_dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            for fn in sorted(os.listdir(d)):
                if not fn.lower().endswith(".apj"):
                    continue
                fn_lower = fn.lower()
                # Exclude destination filename arducopter.apj to avoid circular references
                if fn_lower == "arducopter.apj":
                    continue
                # Exclude legacy unversioned duplicates
                if fn_lower in ("arducopter_solo.apj", "arducopter_green.apj"):
                    continue

                if is_green:
                    if "green" not in fn_lower:
                        continue
                else:
                    if "green" in fn_lower:
                        continue
                    if not any(token in fn_lower for token in ("solo", "black", "cubesolo")):
                        continue

                fp = os.path.join(d, fn)
                if os.path.isfile(fp) and os.path.getsize(fp) > 100000:
                    real_fp = os.path.realpath(fp) if hasattr(os.path, 'realpath') else fp
                    if real_fp in seen_paths:
                        continue
                    seen_paths.add(real_fp)
                    ver = parse_apj_version(fn)
                    # (version_tuple, file_size, file_path, filename)
                    candidates.append((ver, os.path.getsize(fp), fp, fn))
        except Exception:
            pass

    if candidates:
        # Sort by version tuple descending, then file size descending
        candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
        cand_descriptions = []
        for c in candidates:
            ver_str = ".".join(str(p) for p in c[0]) if c[0] != (0, 0, 0) else "unversioned"
            cand_descriptions.append("%s (v%s, %s)" % (c[3], ver_str, _fmt_size(c[1])))
        _log("[*] Discovered %d %s firmware candidate(s):\n%s" % (
            len(candidates), norm_cube, "\n".join("      - " + cd for cd in cand_descriptions)
        ), "INFO")
        best = candidates[0]
        best_ver_str = ".".join(str(p) for p in best[0]) if best[0] != (0, 0, 0) else "unversioned"
        _log("[✓] Automatically selected highest version: %s (v%s, %s)" % (
            best[3], best_ver_str, _fmt_size(best[1])
        ), "SUCCESS")
        return best[2]

    # Fallback to standard versioned filename conventions
    for d in search_dirs:
        if not d or not os.path.isdir(d):
            continue
        named = "arducopter_CubeGreen_4.7.1.apj" if is_green else "arducopter_CubeSolo_4.7.1.apj"
        p = os.path.join(d, named)
        if os.path.isfile(p) and os.path.getsize(p) > 100000:
            _log("[*] Fallback selected standard firmware: %s" % named, "INFO")
            return p

    return None


find_apj_file = get_latest_apj


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _safe_copy_file(src, dst):
    """
    Safely copy src to dst, skipping if src and dst are the same file
    to prevent shutil.Error: '... and ... are the same file'.
    """
    if not src or not os.path.exists(src):
        return False
    try:
        src_abs = os.path.abspath(src)
        dst_abs = os.path.abspath(dst)
        if src_abs == dst_abs:
            return True
        if os.path.exists(dst):
            if hasattr(os.path, "samefile"):
                try:
                    if os.path.samefile(src, dst):
                        return True
                except Exception:
                    pass
        dst_dir = os.path.dirname(dst)
        if dst_dir and not os.path.exists(dst_dir):
            try:
                compat.makedirs(dst_dir)
            except Exception:
                pass
        shutil.copyfile(src, dst)
        return True
    except Exception:
        try:
            _run_local("cp -f '%s' '%s' 2>/dev/null || true" % (src, dst))
            return os.path.exists(dst)
        except Exception:
            return False

def _run_local(cmd):
    """Run a shell command locally. Returns (rc, output_str)."""
    cwd_dir = TMP_UPDATES_DIR if os.path.exists(TMP_UPDATES_DIR) else None
    p = subprocess.Popen(
        cmd, shell=True, cwd=cwd_dir,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    out, _ = p.communicate()
    if sys.version_info[0] >= 3:
        out = out.decode("utf-8", "ignore")
    return p.returncode, out


# PTY and select for automatic interactive password input on Linux
try:
    import pty
    import select
except ImportError:
    pty = None
    select = None

SOLO_ROOT_PASSWORD = "TjSDBkAu"


def _run_with_ssh_auth(cmd, host="10.1.1.1", password=SOLO_ROOT_PASSWORD, timeout=600):
    """
    Run an ssh or scp command. If public key authentication works, it connects immediately.
    If the remote host prompts for password:, the pseudo-terminal sends password automatically.
    """
    cwd_dir = TMP_UPDATES_DIR if os.path.exists(TMP_UPDATES_DIR) else None
    if pty is not None and select is not None:
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(
                cmd, shell=True, cwd=cwd_dir,
                stdin=slave, stdout=slave, stderr=slave,
                close_fds=True, preexec_fn=os.setsid
            )
        except Exception:
            proc = subprocess.Popen(
                cmd, shell=True, cwd=cwd_dir,
                stdin=slave, stdout=slave, stderr=slave,
                close_fds=True
            )
        os.close(slave)

        output = []
        start = time.time()
        sent_pass = False

        while proc.poll() is None:
            r, _, _ = select.select([master], [], [], 0.5)
            if r:
                try:
                    data = os.read(master, 2048)
                    if not data:
                        break
                    output.append(data)
                    if not sent_pass and b"password:" in data.lower():
                        os.write(master, (password + "\n").encode("utf-8"))
                        sent_pass = True
                except OSError:
                    break
            if time.time() - start > timeout:
                try:
                    proc.kill()
                except Exception:
                    pass
                break

        # Drain remaining buffer
        while True:
            r, _, _ = select.select([master], [], [], 0.2)
            if r:
                try:
                    data = os.read(master, 2048)
                    if not data:
                        break
                    output.append(data)
                except OSError:
                    break
            else:
                break
        os.close(master)
        proc.wait()
        raw = b"".join(output)
        if sys.version_info[0] >= 3:
            out_str = raw.decode("utf-8", "ignore")
        else:
            out_str = raw
        return proc.returncode, out_str
    else:
        return _run_local(cmd)


def _ssh(cmd, host, saved_ssid=None, log_callback=None):
    """Run a command on a remote host (or locally if localhost/SOLO_IP)."""
    if not host or host in (SOLO_IP, "127.0.0.1", "localhost"):
        return _run_local(cmd)
    ssh_cmd = 'ssh %s %s@%s "%s"' % (SSH_OPTS, SSH_USER, host, cmd.replace('"', '\\"'))
    return _run_with_ssh_auth(ssh_cmd, host=host)


def _scp_to(local_path, remote_host, remote_path, saved_ssid=None, log_callback=None, progress_callback=None):
    """SCP a local file to a remote host (or copy locally if localhost/SOLO_IP)."""
    if not remote_host or remote_host in (SOLO_IP, "127.0.0.1", "localhost"):
        dst_dir = os.path.dirname(remote_path)
        if dst_dir and not os.path.exists(dst_dir):
            compat.makedirs(dst_dir)
        shutil.copyfile(local_path, remote_path)
        if progress_callback:
            try:
                progress_callback(100)
            except Exception:
                pass
        return 0, "Transfer successful"
    scp_cmd = 'scp %s "%s" %s@%s:"%s"' % (
        SSH_OPTS, local_path, SSH_USER, remote_host, remote_path
    )
    return _run_with_ssh_auth(scp_cmd, host=remote_host, timeout=900)


def _get_file_md5(filepath):
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def release_mavlink_connections():
    """Release/pause any active MAVLink sockets in the web server before flashing."""
    try:
        import cockpit_mgr
        if hasattr(cockpit_mgr, "mav_client") and cockpit_mgr.mav_client:
            cockpit_mgr.mav_client.paused = True
            if cockpit_mgr.mav_client.sock:
                try:
                    cockpit_mgr.mav_client.sock.close()
                except Exception:
                    pass
                cockpit_mgr.mav_client.sock = None
    except Exception:
        pass
    try:
        from modules import telemetry
        if hasattr(telemetry, "mav_client") and telemetry.mav_client:
            telemetry.mav_client.paused = True
            if telemetry.mav_client.sock:
                try:
                    telemetry.mav_client.sock.close()
                except Exception:
                    pass
                telemetry.mav_client.sock = None
    except Exception:
        pass


def bake_web_utility_into_tar(tar_path, app_base_dir=None, log_func=None):
    """
    Inject SoloWebUtility2 application files and /etc/rc.local startup entry
    into 3dr-solo.tar.gz, then regenerate the .md5 checksum file.
    """
    import tarfile
    try:
        import StringIO
        from StringIO import StringIO
    except ImportError:
        from io import BytesIO as StringIO

    if not os.path.isfile(tar_path):
        if log_func:
            log_func("[!] Cannot bake into tarball: %s does not exist" % tar_path, "WARNING")
        return False

    if app_base_dir is None:
        app_base_dir = os.path.dirname(os.path.abspath(__file__))

    if log_func:
        log_func("[*] Baking SoloWebUtility2 into %s..." % os.path.basename(tar_path), "INFO")

    tmp_dir = os.path.dirname(tar_path)
    tmp_path = os.path.join(tmp_dir, "3dr-solo-baked.tar.gz.tmp")
    if os.path.exists(tmp_path):
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    TOP_FILES = [
        "app.py", "webcore.py", "compat.py", "param_fetch.py",
        "auth.py", "change_password.py", "presets.py", "param_mgr.py", "wifi_mgr.py",
        "flash_mgr.py", "pair_mgr.py", "calib_mgr.py", "controller_mgr.py",
        "cockpit_mgr.py", "version_mgr.py", "arducopter_builder.py", "version.json"
    ]
    TREE_DIRS = ["modules", "templates", "static", "bin"]
    SKIP_EXTS = (".pyc", ".pyo", ".apk", ".idsig")
    SKIP_DIRS = ("__pycache__", ".git", "firmware", "firmware_files", "cache_staging", "tmp_updates", "build", "dist", "docs")

    rc_local_content = (
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
        "elif [ -f /log/SoloWebUtility2/solo_web_app.py ]; then\n"
        "    cd /log/SoloWebUtility2 && nohup /usr/bin/python /log/SoloWebUtility2/solo_web_app.py 80 </dev/null >/tmp/solo_web.log 2>&1 &\n"
        "elif [ -f /log/SoloWebUtility2/app.py ]; then\n"
        "    cd /log/SoloWebUtility2 && nohup /usr/bin/python /log/SoloWebUtility2/app.py 80 </dev/null >/tmp/solo_web.log 2>&1 &\n"
        "elif [ -f /usr/bin/SoloWebUtility2/solo_web_app.py ]; then\n"
        "    cd /usr/bin/SoloWebUtility2 && nohup /usr/bin/python /usr/bin/SoloWebUtility2/solo_web_app.py 80 </dev/null >/tmp/solo_web.log 2>&1 &\n"
        "elif [ -f /usr/bin/SoloWebUtility2/app.py ]; then\n"
        "    cd /usr/bin/SoloWebUtility2 && nohup /usr/bin/python /usr/bin/SoloWebUtility2/app.py 80 </dev/null >/tmp/solo_web.log 2>&1 &\n"
        "fi\n"
        "\n"
        "exit 0\n"
    )

    try:
        with tarfile.open(tar_path, "r:gz") as tin:
            members = tin.getmembers()
            with tarfile.open(tmp_path, "w:gz") as tout:
                # Copy existing members (skip any existing rc.local or SoloWebUtility2 files to avoid duplicates)
                for m in members:
                    if m.name in ("etc/rc.local", "./etc/rc.local") or "SoloWebUtility" in m.name:
                        continue
                    if m.isreg():
                        tout.addfile(m, tin.extractfile(m))
                    else:
                        tout.addfile(m)

                # Add etc/rc.local
                ti_rc = tarfile.TarInfo(name="etc/rc.local")
                ti_rc.size = len(rc_local_content)
                ti_rc.mode = 0o755
                ti_rc.mtime = int(time.time())
                rc_bytes = rc_local_content.encode("utf-8") if sys.version_info[0] >= 3 else rc_local_content
                tout.addfile(ti_rc, StringIO(rc_bytes))

                # Add SoloWebUtility2 application files under usr/bin/SoloWebUtility2/
                injected_count = 0
                for fn in TOP_FILES:
                    fp = os.path.join(app_base_dir, fn)
                    if os.path.isfile(fp):
                        arcname = "usr/bin/SoloWebUtility2/%s" % fn
                        tout.add(fp, arcname=arcname)
                        injected_count += 1

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
                                tout.add(fp, arcname=arcname)
                                injected_count += 1

        # Replace original tarball with baked version
        if os.path.exists(tar_path):
            try:
                os.remove(tar_path)
            except Exception:
                pass
        os.rename(tmp_path, tar_path)

        # Regenerate MD5
        md5_calc = hashlib.md5()
        with open(tar_path, "rb") as f:
            while True:
                buf = f.read(65536)
                if not buf:
                    break
                md5_calc.update(buf)
        digest = md5_calc.hexdigest()
        md5_file = tar_path + ".md5"
        base_name = os.path.basename(tar_path)
        with open(md5_file, "w") as f:
            f.write("%s  %s\n" % (digest, base_name))

        if log_func:
            log_func("[OK] Baked SoloWebUtility2 (%d files) into %s (MD5: %s)." % (
                injected_count, base_name, digest[:8]
            ), "SUCCESS")
        return True
    except Exception as ex:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        if log_func:
            log_func("[!] Failed baking SoloWebUtility2 into tarball: %s" % str(ex), "WARNING")
        return False


# ---------------------------------------------------------------------------
# FlashManager
# ---------------------------------------------------------------------------

class FlashManager(object):
    def __init__(self):
        self.is_flashing  = False
        self.progress     = 0
        self.current_stage = "Idle"
        self.log_buffer   = []
        self.status       = "ready"   # ready | running | success | error
        self.lock         = threading.Lock()

    # -----------------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------------
    def log(self, message, level="INFO"):
        ts    = time.strftime("%H:%M:%S")
        entry = {"time": ts, "level": level, "message": message}
        with self.lock:
            self.log_buffer.append(entry)
            if len(self.log_buffer) > 600:
                self.log_buffer = self.log_buffer[-600:]
        print("[%s] [%s] %s" % (ts, level, message))

    # -----------------------------------------------------------------------
    # State access
    # -----------------------------------------------------------------------
    def get_state(self):
        with self.lock:
            return {
                "is_flashing":   self.is_flashing,
                "progress":      self.progress,
                "current_stage": self.current_stage,
                "status":        self.status,
                "logs":          list(self.log_buffer)
            }

    def reset_state(self):
        with self.lock:
            self.is_flashing  = False
            self.progress     = 0
            self.current_stage = "Idle"
            self.status       = "ready"
            self.log_buffer   = []

    def _set(self, stage=None, progress=None):
        with self.lock:
            if stage    is not None: self.current_stage = stage
            if progress is not None: self.progress      = progress

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------
    def start_flash(self, cube_type="solo", install_mode="full"):
        try:
            import cockpit_mgr
            if cockpit_mgr.is_vehicle_armed():
                return False, "SAFETY INTERLOCK: Cannot install firmware while vehicle is ARMED!"
        except Exception:
            pass
        if self.is_flashing:
            return False, "Flashing already in progress."
        self.reset_state()
        self.is_flashing = True
        self.status      = "running"
        t = threading.Thread(target=self._pipeline, args=(cube_type, install_mode))
        t.daemon = True
        t.start()
        return True, "Firmware installation pipeline started."

    # -----------------------------------------------------------------------
    # Internal command runner
    # -----------------------------------------------------------------------
    def _run(self, cmd, host=None, label=""):
        """Run command locally or remotely; log all output; return rc."""
        location = " @%s" % host if host else ""
        self.log("[CMD%s] %s" % (location, cmd), "INFO")
        if host:
            saved_ssid = getattr(self, "saved_ssid", None)
            rc, out = _ssh(cmd, host, saved_ssid=saved_ssid, log_callback=self.log)
        else:
            rc, out = _run_local(cmd)
        for line in out.splitlines():
            l = line.strip()
            if l:
                self.log("    %s" % l, "INFO")
        if rc != 0:
            self.log("[!] Command returned rc=%d" % rc, "WARNING")
        return rc

    def _find_pixhawk(self):
        for p in PIXHAWK_BINS:
            if os.path.exists(p):
                return p
        return PIXHAWK_BINS[-1]  # bundled fallback

    # -----------------------------------------------------------------------
    # Main pipeline
    # -----------------------------------------------------------------------
    def _pipeline(self, cube_type, install_mode):
        try:
            self.saved_ssid = None
            self.modified_profiles = []

            norm_cube = normalize_cube_type(cube_type)
            self.log("=" * 60, "HEADER")
            self.log("🛸 3DR SOLO OPEN SOLO LATEST STABLE FIRMWARE INSTALLER", "HEADER")
            self.log("Cube: %s (%s) | Mode: %s" % (
                "Proficnc Green Cube" if norm_cube == "CubeGreen" else "3DR Solo Cube (Black)",
                norm_cube,
                "Full Install" if install_mode == "full" else "Cube Only"
            ), "INFO")
            self.log("=" * 60, "HEADER")

            # ================================================================
            # STAGE 1: Prepare Staging Directories & Select Latest Firmware
            # ================================================================
            self._set("Stage 1/3 — Preparing Installation", 5)
            self.log("\n[STAGE 1] Preparing installation environment...", "STAGE")

            # Ensure rootfs is mounted read-write and directories exist
            _run_local("mount -o remount,rw / 2>/dev/null || true")
            _run_local("mkdir -p /firmware /tmp/updates /log/updates")
            for d in ["/firmware", "/tmp/updates", "/log/updates", TMP_UPDATES_DIR]:
                if not os.path.exists(d):
                    try:
                        compat.makedirs(d)
                    except Exception:
                        pass

            # Scan firmware directory for latest versioned APJ
            apj_selected = get_latest_apj(cube_type, log_fn=self.log)
            if not apj_selected or not os.path.isfile(apj_selected):
                checked_dirs = [d for d in [
                    "/log/firmware",
                    "/log/updates",
                    "/usr/bin/SoloWebUtility2/firmware",
                    "/log/SoloWebUtility2/firmware",
                    "/home/root/SoloWebUtility2/firmware",
                    FIRMWARE_DIR,
                    os.path.join(BASE_DIR, "firmware"),
                    "/firmware",
                    "/tmp/updates",
                    TMP_UPDATES_DIR
                ] if os.path.isdir(d)]
                self.log("[!] Searched existing directories: %s" % (", ".join(checked_dirs) if checked_dirs else "none"), "ERROR")
                raise IOError("No matching ArduCopter firmware (.apj) found for %s (%s)" % (cube_type, norm_cube))

            self.log("[*] Selected latest ArduCopter firmware: %s" % os.path.basename(apj_selected), "INFO")
            _safe_copy_file(apj_selected, os.path.join(TMP_UPDATES_DIR, "arducopter.apj"))

            # Locate copter i.MX6 firmware
            solo_tar_path = os.path.join(FIRMWARE_DIR, "3dr-solo.tar.gz")
            if not os.path.exists(solo_tar_path):
                for alt_d in ["/log/firmware", "/log/updates", "/usr/bin/SoloWebUtility2/firmware", "/log/SoloWebUtility2/firmware", os.path.join(BASE_DIR, "firmware"), "/tmp/updates"]:
                    alt_p = os.path.join(alt_d, "3dr-solo.tar.gz")
                    if os.path.exists(alt_p):
                        solo_tar_path = alt_p
                        break

            if install_mode == "full":
                if not os.path.exists(solo_tar_path):
                    raise IOError("3dr-solo.tar.gz not found in firmware directory")
                _safe_copy_file(solo_tar_path, os.path.join(TMP_UPDATES_DIR, "3dr-solo.tar.gz"))
                solo_md5_path = solo_tar_path + ".md5"
                if os.path.exists(solo_md5_path):
                    _safe_copy_file(solo_md5_path, os.path.join(TMP_UPDATES_DIR, "3dr-solo.tar.gz.md5"))

            self._set(progress=20)
            self.log("[✓] Staging environment ready.", "SUCCESS")

            # ================================================================
            # STAGE 2: Flash Cube Autopilot
            # ================================================================
            self._set("Stage 2/3 — Flashing Cube Autopilot", 22)
            self.log("\n[STAGE 2] Flashing ArduCopter firmware to Cube...", "STAGE")

            # Ensure / is remounted read-write and staging directories exist
            _run_local("mount -o remount,rw / 2>/dev/null || true")
            _run_local("mkdir -p /firmware /tmp/updates /log/updates")

            # Locate APJ file across all staged and repository locations
            apj_src = find_apj_file(cube_type, log_fn=self.log)
            if not apj_src or not os.path.isfile(apj_src):
                raise IOError("Could not locate ArduCopter firmware (.apj) for cube type: %s (%s)" % (cube_type, norm_cube))

            self.log("[*] Selected ArduCopter firmware binary: %s" % os.path.basename(apj_src), "INFO")

            # Native package checksum validation
            self.log("[*] Validating package MD5 natively in Python...", "INFO")
            solo_tar_path = os.path.join(TMP_UPDATES_DIR, "3dr-solo.tar.gz")
            if not os.path.exists(solo_tar_path):
                for alt_d in ["/log/firmware", "/log/updates", "/usr/bin/SoloWebUtility2/firmware", "/log/SoloWebUtility2/firmware", os.path.join(BASE_DIR, "firmware"), "/tmp/updates"]:
                    alt_p = os.path.join(alt_d, "3dr-solo.tar.gz")
                    if os.path.exists(alt_p):
                        solo_tar_path = alt_p
                        break
            if os.path.exists(solo_tar_path):
                md5_val = _get_file_md5(solo_tar_path)
                self.log("    Package MD5 (3dr-solo.tar.gz): %s" % md5_val, "INFO")

            # a) Release/close active MAVLink connection before flashing so /dev/ttyS1 is not locked
            self.log("[*] Releasing active MAVLink telemetry connections...", "INFO")
            release_mavlink_connections()

            # Run sololink_config --update-prepare pixhawk FIRST so /dev/ttyS1 is not locked
            # Note: on 3DR Solo, sololink_config --update-prepare pixhawk wipes /firmware/*
            self.log("[*] sololink_config --update-prepare pixhawk...", "INFO")
            self._run("sololink_config --update-prepare pixhawk 2>/dev/null || true")

            # b) Copy selected .apj to /firmware/arducopter.apj AFTER sololink_config --update-prepare completes
            _run_local("mkdir -p /firmware /tmp/updates")
            self.log("[*] Staging ArduCopter firmware to /firmware/arducopter.apj...", "INFO")
            _safe_copy_file(apj_src, "/tmp/updates/arducopter.apj")
            _safe_copy_file(apj_src, "/firmware/arducopter.apj")
            _run_local("chmod 644 /firmware/arducopter.apj 2>/dev/null || true")

            # Verify os.path.exists('/firmware/arducopter.apj') is True AFTER update-prepare
            if not os.path.exists("/firmware/arducopter.apj") or not os.path.isfile("/firmware/arducopter.apj") or os.path.getsize("/firmware/arducopter.apj") < 100000:
                raise IOError("Failed to stage /firmware/arducopter.apj: file missing or empty after sololink_config update-prepare")

            self.log("[✓] Verified /firmware/arducopter.apj is present (%d bytes)." % os.path.getsize("/firmware/arducopter.apj"), "SUCCESS")

            self._set(progress=25)

            # c) Use system /usr/bin/pixhawk.py --load /firmware/arducopter.apj and verify exit code 0
            pixhawk_bin = "/usr/bin/pixhawk.py"
            if not os.path.exists(pixhawk_bin):
                pixhawk_bin = self._find_pixhawk()
            _run_local("chmod a+x /usr/bin/pixhawk.py /usr/bin/uploader.py 2>/dev/null || true")

            self.log("[*] %s --load /firmware/arducopter.apj" % pixhawk_bin, "INFO")
            cmd_load = "%s --load /firmware/arducopter.apj" % pixhawk_bin
            try:
                proc_load = subprocess.Popen(
                    cmd_load, shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT
                )
                while True:
                    line = proc_load.stdout.readline()
                    if not line and proc_load.poll() is not None:
                        break
                    if line:
                        l = line.rstrip()
                        if l:
                            self.log("    %s" % l, "INFO")
                rc_load = proc_load.poll()
            except Exception as ex_load:
                rc_load = -1
                self.log("[!] Exception running pixhawk.py: %s" % str(ex_load), "WARNING")

            if rc_load != 0:
                raise RuntimeError("pixhawk.py failed to flash Cube autopilot (exit code %d)" % rc_load)
            # Do NOT run rm -f /firmware/*.apj! pixhawk.py handles moving it to /firmware/loaded/

            self._set(progress=70)
            self.log("[✓] Cube Autopilot flash complete. Cube reboots automatically.", "SUCCESS")

            # Cube-only mode ends here
            if install_mode == "cube_only":
                self._set("Complete — Cube Flashed", 100)
                self.status = "success"
                self.log("\n" + "=" * 60, "HEADER")
                self.log("🎉 CUBE AUTOPILOT UPDATE COMPLETE!", "SUCCESS")
                self.log("Run IMU and Compass calibration via your Solo App.", "INFO")
                self.log("=" * 60, "HEADER")
                self.is_flashing = False
                return

            # Stage pre-baked i.MX6 firmware directly into /tmp/updates/ and /log/updates/
            # (No on-the-fly recompression on the drone CPU)
            if install_mode == "full":
                self.log("[*] Staging pre-baked i.MX6 firmware for update...", "INFO")
                if not os.path.exists(solo_tar_path):
                    for alt_d in ["/log/firmware", os.path.join(BASE_DIR, "firmware"), "/tmp/updates", "/log/updates"]:
                        alt_p = os.path.join(alt_d, "3dr-solo.tar.gz")
                        if os.path.exists(alt_p) and os.path.getsize(alt_p) > 1000000:
                            solo_tar_path = alt_p
                            break

                _safe_copy_file(solo_tar_path, "/tmp/updates/3dr-solo.tar.gz")
                _safe_copy_file(solo_tar_path, "/log/updates/3dr-solo.tar.gz")

                solo_md5_path = solo_tar_path + ".md5"
                if not os.path.exists(solo_md5_path):
                    solo_md5_path = os.path.join(TMP_UPDATES_DIR, "3dr-solo.tar.gz.md5")

                if os.path.exists(solo_md5_path):
                    _safe_copy_file(solo_md5_path, "/tmp/updates/3dr-solo.tar.gz.md5")
                    _safe_copy_file(solo_md5_path, "/log/updates/3dr-solo.tar.gz.md5")
                elif os.path.exists("/log/updates/3dr-solo.tar.gz"):
                    md5_digest = _get_file_md5("/log/updates/3dr-solo.tar.gz")
                    for md5_dst in ["/tmp/updates/3dr-solo.tar.gz.md5", "/log/updates/3dr-solo.tar.gz.md5"]:
                        with open(md5_dst, "w") as f:
                            f.write("%s  3dr-solo.tar.gz\n" % md5_digest)

                if os.path.isfile("/log/updates/3dr-solo.tar.gz") and os.path.getsize("/log/updates/3dr-solo.tar.gz") > 1000000:
                    self.log("[✓] Verified /log/updates/3dr-solo.tar.gz staged (%d bytes)." % os.path.getsize("/log/updates/3dr-solo.tar.gz"), "SUCCESS")
                else:
                    self.log("[!] Warning: /log/updates/3dr-solo.tar.gz not found or under 1MB", "WARNING")

            self._run("touch /log/updates/UPDATE /log/updates/RESETSETTINGS")

            # ================================================================
            # STAGE 3: Stage Controller iMX6 update (via SSH to 10.1.1.1)
            # ================================================================
            self._set("Stage 3/3 — Staging Controller iMX6 Update", 72)
            self.log("\n[STAGE 3] Connecting to Controller (%s) to stage update..." % CONTROLLER_IP, "STAGE")

            controller_staged = False
            if not os.path.exists(CTRL_TAR):
                self.log("[!] Controller tarball not found: %s" % CTRL_TAR, "WARNING")
                self.log("[!] Skipping controller update — copter update only.", "WARNING")
            else:
                # Prepare dirs on controller
                self.log("[*] Preparing /tmp/updates and /log/updates on controller...", "INFO")
                rc = self._run("mkdir -p /tmp/updates /log/updates", host=CONTROLLER_IP)
                if rc != 0:
                    self.log("[!] FAILED to connect to controller over SSH (rc=%d). Staging aborted." % rc, "ERROR")
                    self.status = "error"
                    self._set("Error: Controller unreachable over SSH", self.progress)
                    self.is_flashing = False
                    return
                else:
                    # sololink_config --update-prepare on controller
                    self.log("[*] sololink_config --update-prepare sololink (controller)...", "INFO")
                    self._run("sololink_config --update-prepare sololink 2>/dev/null || true", host=CONTROLLER_IP)

                    # SCP controller tarball
                    self._set(progress=80)
                    self.log("[*] Uploading 3dr-controller.tar.gz to controller...", "INFO")
                    rc, out = _scp_to(CTRL_TAR, CONTROLLER_IP, "/tmp/updates/3dr-controller.tar.gz", saved_ssid=self.saved_ssid, log_callback=self.log)
                    for line in out.splitlines():
                        l = line.strip()
                        if l:
                            self.log("    %s" % l, "INFO")

                    if rc != 0:
                        self.log("[!] Failed to transfer 3dr-controller.tar.gz to Controller.", "ERROR")
                        self.status = "error"
                        self._set("Error: Controller file transfer failed", self.progress)
                        self.is_flashing = False
                        return

                    # Write MD5 on controller
                    if os.path.exists(CTRL_TAR_MD5):
                        rc, out = _scp_to(CTRL_TAR_MD5, CONTROLLER_IP, "/tmp/updates/3dr-controller.tar.gz.md5", saved_ssid=self.saved_ssid, log_callback=self.log)
                    else:
                        ctrl_md5 = _get_file_md5(CTRL_TAR)
                        self._run(
                            "echo '%s  3dr-controller.tar.gz' > /tmp/updates/3dr-controller.tar.gz.md5" % ctrl_md5,
                            host=CONTROLLER_IP
                        )

                    # Stage update flags
                    self._run("cp /tmp/updates/3dr-controller.tar.* /log/updates/ 2>/dev/null || true", host=CONTROLLER_IP)
                    self._run("touch /log/updates/UPDATE /log/updates/RESETSETTINGS", host=CONTROLLER_IP)
                    self._set(progress=90)
                    self.log("[✓] Controller iMX6 update staged successfully.", "SUCCESS")
                    controller_staged = True

                    # Establish RUN_PAIR_ONCE marker file confirmation flag
                    self.log("[Wi-Fi] Staging one-time post-update pairing trigger...", "INFO")
                    self._run("mkdir -p /log/updates && touch /log/updates/RUN_PAIR_ONCE")

                    try:
                        from modules.telemetry import set_copter_boot_in_progress, set_live_copter_status
                        set_copter_boot_in_progress(True)
                        set_live_copter_status(False)
                    except Exception:
                        pass

                    # Trigger copter reboot locally
                    self.log("\n[AUTO-REBOOT] Triggering copter reboot...", "HEADER")
                    self._run("nohup sh -c 'sleep 2 && reboot' >/dev/null 2>&1 &")
                    self.log("[✓] Copter reboot triggered cleanly.", "SUCCESS")

                    # Final background 'nohup sh -c "sleep 2 && reboot"' command executed on controller at 10.1.1.1
                    self.log("\n[AUTO-REBOOT] Triggering controller reboot at 10.1.1.1...", "HEADER")
                    self._run("nohup sh -c 'sleep 2 && reboot' >/dev/null 2>&1 &", host=CONTROLLER_IP)
                    self.log("[✓] Controller reboot triggered.", "SUCCESS")

            # ================================================================
            # Finalize staging and notify web layer
            # ================================================================
            self._set("Rebooting — Applying Updates", 100)
            self.status = "success"
            self.is_flashing = False
            self.log("\n" + "=" * 60, "HEADER")
            if controller_staged:
                self.log("🎉 FULL OPEN SOLO LATEST STABLE INSTALLATION STAGED SUCCESSFULLY!", "SUCCESS")
                self.log("Copter and Controller are rebooting and applying updates.", "INFO")
            else:
                self.log("🎉 COPTER OPEN SOLO LATEST STABLE STAGED (Controller skipped)!", "SUCCESS")
                self.log("Copter is rebooting and applying updates.", "INFO")
                self.log("[Wi-Fi] Staging one-time post-update pairing trigger...", "INFO")
                self._run("mkdir -p /log/updates && touch /log/updates/RUN_PAIR_ONCE")
                self.log("\n[AUTO-REBOOT] Triggering copter reboot...", "HEADER")
                self._run("nohup sh -c 'sleep 2 && reboot' >/dev/null 2>&1 &")
                self.log("[✓] Copter reboot triggered cleanly.", "SUCCESS")
            self.log("Wait ~6 minutes for reboot to finish.", "INFO")
            self.log("Then hold the Pair button on the bottom of the Solo for 5 seconds. The SoloLink Wi-Fi network must also be connected to continue.", "INFO")
            self.log("=" * 60, "HEADER")

        except Exception as e:
            self.status = "error"
            self._set(stage="Error: %s" % str(e))
            self.log("[CRITICAL ERROR] %s" % str(e), "ERROR")
        finally:
            self.is_flashing = False


# Singleton
flash_engine = FlashManager()
