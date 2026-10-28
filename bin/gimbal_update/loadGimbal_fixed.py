#!/usr/bin/env python
# Drop-in replacement for Open Solo's /usr/bin/loadGimbal.py (run at boot by /etc/rcS.d/S62updateGimbal.sh).
# Changes from the Open Solo version (see gimbal_fix/REPORT.md):
#   * waits up to 10 s for the Cube's USB serial device (was 2 s; Open Solo already raised pixhawk.py to 5 s)
#   * picks the MAVLink interface (-if00) when the Cube shows more than one USB serial port
#   * warns when the Cube sends MAVLink 2 on USB (SERIAL0_PROTOCOL=2): the gimbal tools only speak MAVLink 1
#   * logs every step to /log/gimbal_update.log, so a failed boot-time update can be read afterwards
#   * time limits on gimbal_setup: its upload loops have none, and a stalled upload used to hang the boot
#     (before sshd and pairing) for as long as the gimbal kept asking for blocks
#   * accepts the version even when gimbal_setup crashes afterwards (unset assembly date on replacement gimbals)
#   * retries the upload once when it fails (the gimbal stays in its bootloader, which the loader handles)
# Runs on the Solo (Python 2.7); also usable by hand:  python -u loadGimbal.py [/firmware/gimbal_firmware_1.3.6.ax]
from __future__ import print_function

import subprocess, sys, os, time, glob, argparse, threading
from datetime import datetime
from re import search
from json import loads
sys.path.append("/usr/bin")
import clock

SELECT_GPIO = "21"
ENABLE_GPIO = "19"

firmware_ext = "ax"
version_file = "/AXON_VERSION"
log_file = "/log/gimbal_update.log"
gimbal_setup = "/usr/bin/gimbal_setup"
dev_pattern_usb = "/dev/serial/by-id/usb*"
usb_timeout = 10
probe_timeout = 60      # version check
flash_timeout = 300     # a good flash takes about 2 minutes


class Tee(object):
    """Copy everything printed to the console into the log file."""
    def __init__(self, path):
        self.out = sys.stdout
        self.log = None
        try:
            if os.path.exists(path) and os.path.getsize(path) > 256 * 1024:
                os.rename(path, path + ".1")
            self.log = open(path, "a")
        except Exception:
            pass

    def write(self, s):
        self.out.write(s)
        self.out.flush()
        if self.log:
            self.log.write(s)
            self.log.flush()

    def flush(self):
        self.out.flush()


def setGPIODir(gpio, direction):
    dir_fd = open("/sys/class/gpio/gpio"+str(gpio)+"/direction", "w")
    dir_fd.write(direction)
    dir_fd.close()

def openGPIO(gpio):
    # Check and see if the GPIO is already exported
    if not os.path.isdir("/sys/class/gpio/gpio"+str(gpio)):
        # Otherwise export it
        exp_fd = open("/sys/class/gpio/export", "w")
        exp_fd.write(gpio)
        exp_fd.close()

    setGPIODir(gpio, "out")

def closeGPIO(gpio):
    unexp_fd = open("/sys/class/gpio/unexport", "w")
    unexp_fd.write(gpio)
    unexp_fd.close()

def setGPIO(gpio, value):
    val_fd = open("/sys/class/gpio/gpio"+str(gpio)+"/value", "w")
    val_fd.write(value)
    val_fd.close()

def openSetClose(gpio, value):
    openGPIO(gpio)
    setGPIO(gpio, value)
    closeGPIO(gpio)

def writeVerFile(AxonVersion, AxonRelease):
    verFile = open(version_file,"w")
    verFile.write(AxonVersion + '\n')
    verFile.write(AxonRelease + '\n')
    verFile.close()

def removeVerFile():
    try:
        os.remove(version_file)
    except Exception:
        pass

def disconnectAndExit():
    openSetClose(ENABLE_GPIO, "1") # disable
    openSetClose(SELECT_GPIO, "0") # uninit
    os.system("echo none > /sys/class/leds/user2/trigger")
    print("[gimbal] done %s" % datetime.now())
    sys.exit()

def find_usb_dev():
    """The Cube's USB serial device, or None. ArduPilot 4.x on a Cube can show two USB serial ports
    (-if00 is MAVLink/SERIAL0, -if02 is the second port); always use -if00 when there is a choice."""
    devs = sorted(glob.glob(dev_pattern_usb))
    if not devs:
        return None
    if len(devs) > 1:
        print("USB serial devices: %s" % ", ".join(devs))
        for d in devs:
            if d.endswith("-if00"):
                return d
    return devs[0]

# return device name or None
def create_usb_serial(timeout=usb_timeout):
    openSetClose(SELECT_GPIO, "1") # init
    openSetClose(ENABLE_GPIO, "0") # enable
    start_us = clock.gettime_us(clock.CLOCK_MONOTONIC)
    now_us = start_us
    timeout_us = timeout * 1000000
    while find_usb_dev() is None and (now_us - start_us) < timeout_us:
        now_us = clock.gettime_us(clock.CLOCK_MONOTONIC)
        time.sleep(0.01)
    # give udev a moment to finish the by-id links for every interface
    time.sleep(0.5)
    dev_name = find_usb_dev()
    if dev_name:
        print("USB device %s after %0.1f s" % (dev_name, (clock.gettime_us(clock.CLOCK_MONOTONIC) - start_us) / 1e6))
    return dev_name

def count_heartbeats(buf):
    """Count autopilot HEARTBEAT headers (component 1) in raw bytes: returns (mavlink1, mavlink2)."""
    v1 = v2 = 0
    for i in range(len(buf) - 9):
        # MAVLink 1: FE len=9 seq sysid compid=1 msgid=0
        if buf[i] == 0xFE and buf[i+1] == 9 and buf[i+4] == 1 and buf[i+5] == 0:
            v1 += 1
        # MAVLink 2: FD len=9 incompat(0/1) compat=0 seq sysid compid=1 msgid=0 0 0
        elif (buf[i] == 0xFD and buf[i+1] == 9 and buf[i+2] in (0, 1) and buf[i+3] == 0 and buf[i+6] == 1
              and buf[i+7] == 0 and buf[i+8] == 0 and buf[i+9] == 0):
            v2 += 1
    return v1, v2

def usb_mavlink_version(dev, secs=3.5):
    """1 or 2: what the Cube sends on USB (set by its SERIAL0_PROTOCOL); None if no heartbeat was seen."""
    import serial
    try:
        port = serial.Serial(dev, 115200, timeout=0.2)
    except Exception as e:
        print("could not open %s: %s" % (dev, e))
        return None
    buf = bytearray()
    end = time.time() + secs
    while time.time() < end:
        buf.extend(bytearray(port.read(512)))
    port.close()
    v1, v2 = count_heartbeats(buf)
    print("Cube USB: %d bytes in %.1f s, %d MAVLink1 / %d MAVLink2 heartbeats" % (len(buf), secs, v1, v2))
    if v2 > v1:
        return 2
    if v1:
        return 1
    return None

def run_logged(cmd, timeout):
    """Run a command, showing (and logging) its output as it comes; kill it after timeout seconds.
    Returns (exit code, output); the exit code is -9 when it was killed."""
    print("$ " + " ".join(cmd))
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    killed = []
    def kill():
        killed.append(True)
        try:
            p.kill()
        except OSError:
            pass
    watchdog = threading.Timer(timeout, kill)
    watchdog.start()
    out = []
    try:
        while True:
            c = p.stdout.read(1)
            if not c:
                break
            if not isinstance(c, str):
                c = c.decode("latin-1")
            sys.stdout.write(c)
            out.append(c)
        rc = p.wait()
    finally:
        watchdog.cancel()
    if killed:
        print("\n[gimbal] stopped after %d s (time limit)" % timeout)
    print("(exit %d)" % rc)
    return rc, "".join(out)

# Bootloading process
sys.stdout = Tee(log_file)
print("")
print("Gimbal update startup script v1.1.8-fix1  %s" % datetime.now())

parser = argparse.ArgumentParser()
parser.add_argument("file_specified", nargs='?')
args = parser.parse_args()

if args.file_specified and not os.path.isfile(args.file_specified):
    print("File \"%s\" not found" % (args.file_specified))
    sys.exit()

if args.file_specified:
    latest = args.file_specified
else:
    # See what versions we have, sorted by file timestamp
    files = sorted(glob.glob('/firmware/*.%s' % firmware_ext), key=os.path.getmtime)
    if not files:
        print("No Gimbal firmware available for update.")
        sys.exit()

    # Pop the first (newest) firmware from the array
    latest = files[-1]

    # Cleanup old firmwares
    if len(files) > 1:
        for i in range(len(files) - 1):
            print("Removing old firmware: %s" % files[i])
            os.remove(files[i])

# Get the versions from px4 json
latest_json = loads(open(latest).read())
AxonVersion = latest_json['version']
AxonRelease = latest_json['release']
print("Firmware file %s is version %s" % (latest, AxonVersion))

# Set the LED to a blink pattern so the user knows we're updating
os.system("echo timer > /sys/class/leds/user2/trigger")
os.system("echo 1000 > /sys/class/leds/user2/delay_on")
os.system("echo 100 > /sys/class/leds/user2/delay_off")

pixhawk_usb = create_usb_serial()
if pixhawk_usb is None:
    print("[gimbal] STAGE usb: no Cube USB serial device after %d s. Exiting." % usb_timeout)
    disconnectAndExit()

if usb_mavlink_version(pixhawk_usb) == 2:
    print("[gimbal] WARNING: the Cube sends MAVLink 2 on USB (SERIAL0_PROTOCOL=2). The gimbal tools use pymavlink "
          "1.1.56, which only reads MAVLink 1, so gimbal messages get lost. Set SERIAL0_PROTOCOL=1 on the Cube and "
          "reboot it (gimbal_fix/fix_cube_serial.py does this).")

semver = "unknown"
rc, output = run_logged([gimbal_setup, "--port=%s" % pixhawk_usb], probe_timeout)
# gimbal_setup prints the version first; it can crash afterwards on a gimbal with an unset assembly date
# ("timestamp out of range for platform time_t"), so a version in the output counts even when it failed.
version = search(r"Software version: v(\d+.\d+.\d+)", output)
if "Gimbal in bootloader" in output:
    print("[gimbal] gimbal is in its bootloader (no application): loading firmware")
elif version:
    semver = version.group(1)
elif rc != 0:
    print("[gimbal] STAGE probe: no reply from the gimbal through the Cube, exiting")
    removeVerFile()
    disconnectAndExit()

print("Current gimbal software version %s" % semver)

# Quit early if the gimbal version is the latest (unless called directly with a firmware)
if semver == AxonVersion and not args.file_specified:
    print("Gimbal firmware is current, not updating.")
    writeVerFile(AxonVersion, AxonRelease)
    disconnectAndExit()

print("Loading file %s" % (os.path.abspath(latest)))

# Bootload the gimbal now. A failed upload leaves the gimbal in its bootloader; the second try picks it up there.
for attempt in (1, 2):
    ret, output = run_logged([gimbal_setup, "--port=%s" % pixhawk_usb, latest], flash_timeout)
    if ret == 0:
        break
    print("[gimbal] STAGE upload: attempt %d failed" % attempt)
    time.sleep(3)

if ret != 0:
    print("Unable to update the gimbal, closing")
else:
    print("Succesfully updated the gimbal")
    writeVerFile(AxonVersion, AxonRelease)

disconnectAndExit()
