# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec for Model Hub.

Produces a single portable .exe with embedded frontend.

Usage:
    pyinstaller build.spec

Or with UPX compression:
    pyinstaller build.spec --upx-dir=C:/path/to/upx
"""

import sys
import os
from pathlib import Path

block_cipher = None

# -- Collect all backend Python files --
backend_src = [
    ("backend", "backend"),
    ("backend/cookbook", "backend/cookbook"),
    ("backend/cookbook/data", "backend/cookbook/data"),
]

# -- Collect frontend static files --
frontend_src = [
    ("frontend/index.html", "frontend"),
    ("frontend/style.css", "frontend"),
    ("frontend/script.js", "frontend"),
]

datas = []
for src, dst in backend_src:
    p = Path(__file__).parent / src
    if p.is_dir():
        for f in p.rglob("*"):
            if f.suffix in (".py", ".json", ".txt") and "__pycache__" not in f.parts:
                datas.append((str(f), dst))
    elif p.is_file():
        datas.append((str(p), dst))

for src, dst in frontend_src:
    p = Path(__file__).parent / src
    datas.append((str(p), dst))

# Also include requirements.txt and version info
for extra in ["requirements.txt", "CHANGELOG.md", "LICENSE"]:
    p = Path(__file__).parent / extra
    if p.exists():
        datas.append((str(p), "."))

a = Analysis(
    ["server.py"],
    pathex=[Path(__file__).parent],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "flask",
        "json",
        "os",
        "platform",
        "subprocess",
        "threading",
        "time",
        "webbrowser",
        "urllib",
        "shutil",
        "pathlib",
        "dataclasses",
        "re",
        "typing",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pytest",
        "numpy",
        "matplotlib",
        "PIL",
        "cv2",
        "pandas",
        "scipy",
    ],
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
    name="model-hub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_travis=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

# Also create a console-enabled exe for debugging
exe_debug = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="model-hub-console",
    debug=True,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_travis=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
