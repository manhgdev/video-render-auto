# -*- mode: python ; coding: utf-8 -*-
# python3 -m PyInstaller packaging/macos/VideoBuilder.spec

import sys
from pathlib import Path

ROOT = Path(SPEC).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import videobuilder.version as version

block_cipher = None

a = Analysis(
    [str(ROOT / "videobuilder" / "gui" / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "videobuilder",
        "videobuilder.core",
        "videobuilder.core.pipeline",
        "videobuilder.core.ffmpeg_setup",
        "videobuilder.gui",
        "videobuilder.gui.app",
        "videobuilder.version",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name=version.exe_stem(),
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
)

app = BUNDLE(
    exe,
    name=f"{version.exe_stem()}.app",
    icon=None,
    bundle_identifier="dev.manhg.videobuilder",
)
