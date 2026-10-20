@echo off
title Deploy Solo Web Utility to 3DR Solo Copter
cd /d "%~dp0"
echo ======================================================================
echo           3DR SOLO WEB UTILITY - DEPLOY TO COPTER (10.1.1.10)
echo ======================================================================
echo Ensure your PC is connected to the Solo Wi-Fi (SoloLink_...).
echo (Requires: pip install paramiko  -- on this PC only)
echo.
python deploy_to_drone.py
pause
