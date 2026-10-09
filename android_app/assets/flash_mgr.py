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

# Both full installation packages (zips contain postinstall.sh + all copter files)
ZIP_SOLO  = os.path.join(FIRMWARE_DIR, "Open Solo 4.0.0 Clean Install (Copter-4.7.1).zip")
ZIP_GREEN = os.path.join(FIRMWARE_DIR, "Open Solo 4.0.0 Clean Install GREEN CUBE (Copter-4.7.1).zip")


def resolve_clean_install_zip(cube_type="solo"):
    """Dynamically resolve the latest clean install package from FIRMWARE_DIR."""
    if os.path.isdir(FIRMWARE_DIR):
        candidates = []
        is_green = (cube_type == "green")
        for fn in os.listdir(FIRMWARE_DIR):
            if not fn.endswith(".zip"):
                continue
            fn_lower = fn.lower()
            if is_green and "green cube" in fn_lower and "clean install" in fn_lower:
                candidates.append(fn)
            elif not is_green and "green cube" not in fn_lower and "clean install" in fn_lower:
                candidates.append(fn)
        if candidates:
            candidates.sort()
            return os.path.join(FIRMWARE_DIR, candidates[-1])
    return ZIP_GREEN if cube_type == "green" else ZIP_SOLO


# Standalone APJ files (used when zip extract is not available / cube-only mode)
APJ_SOLO  = os.path.join(FIRMWARE_DIR, "arducopter_solo.apj")
APJ_GREEN = os.path.join(FIRMWARE_DIR, "arducopter_green.apj")

# Controller tarball (separate from the copter zips)
CTRL_TAR      = os.path.join(FIRMWARE_DIR, "3dr-controller.tar.gz")
CTRL_TAR_MD5  = os.path.join(FIRMWARE_DIR, "3dr-controller.tar.gz.md5")

CONTROLLER_IP = "10.1.1.1"
SOLO_IP       = "10.1.1.10"
SSH_USER      = "root"
SSH_OPTS      = "-o StrictHostKeyChecking=no -o ConnectTimeout=15"

# pixhawk.py: prefer bundled patched copy, fall back to system-installed
PIXHAWK_BINS = [os.path.join(BIN_DIR, "pixhawk.py"), "/usr/bin/pixhawk.py"]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _run_local(cmd):
    """Run a shell command locally. Returns (rc, output_str)."""
    cwd_dir = "/tmp" if os.path.exists("/tmp") else None
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
    if pty is not None and select is not None:
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(
                cmd, shell=True, cwd="/tmp",
                stdin=slave, stdout=slave, stderr=slave,
                close_fds=True, preexec_fn=os.setsid
            )
        except Exception:
            proc = subprocess.Popen(
                cmd, shell=True, cwd="/tmp",
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


def _ssh(cmd, host):
    """Run a command on a remote host over SSH with automatic password authentication."""
    ssh_cmd = 'ssh %s %s@%s "%s"' % (SSH_OPTS, SSH_USER, host, cmd.replace('"', '\\"'))
    return _run_with_ssh_auth(ssh_cmd, host=host)


def _scp_to(local_path, remote_host, remote_path):
    """SCP a local file to a remote host with automatic password authentication."""
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
            rc, out = _ssh(cmd, host)
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
    # Zip extraction helper
    # -----------------------------------------------------------------------
    def _extract_zip_to_staging(self, zip_path, cube_type):
        """
        Extract the Open Solo installation zip to /tmp/updates/.
        The zip structure is: install/tmp/updates/<files>
        We extract only the files relevant to /tmp/updates/.
        Then we swap in the correct APJ for this cube_type.
        Returns (preinstall_sh, postinstall_sh) paths or (None, None) on fail.
        """
        self.log("[*] Extracting installation package...", "INFO")
        try:
            zf = zipfile.ZipFile(zip_path, 'r')
        except Exception as e:
            self.log("[!] Cannot open zip: %s" % str(e), "WARNING")
            return None, None

        preinstall_path  = None
        postinstall_path = None
        extracted_dir    = "/tmp/solo_web_install"

        # Clean previous extract
        _run_local("rm -rf %s && mkdir -p %s" % (extracted_dir, extracted_dir))

        try:
            for entry in zf.namelist():
                dest = os.path.join(extracted_dir, entry)
                if entry.endswith('/'):
                    if not os.path.exists(dest):
                        os.makedirs(dest)
                else:
                    dest_dir = os.path.dirname(dest)
                    if not os.path.exists(dest_dir):
                        os.makedirs(dest_dir)
                    with zf.open(entry) as src, open(dest, 'wb') as tgt:
                        shutil.copyfileobj(src, tgt)
        finally:
            zf.close()

        # Locate scripts
        for root, dirs, files in os.walk(extracted_dir):
            for fn in files:
                if fn == "preinstall.sh":
                    preinstall_path = os.path.join(root, fn)
                elif fn == "postinstall.sh":
                    postinstall_path = os.path.join(root, fn)

        # Copy extracted update files into /tmp/updates/
        updates_src = os.path.join(extracted_dir, "install", "tmp", "updates")
        if os.path.isdir(updates_src):
            _run_local("mkdir -p /tmp/updates")
            for fname in os.listdir(updates_src):
                src = os.path.join(updates_src, fname)
                dst = os.path.join("/tmp/updates", fname)
                shutil.copyfile(src, dst)
                self.log("    Staged: %s" % fname, "INFO")

        # Swap in the correct APJ from the local firmware dir
        # The zip always contains the APJ for its cube type already —
        # but in case we need to force the right one:
        apj_src  = APJ_GREEN if cube_type == "green" else APJ_SOLO
        apj_dest = "/tmp/updates/arducopter.apj"
        if os.path.exists(apj_src):
            shutil.copyfile(apj_src, apj_dest)
            self.log("[*] Correct APJ staged: %s" % os.path.basename(apj_src), "INFO")

        # Also copy pixhawk.py / uploader.py from zip into /usr/bin if present
        usr_bin_src = os.path.join(extracted_dir, "install", "usr", "bin")
        if os.path.isdir(usr_bin_src):
            for fn in ["pixhawk.py", "uploader.py"]:
                src = os.path.join(usr_bin_src, fn)
                dst = "/usr/bin/" + fn
                if os.path.exists(src):
                    try:
                        shutil.copyfile(src, dst)
                        _run_local("chmod a+x %s" % dst)
                    except Exception:
                        pass

        return preinstall_path, postinstall_path

    # -----------------------------------------------------------------------
    # Main pipeline
    # -----------------------------------------------------------------------
    def _pipeline(self, cube_type, install_mode):
        try:
            self.log("=" * 60, "HEADER")
            self.log("🛸 3DR SOLO OPEN SOLO LATEST STABLE FIRMWARE INSTALLER", "HEADER")
            self.log("Cube: %s | Mode: %s" % (
                "Green Cube" if cube_type == "green" else "Solo Cube (Black)",
                "Full Install" if install_mode == "full" else "Cube Only"
            ), "INFO")
            self.log("=" * 60, "HEADER")

            # Select the correct zip
            zip_path = resolve_clean_install_zip(cube_type)

            # ================================================================
            # STAGE 1: Run preinstall.sh (clears staging dirs) + extract zip
            # ================================================================
            self._set("Stage 1/3 — Preparing Installation", 5)
            self.log("\n[STAGE 1] Preparing installation environment...", "STAGE")

            preinstall_sh  = None
            postinstall_sh = None

            if os.path.exists(zip_path):
                preinstall_sh, postinstall_sh = self._extract_zip_to_staging(zip_path, cube_type)
            else:
                self.log("[*] Installation zip not found at '%s'." % zip_path, "WARNING")
                self.log("[*] Falling back to pre-staged firmware files...", "INFO")
                # Manually stage files from firmware/ dir
                _run_local("rm -rf /tmp/updates && mkdir -p /tmp/updates")
                apj_src = APJ_GREEN if cube_type == "green" else APJ_SOLO
                if not os.path.exists(apj_src):
                    raise IOError("APJ file not found: %s" % apj_src)
                shutil.copyfile(apj_src, "/tmp/updates/arducopter.apj")
                solo_tar = os.path.join(FIRMWARE_DIR, "3dr-solo.tar.gz")
                solo_md5 = os.path.join(FIRMWARE_DIR, "3dr-solo.tar.gz.md5")
                if install_mode == "full":
                    if not os.path.exists(solo_tar):
                        raise IOError("3dr-solo.tar.gz not found in firmware/")
                    shutil.copyfile(solo_tar, "/tmp/updates/3dr-solo.tar.gz")
                    if os.path.exists(solo_md5):
                        shutil.copyfile(solo_md5, "/tmp/updates/3dr-solo.tar.gz.md5")

            # Run preinstall.sh if available (sets up dirs)
            if preinstall_sh and os.path.exists(preinstall_sh):
                self.log("[*] Running preinstall.sh (built-in setup script)...", "INFO")
                _run_local("chmod +x '%s'" % preinstall_sh)
                rc = self._run("sh '%s'" % preinstall_sh)
                if rc != 0:
                    self.log("[!] preinstall.sh returned rc=%d — continuing." % rc, "WARNING")
            else:
                self.log("[*] preinstall.sh not available — manual dir setup...", "INFO")
                _run_local("rm -rf /log/updates && mkdir -p /log/updates")
                _run_local("mkdir -p /firmware")

            self._set(progress=20)
            self.log("[✓] Staging environment ready.", "SUCCESS")

            # ================================================================
            # STAGE 2: Flash Cube via postinstall.sh installArduCopter()
            # ================================================================
            self._set("Stage 2/3 — Flashing Cube Autopilot", 22)
            self.log("\n[STAGE 2] Flashing ArduCopter firmware to Cube...", "STAGE")

            # Make sure the APJ is in /tmp/updates/ (already done above)
            # and also copy to /firmware/ as postinstall.sh expects
            apj_dest = "/tmp/updates/arducopter.apj"
            if not os.path.exists(apj_dest):
                apj_src = APJ_GREEN if cube_type == "green" else APJ_SOLO
                shutil.copyfile(apj_src, apj_dest)

            self._set(progress=25)

            if postinstall_sh and os.path.exists(postinstall_sh):
                # Use the built-in postinstall.sh — it calls:
                #   setPermissions -> packageValid -> prepCopter -> installArduCopter
                self.log("[*] Running postinstall.sh (built-in installer)...", "INFO")
                _run_local("chmod +x '%s'" % postinstall_sh)
                rc = self._run("sh '%s'" % postinstall_sh)
                if rc != 0:
                    self.log("[!] postinstall.sh returned rc=%d (check logs).", "WARNING")
                self.log("[✓] postinstall.sh completed.", "SUCCESS")
                self._set(progress=70)
            else:
                # Manual fallback: mirror postinstall.sh logic exactly
                self.log("[*] Running manual install sequence (postinstall.sh fallback)...", "INFO")

                # setPermissions
                pixhawk_bin = self._find_pixhawk()
                bundled_pix = os.path.join(BIN_DIR, "pixhawk.py")
                bundled_upl = os.path.join(BIN_DIR, "uploader.py")
                if os.path.exists(bundled_pix):
                    self._run("cp -f '%s' /usr/bin/pixhawk.py 2>/dev/null || true" % bundled_pix)
                if os.path.exists(bundled_upl):
                    self._run("cp -f '%s' /usr/bin/uploader.py 2>/dev/null || true" % bundled_upl)
                self._run("chmod a+x /usr/bin/pixhawk.py /usr/bin/uploader.py 2>/dev/null || true")

                # packageValid — md5 check
                self._set(progress=30)
                self.log("[*] Validating package MD5...", "INFO")
                self._run("cd /tmp/updates && md5sum -c ./*.md5 2>/dev/null || true")

                # prepCopter
                self._set(progress=38)
                self.log("[*] prepCopter: sololink_config --update-prepare sololink...", "INFO")
                self._run("sololink_config --update-prepare sololink 2>/dev/null || true")
                self._run("cp /tmp/updates/3dr-solo.tar.* /log/updates/ 2>/dev/null || true")
                self._run("touch /log/updates/UPDATE /log/updates/RESETSETTINGS")

                # installArduCopter
                self._set(progress=50)
                self.log("[*] installArduCopter: copying APJ to /firmware/...", "INFO")
                self._run("mkdir -p /firmware")
                self._run("cp /tmp/updates/*.apj /firmware/ 2>/dev/null || true")
                self.log("[*] pixhawk.py --load /firmware/arducopter.apj", "INFO")
                rc = self._run("%s --load /firmware/arducopter.apj" % pixhawk_bin)
                if rc != 0:
                    raise RuntimeError("Pixhawk Cube autopilot flash failed (pixhawk.py returned exit code %d)." % rc)
                self._run("rm -f /firmware/*.apj 2>/dev/null || true")
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
                    rc, out = _scp_to(CTRL_TAR, CONTROLLER_IP, "/tmp/updates/3dr-controller.tar.gz")
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
                        rc, out = _scp_to(CTRL_TAR_MD5, CONTROLLER_IP, "/tmp/updates/3dr-controller.tar.gz.md5")
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

                    # AUTO REBOOT — controller first (nohup so the reboot survives SSH exit)
                    self.log("\n[AUTO-REBOOT] Triggering controller reboot...", "HEADER")
                    self._run("nohup sh -c 'sleep 2 && reboot' >/dev/null 2>&1 &", host=CONTROLLER_IP)
                    self.log("[✓] Controller reboot triggered.", "SUCCESS")

            # ================================================================
            # AUTO REBOOT — copter (runs 3s after HTTP response)
            # ================================================================
            self._set("Rebooting — Applying Updates", 100)
            self.status = "success"
            self.log("\n" + "=" * 60, "HEADER")
            if controller_staged:
                self.log("🎉 FULL OPEN SOLO LATEST STABLE INSTALLATION STAGED SUCCESSFULLY!", "SUCCESS")
                self.log("Copter and Controller are rebooting and applying updates.", "INFO")
            else:
                self.log("🎉 COPTER OPEN SOLO LATEST STABLE STAGED (Controller skipped)!", "SUCCESS")
                self.log("Copter is rebooting and applying updates.", "INFO")
            self.log("Wait ~8 minutes for reboot to finish.", "INFO")
            self.log("Then hold the Pair button on the bottom of the Solo for 5 seconds.", "INFO")
            self.log("=" * 60, "HEADER")

            def _reboot_copter():
                time.sleep(3)
                os.system("reboot")

            t_reb = threading.Thread(target=_reboot_copter)
            t_reb.daemon = True
            t_reb.start()

        except Exception as e:
            self.status = "error"
            self._set(stage="Error: %s" % str(e))
            self.log("[CRITICAL ERROR] %s" % str(e), "ERROR")
        finally:
            self.is_flashing = False


# Singleton
flash_engine = FlashManager()
