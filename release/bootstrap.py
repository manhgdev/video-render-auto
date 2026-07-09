#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kiểm tra Python/tkinter — dùng chung cho run.py và build.py."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def venv_python(root: Path | None = None) -> Path:
    root = root or project_root()
    if sys.platform == "win32":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def should_use_project_venv() -> bool:
    """Homebrew / PEP 668: dùng .venv thay vì pip vào system Python."""
    if os.environ.get("VIRTUAL_ENV"):
        return False
    if sys.prefix != sys.base_prefix:
        return False
    if not sys.platform.startswith("darwin"):
        return False
    externally_managed = (
        Path(sys.base_prefix)
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "EXTERNALLY-MANAGED"
    )
    return externally_managed.exists()


def maybe_reexec_into_project_venv(script: str, argv: list[str] | None = None) -> None:
    if not should_use_project_venv():
        return
    root = project_root()
    py = venv_python(root)
    if not py.exists():
        import venv

        venv.EnvBuilder(with_pip=True).create(root / ".venv")
    args = argv if argv is not None else sys.argv
    os.execv(str(py), [str(py), script, *args[1:]])


def check_python() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit(f"Cần Python 3.10+ (hiện: {sys.version_info.major}.{sys.version_info.minor})")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        raise SystemExit("Thiếu tkinter — cài Python có Tcl/Tk (python.org, không dùng embed)")


def _interpreter_has_tkinter(python: Path) -> bool:
    try:
        proc = subprocess.run(
            [str(python), "-c", "import tkinter"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def find_macos_python_with_tkinter() -> Path | None:
    if not sys.platform == "darwin":
        return None

    patterns = [
        "/Library/Frameworks/Python.framework/Versions/*/bin/python3",
        "/Library/Frameworks/Python.framework/Versions/*/bin/python3.*",
        "/Applications/Python 3*/bin/python3",
    ]
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(Path(path) for path in glob.glob(pattern))

    for python in sorted(candidates, reverse=True):
        if python.is_file() and os.access(python, os.X_OK) and _interpreter_has_tkinter(python):
            return python
    return None


def detect_platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def try_install_python_windows() -> bool:
    if not shutil.which("winget"):
        return False
    print("Đang cài Python qua winget...")
    try:
        proc = subprocess.run(
            [
                "winget", "install", "-e", "--id", "Python.Python.3.12",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    text = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0 or "already installed" in text.lower()


def ensure_python(script: str | None = None, argv: list[str] | None = None) -> None:
    try:
        check_python()
        return
    except SystemExit as exc:
        if detect_platform() == "macos":
            python = find_macos_python_with_tkinter()
            if python is not None:
                script_path = script or sys.argv[0]
                args = argv if argv is not None else sys.argv
                os.execv(str(python), [str(python), script_path, *args[1:]])
        print(exc, file=sys.stderr)

    if detect_platform() == "windows" and try_install_python_windows():
        print("Đã cài Python — mở terminal mới rồi chạy lại.", file=sys.stderr)
    else:
        print("Tải Python: https://www.python.org/downloads/", file=sys.stderr)
        if detect_platform() == "windows":
            print('Tick "Add python.exe to PATH"', file=sys.stderr)
    raise SystemExit(1)
