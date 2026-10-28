# Gimbal firmware update on Open Solo: causes and fix

**Status (2026-10-27):** the replacement gimbal on this Solo was updated from **v0.28.2 to 1.3.6** by Open Solo's own
boot-time updater (`S62updateGimbal.sh` -> `loadGimbal.py`) using the built-in `/firmware/gimbal_firmware_1.3.6.ax`.
After a power cycle, tilt control and the GoPro camera controls work again. The camera controls had been missing
because the old gimbal firmware never reported the GoPro (see cause 1).

Setup:

* Copter: Open Solo 4.0.0 (`3dr-solo.tar.gz`, built 2020-01-26)
* Cube: ArduCopter **4.7.2**, CubeSolo
* Compared against: stock `solo_2.4.2.tar.gz` with Cube firmware 1.3.1

## 1. Why the gimbal never updated itself

The rootfs is **not** the problem. Open Solo ships the stock 3DR gimbal updater unchanged, apart from one line in
`loadGimbal.py`. Its firmware file, gimbal tools, pymavlink and boot slot are byte-identical to stock 2.4.2 (diff in
section 4).

The updater on the iMX6 reaches the gimbal through the Cube: iMX6 -> USB (SERIAL0) -> Cube's MAVLink router ->
SERIAL4 -> gimbal. With a replacement gimbal on ArduCopter 4.7.2, four separate things break that path. Each one is
enough to stop the update on its own:

| # | Cause | Effect | Evidence |
|---|---|---|---|
| 1 | **The Cube walls off the gimbal port.** When the Solo gimbal driver (`MNT1_TYPE=2`) gets the first `GIMBAL_REPORT`, it calls `GCS_MAVLINK::set_channel_private(chan)` (ArduPilot `libraries/AP_Mount/SoloGimbal.cpp`, `receive_feedback()`). In `MAVLink_routing.cpp` `forward()`, messages *from* a private port are not forwarded unless a `GOPRO_HEARTBEAT` has been seen (`gopro_status_check`). Messages *to* a private port are dropped unless they target a learned (sysid, compid). | Nothing from the gimbal reaches the iMX6, so the boot log says `Failed to find an attached gimbal`. The bootloader handshake and firmware blocks carry no target field, so they can never reach the gimbal. With old gimbal firmware that sends no `GOPRO_HEARTBEAT`, the camera controls are also dead. | Read-only probe with `MNT1_TYPE=2`: zero messages from component 154 on USB or on telemetry. Code checked at tag `Copter-4.7.2`. |
| 2 | **The gimbal uses MAVLink system ID 0.** A gimbal comes up as sysid 0 after power-on. The 4.x driver sets `GMB_SYSID=1`, but only in the gimbal's RAM. ArduPilot's `learn_route()` never learns sysid 0 ("don't learn routes to the broadcast system"). | With the driver off (`MNT1_TYPE=0`), the gimbal's messages get out, but the Cube has no route back in. The version request and the "enter bootloader" command go unanswered: `No response from gimbal, exiting.` | Probe with `MNT1_TYPE=0`: gimbal sends `sys 0 comp 154`. After the driver had set ID 1, a Cube-only reboot with `MNT1_TYPE=0` gave `sys 1 comp 154`, and the gimbal answered `GMB_SWVER`. |
| 3 | **3DR's `gimbal_setup` crashes on an unset assembly date.** `setup.py:468` `time.ctime(asm_time)` raises `ValueError: timestamp out of range for platform time_t` when the gimbal's `GMB_ASM_TIME` is out of range for the Solo's 32-bit `time_t`. This happened with this replacement gimbal. | The version check exits with an error, `loadGimbal.py` reports `Failed to find an attached gimbal`, and nothing is flashed. This would also happen on **stock 2.4.2**, which has the identical file. | Boot log 20:45. |
| 4 | **The 3DR loader has no overall time limit.** `firmware_loader.py` `upload_data()` loops for as long as the gimbal keeps sending handshakes, and `finish_upload()` has a `while True`. | One attempt ran for more than 10 minutes without finishing. Because the updater runs in `rcS` before `sshd` and pairing, the Solo could not be reached and did not pair until it was power-cycled. | Boot at 21:11: the log stops at `Erasing flash...`; ping worked, SSH port closed. |

Also found, minor:

* `setup_param.fetch_param()` returns the first `PARAM_VALUE` of *any* name. While the gimbal is streaming its
  parameter list, the version check can therefore read the wrong value. It printed `v0.0.0` for a v0.28.2 gimbal.
  That still differs from 1.3.6, so the update still ran.
* Open Solo `pixhawk.py` (not gimbal-related):
  * line 435 has `rebootPixhawk` without `()`, so a failed Cube flash never reboots the Cube
  * line 672 has `if doFirmware():`, but `doFirmware()` never returns a value, so the post-flash re-check never runs
  * neither bug exists in stock 2.4.2

What the first version of this report got wrong:

* It named MAVLink 2 on the Cube's USB port (`SERIAL0_PROTOCOL=2`) as the main cause. The Solo's pymavlink 1.1.56
  does only parse MAVLink 1. But this drone was already at `SERIAL0_PROTOCOL=1` and still failed for causes 1-4.
  `SERIAL0_PROTOCOL=1` is still recommended (the successful update ran with it), but it is not the fix.
* It guessed that the earlier red flashing meant the gimbal was in its bootloader with its application erased. The
  gimbal turned out to be running its application (green, levelling) when checked. The red flashing remains
  unexplained. In the bootloader, the LED flashed **yellow** and the gimbal was limp.

## 2. Procedure that worked on this drone

Props off throughout.

1. Set `MNT1_TYPE=2` and reboot **only the Cube** (`MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN`, 246). The driver gives the
   gimbal ID 1.
2. Set `MNT1_TYPE=0` and reboot **only the Cube** again. The gimbal keeps its power and ID 1, and its port is no
   longer private.
3. Install the fixed `/usr/bin/gimbal/setup.py` (cause 3).
4. Reboot **only the iMX6** (`reboot`). The stock boot updater read the version and put the gimbal in its bootloader.
   This first attempt then stalled (cause 4) until the drone was power-cycled.
5. After the power cycle, the boot updater found the gimbal in its bootloader and flashed 1.3.6 in about 2 minutes:
   `Upload successful` / `Succesfully updated the gimbal`, and `/AXON_VERSION` = 1.3.6.
6. Set `MNT1_TYPE=2` again and restore the original boot script, then power-cycle. Tilt and camera controls confirmed.

Step 5 worked even with the driver off after a power cycle. The bootloader's frames are routable even though the
application starts at sysid 0, so cause 2 only blocks the very first step: telling the running application to enter
its bootloader.

## 3. Permanent fix

**On the drone (files in this folder):**

* `gimbal_setup.py` -> `/usr/bin/gimbal/setup.py`: catches the assembly-date crash (cause 3). Installed on this drone;
  the original is at `/usr/bin/gimbal/setup.py.orig`.
* `loadGimbal.py` -> `/usr/bin/loadGimbal.py`: stock behaviour plus:
  * time limits: 60 s for the version check, 300 s for the flash (cause 4)
  * accepts a version even if `gimbal_setup` fails afterwards
  * 10 s USB wait
  * a log in `/log/gimbal_update.log`
  * one retry

  **Not installed** on this drone; the stock loader is still in place.

**For replacement gimbals on Open Solo (utility-triggered):** automate section 2 as a SoloRevert action:

* `MNT1_TYPE` dance with Cube-only reboots
* run the time-limited loader
* restore `MNT1_TYPE=2`

This needs no ArduPilot change. While it runs, the gimbal is not driven by the Cube.

**Proper fix (ArduPilot, for Open Solo / upstream):** this is a proposal and has not been built or tested. With
`HAL_SOLO_GIMBAL_ENABLED`, `MAVLink_routing::forward()` should let the gimbal-update traffic pass to and from the
private Solo gimbal port:

* `DATA_TRANSMISSION_HANDSHAKE` (130) and `ENCAPSULATED_DATA` (131)
* `PARAM_REQUEST_READ` / `PARAM_VALUE` to and from component 154, for the version check

Then the stock boot updater works with `MNT1_TYPE=2`, as it did on stock Cube firmware 1.3.1.

## 4. Stock 2.4.2 vs Open Solo rootfs: gimbal pipeline

| Path | Stock 2.4.2 | Open Solo 4.0.0 |
|---|---|---|
| `/etc/rcS.d/S62updateGimbal.sh` -> `/usr/bin/loadGimbal.py`, after `S60pixhawk` | same | same |
| `/firmware/gimbal_firmware_1.3.6.ax` | md5 `f5fba21a` | identical |
| `/usr/bin/gimbal/*.py` (`gimbal_setup`, loader, helpers) | | identical |
| pymavlink 1.1.56 (MAVLink 1 only, no `dialects/v20`) | | identical parser |
| `loadGimbal.py:71` USB pattern | `usb-3D_Robotics*` | `usb*` (needed for the CubeSolo's USB ID) |
| Cube firmware in `/firmware` | `ArduCopter-1.3.1.px4` | `arducopter.apj` (4.0.1); the 4.7.1 install zip flashes its own apj |

Open Solo's embedded CubeSolo defaults set `SERIAL1_PROTOCOL 1`, `SERIAL4_PROTOCOL 1`, `SERIAL4_BAUD 230` and
`MNT1_TYPE 2`. `SERIAL0_PROTOCOL` is left at the ArduPilot default of 2.

## 5. Files

* `loadGimbal.py`: patched boot updater (section 3)
* `gimbal_setup.py`: fixed `/usr/bin/gimbal/setup.py`
* `gimbal_probe.py`: read-only look at what the gimbal sends through the Cube (IDs, application vs bootloader)
* `fix_cube_serial.py`: checks or sets `SERIAL0/SERIAL4_PROTOCOL=1` and `SERIAL4_BAUD=230` through the telemetry forwarder

## 6. Not verified

* Why the 21:11 attempt stalled while the next one finished in 2 minutes.
* The patched `loadGimbal.py` has not been run on a drone. It compiles, and its time limit was tested on a PC.
* The proposed ArduPilot change has not been written, built or tested.
