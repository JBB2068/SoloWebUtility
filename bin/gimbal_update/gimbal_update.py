#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Update gimbal firmware -- Open Solo 4.0.0 with ArduCopter 4.x (CubeSolo / CubeGreen).

On ArduCopter 4.x the stock boot-time gimbal updater cannot reach the gimbal (the Solo gimbal
driver makes the gimbal's port private, and a freshly powered gimbal uses MAVLink system id 0).
This runs the procedure from HANDOFF_SoloWebUtility.md, verified on a drone on 2026-10-27:

  1. preflight      before anything touches the Cube: put the bundled gimbal_firmware_1.3.6.ax
                    (MD5-checked) in /firmware and remove any other .ax there (nothing is changed when
                    the right file is already in place); then lock, not armed,
                    Open Solo gimbal tools present, a 4.x Cube; remember MNT1_TYPE (persistently, so it
                    is put back even after a power loss)
  2. probe          is the gimbal already in its bootloader? then go straight to 6
  3. id 1           MNT1_TYPE=2 + Cube-only reboot: the driver gives the gimbal system id 1 (in RAM)
  4. open port      MNT1_TYPE=0 + Cube-only reboot: port no longer private, gimbal keeps id 1;
                    check the gimbal now sends from sys 1 / comp 154 and answers GMB_SWVER
  5. version        skip the flash when the gimbal already runs the .ax version (unless forced)
  6. flash          fixed gimbal_setup with a 300 s limit, one retry; write /AXON_VERSION
  7. restore        (always, in finally) MNT1_TYPE back + Cube-only reboot, Cube USB link off

Tested on real gimbals: once by hand (separate scripts over SSH plus the stock boot updater) and
once with this tool from the web page.

Only the Cube reboots; the iMX6 (and the web server) keep running. The web server starts this
file detached (python -u gimbal_update.py --run [--force]) and polls STATUS_FILE, so a browser
or Wi-Fi drop cannot interrupt it.

Python 2.7 (the Solo) and 3 (tests on a PC). No packages beyond the Solo's pymavlink.
"""
from __future__ import print_function

import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# Everything the procedure touches on the Solo (tests point these somewhere else)
PATHS = {
    "status": "/tmp/gimbal_update_status.json",          # polled by the web page
    "lock": "/tmp/gimbal_update.lock",
    "state": "/log/gimbal_update_state.json",            # persistent: MNT1_TYPE to put back
    "log": "/log/gimbal_update.log",
    "firmware_dir": "/firmware",
    "gimbal_dir": "/usr/bin/gimbal",
    "gimbal_setup": "/usr/bin/gimbal_setup",
    "boot_loader": "/usr/bin/loadGimbal.py",
    "version_file": "/AXON_VERSION",
    "gpio": "/sys/class/gpio",
    "usb_glob": "/dev/serial/by-id/usb*",
    "python": "/usr/bin/python",
    # persistent setting: the "Install time-limited updater" button is offered only when this exists
    "boot_updater_enabled": "/log/gimbal_boot_updater_enabled",
}
# Tests (and bench setups) can move these with GIMBAL_UPDATE_PATHS='{"status": "...", ...}'
try:
    PATHS.update(json.loads(os.environ.get("GIMBAL_UPDATE_PATHS", "") or "{}"))
except ValueError:
    pass
FIXED_SETUP = os.path.join(HERE, "gimbal_setup_fixed.py")
FIXED_LOADER = os.path.join(HERE, "loadGimbal_fixed.py")
CUBE_MOUNT = os.path.join(HERE, "cube_mount.py")

# Gimbal firmware bundled with the utility (byte-identical to Open Solo's /firmware copy)
FW_NAME = "gimbal_firmware_1.3.6.ax"
FW_SIZE = 48171
FW_MD5 = "f5fba21a9ebf376566e88fe670546aea"
BUNDLED_FW = os.path.join(HERE, FW_NAME)

SELECT_GPIO, ENABLE_GPIO = "21", "19"
GIMBAL_COMP = 154
CUBE_MOUNT_TIMEOUT = 200        # cube_mount.py: up to 30 s + 90 s waits plus settle
PROBE_SECS = 10
FLASH_TIMEOUT = 300             # a good flash takes about 2 minutes
FLASH_ATTEMPTS = 2
USB_WAIT = 10
RESTORE_WAIT = 120            # restore after a power loss: wait this long for the Cube
STEPS = 7

PROGRESS_RE = re.compile(r"Uploading\s+([\d.]+)\s*kB\s+of\s+([\d.]+)\s*kB\s*-\s*(\d+)%")


class GimbalUpdateError(Exception):
    pass


# ---------------------------------------------------------------------------------------------
# Small file helpers
# ---------------------------------------------------------------------------------------------
def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.rename(tmp, path)


def _lf_bytes(path):
    """File contents with CRLF turned into LF (the PC copies are CRLF; '#!...python\\r' would not run)."""
    with open(path, "rb") as f:
        return f.read().replace(b"\r\n", b"\n")


def _md5(data):
    return hashlib.md5(data).hexdigest()


def pid_alive(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def decode_swver(value):
    """GMB_SWVER float -> 'major.minor.rev' (the gimbal packs 4 signed bytes into the float)."""
    major, minor, rev = struct.unpack('4b', struct.pack('>f', value))[:3]
    return "%d.%d.%d" % (major, minor, rev)


def _file_md5(path):
    with open(path, "rb") as f:
        return _md5(f.read())


def _bundled_fw_bytes():
    """The bundled .ax, or raise GimbalUpdateError when it is missing or not the expected file."""
    if not os.path.isfile(BUNDLED_FW):
        raise GimbalUpdateError("the utility's copy of %s is missing: reinstall the Solo Web Utility" % FW_NAME)
    with open(BUNDLED_FW, "rb") as f:
        data = f.read()
    if len(data) != FW_SIZE or _md5(data) != FW_MD5:
        raise GimbalUpdateError("the utility's copy of %s is damaged (%d bytes, MD5 %s; expected %d bytes, MD5 %s): "
                                "reinstall the Solo Web Utility" % (FW_NAME, len(data), _md5(data), FW_SIZE, FW_MD5))
    return data


def bundled_firmware():
    """(name, version, release) of the bundled .ax when it is intact, else None (for the web page)."""
    try:
        _bundled_fw_bytes()
    except GimbalUpdateError:
        return None
    meta = _read_json(BUNDLED_FW) or {}
    return FW_NAME, str(meta.get("version", "")), str(meta.get("release", ""))


def _sync():
    try:
        subprocess.call(["sync"])
    except Exception:
        pass


def prepare_firmware(log):
    """Step-1 preflight from the brief, before anything touches the Cube: mkdir -p /firmware; if the
    .ax there is missing or has the wrong MD5, copy the bundled one to .tmp, check its MD5, mv, sync,
    and remove every other .ax in /firmware; read the version from the file's JSON. When the right file
    is already in place nothing is changed (other .ax files are left alone too).
    Returns (path, version, release); raises GimbalUpdateError with a clear message on any problem."""
    data = _bundled_fw_bytes()
    fw_dir = PATHS["firmware_dir"]
    if os.path.exists(fw_dir) and not os.path.isdir(fw_dir):
        raise GimbalUpdateError("%s exists but is not a folder" % fw_dir)
    try:
        if not os.path.isdir(fw_dir):
            os.makedirs(fw_dir)
    except OSError as e:
        raise GimbalUpdateError("could not create %s: %s" % (fw_dir, e))
    dst = os.path.join(fw_dir, FW_NAME)
    if os.path.isfile(dst) and _file_md5(dst) == FW_MD5:
        log("%s is in place (MD5 %s): nothing changed" % (dst, FW_MD5))
    else:
        log("Copying the bundled %s to %s" % (FW_NAME, dst) if not os.path.isfile(dst)
            else "%s has the wrong MD5 (%s): replacing it with the bundled copy" % (dst, _file_md5(dst)))
        tmp = dst + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            got = _file_md5(tmp)
            if got != FW_MD5:
                raise GimbalUpdateError("the copy %s has MD5 %s, expected %s (is %s full?)" % (tmp, got, FW_MD5, fw_dir))
            os.rename(tmp, dst)
        except (IOError, OSError) as e:
            raise GimbalUpdateError("could not copy %s to %s: %s" % (FW_NAME, fw_dir, e))
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        _sync()
        if _file_md5(dst) != FW_MD5:
            raise GimbalUpdateError("%s still has the wrong MD5 after copying" % dst)
        log("Copied and checked: MD5 %s" % FW_MD5)
        for other in sorted(glob.glob(os.path.join(fw_dir, "*.ax"))):
            if os.path.abspath(other) != os.path.abspath(dst):
                try:
                    os.remove(other)
                    log("Removed other gimbal firmware %s" % other)
                except OSError as e:
                    raise GimbalUpdateError("could not remove %s: %s" % (other, e))
        _sync()
    meta = _read_json(dst)
    if not isinstance(meta, dict) or not str(meta.get("version", "")):
        raise GimbalUpdateError("could not read the firmware version from %s" % dst)
    return dst, str(meta["version"]), str(meta.get("release", ""))


# ---------------------------------------------------------------------------------------------
# Installing the fixed 3DR tools (keeps a .orig of the original)
# ---------------------------------------------------------------------------------------------
def _install_file(src, dst):
    """Copy src over dst (LF line endings, mode 755), keeping dst.orig once. Returns 'installed'/'current'."""
    new = _lf_bytes(src)
    if os.path.isfile(dst) and _md5(_lf_bytes(dst)) == _md5(new):
        return "current"
    orig = dst + ".orig"
    if os.path.isfile(dst) and not os.path.exists(orig):
        shutil.copy2(dst, orig)
    tmp = dst + ".new"
    with open(tmp, "wb") as f:
        f.write(new)
    os.chmod(tmp, 0o755)
    os.rename(tmp, dst)
    return "installed"


def _restore_file(dst):
    orig = dst + ".orig"
    if not os.path.isfile(orig):
        return "no backup"
    shutil.copy2(orig, dst)
    return "restored"


def _file_state(dst, fixed):
    if not os.path.isfile(dst):
        return "missing"
    if _md5(_lf_bytes(dst)) == _md5(_lf_bytes(fixed)):
        return "fixed"
    return "original"


def boot_updater_install_enabled():
    return os.path.isfile(PATHS["boot_updater_enabled"])


def set_boot_updater_install_enabled(on):
    """The setting that offers "Install time-limited updater" (off by default: it is untested on a drone)."""
    if on:
        with open(PATHS["boot_updater_enabled"], "w") as f:
            f.write("enabled %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    elif os.path.exists(PATHS["boot_updater_enabled"]):
        os.remove(PATHS["boot_updater_enabled"])
    return "on" if on else "off"


def boot_updater_state():
    return {"state": _file_state(PATHS["boot_loader"], FIXED_LOADER),
            "backup": os.path.isfile(PATHS["boot_loader"] + ".orig"),
            "install_enabled": boot_updater_install_enabled(),
            "setup_state": _file_state(os.path.join(PATHS["gimbal_dir"], "setup.py"), FIXED_SETUP)}


def install_boot_updater():
    if not boot_updater_install_enabled():
        raise GimbalUpdateError("the time-limited boot updater is untested on a drone: turn on the setting "
                                "after testing it by hand before installing it")
    if not os.path.isfile(PATHS["boot_loader"]):
        raise GimbalUpdateError("%s not found: this is not Open Solo's gimbal boot updater" % PATHS["boot_loader"])
    return _install_file(FIXED_LOADER, PATHS["boot_loader"])


def restore_boot_updater():
    return _restore_file(PATHS["boot_loader"])


# ---------------------------------------------------------------------------------------------
# Hardware access on the Solo. Tests replace this class.
# ---------------------------------------------------------------------------------------------
class SoloHardware(object):
    def __init__(self, log):
        self.log = log

    # --- Cube over the telemetry forwarder (MAVLink 1, like cube_mount.py) ---
    def cube_info(self):
        """{'armed', 'mnt1_type', 'serial0'} from the autopilot, or None when it does not answer."""
        from pymavlink import mavutil
        m = mavutil.mavlink_connection("udpout:127.0.0.1:14560", source_system=250)
        try:
            hb = None
            t0 = time.time()
            while time.time() - t0 < 30:
                m.mav.heartbeat_send(6, 8, 0, 0, 0)
                h = m.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
                if h is not None and h.get_srcComponent() == 1 and h.type != 6:
                    hb = h
                    break
            if hb is None:
                return None

            def param(name):
                for attempt in range(4):
                    m.mav.param_request_read_send(1, 1, name, -1)
                    end = time.time() + 1.5
                    while time.time() < end:
                        p = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
                        if p is not None and p.param_id.strip("\x00") == name:
                            return p.param_value
                return None
            return {"armed": bool(hb.base_mode & 0x80), "mnt1_type": param("MNT1_TYPE"),
                    "serial0": param("SERIAL0_PROTOCOL")}
        finally:
            m.close()

    def set_mount(self, mount_type, settle):
        """cube_mount.py (verified on the drone): set MNT1_TYPE, reboot only the Cube. True on [CUBE_DONE]."""
        rc, out = run_logged([PATHS["python"], "-u", CUBE_MOUNT, str(int(mount_type)), str(settle)],
                             CUBE_MOUNT_TIMEOUT, self.log)
        return rc == 0 and "[CUBE_DONE]" in out

    # --- Cube USB link to the iMX6 (same GPIOs as loadGimbal.py) ---
    def _gpio(self, n, v):
        base = PATHS["gpio"]
        if not os.path.isdir(os.path.join(base, "gpio" + n)):
            with open(os.path.join(base, "export"), "w") as f:
                f.write(n)
        with open(os.path.join(base, "gpio" + n, "direction"), "w") as f:
            f.write("out")
        with open(os.path.join(base, "gpio" + n, "value"), "w") as f:
            f.write(v)

    def usb_enable(self):
        self._gpio(SELECT_GPIO, "1")
        self._gpio(ENABLE_GPIO, "0")
        t0 = time.time()
        while time.time() - t0 < USB_WAIT:
            if glob.glob(PATHS["usb_glob"]):
                time.sleep(0.5)       # let udev finish the by-id links for every interface
                devs = sorted(glob.glob(PATHS["usb_glob"]))
                return ([d for d in devs if d.endswith("-if00")] or devs)[0]
            time.sleep(0.1)
        return None

    def usb_disable(self):
        try:
            self._gpio(ENABLE_GPIO, "1")
            self._gpio(SELECT_GPIO, "0")
        except Exception as e:
            self.log("[gimbal] could not switch the Cube USB link off: %s" % e)

    def probe(self, secs=PROBE_SECS):
        """What the gimbal sends through the Cube's USB link (as gimbal_probe.py, verified on the drone).
        Returns {'usb', 'sysids', 'bootloader', 'version'}; version only from a GMB_SWVER reply."""
        from pymavlink import mavutil
        info = {"usb": None, "sysids": [], "bootloader": False, "version": None}
        dev = self.usb_enable()
        info["usb"] = dev
        if dev is None:
            self.usb_disable()
            return info
        m = None
        try:
            m = mavutil.mavlink_connection(dev, baud=230400, source_system=255)
            m.mav.srcComponent = GIMBAL_COMP
            sysids = set()
            last_hb = last_ask = 0
            t0 = time.time()
            while time.time() - t0 < secs:
                now = time.time()
                if now - last_hb > 1:
                    m.mav.heartbeat_send(0, 0, 0, 0, 0)
                    last_hb = now
                if now - t0 > 2 and now - last_ask > 2 and info["version"] is None:
                    m.mav.param_request_read_send(1, GIMBAL_COMP, "GMB_SWVER", -1)
                    last_ask = now
                msg = m.recv_match(blocking=True, timeout=0.5)
                if msg is None or msg.get_type() == "BAD_DATA" or msg.get_srcComponent() != GIMBAL_COMP:
                    continue
                sysids.add(msg.get_srcSystem())
                if msg.get_type() == "DATA_TRANSMISSION_HANDSHAKE":
                    info["bootloader"] = True
                elif msg.get_type() == "PARAM_VALUE" and msg.param_id.strip("\x00") == "GMB_SWVER":
                    # check the name: 3DR's fetch_param() takes the first PARAM_VALUE of any name
                    info["version"] = decode_swver(msg.param_value)
            info["sysids"] = sorted(sysids)
        finally:
            try:
                if m is not None:
                    m.close()
            except Exception:
                pass
            self.usb_disable()
        return info

    def install_setup_fix(self):
        return _install_file(FIXED_SETUP, os.path.join(PATHS["gimbal_dir"], "setup.py"))

    def flash(self, fw_path, progress, timeout=FLASH_TIMEOUT):
        """Fixed gimbal_setup with the USB link on. Returns (ok, output)."""
        dev = self.usb_enable()
        if dev is None:
            self.usb_disable()
            self.log("[gimbal] no Cube USB serial device after %d s" % USB_WAIT)
            return False, ""
        self.log("Cube USB device: %s" % dev)
        try:
            rc, out = run_logged([PATHS["gimbal_setup"], "--port=%s" % dev, fw_path], timeout, self.log, progress)
        finally:
            self.usb_disable()
        return rc == 0 and "Upload successful" in out, out


def run_logged(cmd, timeout, log, progress=None):
    """Run cmd, passing each output line ('\\r' or '\\n' ended) to log(); kill it after timeout seconds.
    Returns (exit code, output); the exit code is -9 when it was killed."""
    log("$ " + " ".join(cmd))
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    kwargs = {}
    if hasattr(os, "setsid"):
        kwargs["preexec_fn"] = os.setsid     # own process group: the time limit kills its children too
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, **kwargs)
    except OSError as e:
        log("[gimbal] could not start %s: %s" % (cmd[0], e))
        return 127, ""
    killed = []

    def kill():
        killed.append(True)
        try:
            if hasattr(os, "killpg"):
                os.killpg(p.pid, 9)
            else:
                p.kill()
        except OSError:
            pass
    watchdog = threading.Timer(timeout, kill)
    watchdog.daemon = True
    watchdog.start()
    out, line = [], []
    try:
        while True:
            c = p.stdout.read(1)
            if not c:
                break
            if not isinstance(c, str):
                c = c.decode("latin-1")
            out.append(c)
            if c in "\r\n":
                text = "".join(line).strip()
                line = []
                if text:
                    m = PROGRESS_RE.search(text)
                    if m and progress:
                        progress(int(m.group(3)), text)
                    elif text.startswith("Erasing flash") and progress:
                        progress(0, text)
                    else:
                        log(text)
            else:
                line.append(c)
        if "".join(line).strip():
            log("".join(line).strip())
        rc = p.wait()
    finally:
        watchdog.cancel()
    if killed:
        log("[gimbal] stopped after %d s (time limit)" % timeout)
        rc = -9
    log("(exit %d)" % rc)
    return rc, "".join(out)


# ---------------------------------------------------------------------------------------------
# Status file the web page polls
# ---------------------------------------------------------------------------------------------
class Status(object):
    MAX_LINES = 400

    def __init__(self, force=False, mode="update"):
        self.data = {"state": "running", "mode": mode, "step": 0, "steps": STEPS, "label": "Starting",
                     "percent": 0, "message": "", "force": bool(force), "pid": os.getpid(),
                     "started": time.time(), "updated": time.time(), "finished": None,
                     "from_version": None, "to_version": None, "file_version": None,
                     "result": None, "lines": []}
        self.save()

    def save(self):
        self.data["updated"] = time.time()
        try:
            _write_json(PATHS["status"], self.data)
        except Exception as e:
            print("[gimbal] could not write status: %s" % e)

    def log(self, text):
        stamp = time.strftime("%H:%M:%S")
        print("[%s] %s" % (stamp, text))
        sys.stdout.flush()
        self.data["lines"].append("[%s] %s" % (stamp, text))
        del self.data["lines"][:-self.MAX_LINES]
        self.save()

    def step(self, n, label, percent):
        self.data.update({"step": n, "label": label, "percent": percent})
        self.log("=== Step %d/%d: %s ===" % (n, STEPS, label))

    def finish(self, result, message):
        message = message[:1].upper() + message[1:]
        ok = result in ("updated", "current", "restored")
        self.data.update({"state": "done" if ok else "failed", "label": "Finished" if ok else "Stopped",
                          "result": result, "message": message, "finished": time.time(),
                          "percent": 100 if ok else self.data["percent"]})
        self.log(("[RESULT] %s: %s" % (result, message)))


# ---------------------------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------------------------
def acquire_lock():
    path = PATHS["lock"]
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return True
        except OSError:
            try:
                with open(path) as f:
                    pid = f.read().strip()
            except Exception:
                pid = ""
            if pid_alive(pid):
                return False
            try:
                os.remove(path)        # stale lock from a run that died
            except OSError:
                pass
    return False


def release_lock():
    try:
        with open(PATHS["lock"]) as f:
            if f.read().strip() == str(os.getpid()):
                os.remove(PATHS["lock"])
    except Exception:
        pass


def running_pid():
    """pid of a live run, or None."""
    try:
        with open(PATHS["lock"]) as f:
            pid = f.read().strip()
    except Exception:
        return None
    return int(pid) if pid_alive(pid) else None


# ---------------------------------------------------------------------------------------------
# The procedure
# ---------------------------------------------------------------------------------------------
def _restore_mount(hw, st, mount_type):
    """Put MNT1_TYPE back with a Cube-only reboot; up to 3 tries. Clears the persistent note on success."""
    for attempt in range(1, 4):
        st.log("Restoring MNT1_TYPE=%d (attempt %d)" % (mount_type, attempt))
        if hw.set_mount(mount_type, 5):
            try:
                os.remove(PATHS["state"])
            except OSError:
                pass
            return True
        time.sleep(5)
    return False


def run_update(hw, st, force=False):
    """The 7 steps. Returns the final result ('updated', 'current' or 'failed'); st holds the details."""
    _run_update(hw, st, force)
    return st.data.get("result") or "failed"


def _run_update(hw, st, force):
    restore_to = None          # MNT1_TYPE to put back in finally (None = nothing changed)
    result, message = "failed", "stopped before finishing"
    try:
        # ---- 1. preflight ----------------------------------------------------------------
        st.step(1, "Preflight checks", 2)
        # the firmware file first: nothing touches the Cube until it is in place and checked
        fw_path, fw_version, fw_release = prepare_firmware(st.log)
        st.data["file_version"] = fw_version
        st.log("Firmware file %s is version %s" % (fw_path, fw_version))
        setup_py = os.path.join(PATHS["gimbal_dir"], "setup.py")
        if not (os.path.exists(PATHS["gimbal_setup"]) and os.path.isfile(setup_py)):
            raise GimbalUpdateError("Open Solo's gimbal tools (%s, %s) are missing" % (PATHS["gimbal_setup"], setup_py))

        info = hw.cube_info()
        if info is None:
            raise GimbalUpdateError("the Cube is not answering on the Solo's telemetry link")
        if info.get("armed"):
            raise GimbalUpdateError("the vehicle is ARMED")
        if info.get("mnt1_type") is None:
            raise GimbalUpdateError("the Cube has no MNT1_TYPE parameter: this needs ArduCopter 4.3 or newer "
                                    "(older 4.x names it MNT_TYPE and is not supported by this tool)")
        current = int(info["mnt1_type"])
        saved = _read_json(PATHS["state"]) or {}
        original = int(saved.get("restore_mnt1_type", current))
        if saved:
            st.log("An earlier gimbal update did not finish: MNT1_TYPE will be put back to %d" % original)
        _write_json(PATHS["state"], {"restore_mnt1_type": original, "since": time.time()})
        if current != original:
            restore_to = original
        st.log("MNT1_TYPE = %d (will be %d afterwards)" % (current, original))
        if info.get("serial0") is not None and int(info["serial0"]) != 1:
            st.log("[!] SERIAL0_PROTOCOL = %d. The verified update ran with 1 (MAVLink 1 on USB); "
                   "set it to 1 if the update fails." % int(info["serial0"]))
        st.log("Fixed gimbal_setup: %s" % hw.install_setup_fix())

        # ---- 2. already in the bootloader? ------------------------------------------------
        st.step(2, "Looking for the gimbal", 8)
        probe = hw.probe()
        st.log("Gimbal probe: %s" % json.dumps(probe))
        if probe.get("usb") is None:
            raise GimbalUpdateError("the Cube's USB link to the Solo did not come up")
        in_boot = bool(probe.get("bootloader"))
        version = None
        if in_boot:
            st.log("The gimbal is in its bootloader (no application): going straight to the flash")
        else:
            # ---- 3 + 4. id 1, then open the port ------------------------------------------
            for attempt in (1, 2):
                st.step(3, "Giving the gimbal system ID 1 (Cube reboot)", 15)
                restore_to = original
                if not hw.set_mount(2, 25):
                    raise GimbalUpdateError("could not set MNT1_TYPE=2 and reboot the Cube")
                st.step(4, "Opening the gimbal port (Cube reboot)", 35)
                if not hw.set_mount(0, 5):
                    raise GimbalUpdateError("could not set MNT1_TYPE=0 and reboot the Cube")
                probe = hw.probe()
                st.log("Gimbal probe: %s" % json.dumps(probe))
                if probe.get("bootloader"):
                    in_boot = True
                    break
                if 1 in probe.get("sysids", []) and probe.get("version"):
                    version = probe["version"]
                    break
                if probe.get("sysids") == [0]:
                    st.log("[!] The gimbal is still system ID 0 (it lost power?): starting again at step 3")
                else:
                    st.log("[!] No answer from the gimbal: trying steps 3-4 once more")
            else:
                raise GimbalUpdateError("the gimbal did not answer through the Cube (is it connected and powered?)")

        # ---- 5. version decision ----------------------------------------------------------
        st.step(5, "Checking the gimbal version", 45)
        st.data["from_version"] = "bootloader" if in_boot else version
        if in_boot:
            st.log("No application on the gimbal: flashing %s" % fw_version)
        else:
            st.log("Gimbal runs %s, firmware file is %s" % (version, fw_version))
            if version == fw_version and not force:
                with open(PATHS["version_file"], "w") as f:
                    f.write(fw_version + "\n" + fw_release + "\n")
                result, message = "current", "the gimbal already runs %s; nothing flashed" % fw_version
                return result

        # ---- 6. flash ---------------------------------------------------------------------
        st.step(6, "Flashing gimbal firmware %s" % fw_version, 50)

        def progress(pct, text):
            st.data["percent"] = 50 + int(pct * 0.4)
            st.data["label"] = "Flashing gimbal firmware %s - %s" % (fw_version, text)
            st.save()
            if pct % 10 == 0 and st.data.get("_last_pct") != pct:
                st.data["_last_pct"] = pct
                st.log(text)

        ok = False
        for attempt in range(1, FLASH_ATTEMPTS + 1):
            st.log("Flash attempt %d of %d (time limit %d s)" % (attempt, FLASH_ATTEMPTS, FLASH_TIMEOUT))
            ok, out = hw.flash(fw_path, progress)
            if ok:
                break
            st.log("[!] Flash attempt %d failed" % attempt)
            time.sleep(3)
        if not ok:
            raise GimbalUpdateError("the gimbal firmware upload failed twice. The gimbal stays in its "
                                    "bootloader (flashing yellow); run the update again to recover it")
        with open(PATHS["version_file"], "w") as f:
            f.write(fw_version + "\n" + fw_release + "\n")
        st.data["to_version"] = fw_version
        if restore_to is None:
            restore_to = original      # one Cube-only reboot so the driver takes over the new firmware
        result, message = "updated", "gimbal firmware %s installed" % fw_version
        return result
    except GimbalUpdateError as e:
        result, message = "failed", str(e)
        return result
    except Exception as e:
        result, message = "failed", "unexpected error: %s" % e
        return result
    finally:
        # ---- 7. restore -------------------------------------------------------------------
        st.step(7, "Restoring the gimbal driver (Cube reboot)", 92)
        try:
            hw.usb_disable()
        except Exception:
            pass
        if restore_to is not None:
            if not _restore_mount(hw, st, restore_to):
                result = "failed"
                message = ("%s. MNT1_TYPE could NOT be put back to %d: the gimbal is not driven by the Cube. "
                           "Run the update again, or set MNT1_TYPE=%d in a ground station and reboot."
                           % (message, restore_to, restore_to))
        else:
            try:
                os.remove(PATHS["state"])
            except OSError:
                pass
        if result in ("updated", "current"):
            st.log("Now check that the gimbal levels the camera, follows the tilt paddle, and that the GoPro controls work.")
        st.finish(result, message)


def run_restore(hw, st):
    """Only step 7: used after a power loss or crash left MNT1_TYPE changed."""
    saved = _read_json(PATHS["state"])
    if not saved:
        st.finish("restored", "nothing to restore")
        return "restored"
    target = int(saved.get("restore_mnt1_type", 2))
    st.step(7, "Restoring MNT1_TYPE=%d after an unfinished gimbal update" % target, 50)
    hw.usb_disable()
    t0 = time.time()
    while hw.cube_info() is None and time.time() - t0 < RESTORE_WAIT:   # right after boot the Cube may be slow
        st.log("Waiting for the Cube...")
        time.sleep(5)
    if _restore_mount(hw, st, target):
        st.finish("restored", "MNT1_TYPE is back to %d" % target)
        return "restored"
    st.finish("failed", "MNT1_TYPE could not be put back to %d; set it in a ground station and reboot" % target)
    return "failed"


# ---------------------------------------------------------------------------------------------
# Used by the web server
# ---------------------------------------------------------------------------------------------
def get_status():
    st = _read_json(PATHS["status"]) or {"state": "idle", "lines": []}
    if st.get("state") == "running" and not pid_alive(st.get("pid")) and running_pid() is None:
        st["state"] = "failed"
        st["result"] = "interrupted"
        st["message"] = ("the update stopped unexpectedly (Solo restarted?). "
                         "Run it again: it puts MNT1_TYPE back and recovers the gimbal")
    st["running"] = st.get("state") == "running"
    st["pending_restore"] = os.path.isfile(PATHS["state"])
    fw = bundled_firmware()
    st["firmware_file"] = fw[0] if fw else None
    st["firmware_version"] = fw[1] if fw else None
    st["firmware_md5"] = FW_MD5
    try:
        with open(PATHS["version_file"]) as f:
            st["installed_version"] = f.readline().strip() or None
    except Exception:
        st["installed_version"] = None
    try:
        st["boot_updater"] = boot_updater_state()
    except Exception:
        st["boot_updater"] = None
    st.pop("_last_pct", None)
    return st


def _spawn(args):
    log = open(PATHS["log"], "a")
    devnull = open(os.devnull, "r")
    kwargs = {"stdin": devnull, "stdout": log, "stderr": subprocess.STDOUT, "cwd": "/", "close_fds": True}
    if hasattr(os, "setsid"):
        kwargs["preexec_fn"] = os.setsid      # own session: survives the web server restarting
    python = PATHS["python"] if os.path.exists(PATHS["python"]) else sys.executable
    return subprocess.Popen([python, "-u", os.path.abspath(__file__)] + args, **kwargs)


def start(force=False):
    """Start a detached run. Returns (ok, message)."""
    if running_pid() is not None:
        return False, "A gimbal update is already running."
    try:
        _bundled_fw_bytes()
    except GimbalUpdateError as e:
        return False, str(e)[:1].upper() + str(e)[1:] + "."
    _write_json(PATHS["status"], {"state": "running", "mode": "update", "step": 0, "steps": STEPS,
                                  "label": "Starting", "percent": 0, "message": "", "force": bool(force),
                                  "pid": None, "started": time.time(), "updated": time.time(),
                                  "lines": ["Starting the gimbal update..."]})
    p = _spawn(["--run"] + (["--force"] if force else []))
    st = _read_json(PATHS["status"]) or {}
    if st.get("pid") is None:
        st["pid"] = p.pid
        _write_json(PATHS["status"], st)
    return True, "Gimbal update started."


def recover_on_startup():
    """Called when the web server starts: if a run died with MNT1_TYPE changed, put it back."""
    if os.path.isfile(PATHS["state"]) and running_pid() is None:
        _spawn(["--restore"])
        return True
    return False


def reset_status():
    if running_pid() is not None:
        return False
    try:
        os.remove(PATHS["status"])
    except OSError:
        pass
    return True


def main(argv):
    force = "--force" in argv
    restore_only = "--restore" in argv
    if not acquire_lock():
        print("[gimbal] another gimbal update is running")
        return 1
    try:
        st = Status(force=force, mode="restore" if restore_only else "update")
        hw = SoloHardware(st.log)
        print("")
        st.log("Gimbal firmware update (Solo Web Utility) %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        if restore_only:
            return 0 if run_restore(hw, st) == "restored" else 1
        return 0 if run_update(hw, st, force) in ("updated", "current") else 1
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
