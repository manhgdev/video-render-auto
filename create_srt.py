#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI: py create_srt.py --audio file.mp3 [-o out.srt]"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from videobuilder.core.create_srt import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
