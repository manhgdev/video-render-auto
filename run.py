#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chạy VideoBuilder — mọi OS: py run.py  hoặc  python3 run.py"""

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _venv_python() -> Path:
    if sys.platform == "win32":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def _should_use_project_venv() -> bool:
    if os.environ.get("VIRTUAL_ENV"):
        return False
    if sys.prefix != sys.base_prefix:
        return False
    if not sys.platform.startswith("darwin"):
        return False
    externally_managed = Path(sys.base_prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "EXTERNALLY-MANAGED"
    return externally_managed.exists()


if _should_use_project_venv():
    py = _venv_python()
    if not py.exists():
        import venv

        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
    os.execv(str(py), [str(py), __file__, *sys.argv[1:]])

sys.path.insert(0, str(ROOT))

from videobuilder.core.env_config import load_env

load_env()

from release.bootstrap import ensure_python  # noqa: E402

if __name__ == "__main__":
    ensure_python()
    from videobuilder.gui.app import main

    main()
