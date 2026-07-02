"""Skip Tkinter tests on headless Linux CI."""

import os
import sys

import pytest

requires_tk = pytest.mark.skipif(
    sys.platform.startswith("linux") and not os.environ.get("DISPLAY"),
    reason="Tkinter requires DISPLAY on Linux CI",
)
