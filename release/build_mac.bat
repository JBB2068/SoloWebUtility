@echo off
setlocal
title Build Solo Web Utility v1.2.3 (Mac).zip
python "%~dp0..\mac_installer\build_mac.py"
if errorlevel 1 (
    echo.
    echo [!] BUILD FAILED - see the messages above.
    pause
    exit /b 1
)
pause
