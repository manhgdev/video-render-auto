#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Đọc API key từ .env (project root)."""

from __future__ import annotations

import os
from pathlib import Path

GROQ_API_KEY_ENV = "GROQ_API_KEY"
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"

_loaded = False


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_env(*, force: bool = False) -> None:
    global _loaded
    if _loaded and not force:
        return
    env_path = project_root() / ".env"
    if not env_path.is_file():
        _loaded = True
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        _load_env_manual(env_path)
    _loaded = True


def _load_env_manual(env_path: Path) -> None:
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


def env_api_key(name: str) -> str | None:
    load_env()
    value = (os.environ.get(name) or "").strip()
    return value or None
