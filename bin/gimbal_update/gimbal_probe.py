#!/usr/bin/env python
# Runs ON THE DRONE (Python 2). Read-only look at the gimbal through the Cube's USB link, the same path the gimbal
# updater uses: connects the iMX6 to the Cube's USB (same GPIOs as loadGimbal.py), announces itself the way
# gimbal_setup does (system 255, component 154), then prints every message for a while with its source ids, and
# counts what the Solo's MAVLink-1-only pymavlink could not parse. Nothing is sent to the gimbal except heartbeats
# and one parameter read.
#   usage: python -u gimbal_probe.py [seconds]
from __future__ import print_function
import glob, os, sys, time
sys.path.append("/usr/bin/gimbal")
from pymavlink import mavutil

SELECT_GPIO, ENABLE_GPIO = "21", "19"
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 15

def gpio(n, v):
    if not os.path.isdir("/sys/class/gpio/gpio" + n):
        open("/sys/class/gpio/export", "w").write(n)
    open("/sys/class/gpio/gpio%s/direction" % n, "w").write("out")
    open("/sys/class/gpio/gpio%s/value" % n, "w").write(v)

gpio(SELECT_GPIO, "1"); gpio(ENABLE_GPIO, "0")
try:
    dev, t0 = None, time.time()
    while time.time() - t0 < 10:
        devs = sorted(glob.glob("/dev/serial/by-id/usb*"))
        if devs:
            time.sleep(0.5)
            devs = sorted(glob.glob("/dev/serial/by-id/usb*"))
            dev = ([d for d in devs if d.endswith("-if00")] or devs)[0]
            break
        time.sleep(0.1)
    print("USB devices: %s" % (devs or "none"))
    if dev is None:
        print("[PROBE] no Cube USB serial device")
        sys.exit(2)
    print("using %s" % dev)
    m = mavutil.mavlink_connection(dev, baud=230400, source_system=255)
    m.mav.srcComponent = mavutil.mavlink.MAV_COMP_ID_GIMBAL
    seen, bad, last_hb, asked = {}, 0, 0, False
    end = time.time() + secs
    while time.time() < end:
        if time.time() - last_hb > 1:
            m.mav.heartbeat_send(0, 0, 0, 0, 0)
            last_hb = time.time()
        if not asked and time.time() > end - secs + 2:
            # what gimbal_setup's version query does (works only when the gimbal application is running)
            m.mav.param_request_read_send(1, mavutil.mavlink.MAV_COMP_ID_GIMBAL, "GMB_SWVER", -1)
            asked = True
        msg = m.recv_match(blocking=True, timeout=0.5)
        if msg is None:
            continue
        if msg.get_type() == "BAD_DATA":
            bad += 1
            continue
        key = (msg.get_srcSystem(), msg.get_srcComponent(), msg.get_type())
        if key not in seen:
            print("  first %-32s from sys %3d comp %3d: %s" % (key[2], key[0], key[1], str(msg)[:110]))
        seen[key] = seen.get(key, 0) + 1
    print("summary over %.0f s (sys, comp, type: count):" % secs)
    for k in sorted(seen):
        print("  %3d %3d %-32s %d" % (k[0], k[1], k[2], seen[k]))
    print("unparseable bytes/fragments: %d  (many = the Cube is sending MAVLink 2 on USB, SERIAL0_PROTOCOL=2)" % bad)
    gimbal = [k for k in seen if k[1] == 154]
    if not gimbal:
        print("[PROBE] nothing from the gimbal (component 154) came through the Cube")
    elif any(k[2] == "DATA_TRANSMISSION_HANDSHAKE" for k in gimbal):
        print("[PROBE] gimbal is in its BOOTLOADER (sending DATA_TRANSMISSION_HANDSHAKE)")
    else:
        print("[PROBE] gimbal application is running")
finally:
    gpio(ENABLE_GPIO, "1"); gpio(SELECT_GPIO, "0")
