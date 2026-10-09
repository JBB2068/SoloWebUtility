@echo off
title 3DR Solo Web Utility Local Server
echo ======================================================================
echo                 3DR SOLO WEB UTILITY - LOCAL SERVER
echo ======================================================================
echo Starting local web server on port 8080...
echo.
echo Open your browser and go to:
echo   http://localhost:8080
echo.
echo Default Password: sololink
echo ======================================================================
python solo_web_app.py 8080
pause
