@echo off
REM ===== Build the one-click installer: setup\XTPOS-Setup.exe =====
REM No third-party tools needed - pure PyInstaller.
cd /d "%~dp0"

if not exist ".venv\" (
    echo Creating build virtual environment...
    python -m venv .venv
)
call ".venv\Scripts\activate.bat"

echo Installing build dependencies...
pip install -q -r build-requirements.txt || goto :error

REM ===== Optional code signing =====
REM The "Publisher" line in the Windows UAC / SmartScreen popup comes ONLY from
REM an Authenticode signature -- it stays "Unknown" until the exes are signed
REM with a certificate issued to "Xonal Tech". To enable signing, set ONE of:
REM   set SIGN_THUMBPRINT=<sha1 thumbprint of an installed cert>
REM   set SIGN_PFX=<path to .pfx>   (and optionally  set SIGN_PFX_PASS=<password>)
REM Leave both unset to build unsigned (popup shows "Unknown publisher").
set "DO_SIGN="
if defined SIGN_THUMBPRINT set "DO_SIGN=1"
if defined SIGN_PFX set "DO_SIGN=1"
if defined DO_SIGN (
    where signtool >nul 2>nul || (
        echo ERROR: signing requested but signtool.exe is not on PATH.
        echo Install the Windows SDK or open a "Developer Command Prompt".
        goto :error
    )
    echo Code signing ENABLED ^(publisher will read "Xonal Tech"^).
) else (
    echo Code signing disabled - installer popup will show "Unknown publisher".
)

REM All output lives under setup\. Intermediates go under setup\build\ and are
REM deleted at the end, so the finished setup\ folder is clean: it holds just
REM the single shippable XTPOS-Setup.exe.
REM   setup\build\_pyi\     PyInstaller work files
REM   setup\build\          the three single-file exes as they are compiled
REM   setup\build\app\      the flat payload the installer bundles
REM   setup\XTPOS-Setup.exe   the shippable installer (only survivor)
set "OUT=setup"
set "WORK=setup\build"
set "PYI=setup\build\_pyi"
set "STAGE=setup\build\app"

echo Cleaning previous build output...
REM rmdir handles the read-only files pip drops in *.dist-info\licenses, which
REM trip up PyInstaller's own cleanup.
if exist "%OUT%\" rmdir /s /q "%OUT%"
if exist "build\" rmdir /s /q "build"
if exist "dist\" rmdir /s /q "dist"

echo [1/4] Compiling the POS app (POS.exe, single file)...
pyinstaller pos.spec --noconfirm --distpath "%WORK%" --workpath "%PYI%" || goto :error

echo [2/4] Building the uninstaller...
pyinstaller uninstall.spec --noconfirm --distpath "%WORK%" --workpath "%PYI%" || goto :error

echo [3/4] Building the updater...
pyinstaller update.spec --noconfirm --distpath "%WORK%" --workpath "%PYI%" || goto :error

echo Staging the three executables into a flat payload (%STAGE%)...
mkdir "%STAGE%"
copy /y "%WORK%\POS.exe"       "%STAGE%\POS.exe"       >nul || goto :error
copy /y "%WORK%\Uninstall.exe" "%STAGE%\Uninstall.exe" >nul || goto :error
copy /y "%WORK%\Update.exe"    "%STAGE%\Update.exe"    >nul || goto :error

REM Sign the inner exes before they get bundled, so Update.exe / Uninstall.exe
REM (both elevate) also show "Xonal Tech" when run after install.
if defined DO_SIGN (
    echo Signing the three executables...
    call :sign "%STAGE%\POS.exe"       || goto :error
    call :sign "%STAGE%\Uninstall.exe" || goto :error
    call :sign "%STAGE%\Update.exe"    || goto :error
)

echo [4/4] Building the installer (bundling the flat payload)...
pyinstaller setup.spec --noconfirm --distpath "%OUT%" --workpath "%PYI%" || goto :error

if defined DO_SIGN (
    echo Signing the installer...
    call :sign "%OUT%\XTPOS-Setup.exe" || goto :error
)

REM ===== Update package (for the in-app updater) =====
REM Produces release\XTPOS-<version>.zip + release\manifest.json from the same
REM (signed) exes the installer ships. Host both at UPDATE_BASE_URL and point
REM each install's .env UPDATE_URL at the manifest. Set the host once:
REM   set UPDATE_BASE_URL=https://downloads.example.com/xtpos
if not defined UPDATE_BASE_URL set "UPDATE_BASE_URL="
echo Packaging the update (zip + manifest) into release\ ...
python make_update.py --source "%STAGE%" --base-url "%UPDATE_BASE_URL%" || goto :error

echo Cleaning up intermediates so setup\ holds only the installer...
if exist "%WORK%\" rmdir /s /q "%WORK%"

echo.
echo ============================================================
echo  Done.  Ship this single file:
echo     setup\XTPOS-Setup.exe
echo  Run it on any 64-bit Windows PC - it downloads MariaDB,
echo  asks for the admin password, installs everything, and starts.
echo  The installed app folder is flat (POS.exe, Update.exe,
echo  Uninstall.exe) - no nested _internal directory.
echo  Once installed it appears in Windows "Apps and features" and
echo  can be updated (Check for Updates) or uninstalled from there.
echo.
echo  Update package for existing installs (host these two files):
echo     release\XTPOS-^<version^>.zip   +   release\manifest.json
echo ============================================================
goto :eof

:error
echo.
echo Build failed. See messages above.
exit /b 1

REM ===== signtool wrapper: signs %1 with a timestamp, SHA-256 =====
:sign
set "TS=http://timestamp.digicert.com"
if defined SIGN_TIMESTAMP_URL set "TS=%SIGN_TIMESTAMP_URL%"
if defined SIGN_THUMBPRINT (
    signtool sign /sha1 %SIGN_THUMBPRINT% /fd SHA256 /tr "%TS%" /td SHA256 %1 || exit /b 1
) else (
    if defined SIGN_PFX_PASS (
        signtool sign /f "%SIGN_PFX%" /p "%SIGN_PFX_PASS%" /fd SHA256 /tr "%TS%" /td SHA256 %1 || exit /b 1
    ) else (
        signtool sign /f "%SIGN_PFX%" /fd SHA256 /tr "%TS%" /td SHA256 %1 || exit /b 1
    )
)
exit /b 0
