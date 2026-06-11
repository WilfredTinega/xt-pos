@echo off
REM ===== Build POS.exe with PyInstaller =====
cd /d "%~dp0"

if not exist ".venv\" (
    echo Creating build virtual environment...
    python -m venv .venv
)
call ".venv\Scripts\activate.bat"

echo Installing build dependencies...
pip install -q -r build-requirements.txt || goto :error

echo Cleaning previous build...
if exist "build\" rmdir /s /q "build"
if exist "dist\"  rmdir /s /q "dist"

echo Compiling POS.exe (single file; this can take a couple of minutes)...
pyinstaller pos.spec --noconfirm || goto :error

REM Stamp the build with the current version so the in-app updater can read it.
REM (Sits next to the single-file dist\POS.exe; the Inno installer bundles both.)
set /p APPVER=<VERSION
copy /y VERSION "dist\version.txt" >nul

echo.
echo ============================================================
echo  Done. Built POS v%APPVER% in:  dist\POS.exe
echo  Next, build the installer:  see installer\build-installer.bat
echo ============================================================
goto :eof

:error
echo.
echo Build failed. See messages above.
exit /b 1
