#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys

if sys.version_info < (3, 10):
    raise SystemExit("Cần Python 3.10+")
try:
    import tkinter  # noqa: F401
except ImportError:
    raise SystemExit("Thiếu tkinter — cài Python có Tcl/Tk")

from videobuilder.gui.app import main

if __name__ == "__main__":
    main()
