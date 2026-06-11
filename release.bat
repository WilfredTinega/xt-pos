@echo off
REM ===== Cut a new release: bump the version, then rebuild app + installer =====
REM
REM   release.bat            bump patch  (1.1.0 -> 1.1.1) then build
REM   release.bat minor      bump minor  (1.1.0 -> 1.2.0) then build
REM   release.bat major      bump major  (1.1.0 -> 2.0.0) then build
REM   release.bat 1.4.2      set explicit version then build
REM
REM Tip: edit CHANGELOG.md to describe the changes before (or after) running.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

set "BUMP=%~1"
if "%BUMP%"=="" set "BUMP=patch"

echo Bumping version (%BUMP%)...
%PY% bump_version.py %BUMP% || exit /b 1

echo.
echo Rebuilding app + installer...
call build-all.bat || exit /b 1
