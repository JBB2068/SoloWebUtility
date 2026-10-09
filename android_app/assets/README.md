# 3DR Solo Web Utility (Open Solo 4 with latest ArduCopter stable version)

A concise, self-contained on-board web server and management portal for the **3DR Solo** drone running **Open Solo 4.0.0 / latest ArduCopter stable version**.

> **Version 1.0.1** — Runs on Solo Copter iMX6 (Python 2.7/3.x, zero external deps). Zero framework dependencies in the browser (vanilla JS/CSS/HTML).

---

## 🌟 Features

### ⚡ Firmware Update (Tab 1)
- **Cube Selector:** Stock Solo Cube (Black) or Green Cube — only the APJ file differs; all other files are shared.
- **Scope Selector:** Full Install (Cube + Copter iMX6 + Controller iMX6) or Cube Only.
- **Official 3DR sequence** (mirrors `postinstall.sh` from the firmware ZIPs):
  1. `pixhawk.py --load /firmware/arducopter.apj` — Flash Cube → auto-reboot Cube.
  2. `sololink_config --update-prepare sololink` + copy tarball to `/log/updates` + `touch UPDATE RESETSETTINGS` → Reboot Copter iMX6.
  3. SSH to 10.1.1.1 and stage controller update → Reboot Controller iMX6.
- **Live terminal** with real-time log streaming and 8-minute reboot countdown.
- **Deduplication:** Both cube types share the same `3dr-solo.tar.gz` and `3dr-controller.tar.gz`. Only the APJ file is different.

### 🔗 Automatic Pairing (between firmware flash and calibrations)
- Pairing widget appears directly on the Firmware tab **between** the flash result and the calibration reminders — exactly the correct workflow order.
- Uses **`sololink_config --pair`** via SSH to the Controller (3DR dev method).
- Falls back to `systemctl restart sololink` if `sololink_config` is unavailable.
- Also available as a full dedicated Pairing tab.

### 📶 Wi-Fi & Internet (Tab 2)
- **Connect to Home Wi-Fi:** Uses **`sololink_config --set-wifi-sta '<ssid>' '<pass>'`** (the official 3DR developer method) on the Controller's wlan1 interface.
- **Bi-Directional Routing:** 1-click NAT masquerade (`iptables`) so your home PC can reach `10.1.1.10` through your home LAN without switching networks. Includes the Windows `route add` command you need.
- **Reset to Default:** Clears STA credentials and restores factory SoloLink AP mode.
- **Network Status Panel:** Shows mode, SSID, controller IP, internet, IP forwarding, and controller SSH status.

### ⚙️ Parameters (Tab 4)
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

### 🔒 Security
- Password-protected (default: **`sololink`**).
- SHA-256 salted session tokens, 24-hour TTL.
- In-browser password change modal (🔒 icon in header).
- CLI utility: `python change_password.py` or `python change_password.py --reset`

### 🌐 Captive Portal
- Automatically opens the web browser when a client device (iOS, Android, Windows, macOS) joins the SoloLink Wi-Fi, by intercepting OS captive portal probe requests.

### 🏷️ Versioning
- Starts at **1.0.1**, increments by 0.0.1 each iteration.
- Version visible in header, footer, and REST API.

---

## 🚀 Installation

### Option 1: One-Click from Windows PC
1. Connect your PC to Solo Wi-Fi (`SoloLink_...`).
2. Double-click **`deploy_to_solo.bat`** (or run `python install_to_solo.py`).
3. Open: **http://10.1.1.10** — Password: `sololink`

### Option 2: Manual SCP
```bash
scp -r SoloWebUtility/ root@10.1.1.10:/home/root/
ssh root@10.1.1.10 "nohup python /home/root/SoloWebUtility/solo_web_app.py 80 > /log/solo_web_server.log 2>&1 &"
```

### Local Testing
```bash
python solo_web_app.py 8080
# Visit http://localhost:8080
```

---

## 📋 Recommended Workflow After Clean Install

1. **Firmware Update Tab** → Select cube type → Full Install → Start.
2. Wait for the 8-minute reboot countdown to complete.
3. **Pairing section** (on the same Firmware tab) → Initiate Automated Pairing.
4. **Calibrations** (same tab, below pairing) → IMU Calibration → Compass Calibration.

---

## 📂 Project Structure

```
SoloWebUtility/
├── bin/
│   ├── pixhawk.py               # Pixhawk serial bootloader flasher
│   └── uploader.py              # APJ uploader
├── firmware/
│   ├── arducopter_solo.apj      # latest ArduCopter stable version — Stock Solo Cube (1.37 MB)
│   ├── arducopter_green.apj     # latest ArduCopter stable version — Green Cube (1.42 MB)
│   ├── 3dr-solo.tar.gz          # Copter iMX6 Linux image (76 MB) — shared by both
│   ├── 3dr-solo.tar.gz.md5
│   ├── 3dr-controller.tar.gz    # Controller iMX6 Linux image (75 MB)
│   └── 3dr-controller.tar.gz.md5
├── static/
│   ├── index.html               # Single-page app
│   ├── style.css                # Dark aviation UI
│   └── app.js                   # Vanilla JS client (ES5, no frameworks)
├── auth.py                      # SHA-256 + salt password & session management
├── change_password.py           # CLI password utility
├── flash_mgr.py                 # Firmware flash pipeline (mirrors official postinstall.sh)
├── pair_mgr.py                  # Copter ↔ Controller pairing (sololink_config --pair)
├── calib_mgr.py                 # Automated IMU cal -> Reboot -> Compass cal sequence
├── wifi_mgr.py                  # Wi-Fi mgmt (sololink_config --set-wifi-sta)
├── param_mgr.py                 # Parameter editor + MAVLink live fetch
├── presets.py                   # Parameter presets
├── version_mgr.py               # Version tracker (1.0.1 + 0.0.1)
├── solo_web_app.py              # HTTP server + REST API + captive portal
├── install_to_solo.py           # Remote installer to 10.1.1.10
├── deploy_to_solo.bat           # Windows 1-click deployer
└── run_local_server.bat         # Windows local test launcher
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

---

## 🏷️ Version Management
```bash
python version_mgr.py                           # Print current version
python version_mgr.py --inc "Added GPS preset"  # Increment by 0.0.1
```
