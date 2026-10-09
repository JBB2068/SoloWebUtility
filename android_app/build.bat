@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
if %ERRORLEVEL% neq 0 (
    echo.
    echo BUILD FAILED!
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo Build completed successfully.
pause
