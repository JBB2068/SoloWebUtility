#!/usr/bin/env python
# Runs ON THE DRONE (Python 2), Open Solo / ArduCopter 4.x only. One-time fix so the gimbal updater can talk to the
# gimbal: the Solo's gimbal tools (pymavlink 1.1.56) only speak MAVLink 1, but ArduPilot 4.x sends MAVLink 2 on the
# Cube's USB port (SERIAL0_PROTOCOL=2) unless told otherwise. Also checks the gimbal's own port (SERIAL4: MAVLink 1 at
# 230400) and the mount type. Talks to the Cube through the drone's telemetry forwarder (UDP 127.0.0.1:14560, MAVLink 1),
# so it does not need the USB link. Reboots the Cube if anything changed.
#   usage: python -u fix_cube_serial.py [--check]
import sys, time
from pymavlink import mavutil

WANT = [("SERIAL0_PROTOCOL", 1), ("SERIAL4_PROTOCOL", 1), ("SERIAL4_BAUD", 230)]
check_only = "--check" in sys.argv

def open_cube(wait=30):
    m = mavutil.mavlink_connection("udpout:127.0.0.1:14560", source_system=250)
    t0 = time.time()
    while time.time() - t0 < wait:
        m.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS, mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)
        hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        # the autopilot only: not a GCS (type 6), not the gimbal (component 154)
        if hb is not None and hb.get_srcSystem() not in (250, 255) and hb.type != 6 and hb.get_srcComponent() == 1:
            m.target_system, m.target_component = hb.get_srcSystem(), hb.get_srcComponent()
            return m, hb
    m.close()
    return None, None

def read_param(m, name):
    for attempt in range(4):
        m.mav.param_request_read_send(m.target_system, m.target_component, name, -1)
        end = time.time() + 1.5
        while time.time() < end:
            p = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
            if p is not None and p.param_id.strip("\x00") == name:
                return p.param_value
    return None

def set_param(m, name, value):
    ptype = mavutil.mavlink.MAV_PARAM_TYPE_INT32 if name.endswith("_BAUD") else mavutil.mavlink.MAV_PARAM_TYPE_INT8
    for attempt in range(5):
        m.mav.param_set_send(m.target_system, m.target_component, name, float(value), ptype)
        end = time.time() + 2
        while time.time() < end:
            p = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
            if p is not None and p.param_id.strip("\x00") == name:
                return p.param_value
    return None

m, hb = open_cube(40)
if m is None:
    print("[SERIAL_FIX_FAILED] the Cube is not sending MAVLink to the drone's telemetry forwarder")
    sys.exit(2)
if hb.base_mode & 0x80:
    print("[SERIAL_FIX_FAILED] vehicle is ARMED")
    sys.exit(4)

mount = read_param(m, "MNT1_TYPE")
if mount is None:
    mount = read_param(m, "MNT_TYPE")          # ArduCopter 4.0-4.2 name
print("mount type = %s (2 = Solo gimbal)" % mount)
if mount is not None and int(mount) != 2:
    print("[!] the mount type is not 'Solo gimbal' (2): ArduPilot will not drive the gimbal (the update still routes)")

changed = []
for name, want in WANT:
    have = read_param(m, name)
    print("%s = %s (want %d)" % (name, have, want))
    if have is None:
        print("[SERIAL_FIX_FAILED] could not read %s (is this ArduCopter 4.x?)" % name)
        sys.exit(3)
    if int(have) != want and not check_only:
        v = set_param(m, name, want)
        print("  set %s -> %s" % (name, v))
        if v is None or int(v) != want:
            print("[SERIAL_FIX_FAILED] %s was not accepted" % name)
            sys.exit(5)
        changed.append(name)

if changed:
    time.sleep(2)                                   # let the parameter writes reach storage
    m.mav.command_long_send(m.target_system, m.target_component, 246, 0, 1, 0, 0, 0, 0, 0, 0)   # reboot the autopilot
    m.close()
    print("Cube rebooting so the serial port settings take effect...")
    time.sleep(10)
    m, hb = open_cube(60)
    if m is None:
        print("[SERIAL_FIX_FAILED] the Cube did not come back after the reboot")
        sys.exit(6)
m.close()
print("[SERIAL_FIX_DONE] changed: %s" % (", ".join(changed) or "nothing"))
