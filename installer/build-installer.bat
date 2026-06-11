@echo off
REM ===== Compile the Windows installer with Inno Setup =====
REM Requires Inno Setup 6 (https://jrsoftware.org/isdl.php) -> provides ISCC.exe
cd /d "%~dp0"

if not exist "..\dist\POS.exe" (
    echo ERROR: ..\dist\POS.exe not found.
    echo Run build.bat in the project root first to compile the app.
    exit /b 1
)

REM Locate the Inno Setup compiler.
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    where ISCC.exe >nul 2>nul && set "ISCC=ISCC.exe"
)
if not exist "%ISCC%" (
    if not "%ISCC%"=="ISCC.exe" (
        echo ERROR: Inno Setup compiler ^(ISCC.exe^) not found.
        echo Install Inno Setup 6 from https://jrsoftware.org/isdl.php
        exit /b 1
    )
)

echo Building installer...
"%ISCC%" pos.iss || exit /b 1

echo.
echo ============================================================
echo  Installer created in:  installer\Output\
echo ============================================================
