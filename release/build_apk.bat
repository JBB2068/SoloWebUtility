@echo off
setlocal
title Build Solo Web Utility v1.2.3 Installer.apk
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\android_installer\build.ps1"
if errorlevel 1 (
    echo.
    echo [!] BUILD FAILED - see the messages above.
    pause
    exit /b 1
)
pause
