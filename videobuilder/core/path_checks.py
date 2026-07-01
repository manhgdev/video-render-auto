#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Non-blocking filesystem probes for UI thread safety."""

from __future__ import annotations

import threading
from pathlib import Path

_DEFAULT_TIMEOUT = 0.2


def _probe(path: Path, *, check_dir: bool) -> bool:
    try:
        return path.is_dir() if check_dir else path.is_file()
    except OSError:
        return False


def path_exists_safe(path: str | Path, *, is_dir: bool = False, timeout: float = _DEFAULT_TIMEOUT) -> bool | None:
    """Return True/False if stat finishes within timeout; None if timed out (assume exists)."""
    text = str(path or "").strip()
    if not text:
        return False
    target = Path(text)
    result: list[bool | None] = [None]

    def worker() -> None:
        result[0] = _probe(target, check_dir=is_dir)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None
    return bool(result[0])


def path_is_file_safe(path: str | Path, timeout: float = _DEFAULT_TIMEOUT) -> bool | None:
    return path_exists_safe(path, is_dir=False, timeout=timeout)


def path_is_dir_safe(path: str | Path, timeout: float = _DEFAULT_TIMEOUT) -> bool | None:
    return path_exists_safe(path, is_dir=True, timeout=timeout)


def path_exists_or_assume(path: str | Path, *, is_dir: bool, timeout: float = 0.1) -> bool:
    """Fast UI check: on timeout assume path exists to avoid disabling controls."""
    exists = path_exists_safe(path, is_dir=is_dir, timeout=timeout)
    return True if exists is None else exists
