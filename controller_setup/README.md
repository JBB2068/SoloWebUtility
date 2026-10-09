# 🎮 3DR Solo Controller Button Setup GUI

Standalone visual utility for viewing and reconfiguring **Artoo Controller Physical Buttons** on the 3DR Solo / Open Solo quadcopter (`/usr/bin/extSettings.conf`).

> **Status:** Completely separate from `SoloWebUtility` as requested for testing and review until user approval.

---

## 📖 How `/usr/bin/extSettings.conf` Works

In Open Solo, Artoo controller button presses are transmitted to the Solo companion computer (`10.1.1.10`) and processed by `shotmanager` (`/usr/bin/buttonManager.py` and `/usr/bin/extFunctions.py`).

### 1. Configuration Syntax:
```ini
[extFunctions]
{button},{event} = {shot}, {mode}, {func}
timeOffsetHrs = None
```

- **`{button}`**: The physical button ID on the controller:
  - `4`: **Button A** (Face Left, next to Button B)
  - `5`: **Button B** (Face Left, next to Button A)
  - `6`: **Preset 1** (Top Right camera preset 1)
  - `7`: **Preset 2** (Top Right camera preset 2)
  - `8`: **Camera Trigger** (Top Left shoulder trigger)
  *(Note: The dedicated emergency **Pause** button is located on the Face Right).*
- **`{event}`**: The button gesture duration:
  - `7`: **Short Hold** (`HoldRelease`, held ~0.5s)
  - `5`: **Long Hold** (`LongHold`, held ~2.0s)
  *(Note: Standard single-click taps on A and B are handled directly in `/etc/shotmanager.conf` for default Alt Hold / PosHold).*
- **`{shot}`**: Smart Shot ID (`-1` = None, `0` = Selfie, `1` = Orbit, `2` = Cablecam, `3` = Zipline, `4` = Record, `5` = Follow, `6` = Multipoint, `7` = Panorama, `8` = Rewind, `9` = Transect, `10` = Smart RTL).
- **`{mode}`**: ArduCopter flight mode ID (`-1` = None, `0` = Stabilize, `1` = Acro, `2` = Alt Hold, `3` = Autonomous/Auto, `5` = Loiter, `6` = RTL, `11` = Drift, `13` = Sport, `14` = Flip, `15` = Auto-tune, `16` = PosHold, `17` = Brake, `21` = SmartRTL).
- **`{func}`**: Auxiliary hardware action (`-1` = None, `3` = Gimbal Lock Toggle, `4..6` = Servo 6 Low/Mid/High, `7..9` = Servo 7 Low/Mid/High, `10..12` = Servo 8 Low/Mid/High, `13` = Gripper Close, `14` = Gripper Open).
- **`timeOffsetHrs`**: Time offset in hours (e.g. `None` or `-5`) applied to the GoPro camera clock relative to GPS UTC time.

### 2. Immediate Dynamic Loading:
In `/usr/bin/settings.py`, `readSettingExt()` reads `/usr/bin/extSettings.conf` on every single button press. Therefore, **any changes made via this GUI take effect instantly without restarting services or rebooting the drone!**

---

## 🚀 How to Run the GUI (Standalone Test)

1. Double click:
   [`run_controller_setup.bat`](file:///c:/Users/User/Documents/SoloWebUtility/controller_setup/run_controller_setup.bat)
2. Your browser will automatically open:
   ```
   http://localhost:8089
   ```

### Features included:
- **Interactive Controller Visualizer**: Click physical buttons on the Artoo diagram to jump straight to their configuration.
- **Flight Mode / Smart Shot / Aux Function Selectors**: Clear segmented controls with safety flags (e.g., highlighting modes that should not be used).
- **1-Click Presets**:
  - *Default OpenSolo*
  - *Cinematography & Shots*
  - *Acro & Sport Pilot*
  - *Payload & Gripper / Servo Control*
  - *Survey & SmartRTL*
- **Safety Diff Review**: Shows line-by-line additions and removals before writing to the Solo.
- **Automatic Backups**: Saves timestamped backups on the copter (`/usr/bin/extSettings.conf.backup` & `/usr/bin/extSettings.conf.<timestamp>`) and locally before applying any changes.
- **Raw Config Editor**: Direct INI editor for power users with bi-directional syncing to the GUI.
