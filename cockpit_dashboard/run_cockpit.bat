@echo off
title 3DR Solo Cockpit Flight Deck
echo ======================================================================
echo             3DR SOLO COCKPIT DASHBOARD & TELEMETRY DECK
echo ======================================================================
echo.
echo Launching standalone flight telemetry server on port 8088...
echo.

start "" "http://localhost:8088"
python "%~dp0cockpit_server.py" 8088

if errorlevel 1 (
    echo.
    echo Server stopped or python was not found in PATH.
    pause
)
