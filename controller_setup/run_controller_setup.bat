@echo off
title 3DR Solo Controller Button Setup GUI
echo ======================================================================
echo           3DR SOLO CONTROLLER SETUP GUI (STANDALONE TEST)
echo ======================================================================
echo.
echo Launching Controller Setup server on port 8089...
echo Target configuration: /usr/bin/extSettings.conf (Solo 10.1.1.10)
echo.

start "" "http://localhost:8089"
python "%~dp0controller_server.py" 8089

if errorlevel 1 (
    echo.
    echo Server stopped or python was not found in PATH.
    pause
)
