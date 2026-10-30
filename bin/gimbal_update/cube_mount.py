#!/usr/bin/env python
# Runs ON THE DRONE (Python 2), Open Solo / ArduCopter 4.x. Sets the Cube's MNT1_TYPE and reboots ONLY the Cube
# (the gimbal and the iMX6 keep running), over the drone's telemetry forwarder (UDP 127.0.0.1:14560, MAVLink 1).
# Used for the gimbal update (see HANDOFF_SoloWebUtility.md):
#   python -u cube_mount.py 2 25   # driver on: the Cube's Solo gimbal driver gives the gimbal system id 1
#   python -u cube_mount.py 0 5    # driver off: the gimbal port is no longer private, the gimbal keeps id 1
#   python -u cube_mount.py 2 5    # restore afterwards
# An optional third argument also sets SERIAL0_PROTOCOL (the Cube's USB port) before the same reboot. The Solo's
# pymavlink only reads MAVLink 1, so the update runs with 1 and the port goes back to 2 when it is finished:
#   python -u cube_mount.py 2 5 1  # before the update: USB in MAVLink 1
#   python -u cube_mount.py 2 5 2  # restore afterwards: driver on, USB back to MAVLink 2
# Prints [CUBE_DONE] on success, [CUBE_FAILED] <reason> otherwise. Same steps as the script used on 2026-10-27; only
# the final wait no longer collects the Cube's status texts.
import sys, time
from pymavlink import mavutil
want, settle = int(sys.argv[1]), float(sys.argv[2])
serial0 = int(sys.argv[3]) if len(sys.argv) > 3 else None

def open_cube(wait):
    m = mavutil.mavlink_connection("udpout:127.0.0.1:14560", source_system=250)
    t0 = time.time()
    while time.time() - t0 < wait:
        m.mav.heartbeat_send(6, 8, 0, 0, 0)
        hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=1)
        # the autopilot only: component 1, not a GCS (type 6); the gimbal is component 154
        if hb is not None and hb.get_srcComponent() == 1 and hb.type != 6:
            return m, hb
    m.close()
    return None, None

def param(m, name, value=None):
    for attempt in range(5):
        if value is None:
            m.mav.param_request_read_send(1, 1, name, -1)
        else:
            m.mav.param_set_send(1, 1, name, float(value), mavutil.mavlink.MAV_PARAM_TYPE_INT8)
        end = time.time() + 2
        while time.time() < end:
            p = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
            if p is not None and p.param_id.strip("\x00") == name:
                return p.param_value
    return None

m, hb = open_cube(30)
if m is None:
    print("[CUBE_FAILED] no autopilot heartbeat"); sys.exit(2)
if hb.base_mode & 0x80:
    print("[CUBE_FAILED] vehicle is ARMED"); sys.exit(3)
v = param(m, "MNT1_TYPE", want)
print("MNT1_TYPE set to %s" % v)
if v is None or int(v) != want:
    print("[CUBE_FAILED] MNT1_TYPE not accepted"); sys.exit(4)
serial0_ok = True
if serial0 is not None:
    v = param(m, "SERIAL0_PROTOCOL", serial0)
    print("SERIAL0_PROTOCOL set to %s" % v)
    # still reboot if this fails, so that the MNT1_TYPE just set takes effect; report the failure at the end
    serial0_ok = v is not None and int(v) == serial0
time.sleep(2)                                                  # let the parameter write reach storage
m.mav.command_long_send(1, 1, 246, 0, 1, 0, 0, 0, 0, 0, 0)    # MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN: autopilot only
m.close()
print("Cube rebooting...")
time.sleep(8)
m, hb = open_cube(90)
if m is None:
    print("[CUBE_FAILED] Cube did not come back"); sys.exit(5)
print("Cube back; MNT1_TYPE now %s" % param(m, "MNT1_TYPE"))
if serial0 is not None:
    print("SERIAL0_PROTOCOL now %s" % param(m, "SERIAL0_PROTOCOL"))
end = time.time() + settle                                     # give the gimbal driver time to run (step "driver on")
while time.time() < end:
    m.recv_match(blocking=True, timeout=0.5)
m.close()
if not serial0_ok:
    print("[CUBE_FAILED] SERIAL0_PROTOCOL not accepted"); sys.exit(6)
print("[CUBE_DONE]")
