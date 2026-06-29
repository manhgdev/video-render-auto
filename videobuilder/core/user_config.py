#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thư mục cấu hình user và cache runtime."""

from __future__ import annotations

import sys
import os
from pathlib import Path

from videobuilder.core.ffmpeg_setup import get_app_dir

SETTINGS_FILENAME = ".video_builder_settings.json"
GROQ_CACHE_FILENAME = ".groq_active_models.json"


def get_user_config_dir() -> Path:
    """Exe trong dist/ → thư mục project cha; dev → root repo."""
    app_dir = get_app_dir()
    if getattr(sys, "frozen", False):
        parent = app_dir.parent
        if app_dir.name.lower() == "dist" or (parent / SETTINGS_FILENAME).is_file():
            return parent
    return app_dir


def get_groq_model_cache_file() -> Path:
    return get_user_cache_dir() / GROQ_CACHE_FILENAME


def get_legacy_groq_model_cache_file() -> Path:
    return get_user_config_dir() / GROQ_CACHE_FILENAME


def get_user_cache_dir() -> Path:
    app_name = "VideoBuilder"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / app_name
