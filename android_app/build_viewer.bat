@echo off
rem Builds the SoloLink Viewer app (sololink_viewer.apk). See build_viewer.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_viewer.ps1"
pause
