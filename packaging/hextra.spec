# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Hextra Windows executable.

Build it with::

    python -m PyInstaller --noconfirm --clean packaging/hextra.spec

Output:
    dist/Hextra.exe                 one-file build (default)
    dist/Hextra/Hextra.exe + deps   set HEXTRA_ONEFILE=0 for a folder build

Notes:
* ``uac_admin`` is deliberately False. Hextra elevates itself at runtime
  (``hextra.legacy._ensure_elevated_start``), so the shell still comes up when
  the UAC prompt is dismissed, and ``_resolve_frozen_exe_path`` already knows
  how to relaunch a frozen exe.
* The Windows version resource is generated from ``VERSION`` in
  ``hextra/legacy.py`` at build time, so the exe properties can never drift
  from the app.
* Only QtCore/QtGui/QtWidgets are used, so the other Qt modules are excluded to
  keep the bundle small.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (PyInstaller injects SPECPATH)
for entry in (str(REPO_ROOT), str(REPO_ROOT / "packaging")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from build_info import app_version, render_version_info  # noqa: E402

VERSION = app_version()
ICON = REPO_ROOT / "assets" / "hextra.ico"
ONEFILE = os.environ.get("HEXTRA_ONEFILE", "1").strip().lower() not in {"0", "false", "no", "off"}

# Regenerated on every build; kept out of Git.
VERSION_FILE = REPO_ROOT / "packaging" / "version_info.txt"
VERSION_FILE.write_text(render_version_info(VERSION), encoding="utf-8")

# hextra.api/auth/ops/ui/workers are compatibility shims that nothing imports at
# runtime, so collect them explicitly to keep the frozen package complete.
hiddenimports = [
    "winreg",
    "ctypes.wintypes",
    "csv",
    "shlex",
    "hashlib",
    "base64",
    "importlib.util",
    "psutil",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
]
try:
    from PyInstaller.utils.hooks import collect_submodules

    for package in ("hextra", "replica_ui"):
        hiddenimports += collect_submodules(package)
except Exception as exc:  # pragma: no cover - keep the build going
    print(f"[hextra.spec] WARNING: could not collect submodules: {exc}")

EXCLUDED_QT = [
    "QtNetwork",
    "QtQml",
    "QtQuick",
    "QtQuickWidgets",
    "QtWebEngineCore",
    "QtWebEngineWidgets",
    "QtWebSockets",
    "QtWebChannel",
    "QtBluetooth",
    "QtNfc",
    "QtPositioning",
    "QtSensors",
    "QtSerialPort",
    "QtMultimedia",
    "QtMultimediaWidgets",
    "QtTest",
    "QtSql",
    "QtPdf",
    "QtCharts",
    "QtDataVisualization",
    "Qt3DCore",
    "QtDesigner",
    "QtHelp",
    "QtRemoteObjects",
    "QtScxml",
    "QtStateMachine",
    "QtTextToSpeech",
    "QtSerialBus",
    "QtSpatialAudio",
    "QtHttpServer",
    "QtGraphs",
]

a = Analysis(
    [str(REPO_ROOT / "Hexa.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc_data"] + [f"PyQt6.{name}" for name in EXCLUDED_QT],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe_kwargs = dict(
    name="Hextra",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
)
if ICON.is_file():
    exe_kwargs["icon"] = str(ICON)
else:
    print(f"[hextra.spec] WARNING: icon not found at {ICON}")
if sys.platform == "win32":
    exe_kwargs["version"] = str(VERSION_FILE)

if ONEFILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **exe_kwargs)
    outputs = [REPO_ROOT / "dist" / "Hextra.exe"]
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **exe_kwargs)
    COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="Hextra")
    outputs = [REPO_ROOT / "dist" / "Hextra" / "Hextra.exe"]

print(f"[hextra.spec] version={VERSION} onefile={ONEFILE} platform={sys.platform}")
for path in outputs:
    print(f"[hextra.spec] expected output: {path}")
