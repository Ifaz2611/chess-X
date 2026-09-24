# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Chess-X – one-file executable for Windows & Linux.

Usage:
  pip install pyinstaller
  pyinstaller chess-x.spec                # onefile
  pyinstaller --onedir chess-x.spec       # for faster startup / debugging

On macOS: pyinstaller chess-x.spec (produces .app + onefile binary)
On Linux Wayland: build with QT_QPA_PLATFORM=xcb for overlay compat

Notes:
- Hidden imports: multiprocess, selenium, PyQt6, etc.
- Datas: src/assets
- Binaries: stockfish NOT bundled – user selects via GUI
"""

import sys
from pathlib import Path

block_cipher = None

# Collect PyQt6 and selenium hidden imports
hiddenimports = [
    "multiprocess",
    "multiprocess.pool",
    "multiprocess.queues",
    "selenium",
    "selenium.webdriver",
    "selenium.webdriver.chrome.service",
    "selenium.webdriver.firefox.service",
    "selenium.webdriver.edge.service",
    "selenium.webdriver.remote.webdriver",
    "webdriver_manager.chrome",
    "webdriver_manager.firefox",
    "webdriver_manager.microsoft",
    "PyQt6.sip",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "chess",
    "stockfish",
    "keyboard",
    "pyautogui",
    "pynput",
    "pynput.mouse",
    "pynput.keyboard",
    "platform_info",
    "input_backend",
    "browser_factory",
    "browser_session",
    "utilities",
    "protocol",
]

# Data files (assets)
datas = [
    ("src/assets", "assets"),
    ("src/assets", "src/assets"),
]

# Analysis
a = Analysis(
    ["src/gui.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter.test", "sqlite3"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="chess-x",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI app – no console window on Windows
    disable_windowed_traceback=False,
    argv_emulation=True if sys.platform == "darwin" else False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="src/assets/pawn_32x32.png" if Path("src/assets/pawn_32x32.png").exists() else None,
)

# macOS bundle (optional)
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="Chess-X.app",
        icon="src/assets/pawn_32x32.png" if Path("src/assets/pawn_32x32.png").exists() else None,
        bundle_identifier="com.chessx.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "CFBundleShortVersionString": "2.1.0",
            "NSAppleEventsUsageDescription": "Chess-X needs accessibility access for hotkeys.",
            "NSScreenCaptureUsageDescription": "Chess-X overlay needs screen recording permission.",
        },
    )
