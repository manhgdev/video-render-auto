#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chạy VideoBuilder — mọi OS: py run.py  hoặc  python3 run.py"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from release.bootstrap import ensure_python  # noqa: E402

if __name__ == "__main__":
    ensure_python()
    from videobuilder.gui.app import main

    main()
