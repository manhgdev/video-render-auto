"""Persistent diagnostics for windowed builds where stderr is not visible."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path


def diagnostics_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "VideoBuilder" / "logs"


def diagnostics_file() -> Path:
    return diagnostics_dir() / "videobuilder.log"


def write_diagnostic(message: object, level: str = "info") -> Path | None:
    """Append a line without ever breaking the GUI if the log path is unavailable."""
    try:
        path = diagnostics_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"[{stamp}] [{level.upper()}] {str(message).strip()}\n")
        return path
    except OSError:
        return None
