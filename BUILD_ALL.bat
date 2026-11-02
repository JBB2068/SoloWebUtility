@echo off
setlocal
rem Runs every release step in order, with one pause at the end:
rem   1. firmware\install_rebaked_image.bat  (only when new image parts are waiting in firmware\)
rem   2. release\build_exe.bat   3. release\build_apk.bat   4. release\build_mac.bat
rem Each script runs in its own window-less cmd, started from inside its own folder (exactly like a
rem double-click), with input from NUL so its own "pause" passes straight through.
rem Stops at the first step that fails. The individual .bat files still work on their own.
set "ROOT=%~dp0"
cd /d "%ROOT%"
title Build all - Solo Web Utility
for /f "delims=" %%v in ('python -c "import json;print(json.load(open('version.json'))['version'])" 2^>NUL') do set "VER=%%v"
if not defined VER (set "STEP=reading version.json (is Python installed?)" & goto fail)
set "DIST=%ROOT%release\dist"
echo ======================================================================
echo   Solo Web Utility v%VER% - install the new image, then build EXE, APK and Mac
echo ======================================================================

echo.
echo ---------- [1/4] Firmware image ----------
if not exist "%ROOT%firmware\3dr-solo.tar.gz.part0" goto noparts
cd /d "%ROOT%firmware"
cmd /c install_rebaked_image.bat <NUL
if errorlevel 1 (set "STEP=installing the new firmware image" & goto fail)
goto checkver
:noparts
echo No new image parts in firmware\ - keeping the current 3dr-solo.tar.gz.
:checkver
cd /d "%ROOT%"
python tools\bump_version.py --check || (set "STEP=the version check" & goto fail)

echo.
echo ---------- [2/4] Windows installer ----------
cd /d "%ROOT%release"
cmd /c build_exe.bat <NUL
if errorlevel 1 (set "STEP=build_exe.bat" & goto fail)
if not exist "%DIST%\Solo Web Utility v%VER% Installer.exe" (set "STEP=build_exe.bat (no .exe was made)" & goto fail)

echo.
echo ---------- [3/4] Android installer ----------
cd /d "%ROOT%release"
cmd /c build_apk.bat <NUL
if errorlevel 1 (set "STEP=build_apk.bat" & goto fail)
if not exist "%DIST%\Solo Web Utility v%VER% Installer.apk" (set "STEP=build_apk.bat (no .apk was made)" & goto fail)

echo.
echo ---------- [4/4] Mac installer ----------
cd /d "%ROOT%release"
cmd /c build_mac.bat <NUL
if errorlevel 1 (set "STEP=build_mac.bat" & goto fail)
if not exist "%DIST%\Solo Web Utility v%VER% (Mac).zip" (set "STEP=build_mac.bat (no .zip was made)" & goto fail)

cd /d "%ROOT%"
echo.
echo ======================================================================
echo   [OK] Everything built for v%VER%. In release\dist:
for %%f in ("%DIST%\Solo Web Utility v%VER% Installer.exe" "%DIST%\Solo Web Utility v%VER% Installer.apk" "%DIST%\Solo Web Utility v%VER% (Mac).zip") do echo      %%~nxf  %%~zf bytes
echo   Next: give Antigravity the release prompt.
echo ======================================================================
pause
exit /b 0

:fail
cd /d "%ROOT%"
echo.
echo ======================================================================
echo   [!] STOPPED at %STEP%. Nothing after it was run.
echo       Scroll up for the error. Fix it, then run BUILD_ALL.bat again.
echo ======================================================================
pause
exit /b 1
