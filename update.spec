# -*- mode: python ; coding: utf-8 -*-
# Builds the updater: dist\Update.exe (onefile).
#
# Staged flat into setup\build\app\Update.exe (by build-setup.bat) before the
# installer is built, so it ships inside XTPOS-Setup.exe and lands next to
# POS.exe.
#
#   pyinstaller update.spec --noconfirm
#
import sys; sys.path.insert(0, SPECPATH)  # make winversion.py (beside the spec) importable
from winversion import version_info

a = Analysis(
    ["installer_app/update_wizard.py"],
    pathex=["installer_app"],
    binaries=[],
    datas=[("assets/icon.ico", "assets")],
    hiddenimports=["wizard_ui"],
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
    name="Update",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,            # GUI updater
    uac_admin=True,           # needs admin to replace files in Program Files
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
    version=version_info("Update", "XT POS Updater"),
)
