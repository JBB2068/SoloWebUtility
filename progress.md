# 3DR Solo Web Utility — Project Progress & Architecture Summary

**Version:** 1.0.71  
**Date:** 2026-10-06  

---

## 1. 🎯 Current Progress

### Locked-in Home Wi-Fi & Internet Connectivity Engine (v1.0.65 – v1.0.71)
- **Home Wi-Fi Engine Status: LOCKED IN & UNTOUCHED:**
  - Preserved exact working POSIX heredoc deployment into `/tmp/wpa_client.conf` and `/usr/bin/connect-home-wifi` on Controller (BusyBox v1.22.1).
  - Preserved direct Paramiko SSH transport (`_ssh_paramiko`) for multi-line script deployment.
  - Preserved dedicated Copter internet route configuration (`ensure_copter_internet_route()`).
  - Preserved frontend status polling & modal connectivity handlers (`static/app.js`).

### Firmware Build Polling & Timeout Fixes (v1.0.68 – v1.0.71)
- **Extended Build Polling Timeout (`max_wait=1200`):**
  - Increased `poll_build` timeout from 360 seconds (6 minutes) to 1200 seconds (20 minutes) in [arducopter_builder.py](file:///c:/Users/User/Documents/SoloWebUtility/arducopter_builder.py#L337) to accommodate peak compilation load on `custom.ardupilot.org`.
- **Live Terminal Build Logging:**
  - Integrated real-time status logging inside build polling loops (`[*] Server build progress (CubeSolo): X% (BUILDING)`), providing visible terminal feedback during custom ArduPilot compilation.
- **Resilient Response Parsing:**
  - Enhanced API response evaluation to inspect both nested `progress` objects and root `state`/`status` fields (`SUCCESS`, `FINISHED`, `COMPLETE`, `COMPLETED`, `artifact_available`).

### Firmware Builder & Calibration Pipeline
- Integrated **latest ArduCopter stable version APJ Builder Engine** (`arducopter_builder.py`) for Stock Solo Cube (Black) and Green Cube.
- Preserved 6-axis accelerometer calibration sequence, IMU calibration, autopilot reboot, and compass calibration.

---

## 2. 🏗️ Architectural Decisions

| Area | Decision & Rationale |
| :--- | :--- |
| **Dual Python Support** | App server and utilities are written strictly for **Python 2.7 & 3.x dual compatibility** to run natively on Copter i.MX6 Linux, Controller BusyBox, and Windows host machines. |
| **Volatile Wi-Fi Storage** | Home Wi-Fi credentials are saved exclusively in RAM (`/tmp/wpa_client.conf`). Credentials and non-default network routes are automatically cleared on reboot to guarantee safety and default SoloLink AP mode recovery. |
| **Armed Safety Interlock** | Wi-Fi configuration changes, hostapd cycling, and firmware flashing are hard-locked whenever `cockpit_mgr.is_vehicle_armed()` returns `True`. Armed state automatically disconnects home Wi-Fi. |
| **Direct Paramiko Transport** | Multi-line remote script deployments use Paramiko's SSH client directly (`_ssh_paramiko`) to bypass Windows command shell escaping quirks. |
| **Dedicated Copter Gateway Routing** | `ensure_copter_internet_route()` enforces Copter default gateway (`10.1.1.1`) and DNS resolvers (`8.8.8.8`, `1.1.1.1`) so firmware builds and downloads proceed through Controller NAT masquerade (`iptables`). |

---

## 3. 🚀 Next Steps

1. **User Testing:**
   - Run `deploy_to_solo.exe` while connected to SoloLink Wi-Fi to install v1.0.71 onto the drone and controller.
2. **Verify Check for Updates & Custom Build:**
   - Click **Check for Updates** or **Force Rebuild APJs** in the Firmware Update tab.
   - Confirm real-time server build progress logs (`[*] Server build progress: X% (BUILDING)`) appear in the modal terminal without premature 360s timeouts.
