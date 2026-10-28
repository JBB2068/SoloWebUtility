# Handoff: add "Update gimbal firmware" to the Solo Web Utility

This brief is for the conversation that builds the Solo Web Utility, which runs on the Solo's iMX6. It explains how to
update a 3DR Solo gimbal's firmware on **Open Solo 4.0.0 with ArduCopter 4.7.x** (CubeSolo). That does not work
automatically today.

The procedure below was worked out and **verified on a real drone on 2026-10-27**: a replacement gimbal went from
v0.28.2 to 1.3.6, and tilt control and GoPro controls work afterwards. The full investigation is in `REPORT.md`.

Files that come with this brief, all Python 2.7 for the iMX6:

| File | Role | Tested on the drone? |
|---|---|---|
| `cube_mount.py` | Sets `MNT1_TYPE` and reboots only the Cube, over the telemetry forwarder | yes (same steps; the final wait was simplified afterwards) |
| `gimbal_setup.py` | Fixed copy of 3DR's `/usr/bin/gimbal/setup.py` (assembly-date crash) | yes, installed and used |
| `gimbal_probe.py` | Read-only: enables the Cube USB link and lists what the gimbal sends (ids, app vs bootloader) | yes |
| `loadGimbal.py` | Stock boot updater plus time limits, log file, retry, version parsing fix | **no**: compiles, and its time limit was tested on a PC only |
| `fix_cube_serial.py` | Checks or sets `SERIAL0_PROTOCOL=1`, `SERIAL4_PROTOCOL=1`, `SERIAL4_BAUD=230` | `--check` mode only |

## Why the stock path fails (short version)

The gimbal updater on the iMX6 reaches the gimbal via: Cube USB (SERIAL0) -> ArduPilot's MAVLink router ->
SERIAL4 -> gimbal (component 154). On ArduCopter 4.7.x:

1. **The gimbal port is private.** With `MNT1_TYPE=2`, the Solo gimbal driver marks the gimbal's port private
   (`SoloGimbal::receive_feedback()` -> `set_channel_private`). The router then:
   * does not forward the gimbal's messages, unless it has seen a `GOPRO_HEARTBEAT`
   * never forwards untargeted messages into the port. The bootloader handshake and the firmware data blocks are
     untargeted.
2. **The gimbal powers up as system ID 0.** The driver sets `GMB_SYSID=1`, in RAM only. ArduPilot never learns a
   route to sysid 0, so with the driver off nothing reaches the gimbal either.
3. **3DR's `gimbal_setup` crashes** (`ValueError: timestamp out of range for platform time_t`) on a gimbal with an
   unset `GMB_ASM_TIME`, then reports "no gimbal".
4. **3DR's flashing loops have no time limit** (`firmware_loader.py` `upload_data()` / `finish_upload()`). One
   attempt ran for more than 10 minutes. At boot, that blocked sshd and pairing until the drone was power-cycled.

Points 1 and 2 are why the procedure toggles `MNT1_TYPE` with Cube-only reboots. Points 3 and 4 are why the fixed
tools are needed.

## The drone environment (as found)

**Software**

* Python 2.7
* pymavlink 1.1.56: MAVLink 1 only, no `dialects/v20`
* pyserial 2.7
* BusyBox 1.21. `head` needs `-n N`, not `-N`.

**Root filesystem**

* aufs: a read-only squashfs plus a writable layer at `/mnt/rootfs.rw` (`/dev/mmcblk0p3`, ext3).
* Changes in `/usr/bin` survive reboots. A factory reset or reinstall drops them.
* `/log` is persistent (`/dev/mmcblk0p4`). `/var/log` is a symlink to `/log`. `/tmp` is cleared at boot.

**Talking to the Cube from the iMX6**

* The telemetry UART `/dev/ttymxc1` (921600, SERIAL1) is owned by `telem_forwarder`.
* Scripts reach the Cube in MAVLink 1 through **`udpout:127.0.0.1:14560`**:
  * target (1, 1)
  * use `source_system=250`
  * send a GCS heartbeat first
  * when picking the autopilot heartbeat, ignore component 154 and type 6
* This is how `cube_mount.py` works.

**Cube USB link to the iMX6**

* It is off normally. Turn it on with GPIO21=1 (select) and GPIO19=0 (enable, active low). Turn it off with
  GPIO19=1 and GPIO21=0. `loadGimbal.py` and `gimbal_probe.py` show how.
* Device: `/dev/serial/by-id/usb-ArduPilot_CubeSolo_<serial>-if00`. On this drone it is the only one and appears
  within about 1 s.

**Cube parameters on this drone**

| Parameter | Value |
|---|---|
| `SERIAL0_PROTOCOL` | 1 (MAVLink 1 on USB; recommended) |
| `SERIAL4_PROTOCOL` | 1 |
| `SERIAL4_BAUD` | 230 |
| `MNT1_TYPE` | 2 (named `MNT_TYPE` on ArduCopter 4.0-4.2) |

**Gimbal tools**

* `/usr/bin/gimbal_setup` -> `/usr/bin/gimbal/setup.py`
* `gimbal_setup --port=<dev>` prints `Software version: vX.Y.Z`, or `Gimbal in bootloader, only firmware loading is
  available` (exit 0).
* `gimbal_setup --port=<dev> <file.ax>` flashes. It ends with `Upload successful` and exit 0.
* The firmware file is `/firmware/gimbal_firmware_1.3.6.ax`: JSON, `version` = `1.3.6`, zlib+base64 image.

**Gimbal state as seen from outside**

| State | LED | Gimbal | On the wire |
|---|---|---|---|
| Application running | pulsing green | levels the camera | `GIMBAL_REPORT` / `HEARTBEAT` (type 26) from comp 154 |
| Bootloader (no application) | flashing yellow | limp | `DATA_TRANSMISSION_HANDSHAKE` from comp 154, bootloader v4.0 |

**Reading the gimbal version directly**

* Read parameter `GMB_SWVER` from (1, 154).
* Decode with `struct.unpack('4b', struct.pack('>f', value))[:3]`, giving major, minor, rev.
* **Check that the reply's `param_id` is `GMB_SWVER`.** 3DR's `setup_param.fetch_param()` accepts the first
  `PARAM_VALUE` of any name. It printed `v0.0.0` for a v0.28.2 gimbal.

**Timing seen**

| Step | Time |
|---|---|
| Cube-only reboot until heartbeat returns | about 10-20 s |
| Successful flash | about 2 min (erase, then 64.32 kB in 253-byte blocks) |

## Procedure the utility should run

Run it as **one script on the drone, started detached** (`setsid` / `nohup ... &`). It should write a log, for example
`/log/gimbal_update.log`, plus a small status file that the web page polls. The web server runs on the same iMX6 and
is not restarted by any step: only the Cube reboots. But a browser or WiFi disconnect must never leave the Cube with
its gimbal driver off.

1. **Preflight.** Refuse to start if:
   * another run is in progress (lock file)
   * the vehicle is armed (`HEARTBEAT.base_mode & 0x80`)
   * this is not Open Solo with a 4.x Cube
   * there is no `/firmware/*.ax`

   Then:
   * record the current `MNT1_TYPE` (normally 2)
   * tell the user: props off, on the ground, do not power off, the gimbal will not respond to tilt during the update
2. **Is the gimbal already in its bootloader?** Run `gimbal_probe.py` or an equivalent check. If yes (flashing yellow,
   limp), skip to step 5. This path worked after a power cycle with `MNT1_TYPE=0`.
3. **Give the gimbal ID 1:** `python -u cube_mount.py 2 25`. The driver sets the gimbal to sysid 1 in RAM.
4. **Open the port:** `python -u cube_mount.py 0 5`. This is a Cube-only reboot, so the gimbal keeps power and ID 1.
   Verify that the gimbal now sends from **sys 1, comp 154** and answers `GMB_SWVER`. If it still shows sys 0, the
   gimbal lost power: start again at step 3.
5. **Version decision.** If the gimbal reports the `.ax` version (1.3.6) and the user did not choose "force", go to
   step 7.
6. **Flash.** Use the fixed `gimbal_setup.py` (copy it to `/usr/bin/gimbal/setup.py`, keeping a `.orig`, or run a copy
   next to the other `/usr/bin/gimbal/*.py` modules):
   * `gimbal_setup --port=<if00 device> /firmware/gimbal_firmware_1.3.6.ax`, with the USB link enabled via the GPIOs
     as above
   * **time limit about 300 s**, kill and retry once; `loadGimbal.py` `run_logged()` shows the pattern
   * stream its output to the page: `Erasing flash...`, `Uploading x of y kB - N%`, `Upload successful`
   * on success, write `/AXON_VERSION` (version and release lines), as `loadGimbal.py` does
7. **Restore, in a `finally`.** Run `python -u cube_mount.py <recorded MNT1_TYPE> 5` and disable the USB link
   (GPIO19=1, GPIO21=0). Then ask the user to confirm the gimbal levels, follows the tilt paddle, and the GoPro
   controls work.

**Do not power-cycle the drone between steps 3 and 6.** A power cycle resets the gimbal to ID 0. If the gimbal is left
in its bootloader (power lost mid-flash), running the tool again recovers it through step 2. The bootloader cannot be
erased by this procedure.

## Also install, once, so the boot updater cannot hang the drone

The stock `S62updateGimbal.sh` -> `/usr/bin/loadGimbal.py` still runs at every boot. If a flash is ever interrupted,
the next boot retries it with no time limit (cause 4). That is what blocked sshd and pairing on 2026-10-27.

The tool should install, keeping `.orig` backups:

* `loadGimbal.py` -> `/usr/bin/loadGimbal.py`. **Test this first: it is untested on a drone.**
* `gimbal_setup.py` -> `/usr/bin/gimbal/setup.py`

## Not known / not verified

* Why the first flash attempt stalled for more than 10 minutes while the next one took 2 minutes. During it, the user
  saw all Solo arm LEDs flash red and the controller disconnect.
* The patched `loadGimbal.py` on a real boot.
* Whether `SERIAL0_PROTOCOL=2` breaks the flash. It is likely fine with little USB traffic, but the verified run used
  1. Keep 1, or check it with `fix_cube_serial.py --check`.
* The real fix is in ArduPilot: let the Solo gimbal port pass `DATA_TRANSMISSION_HANDSHAKE` (130),
  `ENCAPSULATED_DATA` (131) and `PARAM_*` to and from component 154. That change has not been written. With it, the
  stock boot updater would work as it did on 3DR's 1.3.1 Cube firmware, and this procedure would not be needed.
