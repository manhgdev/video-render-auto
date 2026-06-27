#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path

from videobuilder.core.ffmpeg_setup import get_app_dir

from videobuilder.gui.constants import OUTPUT_BASENAME

def get_settings_file() -> Path:
    """Exe trong dist/ dùng chung settings với thư mục project cha."""
    app_dir = get_app_dir()
    if getattr(sys, "frozen", False):
        parent = app_dir.parent / ".video_builder_settings.json"
        if app_dir.name.lower() == "dist" or parent.is_file():
            return parent
    return app_dir / ".video_builder_settings.json"


def _blocked_output_dirs() -> set[Path]:
    blocked = set()
    for env_key in ("SystemRoot", "WINDIR"):
        root = os.environ.get(env_key, "").strip()
        if root:
            try:
                blocked.add(Path(root).resolve())
            except OSError:
                pass
    for path in (r"C:\Windows", r"C:\Windows\System32"):
        try:
            blocked.add(Path(path).resolve())
        except OSError:
            pass
    return blocked


def is_writable_output_dir(folder: Path) -> bool:
    try:
        folder = folder.resolve()
    except OSError:
        return False
    blocked = _blocked_output_dirs()
    if folder in blocked:
        return False
    for bad in blocked:
        if bad in folder.parents:
            return False
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
    if not folder.is_dir():
        return False
    probe = folder / "._vb_write_test"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def default_output_folder() -> Path:
    """Mặc định: cạnh file .exe; nếu không ghi được → Downloads / Videos / Desktop."""
    candidates = [get_app_dir(), Path.home() / "Downloads", Path.home() / "Videos", Path.home() / "Desktop"]
    seen = set()
    for folder in candidates:
        key = str(folder).lower()
        if key in seen:
            continue
        seen.add(key)
        if is_writable_output_dir(folder):
            return folder.resolve()
    return Path.home() / "Desktop"


def default_output_path() -> Path:
    return default_output_folder() / OUTPUT_BASENAME


def normalize_output_path(path: str | Path) -> Path:
    p = Path(str(path).strip() or str(default_output_path()))
    if p.suffix.lower() != ".mp4":
        p = p.with_suffix(".mp4")
    if is_writable_output_dir(p.parent):
        return p.resolve()
    return (default_output_folder() / (p.name or OUTPUT_BASENAME)).resolve()

