#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reusable Tkinter progress bar, smooth animation, and status formatters."""

from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass


def clamp_pct(pct: float) -> float:
    return max(0.0, min(100.0, float(pct)))


def truncate(text: str, max_len: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


@dataclass(frozen=True)
class ProgressColors:
    trough: str
    bar: str
    border: str


class CanvasProgressBar:
    """Canvas-based progress bar (footer + tab SRT)."""

    def __init__(self, parent: tk.Misc, colors: ProgressColors, *, height: int = 10):
        self._colors = colors
        self._display = 0.0
        self.wrap = tk.Frame(
            parent,
            bg=colors.trough,
            height=height,
            highlightbackground=colors.border,
            highlightthickness=1,
        )
        self.wrap.pack_propagate(False)
        self.canvas = tk.Canvas(
            self.wrap,
            height=max(4, height - 2),
            bg=colors.trough,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        self._fill = self.canvas.create_rectangle(
            0, 0, 0, height, fill=colors.bar, width=0,
        )
        self.canvas.bind("<Configure>", lambda _e: self.paint(self._display))

    def paint(self, pct: float) -> None:
        self._display = clamp_pct(pct)
        self.canvas.update_idletasks()
        w = max(1, self.canvas.winfo_width())
        h = max(1, self.canvas.winfo_height())
        fill_w = max(0, w * self._display / 100.0)
        self.canvas.coords(self._fill, 0, 0, fill_w, h)


class StatusPresenter:
    """Short status line — only updates when text changes."""

    def __init__(
        self,
        variable: tk.StringVar,
        formatter: Callable[[str], str],
        *,
        done_text: str = "Hoàn thành!",
    ):
        self._var = variable
        self._formatter = formatter
        self._done_text = done_text
        self._last = ""

    def set_raw(self, text: str) -> None:
        if text != self._last:
            self._last = text
            self._var.set(text)

    def update(self, message: str = "", *, done: bool = False) -> None:
        text = self._done_text if done else self._formatter(message)
        self.set_raw(text)

    def reset_last(self) -> None:
        self._last = ""


def short_render_status(message: str) -> str:
    msg = (message or "").strip()
    if not msg:
        return "Đang render..."
    if msg.startswith(("Hoàn tất", "Hoàn thành")):
        return "Hoàn tất..."
    if msg.startswith("Đang encode"):
        return "Đang encode..."
    if msg.startswith("Zoom scene "):
        head = msg[len("Zoom scene ") :].split("...")[0].strip()
        return f"Zoom {head}" if head else "Đang zoom..."
    if msg.startswith(("Hiệu ứng ", "Ghép", "Đợt ", "Gắn audio")):
        return msg.rstrip(".").split("...")[0]
    if msg.startswith(("Chuẩn bị render", "Đang bắt đầu")):
        return "Chuẩn bị..."
    if any(msg.startswith(p) for p in (
        "Đọc audio", "Đọc file prompt", "Map ", "Kiểm tra ảnh", "Chuẩn bị ",
    )) or " — " in msg:
        return "Chuẩn bị..."
    return truncate(msg, 32)


def short_srt_status(message: str) -> str:
    msg = (message or "").strip()
    if not msg:
        return "Đang xử lý..."
    if msg.startswith(("Hoàn thành", "Xong")):
        return "Hoàn thành!"
    if msg.startswith("Tải model"):
        return "Tải model Whisper..."
    if msg.startswith("Chuyển sang CPU"):
        return "Chuyển sang CPU..."
    if msg.startswith("Đang nhận dạng"):
        return "Nhận dạng giọng nói..."
    if msg.startswith("Đang nghe"):
        return "Đang nghe audio..."
    if msg.startswith("Đã có"):
        return msg
    return truncate(msg, 40)


def should_log_render_progress(message: str) -> bool:
    if not message:
        return False
    if message.startswith(("Đang encode", "Hoàn tất", "Hoàn thành")):
        return False
    if message.startswith(("Đọc audio", "Đọc file prompt", "Map ", "Kiểm tra ảnh")):
        return False
    if " — " in message:
        return False
    return True


class SmoothProgressTracker:
    """Smooth monotonic bar toward reported pipeline %."""

    FRAME_MS = 16

    def __init__(
        self,
        root: tk.Misc,
        bar: CanvasProgressBar,
        percent_var: tk.StringVar,
        *,
        is_active: Callable[[], bool],
        decimal_places: int = 1,
    ):
        self._root = root
        self._bar = bar
        self._percent_var = percent_var
        self._is_active = is_active
        self._decimal = decimal_places
        self._reported = 0.0
        self._display = 0.0
        self._last_report_time = 0.0
        self._anim_id: str | None = None

    @property
    def display(self) -> float:
        return self._display

    def _set_percent_label(self, pct: float) -> None:
        if self._decimal == 0:
            self._percent_var.set(f"{pct:.0f}%")
        else:
            self._percent_var.set(f"{pct:.{self._decimal}f}%")

    def reset(self, pct: float = 0.0) -> None:
        self.stop()
        pct = clamp_pct(pct)
        self._reported = pct
        self._display = pct
        self._last_report_time = time.time()
        self._bar.paint(pct)
        self._set_percent_label(pct)

    def start(self, pct: float = 1.0) -> None:
        pct = clamp_pct(pct)
        self._reported = max(1.0, pct) if pct > 0 else pct
        self._display = self._reported
        self._last_report_time = time.time()
        self._bar.paint(self._display)
        self._set_percent_label(self._display)
        self.ensure_animation()

    def stop(self) -> None:
        if self._anim_id is not None:
            self._root.after_cancel(self._anim_id)
            self._anim_id = None

    def ingest(self, pct: float, *, touch_time: bool = True) -> None:
        pct = clamp_pct(pct)
        if pct >= self._reported:
            self._reported = max(1.0, pct) if pct < 100 else 100.0
            if touch_time:
                self._last_report_time = time.time()
        elif touch_time:
            self._last_report_time = time.time()
        if pct >= 100:
            self._reported = 100.0
            self._last_report_time = time.time()
        self.ensure_animation()

    def ensure_animation(self) -> None:
        if self._anim_id is None:
            self._tick()

    def _tick(self) -> None:
        if not self._is_active():
            self._anim_id = None
            return

        reported = max(1.0, self._reported)
        prev = max(1.0, self._display)

        if reported >= 100:
            target = 100.0
        else:
            target = reported
            stale = time.time() - self._last_report_time
            if stale > 0.15:
                target = max(target, min(reported + 1.5, 99.5))

        if target > prev + 0.001:
            step = max(0.035, (target - prev) * 0.4)
            display = min(target, prev + step)
        else:
            display = prev

        display = max(prev, display)
        display = min(100.0 if reported >= 100 else 99.8, display)

        self._display = display
        self._bar.paint(display)
        self._set_percent_label(display)

        self._anim_id = self._root.after(self.FRAME_MS, self._tick)


class DirectProgressTracker:
    """Immediate bar updates — for SRT tab."""

    def __init__(
        self,
        bar: CanvasProgressBar,
        percent_var: tk.StringVar,
        status: StatusPresenter | None = None,
    ):
        self._bar = bar
        self._percent_var = percent_var
        self._status = status
        self._display = 0.0

    @property
    def display(self) -> float:
        return self._display

    def reset(self, pct: float = 0.0) -> None:
        self._display = clamp_pct(pct)
        self._bar.paint(self._display)
        self._percent_var.set("0%" if pct == 0 else f"{self._display:.0f}%")
        if self._status:
            self._status.reset_last()

    def report(self, pct: float, message: str = "") -> None:
        pct = clamp_pct(pct)
        self._display = max(self._display, pct)
        self._bar.paint(self._display)
        self._percent_var.set(f"{self._display:.0f}%")
        if not self._status:
            return
        if pct >= 100:
            self._status.update(done=True)
        else:
            self._status.update(message)
