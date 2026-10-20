# 3DR Solo Web Utility (Open Solo 4 with latest ArduCopter stable version)

A concise, self-contained on-board web server and management portal for the **3DR Solo** drone running **Open Solo 4.0.0 / latest ArduCopter stable version**.

> **Version 1.2.0** — Runs on Solo Copter i.MX6 (Python 2.7, zero external deps). Zero framework dependencies in the browser (vanilla JS/CSS/HTML).

---

## 🌟 Features

### ⚡ Firmware Update (Tab 1)
- **Cube Selector:** Stock Solo Cube (Black) or Green Cube.
- **ArduCopter Version Control:** Revert to any of the last 3 ArduCopter builds or flash a custom `.apj` uploaded directly from the browser (older builds are archived to `firmware/previous` instead of deleted).
- **Scope Selector:** Full Install (Cube + Copter i.MX6 + Controller i.MX6) or Cube Only. Cube-only installs no longer require the extended reboot countdown.
- **Golden Image:** Ships ArduCopter 4.7.2 (4.7.1 preserved for instant rollback).
- **Official 3DR sequence** (mirrors `postinstall.sh` from the firmware ZIPs):
  1. `pixhawk.py --load /firmware/arducopter.apj` — Flash Cube → auto-reboot Cube.
  2. `sololink_config --update-prepare sololink` + copy tarball to `/log/updates` + `touch UPDATE RESETSETTINGS` → Reboot Copter i.MX6.
  3. SSH to 10.1.1.1 and stage controller update → Reboot Controller i.MX6.
- **Live terminal** with real-time log streaming and reboot progress.

### 📶 Wi-Fi & Internet (Tab 2)
- **Connect to Home Wi-Fi:** Uses stock station mode, session-only with an arm interlock and live step diagnostics.
- **Bi-Directional Routing:** 1-click NAT masquerade (`iptables`) so your home PC can reach `10.1.1.10` through your home LAN without switching networks.
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

### 🏷️ Versioning
- Version: **1.2.0**
- Version visible in header, footer, and REST API (`version.json`).
