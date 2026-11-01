#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Update the factory reset (recovery, GOLDEN) partition of the Solo and of the Solo Controller.

A hardware factory reset boots GOLDEN (mmcblk0p1), erases the settings and /log, and Open Solo's
golden_to_system.sh then copies GOLDEN onto the system partition. So GOLDEN decides what a factory
reset brings back. Open Solo 4's own "Update Factory Reset Partition" packages (Solex, SidePilot)
and makeGolden.sh reformat GOLDEN and copy the running boot files onto it without checks. This tool
does the same job with golden_write.sh, which never reformats GOLDEN, keeps a backup in /log,
stages and MD5-checks every file, writes each one as <name>.new before renaming it into place,
marks an unfinished run and re-checks everything at the end. No factory reset is triggered.

  Solo:        golden_write.sh runs on the Solo itself.
  Controller:  the script is copied to the Controller over SSH and run there in the background;
               its output (/log/golden_update.out on the Controller) is polled.

The web server starts this file detached (python -u golden_update.py --run <action> --target
<solo|controller>) and polls STATUS_FILE, so a browser or Wi-Fi drop cannot interrupt it.
Python 2.7 (the Solo) and 3 (tests on a PC). Not yet run on a drone.
"""
from __future__ import print_function

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "golden_write.sh")

PATHS = {
    "status": "/tmp/golden_update_status.json",      # polled by the web page
    "lock": "/tmp/golden_update.lock",
    "log": "/log/golden_update.log",
    "info": "/log/golden_info.json",                 # last known state of both GOLDEN partitions
    "sh": "/bin/sh",
    "python": "/usr/bin/python",
}
try:
    PATHS.update(json.loads(os.environ.get("GOLDEN_UPDATE_PATHS", "") or "{}"))
except ValueError:
    pass

CONTROLLER_IP = "10.1.1.1"
CONTROLLER_PASSWORD = "TjSDBkAu"      # Open Solo's default root password (same as flash_mgr.py)
SSH_OPTS = ("-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o GlobalKnownHostsFile=/dev/null "
            "-o ConnectTimeout=10")
REMOTE_SCRIPT = "/tmp/swu_golden_write.sh"
REMOTE_OUT = "/log/golden_update.out"
REMOTE_RC = "/log/golden_update.rc"

TARGETS = ("solo", "controller", "both")
DEVICE_NAMES = {"solo": "Solo", "controller": "Controller"}
ACTIONS = ("status", "write", "restore")
WRITE_TIMEOUT = 1200            # write/restore: backup + staging + writing ~85 MB, with slow eMMC
STATUS_TIMEOUT = 240
POLL_SECS = 3
TESTED_ON_DRONE = False


class GoldenUpdateError(Exception):
    pass


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


def _text(b):
    if isinstance(b, bytes):
        return b.decode("utf-8", "replace")
    return b


def pid_alive(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------------------------
# Running golden_write.sh: locally (Solo) or on the Controller over SSH
# ---------------------------------------------------------------------------------------------
def run_local(action, on_line, timeout):
    """Run golden_write.sh on this device, passing each output line to on_line. Returns the output."""
    env = dict(os.environ)
    # run an LF copy: a Windows checkout may have given the script CRLF line endings
    with open(SCRIPT, "rb") as f:
        script = f.read().replace(b"\r\n", b"\n")
    local = os.path.join(PATHS.get("tmp", "/tmp"), "swu_golden_write.sh")
    with open(local, "wb") as f:
        f.write(script)
    p = subprocess.Popen([PATHS["sh"], local, action], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env=env, cwd="/")
    killed = []

    def kill():
        killed.append(True)
        try:
            p.kill()
        except OSError:
            pass
    t = threading.Timer(timeout, kill)
    t.daemon = True
    t.start()
    out = []
    try:
        for raw in iter(p.stdout.readline, b""):
            line = _text(raw).rstrip("\r\n")
            out.append(line)
            on_line(line)
        p.wait()
    finally:
        t.cancel()
    if killed:
        out.append("[GOLDEN_FAILED] stopped after %d s (time limit)" % timeout)
        on_line(out[-1])
    return "\n".join(out)


def ssh(cmd, timeout=60):
    """Run cmd on the Controller. Returns (exit code, output). Answers the password prompt if one comes."""
    full = "ssh %s root@%s %s" % (SSH_OPTS, CONTROLLER_IP, "'" + cmd.replace("'", "'\\''") + "'")
    try:
        import pty
        import select
    except ImportError:
        pty = None
    if pty is None:
        p = subprocess.Popen(full, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = p.communicate()
        return p.returncode, _text(out)
    master, slave = pty.openpty()
    proc = subprocess.Popen(full, shell=True, stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    os.close(slave)
    output, sent, start = [], False, time.time()
    try:
        while True:
            r, _, _ = select.select([master], [], [], 0.5)
            if r:
                try:
                    data = os.read(master, 4096)
                except OSError:
                    data = b""
                if not data:
                    if proc.poll() is not None:
                        break
                else:
                    output.append(data)
                    if not sent and b"password:" in data.lower():
                        os.write(master, (CONTROLLER_PASSWORD + "\n").encode("ascii"))
                        sent = True
            elif proc.poll() is not None:
                break
            if time.time() - start > timeout:
                proc.kill()
                break
        proc.wait()
    finally:
        os.close(master)
    text = _text(b"".join(output)).replace("\r", "")
    return proc.returncode, text


def run_controller(action, on_line, timeout):
    """Copy golden_write.sh to the Controller, start it in the background there, poll its output."""
    with open(SCRIPT, "rb") as f:
        script = f.read().replace(b"\r\n", b"\n")
    b64 = _text(base64.b64encode(script))
    # the Controller's BusyBox has no base64 applet; its python2.7 decodes the script
    push = ("python -c \"import base64,sys;open(sys.argv[1],'wb').write(base64.b64decode(sys.argv[2]))\" "
            "%s %s && echo PUSH_OK" % (REMOTE_SCRIPT, b64))
    rc, out = ssh(push, 60)
    if "PUSH_OK" not in out:
        line = "[GOLDEN_FAILED] could not reach the Controller over SSH (%s)" % (out.strip().splitlines()[-1:] or ["no answer"])[0]
        on_line(line)
        return line
    start = ("rm -f %(out)s %(rc)s; (sh %(s)s %(a)s > %(out)s 2>&1; echo $? > %(rc)s) </dev/null >/dev/null 2>&1 & "
             "echo STARTED" % {"out": REMOTE_OUT, "rc": REMOTE_RC, "s": REMOTE_SCRIPT, "a": action})
    rc, out = ssh(start, 30)
    if "STARTED" not in out:
        line = "[GOLDEN_FAILED] could not start the update on the Controller"
        on_line(line)
        return line
    seen, t0, misses = 0, time.time(), 0
    lines = []
    while True:
        time.sleep(POLL_SECS)
        rc, out = ssh("cat %s 2>/dev/null; echo __RC__; cat %s 2>/dev/null" % (REMOTE_OUT, REMOTE_RC), 30)
        if "__RC__" not in out:
            misses += 1
            if misses >= 20:            # about a minute without the Controller
                line = ("[GOLDEN_FAILED] lost the Controller: its update may still be running. Check again in a "
                        "few minutes; an unfinished run is shown on the card")
                on_line(line)
                lines.append(line)
                break
            continue
        misses = 0
        body, rcpart = out.split("__RC__", 1)
        got = [l for l in body.split("\n") if l.startswith("[GOLDEN")]
        for l in got[seen:]:
            lines.append(l)
            on_line(l)
        seen = len(got)
        if rcpart.strip():
            break
        if time.time() - t0 > timeout:
            line = "[GOLDEN_FAILED] no result from the Controller after %d s" % timeout
            on_line(line)
            lines.append(line)
            break
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Status file the web page polls
# ---------------------------------------------------------------------------------------------
class Status(object):
    MAX_LINES = 400

    def __init__(self, target, action):
        self.data = {"state": "running", "target": target, "action": action, "step": 0, "steps": 5,
                     "label": "Starting", "percent": 0, "message": "", "pid": os.getpid(),
                     "started": time.time(), "updated": time.time(), "finished": None, "result": None,
                     "lines": []}
        self.save()

    def save(self):
        self.data["updated"] = time.time()
        try:
            _write_json(PATHS["status"], self.data)
        except Exception as e:
            print("[golden] could not write status: %s" % e)

    def log(self, text):
        stamp = time.strftime("%H:%M:%S")
        print("[%s] %s" % (stamp, text))
        sys.stdout.flush()
        self.data["lines"].append("[%s] %s" % (stamp, text))
        del self.data["lines"][:-self.MAX_LINES]
        self.save()

    def _set_pct(self, local):
        """Progress of the current device (0-100), spread over all devices of the run."""
        self.data["device_pct"] = local
        i, n = self.data.get("device_index", 1), self.data.get("device_count", 1)
        self.data["percent"] = int(((i - 1) * 100 + local) / float(n))

    def on_line(self, line):
        m = re.match(r"\[GOLDEN\] step (\d+) (.*)", line)
        if m:
            n = int(m.group(1))
            self.data.update({"step": n, "label": m.group(2)})
            self._set_pct({1: 5, 2: 15, 3: 35, 4: 55, 5: 95}.get(n, 0))
        if line.startswith("[GOLDEN] writing ") and self.data.get("device_pct", 0) < 90:
            self._set_pct(min(90, self.data.get("device_pct", 55) + 8))
        if line.startswith("[GOLDEN"):
            self.log(line.replace("[GOLDEN] ", "", 1))

    def finish(self, result, message):
        ok = result == "done"
        self.data.update({"state": "done" if ok else "failed", "label": "Finished" if ok else "Stopped",
                          "result": result, "message": message, "finished": time.time(),
                          "percent": 100 if ok else self.data["percent"]})
        self.log("[RESULT] %s: %s" % (result, message))


# ---------------------------------------------------------------------------------------------
# Parsing "status" output into what the card shows
# ---------------------------------------------------------------------------------------------
def _is_boot_file(name):
    return name.endswith(".squashfs") or name.endswith(".dtb") or name in ("uImage", "u-boot.imx")


def parse_status(output):
    info = {"image": None, "marker": None, "backup_files": 0, "boot": {}, "golden": {}, "space": {},
            "boot_os": None, "boot_swu": None, "golden_os": None, "golden_swu": None, "ok": False}
    for line in output.split("\n"):
        if line.startswith("[GOLDEN_DONE] status"):
            info["ok"] = True
        if not line.startswith("[GOLDEN] "):
            continue
        rest = line[len("[GOLDEN] "):]
        key, _, val = rest.partition(" ")
        parts = val.split()
        if key == "image":
            info["image"] = val.strip()
        elif key == "marker":
            info["marker"] = val.strip()
        elif key == "backup":
            try:
                info["backup_files"] = int(parts[0])
            except (IndexError, ValueError):
                pass
        elif key in ("boot", "golden") and len(parts) == 3:
            info[key][parts[0]] = {"size": int(parts[1]), "md5": parts[2]}
        elif key in ("boot_versions", "golden_versions"):
            kv = dict(p.split("=", 1) for p in parts if "=" in p)
            pre = key.split("_")[0]
            info[pre + "_os"] = None if kv.get("os", "-") == "-" else kv["os"]
            info[pre + "_swu"] = None if kv.get("swu", "-") == "-" else kv["swu"]
        elif key == "space":
            info["space"] = dict((k, int(v)) for k, v in (p.split("=", 1) for p in parts if "=" in p))
    boot = dict((n, v["md5"]) for n, v in info["boot"].items() if _is_boot_file(n))
    gold = dict((n, v["md5"]) for n, v in info["golden"].items() if _is_boot_file(n))
    info["same_as_running"] = bool(boot) and boot == gold
    info["summary"] = summarize(info)
    return info


def describe(os_ver, swu_ver):
    if not os_ver:
        return "unknown"
    name = ("Open Solo %s" % os_ver) if os_ver.split(".")[0] in ("3", "4", "5") else ("3DR %s" % os_ver)
    if swu_ver:
        name += " with Solo Web Utility v%s" % swu_ver
    return name


def summarize(info):
    if info.get("marker"):
        return "Unfinished update: run it again, or restore the backup"
    if not info.get("golden"):
        return "Empty or unreadable"
    if info.get("same_as_running"):
        if not info.get("boot_os"):
            return "Same as the running system"
        return "Same as the running system (%s)" % describe(info.get("boot_os"), info.get("boot_swu"))
    return describe(info.get("golden_os"), info.get("golden_swu"))


def save_info(target, info):
    allinfo = _read_json(PATHS["info"]) or {}
    info = dict(info)
    info["checked"] = time.time()
    allinfo[target] = info
    try:
        _write_json(PATHS["info"], allinfo)
    except Exception as e:
        print("[golden] could not save %s: %s" % (PATHS["info"], e))


# ---------------------------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------------------------
def _run_one(st, target, action, runner):
    """One action on one device, logging into st; afterwards always reads GOLDEN back. Returns (result, message)."""
    runner = runner or (run_controller if target == "controller" else run_local)
    who = DEVICE_NAMES[target]
    st.data["device"] = target
    st.log("%s: %s the factory reset partition (%s)" % (
        who, {"status": "checking", "write": "updating", "restore": "restoring"}[action],
        time.strftime("%Y-%m-%d %H:%M:%S")))
    result, message = "failed", "stopped before finishing"
    try:
        timeout = STATUS_TIMEOUT if action == "status" else WRITE_TIMEOUT
        out = runner(action, st.on_line, timeout)
        failed = re.search(r"\[GOLDEN_FAILED\] ([^\n]*)", out)
        if "[GOLDEN_DONE] %s" % action in out:
            result = "done"
            message = {"status": "checked", "write": "the factory reset partition now holds the running system",
                       "restore": "the factory reset partition was put back from the backup"}[action]
        else:
            message = failed.group(1) if failed else "no result"
        if action == "status":
            info = parse_status(out)
        else:
            st.data.update({"step": 5, "label": "Reading the factory reset partition back"})
            st.save()
            info = parse_status(runner("status", lambda l: None, STATUS_TIMEOUT))
        if info.get("ok"):
            save_info(target, info)
            st.log("%s factory reset partition: %s" % (who, info["summary"]))
    except Exception as e:
        result, message = "failed", "unexpected error: %s" % e
    st.log("%s: %s" % (who, message))
    return result, message


def run_job(target, action, runner=None):
    """Run one action on the Solo, the Controller or both (Solo first, then the Controller, each on its own:
    a failure on one does not stop the other). Returns 'done' only when every device succeeded."""
    st = Status(target, action)
    devices = ["solo", "controller"] if target == "both" else [target]
    results = []
    for i, dev in enumerate(devices):
        st.data.update({"device_index": i + 1, "device_count": len(devices), "step": 0, "percent": 0})
        results.append((dev,) + _run_one(st, dev, action, runner))
    ok = all(r == "done" for _, r, _ in results)
    message = "; ".join("%s: %s" % (DEVICE_NAMES[d], m) for d, _, m in results)
    st.finish("done" if ok else "failed", message)
    return "done" if ok else "failed"


# ---------------------------------------------------------------------------------------------
# Lock and web server helpers
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
                os.remove(path)
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
    try:
        with open(PATHS["lock"]) as f:
            pid = f.read().strip()
    except Exception:
        return None
    return int(pid) if pid_alive(pid) else None


def get_status():
    st = _read_json(PATHS["status"]) or {"state": "idle", "lines": []}
    if st.get("state") == "running" and not pid_alive(st.get("pid")) and running_pid() is None:
        st["state"] = "failed"
        st["result"] = "interrupted"
        st["message"] = ("the run stopped unexpectedly (Solo restarted?). Check the factory reset partitions: "
                         "an unfinished update can be run again or restored from the backup")
    st["running"] = st.get("state") == "running"
    st["partitions"] = _read_json(PATHS["info"]) or {}
    st["tested_on_drone"] = TESTED_ON_DRONE
    return st


def _spawn(args):
    log = open(PATHS["log"], "a")
    devnull = open(os.devnull, "r")
    kwargs = {"stdin": devnull, "stdout": log, "stderr": subprocess.STDOUT, "cwd": "/", "close_fds": True}
    if hasattr(os, "setsid"):
        kwargs["preexec_fn"] = os.setsid      # own session: survives the web server restarting
    python = PATHS["python"] if os.path.exists(PATHS["python"]) else sys.executable
    return subprocess.Popen([python, "-u", os.path.abspath(__file__)] + args, **kwargs)


def start(target, action):
    """Start a detached run. Returns (ok, message)."""
    if target not in TARGETS or action not in ACTIONS:
        return False, "Unknown target or action."
    if running_pid() is not None:
        return False, "A factory reset partition job is already running."
    if action == "restore":
        for dev in (["solo", "controller"] if target == "both" else [target]):
            info = (_read_json(PATHS["info"]) or {}).get(dev) or {}
            if info and not info.get("backup_files"):
                return False, "There is no backup to restore on the %s." % DEVICE_NAMES[dev]
    _write_json(PATHS["status"], {"state": "running", "target": target, "action": action, "step": 0, "steps": 5,
                                  "label": "Starting", "percent": 0, "message": "", "pid": None,
                                  "started": time.time(), "updated": time.time(), "lines": ["Starting..."]})
    p = _spawn(["--run", action, "--target", target])
    st = _read_json(PATHS["status"]) or {}
    if st.get("pid") is None:
        st["pid"] = p.pid
        _write_json(PATHS["status"], st)
    return True, "Started."


def reset_status():
    if running_pid() is not None:
        return False
    try:
        os.remove(PATHS["status"])
    except OSError:
        pass
    return True


def main(argv):
    if "--run" not in argv or "--target" not in argv:
        print(__doc__)
        return 2
    action = argv[argv.index("--run") + 1]
    target = argv[argv.index("--target") + 1]
    if target not in TARGETS or action not in ACTIONS:
        print("unknown target/action")
        return 2
    if not acquire_lock():
        print("[golden] another factory reset partition job is running")
        return 1
    try:
        print("")
        return 0 if run_job(target, action) == "done" else 1
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
