#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Get a Cube that was left in its bootloader running its firmware again.

When a Cube flash fails, Open Solo's pixhawk.py deletes the firmware file and means to restart
the Cube, but that call is missing its parentheses ("rebootPixhawk" instead of "rebootPixhawk()"),
so the Cube stays in its bootloader: the arm lights cycle colors until the Solo is power-cycled.

This script connects the Cube's USB the way pixhawk.py does (GPIO 21 = VBUS select, 19 = enable),
and if the Cube answers as a bootloader it sends the PX4 bootloader BOOT command, which starts the
firmware that is on the Cube. It prints one result word on the last line:

  running             the Cube is running its firmware (nothing to do)
  released            the Cube was in its bootloader and has started its firmware
  still-in-bootloader the Cube stays in its bootloader (no valid firmware on it)
  no-usb              no Cube appeared on USB
  busy                pixhawk.py or uploader.py is running; nothing was touched

Python 2.7 (Solo) and 3; pyserial is needed only when a bootloader is found.
"""
from __future__ import print_function

import glob
import os
import re
import sys
import time

GPIO_ROOT = "/sys/class/gpio"
SELECT_GPIO = 21          # VBUS select
ENABLE_GPIO = 19          # D+/D- connect (0 = connected)
BY_ID = "/dev/serial/by-id"

# PX4 / ArduPilot bootloader protocol
GET_SYNC = b"\x21"
BOOT = b"\x30"
EOC = b"\x20"
INSYNC = b"\x12"
OK = b"\x10"

BL_NAME = re.compile(r"[_-]BL[_-]")


def is_bootloader_port(path):
    """'usb-3D_Robotics_PX4_BL_FMU_v2.x_0-if00', 'usb-ArduPilot_CubeSolo-BL_...' -> True."""
    return bool(BL_NAME.search(os.path.basename(path)))


def _gpio_write(name, value, root=GPIO_ROOT):
    try:
        with open(os.path.join(root, name), "w") as f:
            f.write(str(value))
    except (IOError, OSError):
        pass


def _gpio(pin, value, root=GPIO_ROOT):
    if not os.path.isdir(os.path.join(root, "gpio%d" % pin)):
        _gpio_write("export", pin, root)
    _gpio_write("gpio%d/direction" % pin, "out", root)
    _gpio_write("gpio%d/value" % pin, value, root)


def usb_enable(root=GPIO_ROOT):
    _gpio(SELECT_GPIO, 0, root)
    _gpio(ENABLE_GPIO, 1, root)
    _gpio(SELECT_GPIO, 1, root)     # VBUS on
    _gpio(ENABLE_GPIO, 0, root)     # D+/D- connect


def usb_disable(root=GPIO_ROOT):
    _gpio(ENABLE_GPIO, 1, root)     # D+/D- disconnect
    _gpio(SELECT_GPIO, 0, root)     # VBUS off


def flasher_running():
    """True while pixhawk.py or uploader.py runs (they own the Cube's USB)."""
    me = os.getpid()
    for pid in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not pid.isdigit() or int(pid) == me:
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                cmd = f.read()
        except Exception:
            continue
        if (b"pixhawk.py" in cmd or b"uploader.py" in cmd) and b"cube_bootloader.py" not in cmd:
            return True
    return False


def wait_port(by_id=BY_ID, timeout=6.0):
    end = time.time() + timeout
    while True:
        ports = sorted(glob.glob(os.path.join(by_id, "usb*")))
        if ports or time.time() > end:
            return ports
        time.sleep(0.2)


def boot_from_bootloader(port):
    """Talk the bootloader protocol on an open serial-like port. True if BOOT was accepted."""
    try:
        port.reset_input_buffer()
    except Exception:
        pass
    for _ in range(3):
        port.write(GET_SYNC + EOC)
        port.flush()
        r = port.read(2)
        if r == INSYNC + OK:
            break
    else:
        return False
    port.write(BOOT + EOC)
    port.flush()
    r = port.read(2)
    return r == INSYNC + OK or r == b""      # the Cube may drop USB before it answers


def release(by_id=BY_ID, gpio_root=GPIO_ROOT, open_serial=None, settle=6.0, wait=6.0, log=print):
    if flasher_running():
        return "busy"
    usb_enable(gpio_root)
    try:
        ports = wait_port(by_id, wait)
        if not ports:
            log("no USB device from the Cube")
            return "no-usb"
        bl = [p for p in ports if is_bootloader_port(p)]
        if not bl:
            log("Cube is running its firmware: %s" % os.path.basename(ports[0]))
            return "running"
        log("Cube is in its bootloader: %s" % os.path.basename(bl[0]))
        if open_serial is None:
            import serial
            open_serial = lambda p: serial.Serial(p, 115200, timeout=0.5)
        port = open_serial(bl[0])
        try:
            ok = boot_from_bootloader(port)
        finally:
            try:
                port.close()
            except Exception:
                pass
        log("BOOT command %s" % ("accepted" if ok else "not answered"))
        time.sleep(settle)
        if any(is_bootloader_port(p) for p in glob.glob(os.path.join(by_id, "usb*"))):
            return "still-in-bootloader"
        return "released"
    finally:
        usb_disable(gpio_root)


if __name__ == "__main__":
    try:
        result = release()
    except Exception as ex:
        print("error: %s" % ex)
        result = "error"
    print(result)
    sys.exit(0 if result in ("running", "released") else 1)
