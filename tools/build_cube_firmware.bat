@echo off
setlocal
title Build Solo Cube firmware (4.7.3)
cd /d "%~dp0.."
echo Building ArduCopter 4.7.3 for both Solo Cubes on this PC...
echo (needs the internet; takes several minutes; the Solo is not touched)
echo.
python tools\build_cube_firmware.py --version 4.7.3
if errorlevel 1 (
    echo.
    echo [!] BUILD FAILED - see the messages above.
) else (
    echo.
    echo Finished. The files are in release\cube_builds
)
pause
