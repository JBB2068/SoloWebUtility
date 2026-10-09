@echo off
title Install Solo Web Utility to 3DR Solo Copter
cd /d "%~dp0"
echo ======================================================================
echo           3DR SOLO WEB UTILITY - INSTALL TO COPTER (10.1.1.10)
echo ======================================================================
echo Ensure your PC is connected to the Solo Wi-Fi (SoloLink_...).
echo.

if exist "%~dp0deploy_to_solo.exe" (
    "%~dp0deploy_to_solo.exe"
) else (
    python install_to_solo.py
)
