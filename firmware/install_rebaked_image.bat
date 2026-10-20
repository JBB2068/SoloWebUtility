@echo off
rem Installs the rebuilt golden image (no stock ArduCopter 4.0.1). Run the copy that is INSIDE the firmware folder.
pushd "%~dp0"
echo Working in: %CD%
if not exist "3dr-solo.tar.gz.part0" (echo [!] The image parts are not next to this script. Run the copy in SoloWebUtility2\firmware instead. & goto fail)
if not exist "3dr-solo.tar.gz.part1" (echo [!] 3dr-solo.tar.gz.part1 is missing. & goto fail)
if not exist "3dr-solo.tar.gz.part2" (echo [!] 3dr-solo.tar.gz.part2 is missing. & goto fail)
if not exist "3dr-solo.tar.gz.md5.new" (echo [!] 3dr-solo.tar.gz.md5.new is missing. & goto fail)
if not exist "3dr-solo.tar.gz.baked.new" (echo [!] 3dr-solo.tar.gz.baked.new is missing. & goto fail)
echo [1/3] Joining the parts...
copy /b "3dr-solo.tar.gz.part0"+"3dr-solo.tar.gz.part1"+"3dr-solo.tar.gz.part2" "3dr-solo.tar.gz.new" >NUL
if errorlevel 1 (echo [!] Joining failed. & goto fail)
echo [2/3] Checking the MD5...
certutil -hashfile "3dr-solo.tar.gz.new" MD5 | findstr /i "f022fcf462223897bd60d5b495243682" >NUL
if errorlevel 1 (echo [!] MD5 does not match f022fcf462223897bd60d5b495243682. & goto fail)
echo [3/3] Installing the image, its .md5 and its bake record...
move /y "3dr-solo.tar.gz.new" "3dr-solo.tar.gz" >NUL || (echo [!] Could not replace 3dr-solo.tar.gz - is it open in another program? & goto fail)
move /y "3dr-solo.tar.gz.md5.new" "3dr-solo.tar.gz.md5" >NUL
move /y "3dr-solo.tar.gz.baked.new" "3dr-solo.tar.gz.baked" >NUL
del "3dr-solo.tar.gz.part0" "3dr-solo.tar.gz.part1" "3dr-solo.tar.gz.part2"
echo [OK] 3dr-solo.tar.gz rebuilt, MD5 f022fcf462223897bd60d5b495243682
echo      You can now delete install_rebaked_image.bat and run release\build_exe.bat.
popd
pause
exit /b 0
:fail
del "3dr-solo.tar.gz.new" 2>NUL
echo [!] 3dr-solo.tar.gz was not changed.
popd
pause
exit /b 1
