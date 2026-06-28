#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pipeline progress reporting — monotonic % floor, shared across render modes."""

from __future__ import annotations

import threading

PROGRESS_RENDER_MAX = 99.0    # render chính — 100% khi UI báo xong
PROGRESS_FINALE_MAX = 99.5    # speed / strip metadata

_progress_lock = threading.Lock()
_progress_floor = {"v": 0.0}


def report_progress(callback, pct, message):
    if not callback:
        return
    with _progress_lock:
        p = max(_progress_floor["v"], float(pct))
        _progress_floor["v"] = p
    callback(max(0.0, min(PROGRESS_RENDER_MAX, p)), message)


def reset_progress_floor():
    with _progress_lock:
        _progress_floor["v"] = 0.0
