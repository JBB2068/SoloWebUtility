@echo off
rem Rebuilds 3dr-solo.tar.gz from the 3 delivered parts and checks its MD5.
set "FW=C:\Users\User\Documents\SoloWebUtility2\firmware"
if exist "%~dp03dr-solo.tar.gz.part0" set "FW=%~dp0"
cd /d "%FW%" || goto fail
copy /b 3dr-solo.tar.gz.part0+3dr-solo.tar.gz.part1+3dr-solo.tar.gz.part2 3dr-solo.tar.gz.new >NUL || goto fail
certutil -hashfile 3dr-solo.tar.gz.new MD5 | findstr /i "992805c8119318108306392b831b3a8b" >NUL || goto fail
move /y 3dr-solo.tar.gz.new 3dr-solo.tar.gz >NUL || goto fail
del 3dr-solo.tar.gz.part0 3dr-solo.tar.gz.part1 3dr-solo.tar.gz.part2
rem 4.7.1 now lives in previous\ (kept for reverting); remove the old top-level copies
if exist previous\arducopter_CubeSolo_4.7.1.apj del arducopter_CubeSolo_4.7.1.apj 2>NUL
if exist previous\arducopter_CubeGreen_4.7.1.apj del arducopter_CubeGreen_4.7.1.apj 2>NUL
echo [OK] 3dr-solo.tar.gz rebuilt, MD5 992805c8119318108306392b831b3a8b
pause
exit /b 0
:fail
del 3dr-solo.tar.gz.new 2>NUL
echo [!] Join or MD5 check FAILED - 3dr-solo.tar.gz was not changed.
pause
exit /b 1
