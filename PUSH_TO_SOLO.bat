@echo off
title Push changed files to the Solo
cd /d "%~dp0"
echo Connect this PC to the SoloLink Wi-Fi first.
echo.
python tools\push_to_solo.py %*
echo.
pause
