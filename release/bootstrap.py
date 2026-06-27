#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Kiểm tra Python/tkinter — dùng chung cho run.py và build.py."""

from __future__ import annotations

import shutil
import subprocess
import sys


def check_python() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit(f"Cần Python 3.10+ (hiện: {sys.version_info.major}.{sys.version_info.minor})")
    try:
        import tkinter  # noqa: F401
    except ImportError:
        raise SystemExit("Thiếu tkinter — cài Python có Tcl/Tk (python.org, không dùng embed)")


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


def ensure_python() -> None:
    try:
        check_python()
        return
    except SystemExit as exc:
        print(exc, file=sys.stderr)

    if detect_platform() == "windows" and try_install_python_windows():
        print("Đã cài Python — mở terminal mới rồi chạy lại.", file=sys.stderr)
    else:
        print("Tải Python: https://www.python.org/downloads/", file=sys.stderr)
        if detect_platform() == "windows":
            print('Tick "Add python.exe to PATH"', file=sys.stderr)
    raise SystemExit(1)
