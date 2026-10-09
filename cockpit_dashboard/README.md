# 🛸 3DR Solo Cockpit Flight Deck - HUD Overlay Edition

Compact glass-cockpit flight instrument deck and live telemetry HUD for 3DR Solo and Open Solo drones.

> **Status:** Completely separate from `SoloWebUtility` as requested until tested, reviewed, and approved for integration.

---

## 🌟 Key Features

1. **Pure Live Copter MAVLink Telemetry**:
   - Zero simulation: 100% dedicated to live telemetry from the Solo copter (`10.1.1.10:14560`) and controller (`10.1.1.1:14550`).
   - Active heartbeats ($1\text{ Hz}$) keep Solo's `telem_forwarder` transmitting.
   - High-rate data streaming ($10\text{--}20\text{ Hz}$) for Attitude, GPS, HUD, and Battery.

2. **Two-Step Arming Safety Warnings (Default is Cancel)**:
   - Clicking **`ARM MOTORS`** opens a two-step confirmation sequence to prevent accidental propeller activation:
     - **Step 1 of 2**: Warning about spinning propellers; auto-focuses on **`CANCEL (Safe)`**.
     - **Step 2 of 2**: Final confirmation check; auto-focuses on **`CANCEL (Safe)`**.
   - Tapping Cancel, pressing Escape, or clicking outside immediately aborts.
   - Clicking **`DISARM`** when armed disarms the motors immediately for emergency safety.

3. **Live MAVLink Messages Box on Left Side**:
   - Scrolling terminal console on the left HUD overlay displaying live autopilot status messages (`STATUSTEXT`) and system events with timestamps.
   - Color-coded severity highlights (warnings in amber, critical alerts in bold red).
   - Auto-scrolls to ensure the newest messages are always in view.

4. **GPS Section with Number of Satellites**:
   - Prominently displays the live satellite count (e.g. `16 SATS`) directly inside the GPS Navigation card.
   - Color health coding: 🟢 $\ge 10$ Sats, 🟡 $6\text{--}9$ Sats, 🔴 $< 6$ Sats.

5. **Metric / Imperial Unit Toggle (`[🌐 METRIC]` / `[🇺🇸 IMPERIAL]`)**:
   - One-click toggle in the top bar to switch between Metric and Imperial:
     - **Altitude**: Meters ($\text{m}$) $\leftrightarrow$ Feet ($\text{ft}$)
     - **Speed**: Meters per second ($\text{m/s}$) $\leftrightarrow$ Miles per hour ($\text{mph}$)
     - **Distance to Home**: Meters ($\text{m}$) $\leftrightarrow$ Feet / Miles ($\text{ft} / \text{mi}$)
   - Dynamically re-scales both the **Airspeed Tape** and **Altitude Tape** on the canvas, as well as all digital readout boxes.
   - Remembers your preference automatically across sessions (`localStorage`).

6. **Full-Bleed Live HDMI Video Feed & Artificial Horizon**:
   - Live H.264 video feed captured from the GoPro / Gimbal via the HDMI port on the copter's i.MX6.
   - Automatically maintains the Sololink video handshake on TCP `10.1.1.1:5502` and depacketizes incoming RTP H.264 packets on UDP `5600`.
   - Low-latency hardware-accelerated playback directly in the browser via `JMuxer` and Fetch `ReadableStream` (works completely offline).
   - In **`[📹 VIDEO]`** mode, the live video is the background and the artificial horizon pitch ladder, roll pointer, boresight reticle, and tapes float as high-contrast HUD symbology on top of the video feed.
   - Tap **`[📹 VIDEO]`** anytime to toggle back to traditional synthetic-vision artificial horizon mode.

7. **HUD Glass Telemetry Overlays**:
   - Left and Right telemetry panels float as semi-transparent glassmorphism HUD overlays (`backdrop-filter: blur(8px)`) directly on top of the live video or horizon.
   - Tap `[👁️ HUD]` anytime to hide the overlays for a 100% unobstructed full-screen video view.

---

## 🚀 How to Run

1. Double-click:
   [`run_cockpit.bat`](file:///c:/Users/User/Documents/SoloWebUtility/cockpit_dashboard/run_cockpit.bat)
2. On your computer or phone browser (landscape):
   ```
   http://localhost:8088
   ```
   *(Or `http://<your-device-ip>:8088` on your phone)*
