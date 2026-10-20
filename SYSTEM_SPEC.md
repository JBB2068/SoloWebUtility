# 3DR Solo Drone Web Utility - System Specification
This is the single source of truth for the project. Always reference this document when writing, refactoring, or modifying code modules.

## 1. Project Overview
* **Name:** SoloWebUtility2
* **Purpose:** A local Flask-based web interface running from the copter's internal computer to manage, update, configure parameters, and automate workflows for a 3DR Solo drone.
* **Target Environment:** Designed to deploy natively to the aircraft's internal iMX6 Linux environment.

## 2. Technical Stack
* **Backend Framework:** Python 3 (Flask)
* **Drone Communication:** MAVLink / DroneKit / Asynchronous socket loops
* **Frontend Interface:** HTML5, CSS (Bootstrap via CDN), Vanilla JavaScript (Server-Sent Events)

## 3. Project Directory Architecture
All files must adhere strictly to this isolated blueprint folder structure to prevent global variable bleeding and regression bugs:

SoloWebUtility2/
├── app.py                  # Main launcher (Initializes Flask, blueprints, and background daemon threads)
├── SYSTEM_SPEC.md          # This specification document (Do not delete)
├── modules/                # Core Python application logic split into isolated modules
│   ├── __init__.py         # Empty file to make folder an importable package
│   ├── firmware.py         # Flashing pipeline logic (Staging -> APJ -> Copter -> Controller reboots)
│   ├── parameters.py       # Vehicle parameter editor and reader logic
│   ├── controller_firmware.py # Ground station firmware management loops
│   ├── controller.py       # Artoo physical button layouts and extSettings.conf configuration
│   └── video.py            # Video stream and OSD pipeline handling
└── templates/              # Front-end layout templates (Strictly separate from logic)
    ├── index.html          # Main master navigation dashboard
    └── updates.html        # Firmware automation wizard page layout

## 4. Key Automation Rules
* **Strict Real-Time Verification:** Copter connection checks (10.1.1.10) must never rely on a cached variable. Every front-end poll must trigger a live network check.
* **Asynchronous Execution:** Network pings and socket heartbeats must run in independent daemon threads. They must never stall or block the main Flask page rendering handlers.
* **One-Time Pairing Automation:** Right before triggering the post-update reboots, the backend touches a temporary flag file at '/log/updates/RUN_PAIR_ONCE'. The moment the 10.1.1.10 ping returns True after an installation, the script instantly triggers 'sololink_config --pair' over SSH exactly once, then securely erases the flag file.
* **Safety Countdown Guard:** The 8-minute installation countdown bar on updates.html must run continuously upon staging completion. It must only clear out or advance early if 'copter_online' returns an explicit, verified True response from the background ping loop.

## 5. Target Environment Rules (Non-Negotiable)
1. **Embedded Yocto Linux target:** The program runs natively on the 3DR Solo copter's iMX6 companion computer (Yocto Linux).
2. **Absolute dependency isolation:** Only use libraries pre-installed on the stock Solo image.
   * Allowed: standard built-ins (os, sys, socket, struct, threading, time, logging, configparser, subprocess) and `pymavlink`, plus lightweight standard Flask.
   * Prohibited: `dronekit` (unless verified on the image), `paramiko` for local on-drone execution, and any heavy web frameworks.
3. **On-drone loopback networking:** MAVLink connections must use loopback/local UDP (e.g. `127.0.0.1:14560` / `udp:localhost:14560`), never `10.1.1.10`.
4. **Native commands only:** System actions (Wi-Fi, pairing, calibration triggers) must use native Solo CLI binaries in `/usr/bin/` (`sololink_config`, `wpa_cli`, `pktman`, ...) through `subprocess`. No desktop-style automation.
