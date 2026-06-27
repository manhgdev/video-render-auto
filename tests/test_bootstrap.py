"""Test bootstrap / môi trường."""

from release.bootstrap import check_python, detect_platform


def test_detect_platform_windows():
    import sys

    if sys.platform == "win32":
        assert detect_platform() == "windows"


def test_check_python_ok():
    check_python()  # không raise khi chạy test với Python 3.10+ + tkinter
