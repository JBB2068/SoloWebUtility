#!/usr/bin/env python
# Runs ON THE DRONE (Python 2), Open Solo / ArduCopter 4.x. Gives the gimbal MAVLink system ID 1 without the Cube's
# Solo gimbal driver. A gimbal powers up as system 0, and the Cube cannot route anything addressed to system 0.
# The driver sets the ID only once it receives a GIMBAL_REPORT, which a gimbal in a bad state may never send.
# This script instead sends PARAM_SET GMB_SYSID=1 addressed to system 0 (broadcast), component 154, over the Cube's
# USB link. With MNT1_TYPE=0 the gimbal port is not private, so the Cube forwards the broadcast to it. This is the
# same message the driver sends. The gimbal keeps the ID in RAM until it loses power.
#   usage: python -u gimbal_set_sysid.py
# Prints [SYSID_DONE] gimbal is system 1, version X.Y.Z  or  [SYSID_FAILED] <reason>.
from __future__ import print_function
import glob, os, struct, sys, time
from pymavlink import mavutil

SELECT_GPIO, ENABLE_GPIO = "21", "19"
GIMBAL = mavutil.mavlink.MAV_COMP_ID_GIMBAL

def gpio(n, v):
    if not os.path.isdir("/sys/class/gpio/gpio" + n):
        open("/sys/class/gpio/export", "w").write(n)
    open("/sys/class/gpio/gpio%s/direction" % n, "w").write("out")
    open("/sys/class/gpio/gpio%s/value" % n, "w").write(v)

def listen(m, secs, want=None):
    """Read for secs (heartbeat every second). Returns the set of gimbal system ids seen, the message types per id,
    and the first message matching want(msg), if given."""
    sysids, types, found, last_hb = set(), {}, None, 0
    end = time.time() + secs
    while time.time() < end:
        if time.time() - last_hb > 1:
            m.mav.heartbeat_send(0, 0, 0, 0, 0)
            last_hb = time.time()
        msg = m.recv_match(blocking=True, timeout=0.3)
        if msg is None or msg.get_type() == "BAD_DATA" or msg.get_srcComponent() != GIMBAL:
            continue
        sysids.add(msg.get_srcSystem())
        types.setdefault(msg.get_srcSystem(), set()).add(msg.get_type())
        if want is not None and found is None and want(msg):
            found = msg
            break
    return sysids, types, found

def is_param(msg, name, sysid=None):
    return (msg.get_type() == "PARAM_VALUE" and msg.param_id.strip("\x00") == name
            and (sysid is None or msg.get_srcSystem() == sysid))

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
    if dev is None:
        print("[SYSID_FAILED] no Cube USB serial device"); sys.exit(2)
    print("using %s" % dev)
    # announce the way gimbal_setup does (system 255, component 154)
    m = mavutil.mavlink_connection(dev, baud=230400, source_system=255)
    m.mav.srcComponent = GIMBAL

    sysids, types, _ = listen(m, 4)
    print("before: gimbal system ids %s, messages %s"
          % (sorted(sysids), dict((k, sorted(v)) for k, v in types.items())))
    if not sysids:
        print("[SYSID_FAILED] nothing from the gimbal (component 154) came through the Cube"); sys.exit(3)
    if "DATA_TRANSMISSION_HANDSHAKE" in set().union(*types.values()):
        print("[SYSID_FAILED] the gimbal is in its bootloader: no ID needed, flash it directly"); sys.exit(4)

    if 1 not in sysids:
        for attempt in range(1, 6):
            m.mav.param_set_send(0, GIMBAL, "GMB_SYSID", 1.0, mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
            print("sent GMB_SYSID=1 to system 0 component 154 (attempt %d)" % attempt)
            sysids, types, _ = listen(m, 2, lambda msg: msg.get_srcSystem() == 1)
            if 1 in sysids:
                break
        print("after: gimbal system ids %s" % sorted(sysids))
        if 1 not in sysids:
            print("[SYSID_FAILED] the gimbal did not take system ID 1"); sys.exit(5)

    for attempt in range(5):
        m.mav.param_request_read_send(1, GIMBAL, "GMB_SWVER", -1)
        _, _, p = listen(m, 2, lambda msg: is_param(msg, "GMB_SWVER", 1))
        if p is not None:
            version = "%d.%d.%d" % struct.unpack("4b", struct.pack(">f", p.param_value))[:3]
            print("[SYSID_DONE] gimbal is system 1, version %s" % version)
            break
    else:
        print("[SYSID_FAILED] the gimbal is system 1 but did not answer GMB_SWVER"); sys.exit(6)
finally:
    gpio(ENABLE_GPIO, "1"); gpio(SELECT_GPIO, "0")
