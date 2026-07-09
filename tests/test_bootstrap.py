"""Test bootstrap / môi trường."""

from pathlib import Path

from release import bootstrap
from release.bootstrap import check_python, detect_platform


def test_detect_platform_windows():
    import sys

    if sys.platform == "win32":
        assert detect_platform() == "windows"


def test_check_python_ok():
    check_python()  # không raise khi chạy test với Python 3.10+ + tkinter


def test_find_macos_python_with_tkinter_prefers_working_candidate(monkeypatch):
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    monkeypatch.setattr(
        bootstrap.glob,
        "glob",
        lambda pattern: [
            "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3",
            "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3",
        ],
    )
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(bootstrap.os, "access", lambda path, mode: True)
    monkeypatch.setattr(
        bootstrap,
        "_interpreter_has_tkinter",
        lambda python: python == Path("/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"),
    )

    assert bootstrap.find_macos_python_with_tkinter() == Path(
        "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"
    )
