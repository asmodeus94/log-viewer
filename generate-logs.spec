# -*- mode: python ; coding: utf-8 -*-
"""
Konfiguracja PyInstallera dla aplikacji pomocniczej Log Generator (dev-scripts/generate_logs.py).
Zoptymalizowana pod kątem minimalnego rozmiaru paczki binarnej.
"""

import sys
from pathlib import Path

repo_root = Path(SPECPATH).resolve()

excluded_modules = [
    # Nieużywane moduły spoza rdzenia Pythona
    "tkinter",
    "unittest",
    "pydoc",
    "test",
    "curses",
    "xmlrpc",
    # Nieużywane moduły Qt6 / PySide6
    "PySide6.QtNetwork",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtPdf",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtOpenGL",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtXml",
    "PySide6.QtBluetooth",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.Qt3DCore",
    "PySide6.QtCharts",
    "PySide6.QtSpatialAudio",
]

excluded_binaries = {
    # Ciężki rasteryzator programowy Mesa
    "opengl32sw",
    # Zbędne biblioteki Qt6 powiązane z wtyczkami
    "Qt6VirtualKeyboard",
    "Qt6Quick",
    "Qt6Qml",
    "Qt6QmlMeta",
    "Qt6QmlModels",
    "Qt6QmlWorkerScript",
    "Qt6Pdf",
    "Qt6Network",
    "Qt6OpenGL",
    # Zbędne wtyczki Qt6
    "qtvirtualkeyboardplugin",
    "qpdf",
    "qnetworklistmanager",
    "qopensslbackend",
    "qschannelbackend",
    "qcertonlybackend",
    # Nieużywane wtyczki formatów graficznych
    "qtiff",
    "qwebp",
    "qwbmp",
    "qtga",
    "qicns",
}

script_path = str(repo_root / "dev-scripts" / "generate_logs.py")
assets_path = str(repo_root / "assets")

a = Analysis(
    [script_path],
    pathex=[str(repo_root)],
    binaries=[],
    datas=[(assets_path, "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
    optimize=1,
)

# Filtrowanie zbędnych bibliotek binarnych i wtyczek C++
a.binaries = [
    x for x in a.binaries if not any(b.lower() in Path(x[0]).name.lower() for b in excluded_binaries)
]

# Filtrowanie zbędnych plików tłumaczeń Qt
a.datas = [
    x
    for x in a.datas
    if "translations" not in x[0].lower()
    or "qtbase_pl" in x[0].lower()
    or "qtbase_en" in x[0].lower()
]

pyz = PYZ(a.pure)

is_mac = sys.platform == "darwin"
icon_path = repo_root / "assets" / ("icon.icns" if is_mac else "icon.ico")
icon_file = [str(icon_path)] if icon_path.exists() else None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="generate-logs",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

if is_mac:
    app = BUNDLE(
        exe,
        name="generate-logs.app",
        icon=str(icon_path) if icon_path.exists() else None,
        bundle_identifier="com.logviewer.generator",
        info_plist={
            "CFBundleShortVersionString": "1.0.0",
            "NSHighResolutionCapable": "True",
        },
    )
