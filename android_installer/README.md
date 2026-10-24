# Solo Web Utility v1.2.0 — Android installer

Installs Solo Web Utility on a 3DR Solo from a phone, over SoloLink Wi-Fi — the same
files, folders and start-up entry as the Windows installer (`Solo Web Utility v1.2.0 Installer.exe`).
Installer only: afterwards the utility is used in a browser at http://10.1.1.10 and this app
can be deleted. (The separate lightweight SoloLink Viewer app is in `../android_app`.)

- `src/.../SoloInstaller.java` — the installation steps (port of `deploy_to_drone.py`)
- `src/.../MainActivity.java` — screen and SoloLink network binding
- `stage_assets.py` — packs the files listed in `../release/Solo Web Utility v1.2.0.spec`
  plus `manifest.txt` (size + MD5 of each file)
- `libs/jsch-0.2.20-android.jar` — JSch SSH/SFTP library (BSD, see `libs/JSch-LICENSE.txt`),
  compiled for Android from github.com/mwiede/jsch tag jsch-0.2.20 without its optional
  add-ons (Bouncy Castle, JNA, log4j/slf4j, junixsocket)

Build: run `..\release\build_apk.bat` → `..\release\dist\Solo Web Utility v1.2.0 Installer.apk`
