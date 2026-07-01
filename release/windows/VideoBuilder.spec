# -*- mode: python ; coding: utf-8 -*-
# py -m PyInstaller packaging/windows/VideoBuilder.spec

import sys
from pathlib import Path

ROOT = Path(SPEC).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import videobuilder.version as version

block_cipher = None


def _win_version_info():
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    v = version.version_tuple()
    return VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=v,
            prodvers=v,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", version.APP_COMPANY),
                        StringStruct("FileDescription", version.APP_DESCRIPTION),
                        StringStruct("FileVersion", version.APP_VERSION),
                        StringStruct("InternalName", version.APP_NAME),
                        StringStruct("LegalCopyright", version.APP_COPYRIGHT),
                        StringStruct("OriginalFilename", version.exe_filename()),
                        StringStruct("ProductName", version.APP_PRODUCT),
                        StringStruct("ProductVersion", version.APP_VERSION),
                    ],
                )
            ]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )


a = Analysis(
    [str(ROOT / "videobuilder" / "gui" / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "template"), "template"),
        (str(ROOT / "public" / "templates"), "public/templates"),
    ],
    hiddenimports=[
        "videobuilder",
        "videobuilder.core",
        "videobuilder.core.pipeline",
        "videobuilder.core.ffmpeg_setup",
        "videobuilder.core.automation",
        "videobuilder.core.youtube_import",
        "videobuilder.core.audio_pipeline",
        "videobuilder.core.generate_prompts",
        "videobuilder.gui",
        "videobuilder.gui.app",
        "videobuilder.version",
        "groq",
        "edge_tts",
        "yt_dlp",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "torchvision",
        "torchaudio",
        "triton",
        "faster_whisper",
        "ctranslate2",
        "onnxruntime",
        "tensorflow",
        "scipy",
        "pandas",
        "matplotlib",
        "IPython",
        "notebook",
        "pytest",
    ],
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
    icon=None,
    version=_win_version_info() if sys.platform == "win32" else None,
)
