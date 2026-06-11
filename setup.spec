# -*- mode: python ; coding: utf-8 -*-
# Builds the self-contained installer: setup\XTPOS-Setup.exe (onefile).
# It bundles the flat staged payload (setup\build\app\: POS.exe + Update.exe +
# Uninstall.exe — all single-file) as "app_payload".
#
#   Build everything via build-setup.bat, which compiles the three exes, stages
#   them flat into setup\build\app, then runs this spec.
#
import os
import sys; sys.path.insert(0, SPECPATH)  # make winversion.py (beside the spec) importable

from winversion import version_info

PAYLOAD = os.path.join("setup", "build", "app")
if not os.path.isdir(PAYLOAD):
    raise SystemExit(
        "setup/build/app not found. Run build-setup.bat — it compiles the "
        "three exes and stages them flat into setup/build/app first.")

# Bundle every file under the payload as app_payload/... (a flat set of exes).
datas = []
for rootdir, _dirs, files in os.walk(PAYLOAD):
    for f in files:
        full = os.path.join(rootdir, f)
        rel = os.path.relpath(rootdir, PAYLOAD)
        dest = "app_payload" if rel == "." else os.path.join("app_payload", rel)
        datas.append((full, dest))

# Bundle the icon so the wizard window can use it at runtime.
datas.append(("assets/icon.ico", "assets"))
# Bundle the VERSION file so the installer stamps the right version.
datas.append(("VERSION", "."))

a = Analysis(
    ["installer_app/setup_wizard.py"],
    pathex=["installer_app"],
    binaries=[],
    datas=datas,
    hiddenimports=["wizard_ui", "pymysql"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="XTPOS-Setup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,            # GUI wizard
    uac_admin=True,           # request elevation (needed to install MariaDB)
    disable_windowed_traceback=False,
    icon="assets/icon.ico",   # brands XTPOS-Setup.exe
    version=version_info("XTPOS-Setup", "XT POS Installer"),
)
