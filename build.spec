# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec for LAC.

Produces a single portable .exe with embedded frontend.

Usage:
    pyinstaller build.spec
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None
PROJECT_ROOT = Path.cwd()

# `cryptography` is imported ONLY by the separately-delivered Pro plugin
# (lac_pro.grant_crypto), never by model-hub's own code — so PyInstaller's import
# graph from server.py cannot discover it, and putting it in requirements.txt is
# not enough. Collect it explicitly so the shipped exe can decrypt the Pro license
# grant at runtime, INCLUDING the native cryptography.hazmat.bindings._rust
# extension. Without this the exe silently omits it and Pro activation fails.
crypto_datas, crypto_binaries, crypto_hidden = collect_all("cryptography")

# pywebview + its WebView2 (EdgeChromium) backend are imported only at runtime
# by backend/desktop.py; PyInstaller's graph from server.py cannot fully
# discover the native loader/assemblies, so collect them explicitly. Same class
# of ship-blocker as the cryptography omission above.
webview_datas, webview_binaries, webview_hidden = collect_all("webview")

# -- Collect backend Python files --
backend_dirs = [
    "backend",
    "backend/cookbook",
    "backend/cookbook/data",
]

# -- Collect frontend static files --
# The React app (web/dist) is what api.py serves when present; the legacy
# frontend/ is only its fallback. BOTH are bundled so the exe always has a UI,
# but web/dist MUST be built (npm run build) before running PyInstaller or the
# shipped exe silently falls back to the legacy UI.
frontend_dir = PROJECT_ROOT / "frontend"
webdist_dir = PROJECT_ROOT / "web" / "dist"
frontend_exts = (".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".svg",
                 ".gif", ".ico", ".woff", ".woff2", ".ttf", ".json")

datas = []
for d in backend_dirs:
    p = PROJECT_ROOT / d
    if p.is_dir():
        for f in p.rglob("*"):
            if f.suffix in (".py", ".json", ".txt") and "__pycache__" not in f.parts:
                datas.append((str(f), d))

if frontend_dir.is_dir():
    for f in frontend_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() in frontend_exts and "__pycache__" not in f.parts:
            datas.append((str(f), str(f.parent.relative_to(PROJECT_ROOT))))

if not (webdist_dir / "index.html").exists():
    raise SystemExit("web/dist missing — run `npm run build` in web/ before PyInstaller "
                     "(otherwise the exe ships the legacy UI)")
for f in webdist_dir.rglob("*"):
    if f.is_file():
        datas.append((str(f), str(f.parent.relative_to(PROJECT_ROOT))))

for extra in ["requirements.txt", "CHANGELOG.md", "LICENSE"]:
    p = PROJECT_ROOT / extra
    if p.exists():
        datas.append((str(p), "."))

a = Analysis(
    ["server.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=crypto_binaries + webview_binaries,
    datas=datas + crypto_datas + webview_datas,
    hiddenimports=[
        "cli",  # CLI subcommand dispatch (lac.exe pro activate / scan) — see server._is_cli_invocation
        "flask",
        "json", "os", "platform", "subprocess",
        "threading", "time", "webbrowser", "urllib",
        "shutil", "pathlib", "dataclasses", "re", "typing",
        *crypto_hidden,  # cryptography submodules + native _rust (see collect_all above)
        *webview_hidden,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "unittest", "pytest",
        "numpy", "matplotlib", "PIL", "cv2", "pandas", "scipy",
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
    name="lac",
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
    icon="assets/app-icon.ico",
)

exe_debug = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="lac-console",
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
    icon="assets/app-icon.ico",
)
