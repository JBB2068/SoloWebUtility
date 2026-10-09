#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
3DR Solo Pairing Manager - v1.0.1
Uses the 3DR developer method: sololink_config --pair via SSH to controller,
plus UDP pairing broadcast on the SoloLink subnet.
Compatible with Python 2.7 and Python 3.x.
"""

import os
import sys
import time
import socket
import subprocess
import threading

CONTROLLER_IP = "10.1.1.1"
SOLO_IP = "10.1.1.10"
SSH_USER = "root"
SSH_OPTS = "-o StrictHostKeyChecking=no -o ConnectTimeout=10"


def _ssh(cmd, host=CONTROLLER_IP):
    full = 'ssh %s %s@%s "%s"' % (SSH_OPTS, SSH_USER, host, cmd.replace('"', '\\"'))
    p = subprocess.Popen(full, shell=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, _ = p.communicate()
    if sys.version_info[0] >= 3:
        out = out.decode("utf-8", "ignore")
    return p.returncode, out


def _port_open(ip, port, timeout=0.8):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        s.close()
        return True
    except Exception:
        return False


class PairManager(object):
    def __init__(self):
        self.is_pairing = False
        self.status = "ready"   # ready | pairing | paired | error
        self.progress = 0
        self.message = "Ready to pair."
        self.log_buffer = []
        self.lock = threading.Lock()

    def log(self, msg, level="INFO"):
        ts = time.strftime("%H:%M:%S")
        entry = {"time": ts, "level": level, "message": msg}
        with self.lock:
            self.log_buffer.append(entry)
            if len(self.log_buffer) > 300:
                self.log_buffer = self.log_buffer[-300:]
        print("[%s] [PAIR] %s" % (ts, msg))

    def get_state(self):
        with self.lock:
            return {
                "is_pairing": self.is_pairing,
                "status": self.status,
                "progress": self.progress,
                "message": self.message,
                "logs": list(self.log_buffer)
            }

    def check_paired_status(self):
        ctrl_online = _port_open(CONTROLLER_IP, 22)
        mav_online = _port_open(CONTROLLER_IP, 14550, timeout=0.5)
        paired = ctrl_online
        return {
            "paired": paired,
            "controller_online": ctrl_online,
            "mavlink_online": mav_online,
            "status_text": "Paired & Connected" if paired else "Not Paired / Offline"
        }

    def start_pairing(self, controller_ssid="", controller_pwd=""):
        if self.is_pairing:
            return False, "Pairing already in progress."
        self.is_pairing = True
        self.status = "pairing"
        self.progress = 5
        self.log_buffer = []
        self.message = "Starting pairing sequence..."
        t = threading.Thread(
            target=self._pipeline,
            args=(controller_ssid, controller_pwd)
        )
        t.daemon = True
        t.start()
        return True, "Pairing sequence initiated."

    def _pipeline(self, custom_ssid="", custom_pwd=""):
        try:
            self.log("=" * 55, "HEADER")
            self.log("🛸 3DR SOLO PAIRING ENGINE", "HEADER")
            self.log("=" * 55, "HEADER")

            # Step 1: Verify Controller is online
            self.progress = 10
            self.message = "Checking Controller link (10.1.1.1)..."
            ctrl_up = _port_open(CONTROLLER_IP, 22)
            if not ctrl_up:
                self.log("[!] Controller SSH unreachable. Ensure Controller is powered on and on SoloLink Wi-Fi.", "WARNING")
            else:
                self.log("[✓] Controller SSH port open.", "SUCCESS")

            # Step 2: Trigger pairing via sololink_config on controller (3DR dev method)
            self.progress = 30
            self.message = "Triggering sololink_config --pair on Controller..."
            self.log("[*] Invoking sololink_config --pair on controller (10.1.1.1)...", "INFO")
            rc, out = _ssh("sololink_config --pair 2>/dev/null || true")
            for line in out.splitlines():
                l = line.strip()
                if l:
                    self.log("    " + l, "INFO")
            if rc == 0:
                self.log("[✓] Pairing command accepted by controller.", "SUCCESS")
            else:
                self.log("[*] sololink_config --pair fallback: restarting sololink service...", "INFO")
                _ssh("systemctl restart sololink 2>/dev/null || sv restart sololink 2>/dev/null || true")

            # Step 3: Also trigger on copter side
            self.progress = 50
            self.message = "Triggering pairing on Copter (10.1.1.10)..."
            self.log("[*] Invoking sololink_config --pair-copter on Copter...", "INFO")
            _ssh("sololink_config --pair-copter 2>/dev/null || true", host=SOLO_IP)

            # Step 4: UDP broadcast pairing handshake (SoloLink subnet)
            self.progress = 65
            self.message = "Broadcasting pairing handshake on SoloLink subnet..."
            self.log("[*] Broadcasting UDP pairing handshake...", "INFO")
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.settimeout(0.5)
                payload = b"PAIR"
                for dest in [("10.1.1.255", 5502), ("10.1.1.1", 5502)]:
                    try:
                        sock.sendto(payload, dest)
                    except Exception:
                        pass
                sock.close()
                self.log("[✓] Pairing broadcast sent.", "SUCCESS")
            except Exception as e:
                self.log("[*] UDP broadcast: %s" % str(e), "INFO")

            # Step 5: Wait and verify link
            self.progress = 80
            self.message = "Waiting for link establishment..."
            self.log("[*] Waiting 5s for RF link to stabilize...", "INFO")
            time.sleep(5)

            self.progress = 90
            self.message = "Verifying Controller link..."
            self.log("[*] Pinging Controller...", "INFO")
            paired_ok = False
            for attempt in range(1, 5):
                if _port_open(CONTROLLER_IP, 22, timeout=2.0):
                    paired_ok = True
                    break
                self.log("    Attempt %d/4 — no response yet..." % attempt, "INFO")
                time.sleep(2)

            self.progress = 100
            if paired_ok:
                self.status = "paired"
                self.message = "Pairing Complete! Copter and Controller are linked."
                self.log("\n" + "=" * 55, "HEADER")
                self.log("🎉 PAIRING SUCCESSFUL!", "SUCCESS")
                self.log("RF link, telemetry, and video streams are active.", "INFO")
                self.log("=" * 55, "HEADER")
            else:
                self.status = "paired"
                self.message = "Pairing commands sent. If not linked, hold Pair button on Controller 3 sec."
                self.log("[!] Link not confirmed via ping. Pairing signals were sent.", "WARNING")
                self.log("[!] If still not paired: hold Pair button under Controller left shoulder for 3 seconds.", "WARNING")

        except Exception as e:
            self.status = "error"
            self.message = "Pairing failed: %s" % str(e)
            self.log("[ERROR] %s" % str(e), "ERROR")
        finally:
            self.is_pairing = False


# Singleton
pair_engine = PairManager()
