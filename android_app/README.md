# SoloLink Viewer Android App

A lightweight, dedicated Android APK (~33 KB) built to automatically connect to 3DR Solo drone Wi-Fi networks and display the web utility console (`http://10.1.1.10`) in full screen without loading an external browser.

## Features

1. **Auto-Discovery & Connection**:
   - Automatically scans nearby Wi-Fi networks for SSIDs matching `sololink_*` (case-insensitive).
   - Automatically connects using passkey: `sololink`.
   - Supports both Android 10+ (`WifiNetworkSpecifier` + `WifiNetworkSuggestion`) and legacy Android (`WifiConfiguration`).
   - Automatically detects if the device is already connected to a `sololink_*` network.

2. **Dedicated Network Socket Binding (`bindProcessToNetwork`)**:
   - Because 3DR Solo drone Wi-Fi does not provide an external internet connection, modern Android devices normally drop the Wi-Fi or route HTTP requests through cellular data.
   - SoloLink Viewer explicitly binds the application process to the Solo Wi-Fi network interface, ensuring all WebView and socket requests reliably reach `10.1.1.10`.

3. **Fullscreen In-App WebView**:
   - Immersive sticky full screen (hides status bar and navigation bar).
   - Screen wake lock enabled (keeps screen awake while flying / working).
   - In-app WebView client: keeps all links inside the app without launching external browser intents.
   - Cleartext HTTP traffic explicitly enabled for `10.1.1.10` via network security configuration.
   - Hardware accelerated with full JavaScript, DOM storage, and HTML5 video support.

4. **Boot Handling & Auto-Retry**:
   - If Solo is still booting up Linux or starting the web server when Wi-Fi connects, the app detects connection errors and automatically retries every 3 seconds until `http://10.1.1.10` is ready.

## APK Location

- Built APK: `sololink_viewer.apk` (in the project root and `android_app/dist/sololink_viewer.apk`)
- Package Name: `com.sololink.viewer`
- Size: ~33.4 KB

## Installation

### Via ADB (USB Cable or Wi-Fi):
Run `install_apk.bat` or execute:
```bash
adb install -r sololink_viewer.apk
```

### Manual Install:
Transfer `sololink_viewer.apk` to your Android device via USB, email, Google Drive, or local file manager, and tap to install (enable "Install unknown apps" if prompted).

## Rebuilding

To rebuild the APK at any time, run:
```bash
build.bat
```
or PowerShell:
```powershell
.\build.ps1
```
