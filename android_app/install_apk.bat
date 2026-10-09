@echo off
setlocal
cd /d "%~dp0"
echo ==========================================
echo  Installing SoloLink Viewer on Device
echo ==========================================
if exist "dist\sololink_viewer.apk" (
    adb install -r dist\sololink_viewer.apk
) else if exist "sololink_viewer.apk" (
    adb install -r sololink_viewer.apk
) else (
    adb install -r ..\sololink_viewer.apk
)
if %ERRORLEVEL% neq 0 (
    echo.
    echo Installation failed. Ensure your Android device has USB debugging enabled and is connected.
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo Launching SoloLink Viewer...
adb shell am start -n com.sololink.viewer/.MainActivity
pause
