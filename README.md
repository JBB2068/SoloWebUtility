# 3DR Solo Web Utility (Open Solo 4 with latest ArduCopter stable version)

A concise, self-contained on-board web server and management portal for the **3DR Solo** drone running **Open Solo 4.0.0 / latest ArduCopter stable version**.

> **Version 1.2.7** — Runs on Solo Copter i.MX6 (Python 2.7, zero external deps). Zero framework dependencies in the browser (vanilla JS/CSS/HTML).

---

## 🌟 Features

### ⚡ Firmware Update
- **Cube Selector:** Stock Solo Cube (Black) or Green Cube.
- **ArduCopter Version Control:** Revert to any of the last 3 ArduCopter builds or flash a custom `.apj` uploaded directly from the browser (older builds are archived to `firmware/previous` instead of deleted).
- **Scope Selector:** Full Install (Cube + Copter i.MX6 + Controller i.MX6) or Cube Only. Cube-only installs no longer require the extended reboot countdown.
- **Golden Image:** Ships ArduCopter 4.7.2 (4.7.1 preserved for instant rollback).
- **Official 3DR sequence** (mirrors `postinstall.sh` from the firmware ZIPs):
  1. `pixhawk.py --load /firmware/arducopter.apj` — Flash Cube → auto-reboot Cube.
  2. `sololink_config --update-prepare sololink` + copy tarball to `/log/updates` + `touch UPDATE RESETSETTINGS` → Reboot Copter i.MX6.
  3. SSH to 10.1.1.1 and stage controller update → Reboot Controller i.MX6.
- **Live terminal** with real-time log streaming and reboot progress.

### 📶 Wi-Fi & Internet
- **Connect to Home Wi-Fi:** Uses stock station mode, session-only with an arm interlock and live step diagnostics.
- **Bi-Directional Routing:** 1-click NAT masquerade (`iptables`) so your home PC can reach `10.1.1.10` through your home LAN without switching networks.
- **Reset to Default:** Clears STA credentials and restores factory SoloLink AP mode.
- **Network Status Panel:** Shows mode, SSID, controller IP, internet, IP forwarding, and controller SSH status.

### ⚙️ Parameters
- **Live MAVLink Fetch:** Fetches all parameters directly from the Pixhawk autopilot (`/dev/ttymxc1` at 921600 baud).
- **Real-time search** by name, description, or value.
- **Inline edit + single-save** per parameter, or **Save All Changes** batch mode.
- **Export** `solo.param` / **Import** custom `.param` files.
- **1-Click Presets:**
  | Preset | Description |
  |--------|-------------|
  | 🧭 Here/Here2/Here3 GPS | Baud 230400, orientation 38, type mask 15392 |
  | 🛬 Retractable Landing Gear | SERVO6/7 function 29, auto-deploy in RTL/Land |
  | ⚡ Speed: Sport Mode | 15 m/s waypoint, 12 m/s loiter, 35° lean |
  | 🎬 Speed: Cinematic Mode | Smooth 5 m/s cruise, gentle accelerations |
  | 🎣 Bait Drop / Payload Release | PWM8 servo gripper, RC8 toggle |
  | 🔋 Stock 4S LiPo | Factory thresholds (14.0V Low / 13.5V Critical) |
  | ⚡ Li-ion (Madhacker) | 18650/21700 curves (11.6V Low / 11.3V Critical) |

### 🎮 Controller Setup
- **Hardware Button Mapping:** Customize A, B, and Pause buttons on the 3DR Artoo Controller directly via `/usr/bin/extSettings.conf`.
- **Preconfigured Profiles & Live Sync:** Select standard flight behaviors or custom trigger actions, with direct live sync over SSH to the Controller.

### 📖 User Manual
- **Built-in Reference:** Complete on-board system manual and documentation accessible offline directly from the drone.

### 🔒 Security
- Password-protected (default: **`sololink`**).
- SHA-256 salted session tokens, 24-hour TTL.
- In-browser password change modal (🔒 icon in header).
- CLI utility: `python change_password.py` or `python change_password.py --reset`

### 🏷️ Versioning
- Version: **1.2.7**
- Version visible in header, footer, and REST API (`version.json`).

### 🎯 Cockpit HUD & Live Flight Deck
- **Full-Bleed Live HDMI Video Feed:** Real-time H.264 video captured from the GoPro / Gimbal via Solo's HDMI port, decoded with ultra-low latency directly in the browser via JMuxer and Web Media Source Extensions (zero internet or external cloud required).
- **Primary Flight Display (PFD):** Glass-cockpit artificial horizon, pitch ladder, roll pointer, flight director reticle, and dynamic airspeed and altitude ladder tapes.
- **HUD Glass Telemetry Overlays:**
  - **Navigation & Compass:** Mini HSI compass with dynamic heading, home distance, and bearing.
  - **GPS Health:** Live satellite count (e.g., `16 SATS`), fix status (3D Fix, DGPS), HDOP, coordinates, and MSL altitude.
  - **Solo 4S Smart Battery:** Total pack voltage, capacity percentage bar, live current draw (Amps), consumed capacity (mAh), per-cell voltages ($C_1\text{--}C_4$), battery temperature, and estimated remaining flight time.
  - **Link Quality & Vehicle Dynamics:** Telemetry packet rate (Hz), RC signal RSSI percentage, vibration clipping diagnostics, airspeed, and groundspeed.
  - **Live MAVLink Console:** Scrolling HUD terminal logging real-time autopilot status messages (`STATUSTEXT`) with color-coded severity alerts (warnings in amber, critical alerts in bold red).
- **Safety Interlocks & Controls:**
  - **Two-Step Arming Protection:** Protected modal confirmation sequence (auto-focuses safely on Cancel) to prevent accidental motor spin-up, plus instant 1-click emergency disarm.
  - **Metric / Imperial Unit Toggle:** One-click instant switching between Metric ($\text{m}$, $\text{m/s}$) and Imperial ($\text{ft}$, $\text{mph}$).
  - **Display Modes:** 1-click toggles for synthetic artificial horizon vs. live camera video background, HUD glass overlay hide/show, and audible voice callouts.
- **Smart Shots:** The 🎬 SMART SHOTS button opens a row of shots: Orbit, Cable Cam, Pano, Zip Line, Rewind, Transect, Return Home, Follow, Selfie and Multipoint Cable Cam. The utility starts the shot through Open Solo's shot manager (TCP 5507), the same way Solex does; the shot is then flown with the Controller, and Fly (or END SHOT) ends it. The Solo must be armed, and the utility only connects to the shot manager while a shot it started is running, so Solex can connect again as soon as the shot ends. If Solex is already connected, start shots from Solex. Follow uses the phone's GPS from the SoloLink Viewer app: the page passes each fix to `/api/follow/location`, and while Follow runs the server sends the position to the shot manager's Follow socket (UDP 14558) 25 times a second, sets the follow style (Hold Angle, Leash, Free Look) and ends the shot if the phone's position stops for 2 s. Selfie: the server sends the shot its path as an app would (start where the Solo is, end further from the subject and up, by the distance / rise / speed under SELFIE settings), looking at the phone's position or, without it, straight ahead of the Solo; then the speed that flies it. Multipoint Cable Cam: points are recorded with A / B on the Controller, and when the shot manager has made the cable the server sends SOLO_SPLINE_ATTACH at the last point, so the Solo joins the cable where it is.
- **Precision Landing:** ArduPilot's own precision landing and Precision Loiter, fed by the Cockpit. Print the pad from `/landing_pad.html` (a black 6×6-cell square with a 4×4 pattern on a white margin; 24 in is the default size), apply the 🎯 *Precision Landing* preset on the Parameters tab (`PLND_ENABLED 1`, `PLND_TYPE 1` MAVLink, `PLND_LAG 0.25`) and reboot the Cube once. In the Cockpit, 🎯 PRECISION → **PAD FINDER: ON** looks for the pad in the live video (`static/padfinder.js`, ~10 looks a second in the browser) and every sighting goes to the Cube as `LANDING_TARGET` (MAVLink 1, `MAV_FRAME_LOCAL_FRD`, with the distance worked out from the pad's size, since the Solo has no rangefinder). Then **LAND**, **RTL** (the Controller's Home button) and **Smart RTL** land on the pad while it is seen; with the finder on, the camera turns straight down as the Solo starts to land. **CAMERA DOWN** points the gimbal down by hand (moving the paddle takes it back), and **PREC LOITER** turns ArduPilot's Precision Loiter on (aux function 39 over MAVLink), so Fly holds over the pad. While the pad is not seen nothing is sent, and the Solo lands or loiters as it normally would. The Cockpit must stay open, with the video, on the phone or PC for the whole landing. ⚙ PAD sets the pad size, the camera's horizontal FOV (118° for GoPro Wide; raise it if the height shown reads too high), the lens type and the **video delay** (how far the Cockpit's video is behind the GoPro, about 0.4 s; measure it with a stopwatch). ArduPilot's `PLND_LAG` makes up for 0.25 s of the delay at most; the server makes up for the rest by moving each sighting on by the Solo's velocity and turn (from `GLOBAL_POSITION_INT` and `ATTITUDE`) over the remaining delay, so Precision Loiter doesn't chase where the pad was. While a finder is on the Cockpit also keeps its video within 0.15 s of live (0.5 s otherwise).
- **Landing on the takeoff spot (no pad):** 📍 **TAKEOFF SPOT**, turned on before arming, turns the camera straight down when the Solo arms and saves pictures of the ground around the takeoff spot on the climb (about 2, 4 and 8 m, `static/spotfinder.js`), then hands the camera back to the paddle (by 10 m at the latest, or straight away if the paddle is moved or the Solo moves away sideways). On the way back down the live video is turned and scaled to match those pictures using the telemetry heading and height (and a ±8% height search) and slid over them by normalised cross-correlation, only within a few metres of the GPS home. Once three matches in a row agree, each match goes to the Cube as `LANDING_TARGET`, exactly like a pad sighting. Ground that is too plain or repeats (lawn, tiles) is refused on the climb, and an unsure match is never sent, so the Solo then lands on GPS. A pad that is seen always wins. Not yet flight-tested.

---

## 🚀 Installation & Deployment

### Option 1: One-Click from Windows PC
1. Connect your PC to Solo Wi-Fi (`SoloLink_...`).
2. Double-click **`deploy_to_drone.bat`** (or run `python deploy_to_drone.py`).
3. Open: **http://10.1.1.10** — Password: `sololink`

### Option 2: Android Installer
1. Connect your Android device to SoloLink Wi-Fi.
2. Launch the `Solo Web Utility Installer` Android app to deploy directly to the drone.

### Option 3: Mac Installer
1. Connect your Mac to SoloLink Wi-Fi.
2. Run `mac_installer/install_solo_web_utility.command`.

---

## 📋 Recommended Workflow After Clean Install

1. **Firmware Update Tab** → Select cube type → Full Install → Start.
2. Wait for the reboot sequence to complete.
3. **Calibrations** (on the Firmware tab) → IMU Calibration → Compass Calibration.

---

## 📂 Project Structure

```
SoloWebUtility/
├── bin/
│   ├── pixhawk.py               # Pixhawk serial bootloader flasher
│   ├── uploader.py              # APJ uploader
│   └── apj_tool.py              # APJ inspection utility
├── firmware/
│   ├── arducopter_CubeSolo_*.apj  # Current ArduCopter stable — Stock Solo Cube
│   ├── arducopter_CubeGreen_*.apj # Current ArduCopter stable — Green Cube
│   ├── previous/                # Previous builds preserved for rollback
│   ├── 3dr-solo.tar.gz.md5
│   ├── 3dr-controller.tar.gz.md5
│   └── 3dr-solo.tar.gz.baked
├── modules/                     # Modular server components
│   ├── auth.py                  # Authentication & session management
│   ├── calibration.py           # Calibration routines
│   ├── controller.py            # Controller settings & Artoo manager
│   ├── firmware.py              # Firmware staging and flashing
│   ├── parameters.py            # Parameter fetching and editing
│   ├── telemetry.py             # MAVLink telemetry stream
│   └── wifi.py                  # Wi-Fi station mode management
├── static/
│   ├── index.html               # Single-page app
│   ├── style.css                # Dark aviation UI
│   ├── app.js                   # Vanilla JS client (ES5, no frameworks)
│   ├── cockpit.js               # Cockpit HUD canvas and flight instruments
│   └── cockpit.css              # Glass cockpit and HUD styling
├── templates/                   # Server-side HTML templates
├── android_installer/           # Android APK installer project
├── mac_installer/               # macOS native deployment scripts
├── release/                     # PyInstaller release build scripts and specs
├── tools/                       # Firmware baking and packaging tools
├── solo_web_app.py              # Primary HTTP server + REST API
├── deploy_to_drone.bat          # Windows 1-click drone deployer
├── deploy_to_drone.py           # Python deployment engine
└── version.json                 # Version descriptor & changelog
```

---

## 🔒 Password Management

### Web UI
Click 🔒 in the top-right header.

### SSH CLI
```bash
python /home/root/SoloWebUtility/change_password.py          # Interactive change
python /home/root/SoloWebUtility/change_password.py --reset  # Reset to 'sololink'
```
