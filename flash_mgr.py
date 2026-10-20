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

# ---------------------------------------------------------------------------
# Persistent firmware store and Full Install staging
#
# The Solo's update is applied by the initramfs inside the KERNEL image (uImage),
# not by anything in the squashfs. On boot, if /log/updates/UPDATE exists it
# md5-checks the single *.tar.gz in /log/updates, reformats LATEST (mmcblk0p2) and
# unpacks it there. If /log/updates/RESETSETTINGS also exists it then DELETES AND
# RE-CREATES partitions 3 (RWFS, the settings overlay) and 4 (LOG = /log) and
# mkfs's both -- which erases /log/firmware. The hardware factory reset does the
# same and boots the factory GOLDEN partition (mmcblk0p1), which updates never touch.
#
# So a Full Install here stages ONLY the UPDATE flag (never RESETSETTINGS) and
# resets settings itself by emptying the RWFS overlay (/mnt/rootfs.rw) right
# before the reboot. Result: same clean install as RESETSETTINGS, but /log -- and
# the packages in /log/firmware -- survive every Full Install.
# ---------------------------------------------------------------------------
PERSISTENT_FW_DIR = "/log/firmware"
SOLO_TAR_NAME     = "3dr-solo.tar.gz"
CTRL_TAR_NAME     = "3dr-controller.tar.gz"
LOG_UPDATES_DIR   = "/log/updates"
COPTER_RW_MOUNT   = "/mnt/rootfs.rw"
COPTER_RW_DEVICE  = "/dev/mmcblk0p3"
LOG_DEVICE        = "/dev/mmcblk0p4"
INSTALL_LOG       = "/log/solowebutility_install.log"


def find_package(name):
    """Return the persistent copy of a firmware package (never a staging copy)."""
    for d in [PERSISTENT_FW_DIR, FIRMWARE_DIR, os.path.join(BASE_DIR, "firmware")]:
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.path.getsize(p) > 1000000:
            return p
    return None


def _mount_source(mount_point):
    """Device mounted at mount_point according to /proc/mounts, or None."""
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == mount_point:
                    return parts[0]
    except Exception:
        pass
    return None


def _free_bytes(path):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except Exception:
        return -1


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
      'arducopter_CubeSolo_4.7.2.apj' -> (4, 7, 1)
      'arducopter_CubeGreen_4.7.2.apj' -> (4, 7, 1)
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
        named = "arducopter_CubeGreen_4.7.2.apj" if is_green else "arducopter_CubeSolo_4.7.2.apj"
        p = os.path.join(d, named)
        if os.path.isfile(p) and os.path.getsize(p) > 100000:
            _log("[*] Fallback selected standard firmware: %s" % named, "INFO")
            return p

    return None


find_apj_file = get_latest_apj


# ---------------------------------------------------------------------------
# Firmware choices: latest (default), up to 3 previous versions, custom uploads
# ---------------------------------------------------------------------------
PREVIOUS_DIRNAME = "previous"   # written by arducopter_builder when a newer build is staged
CUSTOM_DIRNAME = "custom"       # .apj files uploaded from the browser
MAX_PREVIOUS_CHOICES = 3
MAX_CUSTOM_FILES = 5
MAX_APJ_BYTES = 4 * 1024 * 1024


def _firmware_roots():
    """Folders holding staged / baked-in .apj files (each may have a previous/ subfolder)."""
    roots = []
    for d in ("/log/firmware", "/usr/bin/SoloWebUtility2/firmware", FIRMWARE_DIR,
              os.path.join(BASE_DIR, "firmware")):
        if d not in roots:
            roots.append(d)
    return roots


def _custom_dir():
    return os.path.join(PERSISTENT_FW_DIR if os.path.isdir(PERSISTENT_FW_DIR) else FIRMWARE_DIR, CUSTOM_DIRNAME)


def _apj_matches_cube(fn, is_green):
    fl = fn.lower()
    if is_green:
        return "green" in fl
    return "green" not in fl and any(t in fl for t in ("solo", "black", "cubesolo"))


def _choice(kind, path, version=None, extra=None):
    d = {"kind": kind, "path": path, "file": os.path.basename(path),
         "size": os.path.getsize(path) if os.path.isfile(path) else 0}
    if version:
        d["version"] = ".".join(str(p) for p in version)
    if extra:
        d.update(extra)
    return d


def list_firmware_choices(cube_type="solo"):
    """What the flash page can offer for this Cube:
       latest   -> exactly what a normal install flashes (get_latest_apj)
       previous -> up to 3 older versions of this Cube, newest first (for reverting)
       custom   -> .apj files uploaded from the browser (any board; uploader.py checks the board)"""
    norm = normalize_cube_type(cube_type)
    is_green = (norm == "CubeGreen")
    latest_path = get_latest_apj(cube_type)
    latest_ver = parse_apj_version(os.path.basename(latest_path)) if latest_path else (0, 0, 0)

    previous, seen_versions = [], set([latest_ver])
    found = []
    roots = _firmware_roots()
    search = [os.path.join(r, PREVIOUS_DIRNAME) for r in roots] + roots
    for d in search:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(".apj") or fn.lower() in ("arducopter.apj", "arducopter_solo.apj", "arducopter_green.apj"):
                continue
            if not _apj_matches_cube(fn, is_green):
                continue
            fp = os.path.join(d, fn)
            if not os.path.isfile(fp) or os.path.getsize(fp) <= 100000:
                continue
            ver = parse_apj_version(fn)
            if ver < latest_ver:
                found.append((ver, fp))
    found.sort(key=lambda x: x[0], reverse=True)
    for ver, fp in found:
        if ver in seen_versions:
            continue
        seen_versions.add(ver)
        previous.append(_choice("previous", fp, ver))
        if len(previous) >= MAX_PREVIOUS_CHOICES:
            break

    custom = []
    cdir = _custom_dir()
    if os.path.isdir(cdir):
        files = [os.path.join(cdir, fn) for fn in os.listdir(cdir) if fn.lower().endswith(".apj")]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for fp in files:
            info = {}
            try:
                info = describe_apj(fp)
            except Exception:
                pass
            custom.append(_choice("custom", fp, None, {"board_id": info.get("board_id"),
                                                       "summary": info.get("summary", "")}))

    return {
        "cube": norm,
        "latest": _choice("latest", latest_path, latest_ver) if latest_path else None,
        "previous": previous,
        "custom": custom,
    }


def describe_apj(path_or_text):
    """Validate an ArduPilot .apj (JSON with a base64 'image') and return its key fields.
    Raises ValueError with a readable reason if it is not a usable firmware file."""
    import json
    if isinstance(path_or_text, (bytes, str)) and len(path_or_text) < 4096 and os.path.isfile(path_or_text):
        with open(path_or_text, "rb") as f:
            text = f.read()
    else:
        text = path_or_text
    if len(text) > MAX_APJ_BYTES:
        raise ValueError("file is larger than %d MB" % (MAX_APJ_BYTES // (1024 * 1024)))
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    try:
        j = json.loads(text)
    except Exception:
        raise ValueError("not an ArduPilot .apj file (it is not JSON)")
    if not isinstance(j, dict) or not j.get("image") or "board_id" not in j:
        raise ValueError("not an ArduPilot .apj file (no firmware image / board_id)")
    try:
        board_id = int(j.get("board_id"))
    except Exception:
        raise ValueError("invalid board_id in .apj")
    return {"board_id": board_id,
            "image_size": j.get("image_size"),
            "summary": (j.get("summary") or j.get("description") or "")[:80],
            "git_identity": j.get("git_identity", "")}


def save_custom_apj(filename, content):
    """Store a browser-uploaded .apj in <firmware>/custom (keeps the newest 5).
    Returns the choice dict. Raises ValueError if the file is not a valid .apj."""
    info = describe_apj(content)
    base = os.path.basename((filename or "").replace("\\", "/")) or "custom.apj"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    if not base.lower().endswith(".apj"):
        base += ".apj"
    if base.lower() in ("arducopter.apj", "arducopter_solo.apj", "arducopter_green.apj"):
        base = "custom_" + base
    cdir = _custom_dir()
    if not os.path.isdir(cdir):
        os.makedirs(cdir)
    dst = os.path.join(cdir, base)
    tmp = dst + ".part"
    data = content.encode("utf-8") if not isinstance(content, bytes) else content
    with open(tmp, "wb") as f:
        f.write(data)
    if os.path.exists(dst):
        os.remove(dst)
    os.rename(tmp, dst)
    files = [os.path.join(cdir, fn) for fn in os.listdir(cdir) if fn.lower().endswith(".apj")]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for old in files[MAX_CUSTOM_FILES:]:
        if old != dst:
            try:
                os.remove(old)
            except Exception:
                pass
    return _choice("custom", dst, None, {"board_id": info["board_id"], "summary": info["summary"]})


def resolve_firmware_choice(cube_type, path):
    """Return `path` only if it is one of the files list_firmware_choices() offers
    for this Cube (so the browser cannot point the flasher at an arbitrary file)."""
    if not path:
        return None
    choices = list_firmware_choices(cube_type)
    allowed = [c["path"] for c in choices["previous"] + choices["custom"]]
    if choices["latest"]:
        allowed.append(choices["latest"]["path"])
    real = os.path.realpath(path)
    for a in allowed:
        if os.path.realpath(a) == real:
            return a
    raise ValueError("%s is not an available firmware file for this Cube" % os.path.basename(path))


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


def _check_image_contents(path, prefix, log=None):
    """Raise IOError unless the package holds uImage, u-boot.imx and a <prefix>*.squashfs image
    (catches a Solo image saved as the controller package, or the reverse)."""
    import tarfile
    try:
        tf = tarfile.open(path, "r:gz")
        try:
            names = [os.path.basename(n) for n in tf.getnames()]
        finally:
            tf.close()
    except Exception as e:
        raise IOError("%s is not a readable firmware package (%s). Nothing was changed." % (path, e))
    ok = ("uImage" in names and "u-boot.imx" in names and
          any(n.startswith(prefix) and n.endswith(".squashfs") for n in names))
    if not ok:
        raise IOError("%s does not contain the expected %s*.squashfs firmware image (found: %s). "
                      "Nothing was changed." % (path, prefix, ", ".join(names)))
    if log:
        log("[✓] %s contains the %s image." % (os.path.basename(path), prefix.rstrip("-")), "SUCCESS")


def _read_md5_hash(md5_path):
    """Return the hex digest from a .md5 file (tolerates CRLF), or '' if unreadable."""
    try:
        with open(md5_path, "rb") as f:
            parts = f.read().decode("ascii", "ignore").strip().split()
        return parts[0].lower() if parts else ""
    except Exception:
        return ""


def _write_md5_file(md5_path, digest, filename):
    """Write '<digest>  <filename>' with a Unix LF ending. The Solo/Artoo initramfs
    updater runs BusyBox md5sum -c, which keeps a trailing CR as part of the filename,
    so a CRLF .md5 (written on Windows) makes the update fail silently."""
    with open(md5_path, "wb") as f:
        f.write(("%s  %s\n" % (digest, filename)).encode("ascii"))


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
    def start_flash(self, cube_type="solo", install_mode="full", firmware_path=None):
        try:
            import cockpit_mgr
            if cockpit_mgr.is_vehicle_armed():
                return False, "SAFETY INTERLOCK: Cannot install firmware while vehicle is ARMED!"
        except Exception:
            pass
        if self.is_flashing:
            return False, "Flashing already in progress."
        try:
            chosen = resolve_firmware_choice(cube_type, firmware_path)
        except ValueError as e:
            return False, "Firmware selection rejected: %s" % e
        self.reset_state()
        self.is_flashing = True
        self.status      = "running"
        self._apj_override = chosen     # None = newest version (normal behaviour)
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

    def _check_package(self, name):
        """Locate a package in /log/firmware, check it against its .md5, return (path, md5)."""
        path = find_package(name)
        if not path:
            raise IOError("Full Install needs %s in %s, but it is missing. Run the Solo Web Utility "
                          "installer again (Windows .exe or Android app) to copy the firmware packages "
                          "to the Solo, then retry. Nothing was changed." % (name, PERSISTENT_FW_DIR))
        digest = _get_file_md5(path)
        md5_path = path + ".md5"
        expected = _read_md5_hash(md5_path) if os.path.exists(md5_path) else ""
        if expected and expected != digest:
            raise IOError("%s is corrupt (MD5 %s, expected %s). Run the Solo Web Utility installer again "
                          "(Windows .exe or Android app) to replace it. Nothing was changed." % (path, digest, expected))
        if not expected:
            try:
                _write_md5_file(md5_path, digest, name)
            except Exception:
                pass
        self.log("[✓] %s  %s  (%s)" % (digest, path, _fmt_size(os.path.getsize(path))), "SUCCESS")
        return path, digest

    def _full_install_preflight(self):
        self.log("[*] Full Install pre-flight checks...", "INFO")
        self._solo_src, self._solo_md5 = self._check_package(SOLO_TAR_NAME)
        self._ctrl_src, self._ctrl_md5 = self._check_package(CTRL_TAR_NAME)
        _check_image_contents(self._solo_src, "3dr-solo-", self.log)
        _check_image_contents(self._ctrl_src, "3dr-controller-", self.log)

        # The settings reset empties the RWFS overlay; refuse to run on an unexpected layout.
        rw_dev = _mount_source(COPTER_RW_MOUNT)
        if rw_dev != COPTER_RW_DEVICE:
            raise IOError("Settings overlay %s is not mounted from %s (found: %s). Aborting before any change." % (
                COPTER_RW_MOUNT, COPTER_RW_DEVICE, rw_dev))
        log_dev = _mount_source("/log")
        if log_dev != LOG_DEVICE:
            raise IOError("/log is not mounted from %s (found: %s). Aborting before any change." % (LOG_DEVICE, log_dev))

        need = os.path.getsize(self._solo_src) + 20 * 1024 * 1024
        if os.path.realpath(self._solo_src).startswith(LOG_UPDATES_DIR + "/"):
            need = 0
        free = _free_bytes("/log")
        if 0 <= free < need:
            raise IOError("/log has %s free, %s needed to stage the copter update." % (_fmt_size(free), _fmt_size(need)))
        self.log("[✓] Pre-flight OK: packages verified, overlay %s on %s, /log on %s (%s free)." % (
            COPTER_RW_MOUNT, COPTER_RW_DEVICE, LOG_DEVICE, _fmt_size(free)), "SUCCESS")

    def _stage_copter_update(self):
        """COPY (never move) the copter package from /log/firmware into a clean /log/updates.
        Does not arm the update -- see _arm_copter_update_and_reboot()."""
        self.log("[*] Staging copter update: COPY %s -> %s/%s" % (self._solo_src, LOG_UPDATES_DIR, SOLO_TAR_NAME), "INFO")
        if not os.path.isdir(LOG_UPDATES_DIR):
            compat.makedirs(LOG_UPDATES_DIR)
        # The initramfs installs the LAST *.tar.gz it finds in /log/updates, so leave exactly one.
        for name in os.listdir(LOG_UPDATES_DIR):
            p = os.path.join(LOG_UPDATES_DIR, name)
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)
            except Exception as e:
                raise IOError("Could not clean %s (%s)" % (p, e))
        dst = os.path.join(LOG_UPDATES_DIR, SOLO_TAR_NAME)
        shutil.copyfile(self._solo_src, dst)
        staged_md5 = _get_file_md5(dst)
        if staged_md5 != self._solo_md5:
            raise IOError("Staged copy %s has MD5 %s, expected %s" % (dst, staged_md5, self._solo_md5))
        _write_md5_file(dst + ".md5", staged_md5, SOLO_TAR_NAME)
        self.log("[✓] Staged %s (%s), LF checksum written: %s  %s" % (
            dst, _fmt_size(os.path.getsize(dst)), staged_md5, SOLO_TAR_NAME), "SUCCESS")
        for name in (SOLO_TAR_NAME, CTRL_TAR_NAME):
            if not find_package(name) or not os.path.realpath(find_package(name)).startswith(PERSISTENT_FW_DIR + "/"):
                self.log("[!] Note: %s is not in %s (it will not be available for the next Full Install)." % (name, PERSISTENT_FW_DIR), "WARNING")

    def _ctrl_cmd(self, cmd, what):
        """Run cmd on the controller; return (rc, output). '$' and '`' are escaped so they are
        evaluated on the controller, not by the Solo's local shell."""
        safe = cmd.replace("\\", "\\\\").replace("$", "\\$").replace("`", "\\`")
        self.log("[CMD @%s] %s" % (CONTROLLER_IP, cmd), "INFO")
        rc, out = _ssh(safe, CONTROLLER_IP, saved_ssid=getattr(self, "saved_ssid", None), log_callback=self.log)
        if rc != 0:
            self.log("[!] Controller command failed (%s), rc=%s" % (what, rc), "WARNING")
            for line in out.splitlines()[-5:]:
                if line.strip():
                    self.log("    %s" % line.strip(), "INFO")
        return rc, out

    def _stage_controller_update(self):
        """Stage 3dr-controller.tar.gz on the controller and arm it -- only after it is verified there.
        Raises IOError (nothing armed on the controller or the copter) on any failure."""
        size = os.path.getsize(self._ctrl_src)
        md5 = self._ctrl_md5 or _get_file_md5(self._ctrl_src)
        dst = "/log/updates/" + CTRL_TAR_NAME
        not_armed = " Nothing was armed on the Controller or the Solo; fix this and run Full Install again."

        self._set("Stage 3/3 — Staging Controller Update", 72)
        self.log("\n[STAGE 3] Staging the Controller update (%s)..." % CONTROLLER_IP, "STAGE")

        # 1. Reachable? Clear any earlier staging (old packages, half-copies, flags).
        rc, out = self._ctrl_cmd(
            "mkdir -p /log/updates && rm -f /log/updates/UPDATE /log/updates/RESETSETTINGS "
            "/log/updates/FACTORYRESET /log/updates/UPDATEFAILED /log/updates/*.tar.gz /log/updates/*.md5 "
            "/log/updates/*.part /tmp/updates/3dr-controller.tar.gz* && echo CLEAN_OK",
            "prepare /log/updates")
        if rc != 0 or "CLEAN_OK" not in out:
            raise IOError("Could not reach or prepare the Controller (%s) over SSH (rc=%s)." % (CONTROLLER_IP, rc) + not_armed)

        # 2. Enough room in the controller's /log?
        rc, out = self._ctrl_cmd("echo FREEKB=$(df -k /log | awk 'NR>1 {print $(NF-2)}' | tail -n 1)", "check free space")
        m = re.search(r"FREEKB=(\d+)", out)
        if rc != 0 or not m:
            raise IOError("Could not read the Controller's free space in /log." + not_armed)
        free = int(m.group(1)) * 1024
        if free < size + 10 * 1024 * 1024:
            raise IOError("The Controller has %s free in /log, but %s is needed for its update." % (
                _fmt_size(free), _fmt_size(size + 10 * 1024 * 1024)) + not_armed)
        self.log("[✓] Controller reachable, %s free in /log." % _fmt_size(free), "SUCCESS")

        # 3. Copy straight into /log/updates (a .part name until verified).
        self._set(progress=76)
        self.log("[*] Copying %s (%s) to the Controller..." % (CTRL_TAR_NAME, _fmt_size(size)), "INFO")
        rc, out = _scp_to(self._ctrl_src, CONTROLLER_IP, dst + ".part",
                          saved_ssid=getattr(self, "saved_ssid", None), log_callback=self.log)
        if rc != 0:
            for line in out.splitlines()[-5:]:
                if line.strip():
                    self.log("    %s" % line.strip(), "INFO")
            self._ctrl_cmd("rm -f %s.part" % dst, "remove partial copy")
            raise IOError("Copying %s to the Controller failed (rc=%s)." % (CTRL_TAR_NAME, rc) + not_armed)

        # 4. Verify size and MD5 on the controller itself, then give it its real name + LF .md5.
        self._set(progress=84)
        rc, out = self._ctrl_cmd(
            "sync; echo SIZE=$(wc -c < %(d)s.part) MD5=$(md5sum %(d)s.part | cut -d ' ' -f 1)" % {"d": dst},
            "verify copy")
        ms = re.search(r"SIZE=\s*(\d+)", out)
        mm = re.search(r"MD5=([0-9a-f]{32})", out)
        got_size = int(ms.group(1)) if ms else -1
        got_md5 = mm.group(1) if mm else "?"
        if got_size != size or got_md5 != md5:
            self._ctrl_cmd("rm -f %s.part" % dst, "remove bad copy")
            raise IOError("The copy on the Controller does not match (size %s / MD5 %s, expected %s / %s)." % (
                got_size, got_md5, size, md5) + not_armed)
        rc, out = self._ctrl_cmd(
            "mv %(d)s.part %(d)s && echo '%(m)s  %(n)s' > %(d)s.md5 && sync && "
            "ls /log/updates/*.tar.gz | wc -l | sed 's/^ */TARS=/'" % {"d": dst, "m": md5, "n": CTRL_TAR_NAME},
            "finalize copy")
        mt = re.search(r"TARS=(\d+)", out)
        if rc != 0 or not mt or mt.group(1) != "1":
            raise IOError("Could not finish staging the Controller update (expected exactly one package in "
                          "/log/updates)." + not_armed)
        self.log("[✓] Controller package verified on the Controller (MD5 %s)." % md5, "SUCCESS")

        # 5. Arm (UPDATE + RESETSETTINGS) and confirm the flags exist.
        self._set(progress=90)
        rc, out = self._ctrl_cmd(
            "touch /log/updates/UPDATE /log/updates/RESETSETTINGS && sync && "
            "[ -f /log/updates/UPDATE ] && [ -f /log/updates/RESETSETTINGS ] && echo ARMED_OK",
            "arm controller update")
        if rc != 0 or "ARMED_OK" not in out:
            self._ctrl_cmd("rm -f /log/updates/UPDATE /log/updates/RESETSETTINGS", "disarm")
            raise IOError("Could not arm the Controller update." + not_armed)
        self.log("[✓] Controller update armed (UPDATE + RESETSETTINGS).", "SUCCESS")

    def _arm_copter_update_and_reboot(self):
        """Arm the copter update with UPDATE only (never RESETSETTINGS, which would reformat /log),
        then -- in a detached shell -- empty the settings overlay and reboot.
        The initramfs then reflashes LATEST from /log/updates; /log/firmware is untouched."""
        for flag in ("RESETSETTINGS", "FACTORYRESET", "UPDATEFAILED"):
            try:
                os.remove(os.path.join(LOG_UPDATES_DIR, flag))
            except OSError:
                pass
        if not os.path.isfile(os.path.join(LOG_UPDATES_DIR, SOLO_TAR_NAME + ".md5")):
            raise IOError("Copter update is not staged in %s" % LOG_UPDATES_DIR)
        open(os.path.join(LOG_UPDATES_DIR, "UPDATE"), "w").close()
        self.log("[✓] Copter update armed: %s/UPDATE (no RESETSETTINGS -- /log/firmware is preserved)." % LOG_UPDATES_DIR, "SUCCESS")

        script = (
            "exec >>%(log)s 2>&1; echo \"=== Full Install reboot $(date) ===\"; "
            "sleep 3; sync; "
            "if grep -q '^%(dev)s %(rw)s ' /proc/mounts; then "
            "  cd %(rw)s && for f in * .[!.]* ..?*; do "
            "    [ -e \"$f\" ] || [ -L \"$f\" ] || continue; "
            "    [ \"$f\" = lost+found ] && continue; "
            "    rm -rf \"$f\"; "
            "  done; cd /; echo 'settings overlay emptied'; "
            "else echo 'overlay not mounted as expected - NOT emptied'; fi; "
            "ls -l %(fw)s %(upd)s; sync; reboot"
        ) % {"log": INSTALL_LOG, "dev": COPTER_RW_DEVICE, "rw": COPTER_RW_MOUNT,
             "fw": PERSISTENT_FW_DIR, "upd": LOG_UPDATES_DIR}
        self.log("[*] Settings reset: emptying %s (on %s) and rebooting in 3 s; log in %s" % (
            COPTER_RW_MOUNT, COPTER_RW_DEVICE, INSTALL_LOG), "INFO")
        devnull = open(os.devnull, "w")
        subprocess.Popen(["nohup", "sh", "-c", script], cwd="/", stdout=devnull, stderr=devnull,
                         stdin=devnull, close_fds=True)

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

            # Scan firmware directory for latest versioned APJ (or use the user's choice)
            apj_override = getattr(self, "_apj_override", None)
            if apj_override:
                apj_selected = apj_override
                self.log("[*] Using the firmware chosen in the browser: %s" % apj_override, "WARNING")
            else:
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

            # Full Install pre-flight: everything is checked BEFORE anything is flashed.
            # Full Install always reinstalls Cube + Copter + Controller; it never
            # degrades to a partial install.
            self._solo_src = None
            self._ctrl_src = None
            self._solo_md5 = None
            if install_mode == "full":
                self._full_install_preflight()
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
            apj_src = apj_override or find_apj_file(cube_type, log_fn=self.log)
            if not apj_src or not os.path.isfile(apj_src):
                raise IOError("Could not locate ArduCopter firmware (.apj) for cube type: %s (%s)" % (cube_type, norm_cube))

            self.log("[*] Selected ArduCopter firmware binary: %s" % os.path.basename(apj_src), "INFO")

            if install_mode == "full":
                self.log("    Copter package MD5 (verified in pre-flight): %s" % self._solo_md5, "INFO")

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

            # The Cube's version is unknown from here until it reports again (header shows "checking")
            try:
                import cockpit_mgr
                cockpit_mgr.invalidate_cube_version()
            except Exception:
                pass
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

            # Stage the copter update (copy from /log/firmware; armed only at the very end)
            self._stage_copter_update()

            # ================================================================
            # STAGE 3: Stage Controller iMX6 update (via SSH to 10.1.1.1)
            # ================================================================
            # Copies the package straight into the controller's /log/updates, verifies size and
            # MD5 ON the controller, and only then arms UPDATE + RESETSETTINGS. Any failure
            # raises before anything is armed on either unit.
            self._stage_controller_update()
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

            # Reboot the controller first (it applies its own UPDATE + RESETSETTINGS;
            # nothing on the controller needs to persist).
            self.log("\n[AUTO-REBOOT] Triggering controller reboot at 10.1.1.1...", "HEADER")
            self._ctrl_cmd("nohup sh -c 'sleep 2 && reboot' >/dev/null 2>&1 &", "reboot controller")
            self.log("[✓] Controller reboot triggered. Its screen should show the update in progress.", "SUCCESS")

            if not controller_staged:
                raise IOError("Controller update was not staged; Full Install aborted before arming the copter.")

            # Last step: arm the copter update, reset copter settings (overlay), reboot.
            self.log("\n[AUTO-REBOOT] Arming copter update and rebooting...", "HEADER")
            self._arm_copter_update_and_reboot()

            # ================================================================
            # Finalize staging and notify web layer
            # ================================================================
            self._set("Rebooting — Applying Updates", 100)
            self.status = "success"
            self.is_flashing = False
            self.log("\n" + "=" * 60, "HEADER")
            self.log("🎉 FULL OPEN SOLO INSTALLATION STAGED: Cube flashed, Copter + Controller rebooting to install.", "SUCCESS")
            self.log("Firmware packages stay in %s for the next Full Install." % PERSISTENT_FW_DIR, "INFO")
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
