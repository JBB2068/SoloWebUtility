@echo off
setlocal
title Build Solo Web Utility v1.2.1 Installer.exe
cd /d "%~dp0"
echo ======================================================================
echo   Building "Solo Web Utility v1.2.1 Installer.exe"
echo   (needs internet the first time, to install PyInstaller and paramiko)
echo ======================================================================
python -c "import PyInstaller, paramiko" 2>NUL
if errorlevel 1 (
    echo [*] Installing PyInstaller and paramiko...
    python -m pip install --upgrade pyinstaller paramiko || goto fail
)
echo [*] Making sure the firmware image holds this version of Solo Web Utility...
python "%~dp0..\bake_into_firmware.py" --auto || goto bakefail
python -m PyInstaller --noconfirm --clean --distpath "%~dp0dist" --workpath "%~dp0build" "Solo Web Utility v1.2.1.spec" || goto fail
if not exist "dist\Solo Web Utility v1.2.1 Installer.exe" goto fail
rmdir /s /q build 2>NUL
echo.
echo [OK] Built: %~dp0dist\Solo Web Utility v1.2.1 Installer.exe
pause
exit /b 0
:bakefail
echo.
echo [!] The firmware image could not be rebuilt with the current Solo Web Utility,
echo     so the build was stopped (it never ships an image with an older utility).
echo     Baking needs WSL with squashfs-tools. One-time setup, in an admin PowerShell:
echo         wsl --install
echo         wsl -u root -e sh -c "apt-get update && apt-get install -y squashfs-tools"
pause
exit /b 1
:fail
echo.
echo [!] BUILD FAILED - see the messages above.
pause
exit /b 1
