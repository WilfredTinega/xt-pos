# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec to compile the POS into a single dist\POS.exe (onefile),
# so the installed app folder stays flat — no nested _internal directory.
#
#   pyinstaller pos.spec --noconfirm
#
from PyInstaller.utils.hooks import collect_submodules, collect_all
import sys; sys.path.insert(0, SPECPATH)  # make winversion.py (beside the spec) importable
from winversion import version_info

# pywebview + its .NET/WebView2 backend (pythonnet/clr_loader) need their data
# and submodules collected so the native window works inside the bundle.
webview_datas, webview_binaries, webview_hidden = collect_all("webview")
clr_datas, clr_binaries, clr_hidden = collect_all("clr_loader")

hiddenimports = (
    ["pymysql", "clr", "pythonnet"]
    + collect_submodules("waitress")
    + collect_submodules("sqlalchemy.dialects.mysql")
    + webview_hidden
    + clr_hidden
)

datas = [
    ("templates", "templates"),
    ("static", "static"),
    ("assets/icon.ico", "assets"),   # used as the native window/taskbar icon
    ("VERSION", "."),                # in-app updater reads this as a fallback
] + webview_datas + clr_datas

extra_binaries = webview_binaries + clr_binaries

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=extra_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Onefile: fold the binaries and data straight into the EXE (no COLLECT step),
# so the build produces a single dist\POS.exe with everything inside it.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="POS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,         # windowed app — UI is the native window, no console
    disable_windowed_traceback=False,
    icon="assets/icon.ico",   # brands POS.exe and the app window/taskbar
    version=version_info("POS", "XT POS"),  # CompanyName = Xonal Tech
)
