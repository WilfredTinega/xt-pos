@echo off
REM ===== Build everything: compile POS.exe, then the Windows installer =====
cd /d "%~dp0"

echo [1/2] Compiling the app...
call build.bat || exit /b 1

echo.
echo [2/2] Building the installer...
call "installer\build-installer.bat" || exit /b 1

set /p APPVER=<VERSION
echo.
echo ============================================================
echo  All done. (v%APPVER%)
echo    App:        dist\POS.exe
echo    Installer:  installer\Output\XTPOS-Setup-%APPVER%.exe
echo  Ship the installer .exe to any Windows machine.
echo ============================================================
