#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Image Timeline Video Builder

Công dụng:
- Đọc file prompt có time range dạng [00:00–00:04]
- Lấy ảnh trong thư mục images theo thứ tự tên file
- Ghép mỗi ảnh đúng thời lượng theo time range
- Gắn audio mp3
- Xuất video mp4 16:9

Yêu cầu:
- Cài ffmpeg và ffprobe
- Python 3.9+

Cách dùng:
python build_video_from_prompts.py \
  --audio 1.mp3 \
  --prompts image_prompts_ytb1_V4_1_composited_2d_scenes.txt \
  --images images \
  --output final_video.mp4

Tên ảnh nên đặt theo thứ tự:
001.png
002.png
003.png
...
hoặc
001.jpg
002.jpg
003.jpg
...
"""

import argparse
import json
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from videobuilder.core.progress import (
    PROGRESS_FINALE_MAX,
    PROGRESS_RENDER_MAX,
    report_progress,
    reset_progress_floor,
)


@dataclass
class SubtitleStyle:
    font_size: int | None = 8
    margin_v: int = 18
    outline: int = 1  # 0=nền tối, 1=nền+viền mảnh, 2=nền đậm+viền
    offset_sec: float = 0.0


DEFAULT_SUBTITLE_STYLE = SubtitleStyle()


def _subtitle_render_params(style: SubtitleStyle | None, height: int) -> dict:
    """Readable shorts style: bold white on semi-opaque dark box."""
    style = style or DEFAULT_SUBTITLE_STYLE
    font_size = style.font_size if style.font_size else subtitle_font_size(height)
    level = max(0, min(2, style.outline))
    box_colours = ("&HB0000000", "&HC8000000", "&HE0000000")
    outline_px = 1 if level >= 1 else 0
    return {
        "font_size": font_size,
        "font_name": "Segoe UI",
        "bold": -1,
        "border_style": 4 if outline_px else 3,
        "back_colour": box_colours[level],
        "outline": outline_px,
        "shadow": 0,
        "margin_v": max(0, style.margin_v),
        "primary": "&H00FFFFFF",
        "outline_colour": "&H00000000",
    }


class RenderCancelled(RuntimeError):
    """Render stopped by user."""


def _suspend_pid(pid):
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        access = 0x0800
        handle = kernel32.OpenProcess(access, False, pid)
        if not handle:
            return
        try:
            ntdll.NtSuspendProcess(handle)
        finally:
            kernel32.CloseHandle(handle)
    else:
        import signal
        os.kill(pid, signal.SIGSTOP)


def _resume_pid(pid):
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ntdll = ctypes.windll.ntdll
        access = 0x0800
        handle = kernel32.OpenProcess(access, False, pid)
        if not handle:
            return
        try:
            ntdll.NtResumeProcess(handle)
        finally:
            kernel32.CloseHandle(handle)
    else:
        import signal
        os.kill(pid, signal.SIGCONT)


class ProcessController:
    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._cancelled = False
        self._paused = False
        self._pause_event = threading.Event()
        self._pause_event.set()

    @property
    def cancelled(self):
        return self._cancelled

    @property
    def paused(self):
        return self._paused

    def cancel(self):
        with self._lock:
            self._cancelled = True
            self._paused = False
            self._pause_event.set()
            proc = self._proc
        if proc and proc.poll() is None:
            _terminate_process(proc)

    def pause(self):
        with self._lock:
            if self._cancelled or self._paused:
                return
            self._paused = True
            self._pause_event.clear()
            proc = self._proc
        if proc and proc.poll() is None:
            _suspend_pid(proc.pid)

    def resume(self):
        with self._lock:
            if self._cancelled or not self._paused:
                return
            self._paused = False
            self._pause_event.set()
            proc = self._proc
        if proc and proc.poll() is None:
            _resume_pid(proc.pid)

    def wait_if_paused(self):
        self.raise_if_cancelled()
        while not self._pause_event.wait(timeout=0.25):
            self.raise_if_cancelled()

    def raise_if_cancelled(self):
        if self._cancelled:
            raise RenderCancelled("Đã hủy render")

    def attach(self, proc):
        with self._lock:
            self._proc = proc

    def detach(self):
        with self._lock:
            self._proc = None


def _popen_kwargs():
    return {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    }


def _terminate_process(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _iter_process_stdout(proc, process_controller=None):
    assert proc.stdout is not None
    line_queue = queue.Queue()

    def reader():
        try:
            for line in proc.stdout:
                line_queue.put(line)
        finally:
            line_queue.put(None)

    threading.Thread(target=reader, daemon=True).start()
    while True:
        if process_controller:
            process_controller.wait_if_paused()
        try:
            line = line_queue.get(timeout=0.25)
        except queue.Empty:
            if proc.poll() is not None:
                while True:
                    try:
                        pending = line_queue.get_nowait()
                    except queue.Empty:
                        break
                    if pending is None:
                        return
                    yield pending
                return
            continue
        if line is None:
            return
        yield line


def _wait_process(proc, process_controller=None):
    while True:
        if process_controller:
            process_controller.wait_if_paused()
        try:
            return proc.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            if process_controller and process_controller.cancelled:
                _terminate_process(proc)
                proc.wait()
                process_controller.raise_if_cancelled()


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_ENCODER_CACHE = None

# Hieu ung xfade phu hop video 2D (nhe, nhanh)
TRANSITION_EFFECTS = {
    "fade": "Fade mượt",
    "dissolve": "Dissolve",
    "smoothleft": "Smooth trái",
    "smoothright": "Smooth phải",
    "smoothup": "Smooth lên",
    "smoothdown": "Smooth xuống",
    "wipeleft": "Wipe trái",
    "wiperight": "Wipe phải",
    "circleopen": "Circle mở",
    "circleclose": "Circle đóng",
    "random": "Ngẫu nhiên",
}

TRANSITIONS_2D_RANDOM = [
    "fade",
    "dissolve",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "wipeleft",
    "wiperight",
]

DEFAULT_2D_TRANSITION_DURATION = 0.28

RESOLUTION_PRESETS = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "2k": (2560, 1440),
    "4k": (3840, 2160),
    "shorts": (1080, 1920),
}

FPS_OPTIONS = (24, 30, 60)

ENCODE_QUALITY_OPTIONS = {
    "fast": "Nhanh",
    "balanced": "Cân bằng",
    "quality": "Chất lượng cao",
}

ZOOM_LEVELS = {
    "off": None,
    "light": 1.03,
    "medium": 1.05,
    "strong": 1.07,
}

ZOOM_LEVEL_OPTIONS = {
    "off": "Tắt",
    "light": "Nhẹ",
    "medium": "Vừa",
    "strong": "Mạnh",
}

ENCODER_OVERRIDE_OPTIONS = {
    "auto": "Tự động",
    "h264_nvenc": "NVIDIA GPU",
    "h264_qsv": "Intel GPU",
    "h264_amf": "AMD GPU",
    "h264_mf": "Windows GPU",
    "libx264": "CPU (libx264)",
}

DEFAULT_AUDIO_VOLUME = 1.0
DEFAULT_WATERMARK_OPACITY = 0.7
DEFAULT_PREVIEW_SECONDS = 15.0


def resolve_zoom_level(zoom=False, zoom_level="off"):
    if zoom_level and zoom_level in ZOOM_LEVELS and zoom_level != "off":
        return zoom_level
    return "light" if zoom else "off"


ZOOM_UPSCALE = 2  # upscale trước zoompan → giảm rung (subpixel)


def ken_burns_vf(width, height, fps, zoom_level, duration_sec=1.0):
    """Zoom vào mượt: 1 ảnh + zoompan linear (không eval=frame → không rung)."""
    max_zoom = ZOOM_LEVELS.get(zoom_level)
    if not max_zoom:
        return None
    duration_sec = max(0.1, duration_sec)
    d = max(2, int(round(duration_sec * fps)))
    z_delta = max_zoom - 1.0
    # smoothstep ease-in-out (mượt đầu/cuối)
    on_max = max(d - 1, 1)
    z_expr = f"1+{z_delta:.6f}*(3*pow(on/{on_max},2)-2*pow(on/{on_max},3))"
    up_w = int(width * max_zoom * ZOOM_UPSCALE)
    up_h = int(height * max_zoom * ZOOM_UPSCALE)
    up_w += up_w % 2
    up_h += up_h % 2
    return (
        f"scale={up_w}:{up_h}:force_original_aspect_ratio=increase:flags=bilinear,"
        f"crop={up_w}:{up_h},"
        f"zoompan=z='{z_expr}':d={d}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={width}x{height}:fps={fps},format=yuv420p"
    )


def ken_burns_chain(width, height, fps, zoom_level, duration_sec=1.0):
    kb = ken_burns_vf(width, height, fps, zoom_level, duration_sec)
    return kb


def is_portrait_output(width, height):
    return height > width


def scale_geometry_parts(width, height, zoom_level="off"):
    """Ngang: fill khung (crop). Dọc/Shorts: letterbox — trừ khi bật Zoom thì crop để Ken Burns."""
    if is_portrait_output(width, height) and zoom_level in (None, "", "off"):
        return [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
        ]
    return [
        f"scale={width}:{height}:force_original_aspect_ratio=increase",
        f"crop={width}:{height}",
    ]


def resolve_encoder(encoder_override=None):
    if encoder_override and encoder_override != "auto":
        label = ENCODER_OVERRIDE_OPTIONS.get(encoder_override, encoder_override)
        return encoder_override, label
    return detect_video_encoder()


def escape_subtitle_path(path: Path) -> str:
    text = str(path.resolve()).replace("\\", "/")
    text = text.replace(":", "\\:")
    return text


def subtitle_force_style(style: SubtitleStyle | None, height: int) -> str:
    p = _subtitle_render_params(style, height)
    return (
        f"Fontname={p['font_name']},Fontsize={p['font_size']},Bold={p['bold']},"
        f"BorderStyle={p['border_style']},BackColour={p['back_colour']},"
        f"Outline={p['outline']},Shadow={p['shadow']},"
        f"MarginV={p['margin_v']},Alignment=2,"
        f"PrimaryColour={p['primary']},OutlineColour={p['outline_colour']}"
    )


def _format_srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    whole = int(seconds)
    ms = int(round((seconds - whole) * 1000))
    if ms >= 1000:
        whole += 1
        ms = 0
    h = whole // 3600
    m = (whole % 3600) // 60
    s = whole % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt_from_cues(dest: Path, cues: list[tuple[float, float, str]]) -> None:
    blocks = []
    for i, (start, end, text) in enumerate(cues, 1):
        blocks.append(f"{i}\n{_format_srt_ts(start)} --> {_format_srt_ts(end)}\n{text}")
    dest.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def subtitle_font_size(video_height: int) -> int:
    return max(14, int(video_height * 18 / 1080))


_SRT_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)


def _srt_timestamp_to_sec(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _wrap_subtitle_text(text: str, max_chars: int) -> str:
    words = text.split()
    if not words:
        return ""
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        add = len(word) if not current else len(word) + 1
        if current and current_len + add > max_chars:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += add
    if current:
        lines.append(" ".join(current))
    return "\\N".join(_escape_ass_text(line) for line in lines)


def parse_srt_file(path: Path) -> list[tuple[float, float, str]]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        idx = 1 if re.fullmatch(r"\d+", lines[0]) else 0
        if idx >= len(lines):
            continue
        match = _SRT_TIME.match(lines[idx])
        if not match:
            continue
        start = _srt_timestamp_to_sec(*match.groups()[:4])
        end = _srt_timestamp_to_sec(*match.groups()[4:])
        if end <= start + 0.02:
            continue
        body = " ".join(lines[idx + 1 :])
        if body:
            cues.append((start, end, body))
    return cues


def write_ass_from_cues(
    dest: Path, cues: list[tuple[float, float, str]], width: int, height: int,
    style: SubtitleStyle | None = None,
) -> None:
    style = style or DEFAULT_SUBTITLE_STYLE
    p = _subtitle_render_params(style, height)
    max_chars = max(28, min(52, int(width / 24)))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 0\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{p['font_name']},{p['font_size']},{p['primary']},{p['primary']},"
        f"{p['outline_colour']},{p['back_colour']},"
        f"{p['bold']},0,0,0,100,100,0,0,{p['border_style']},{p['outline']},{p['shadow']},"
        f"2,10,10,{p['margin_v']},1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for start, end, text in cues:
        wrapped = _wrap_subtitle_text(text, max_chars)
        events.append(
            f"Dialogue: 0,{_sec_to_ass_time(start)},{_sec_to_ass_time(end)},"
            f"Default,,0,0,0,,{wrapped}"
        )
    dest.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")


def convert_srt_to_ass(
    src: Path, dest: Path, width: int, height: int, style: SubtitleStyle | None = None,
) -> int:
    cues = parse_srt_file(src)
    if not cues:
        raise ValueError(f"Không đọc được cue nào trong {src.name}")
    write_ass_from_cues(dest, cues, width, height, style)
    _shift_ass_dialogues(dest, (style or DEFAULT_SUBTITLE_STYLE).offset_sec)
    return len(cues)


def _ffmpeg_convert_subtitle_to_ass(src: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(src), "-f", "ass", str(dest),
    ]
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def _ass_time_to_sec(value: str) -> float:
    value = value.strip()
    h, m, rest = value.split(":")
    s, cs = rest.split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100.0


def _sec_to_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    whole = int(seconds)
    cs = int(round((seconds - whole) * 100))
    if cs >= 100:
        whole += 1
        cs = 0
    h = whole // 3600
    m = (whole % 3600) // 60
    s = whole % 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _shift_ass_dialogues(ass_path: Path, offset_sec: float) -> None:
    if abs(offset_sec) < 0.001:
        return
    lines_out = []
    for line in ass_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        match = re.match(r"^(Dialogue:\s*\d+,)([^,]+),([^,]+),(.*)$", line)
        if match:
            start = max(0.0, _ass_time_to_sec(match.group(2)) + offset_sec)
            end = max(start + 0.05, _ass_time_to_sec(match.group(3)) + offset_sec)
            line = (
                f"{match.group(1)}{_sec_to_ass_time(start)},"
                f"{_sec_to_ass_time(end)},{match.group(4)}"
            )
        lines_out.append(line)
    ass_path.write_text("\n".join(lines_out) + "\n", encoding="utf-8-sig")


def _patch_ass_play_res(ass_path: Path, width: int, height: int, style: SubtitleStyle | None = None) -> None:
    style = style or DEFAULT_SUBTITLE_STYLE
    text = ass_path.read_text(encoding="utf-8-sig", errors="replace")
    if "PlayResX:" in text:
        text = re.sub(r"PlayResX:\s*\d+", f"PlayResX: {width}", text)
        text = re.sub(r"PlayResY:\s*\d+", f"PlayResY: {height}", text)
    else:
        text = text.replace(
            "[Script Info]",
            f"[Script Info]\nPlayResX: {width}\nPlayResY: {height}",
            1,
        )
    p = _subtitle_render_params(style, height)
    lines = []
    for line in text.splitlines():
        if line.startswith("Style: Default,"):
            parts = line.split(",")
            if len(parts) > 21:
                parts[1] = p["font_name"]
                parts[2] = str(p["font_size"])
                parts[3] = p["primary"]
                parts[4] = p["primary"]
                parts[5] = p["outline_colour"]
                parts[6] = p["back_colour"]
                parts[7] = str(p["bold"])
                parts[15] = str(p["border_style"])
                parts[16] = str(p["outline"])
                parts[17] = str(p["shadow"])
                parts[21] = str(p["margin_v"])
            line = ",".join(parts)
        lines.append(line)
    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _shift_ass_dialogues(ass_path, style.offset_sec)


def stage_subtitle_for_burn(
    subtitle: Path, workdir: Path, width: int, height: int, style: SubtitleStyle | None = None,
    log_callback=None,
) -> Path:
    style = style or DEFAULT_SUBTITLE_STYLE
    workdir.mkdir(parents=True, exist_ok=True)
    src = Path(subtitle).resolve()

    if src.suffix.lower() == ".ass":
        dest = workdir / "burn.ass"
        shutil.copy2(src, dest)
        _patch_ass_play_res(dest, width, height, style)
        cue_count = sum(
            1 for ln in dest.read_text(encoding="utf-8-sig").splitlines() if ln.startswith("Dialogue:")
        )
    else:
        cues = parse_srt_file(src)
        if not cues:
            raise ValueError(f"Không đọc được cue nào trong {src.name}")
        offset = style.offset_sec
        if abs(offset) > 0.001:
            cues = [
                (max(0.0, s + offset), max(s + offset + 0.05, e + offset), t)
                for s, e, t in cues
            ]
        dest = workdir / "burn.srt"
        write_srt_from_cues(dest, cues)
        cue_count = len(cues)

    if cue_count and log_callback:
        log_msg(
            log_callback,
            f"  {cue_count} cue → {dest.name} (font {style.font_size or subtitle_font_size(height)}px)",
        )
    return dest


def subtitle_filter_expr(
    subtitle, workdir=None, width=1920, height=1080, style: SubtitleStyle | None = None,
    log_callback=None,
) -> str:
    path = Path(subtitle).resolve()
    if not path.is_file():
        raise ValueError(f"Phụ đề không tồn tại: {path}")
    if workdir is not None:
        path = stage_subtitle_for_burn(path, Path(workdir), width, height, style, log_callback)
    escaped = escape_subtitle_path(path)
    force = subtitle_force_style(style, height).replace("'", r"\'")
    return (
        f"subtitles='{escaped}':charenc=UTF-8:original_size={width}x{height}"
        f":force_style='{force}'"
    )


def subtitle_burn_chain(
    subtitle, workdir, width, height, style: SubtitleStyle | None = None, log_callback=None,
    fps=None, reset_pts=False,
) -> str:
    """Burn subs. Use fps= before subs on concat stills (sparse frames break timing)."""
    expr = subtitle_filter_expr(subtitle, workdir, width, height, style, log_callback)
    prefix: list[str] = []
    if fps:
        prefix.append(f"fps={fps}")
    if reset_pts:
        prefix.extend(["settb=AVTB", "setpts=PTS-STARTPTS"])
    if prefix:
        return ",".join(prefix + [expr])
    return expr


def watermark_scale_filter(video_width: int) -> str:
    target_w = max(48, min(320, int(video_width * 0.18)))
    return f"scale={target_w}:-1"


def watermark_overlay_parts(wm_input_idx: int, video_width: int, opacity: float):
    margin = 24
    return [
        f"[{wm_input_idx}:v]{watermark_scale_filter(video_width)},"
        f"format=rgba,colorchannelmixer=aa={opacity:.3f}[wm]",
        f"[vbase][wm]overlay=W-w-{margin}:H-h-{margin}:format=auto,format=yuv420p[vout]",
    ]


def trim_pairs_preview(pairs, preview_seconds):
    if not preview_seconds or preview_seconds <= 0:
        return pairs
    trimmed = []
    for img, start, end in pairs:
        if start >= preview_seconds:
            break
        trimmed.append((img, start, min(end, preview_seconds)))
        if end >= preview_seconds:
            break
    if not trimmed:
        raise RuntimeError("Preview quá ngắn — không có scene nào.")
    return trimmed


def build_boundary_transitions(boundary_count, effect, seed=None):
    if boundary_count <= 0:
        return []
    if effect == "random":
        rng = random.Random(seed)
        return [rng.choice(TRANSITIONS_2D_RANDOM) for _ in range(boundary_count)]
    if effect not in TRANSITION_EFFECTS:
        raise ValueError(f"Hieu ung khong ho tro: {effect}")
    return [effect] * boundary_count


def detect_video_encoder():
    global _ENCODER_CACHE
    if _ENCODER_CACHE:
        return _ENCODER_CACHE

    try:
        out = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-encoders"],
            stderr=subprocess.STDOUT,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        _ENCODER_CACHE = ("libx264", "CPU")
        return _ENCODER_CACHE

    for encoder, label in [
        ("h264_nvenc", "NVIDIA GPU"),
        ("h264_qsv", "Intel GPU"),
        ("h264_amf", "AMD GPU"),
        ("h264_mf", "Windows GPU"),
    ]:
        if encoder in out:
            _ENCODER_CACHE = (encoder, label)
            return _ENCODER_CACHE

    _ENCODER_CACHE = ("libx264", "CPU")
    return _ENCODER_CACHE


def encoder_args(encoder, quality="fast"):
    if encoder == "h264_nvenc":
        if quality == "quality":
            preset, cq = "p5", "20"
        elif quality == "balanced":
            preset, cq = "p3", "21"
        else:
            preset, cq = "p1", "23"
        return ["-c:v", "h264_nvenc", "-preset", preset, "-rc", "vbr", "-cq", cq, "-bf", "0"]
    if encoder == "h264_qsv":
        gq = "20" if quality == "quality" else "22" if quality == "balanced" else "23"
        return ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", gq]
    if encoder == "h264_amf":
        qp = "20" if quality == "quality" else "21" if quality == "balanced" else "22"
        return ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", qp, "-qp_p", qp]
    if encoder == "h264_mf":
        q = "85" if quality == "quality" else "82" if quality == "balanced" else "80"
        return ["-c:v", "h264_mf", "-rate_control", "quality", "-quality", q]
    if quality == "quality":
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
    if quality == "balanced":
        return ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]
    return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "22"]


def build_atempo_chain(factor: float) -> str:
    """factor > 1 = nhanh hơn, < 1 = chậm hơn (giữ đồng bộ với setpts video)."""
    parts: list[str] = []
    remaining = factor
    while remaining > 2.0 + 1e-6:
        parts.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5 - 1e-6:
        parts.append("atempo=0.5")
        remaining /= 0.5
    parts.append(f"atempo={remaining:.4f}".rstrip("0").rstrip("."))
    return ",".join(parts)


def apply_playback_speed(
    path, speed_factor, encoder_override=None, encode_quality="fast",
    log_callback=None, process_controller=None,
    progress_callback=None, progress_base=97.0, progress_span=2.9,
):
    """Sau render: đổi tốc độ video+audio cùng nhau (vẫn khớp nhau)."""
    path = Path(path)
    if abs(speed_factor - 1.0) < 0.001:
        return
    if speed_factor <= 0:
        raise ValueError("Speed phải > 0")

    try:
        from videobuilder.core.ffmpeg_setup import ensure_ffmpeg_on_path
        ensure_ffmpeg_on_path()
    except ImportError:
        pass

    encoder, encoder_label = resolve_encoder(encoder_override)
    before = get_media_duration(path)
    after = before / speed_factor
    log_msg(
        log_callback,
        f"Speed {speed_factor:.2f}x: {before:.1f}s → ~{after:.1f}s ({encoder_label})",
    )

    temp = path.with_name(f"{path.stem}._speed_tmp{path.suffix}")
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-vf", f"setpts=PTS/{speed_factor:.6f}",
        "-af", build_atempo_chain(speed_factor),
        *encoder_args(encoder, encode_quality),
        "-c:a", "aac", "-b:a", "192k",
        str(temp),
    ]
    run(
        cmd,
        log_callback=log_callback,
        process_controller=process_controller,
        progress_callback=progress_callback,
        progress_duration=before,
        progress_base=progress_base,
        progress_span=progress_span,
    )
    temp.replace(path)


def strip_video_metadata(
    path, log_callback=None, process_controller=None,
    progress_callback=None, progress_base=97.0, progress_span=2.9,
):
    """Sau render + speed: xóa metadata (copy stream, không encode lại)."""
    path = Path(path)
    try:
        from videobuilder.core.ffmpeg_setup import ensure_ffmpeg_on_path
        ensure_ffmpeg_on_path()
    except ImportError:
        pass

    duration = get_media_duration(path)
    log_msg(log_callback, "Xóa metadata (title, encoder, handler...)")

    temp = path.with_name(f"{path.stem}._meta_tmp{path.suffix}")
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-map", "0",
        "-c", "copy",
        *strip_metadata_flags(),
        str(temp),
    ]
    run(
        cmd,
        log_callback=log_callback,
        process_controller=process_controller,
        progress_callback=progress_callback,
        progress_duration=duration,
        progress_base=progress_base,
        progress_span=progress_span,
    )
    temp.replace(path)


def _encode_zoom_scene_clip(
    image, duration_sec, out_path, fps, width, height, zoom_level,
    encoder, encode_quality, log_callback=None, process_controller=None,
    progress_callback=None, progress_duration=None, progress_wall_budget=None,
    progress_base=0, progress_span=0,
):
    vf = ken_burns_vf(width, height, fps, zoom_level, duration_sec)
    if not vf:
        raise ValueError("zoom_level phải bật")
    d = max(2, int(round(max(0.1, duration_sec) * fps)))
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(Path(image).resolve()),
        "-vf", vf,
        "-an", "-frames:v", str(d),
        *encoder_args(encoder, encode_quality),
        str(out_path),
    ]
    dur = max(0.1, duration_sec)
    run(
        cmd,
        log_callback=log_callback,
        process_controller=process_controller,
        progress_callback=progress_callback,
        progress_duration=progress_duration or dur,
        progress_wall_budget=progress_wall_budget,
        progress_base=progress_base,
        progress_span=progress_span,
    )


def _concat_clips_copy(clip_paths, output_path, log_callback=None, process_controller=None):
    if len(clip_paths) == 1:
        shutil.copy2(clip_paths[0], output_path)
        return
    workdir = Path(output_path).parent
    concat_list = workdir / f"clips_{Path(output_path).stem}.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in clip_paths),
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(output_path),
    ]
    run(cmd, log_callback=log_callback, process_controller=process_controller)


def _encode_wall_budget(progress_duration: float | None, progress_span: float) -> float:
    """Ước lượng thời gian encode thực (wall clock) — scene zoom thường >> độ dài clip."""
    dur = max(0.1, float(progress_duration or 0))
    span = float(progress_span)
    if span <= 3.0:
        return max(3.0, dur * 20.0)
    if span <= 20.0:
        return max(5.0, dur * 12.0)
    if span <= 50.0:
        return max(8.0, dur * 5.0)
    return max(10.0, dur * 2.5)


def _render_zoom_scene_clips(
    pairs, durations, workdir, fps, width, height, zoom_level,
    encoder, encode_quality, progress_callback=None, progress_base=5, progress_span=65,
    log_callback=None, process_controller=None, name_prefix="zoom",
):
    clips = []
    n = len(pairs)
    if n == 0:
        return clips

    durs = [max(0.1, float(d)) for d in durations]
    scene_span = progress_span / n
    for i, ((img, _, _), dur) in enumerate(zip(pairs, durs)):
        if process_controller:
            process_controller.raise_if_cancelled()
        clip = workdir / f"{name_prefix}_{i:05d}.mp4"
        base = progress_base + i * scene_span
        report_progress(progress_callback, base, f"Zoom scene {i + 1}/{n}...")
        _encode_zoom_scene_clip(
            img, dur, clip, fps, width, height, zoom_level,
            encoder, encode_quality, log_callback, process_controller,
            progress_callback=progress_callback,
            progress_duration=dur,
            progress_base=base,
            progress_span=scene_span,
        )
        clips.append(clip)
    return clips


def scale_filter_chain(index, width, height, fps, zoom_level="off", scene_duration=1.0):
    kb = ken_burns_chain(width, height, fps, zoom_level, scene_duration)
    if kb:
        return f"[{index}:v]{kb}[v{index}]"
    parts = [
        f"{p}:flags=bilinear" if p.startswith("scale=") else p
        for p in scale_geometry_parts(width, height, zoom_level)
    ]
    chain = ",".join(parts + ["setsar=1", f"fps={fps}", "format=yuv420p"])
    return f"[{index}:v]{chain}[v{index}]"


def _append_scene_inputs(cmd, pairs, durations, fps, zoom_level):
    use_zoom = zoom_level and zoom_level != "off"
    for (img, _, _), dur in zip(pairs, durations):
        if use_zoom:
            cmd += ["-loop", "1", "-i", str(img.resolve())]
        else:
            cmd += [
                "-framerate", str(fps),
                "-loop", "1",
                "-t", f"{dur:.3f}",
                "-i", str(img.resolve()),
            ]
    return cmd


def _run_xfade_pass(
    pairs, durations, workdir, fps, width, height, zoom_level,
    transition, boundary_types, encoder, encode_quality, output_path,
    log_callback=None, process_controller=None,
    progress_callback=None, progress_base=5, progress_span=73,
):
    n = len(pairs)
    if len(boundary_types) != max(0, n - 1):
        raise ValueError(f"Can {n - 1} hieu ung, co {len(boundary_types)}")

    scene_durs = [max(0.1, end - start) for _, start, end in pairs]
    use_zoom = zoom_level and zoom_level != "off"
    zoom_span = progress_span * 0.55 if use_zoom else 0.0
    encode_span = progress_span - zoom_span
    encode_base = progress_base + zoom_span
    total_dur = max(1.0, sum(scene_durs) - transition * max(0, n - 1))

    if use_zoom:
        log_msg(log_callback, f"Zoom + hiệu ứng: render {n} scene rồi crossfade...")
        clip_paths = _render_zoom_scene_clips(
            pairs, durations, workdir, fps, width, height, zoom_level,
            encoder, encode_quality, progress_callback, progress_base, zoom_span,
            log_callback, process_controller, name_prefix="xfade_zoom",
        )
        scale_parts = [
            f"[{i}:v]fps={fps},setsar=1,format=yuv420p[v{i}]"
            for i in range(n)
        ]
        cmd = ["ffmpeg", "-y"]
        for clip in clip_paths:
            cmd += ["-i", str(clip)]
    else:
        scale_parts = [
            scale_filter_chain(i, width, height, fps, zoom_level, durations[i])
            for i in range(n)
        ]
        cmd = ["ffmpeg", "-y"]
        _append_scene_inputs(cmd, pairs, durations, fps, zoom_level)

    xfade_parts = []
    prev = "[v0]"
    for i in range(1, n):
        out = f"[vx{i}]" if i < n - 1 else "[vout]"
        offset = sum(scene_durs[:i]) - transition
        effect = boundary_types[i - 1]
        xfade_parts.append(
            f"{prev}[v{i}]xfade=transition={effect}:duration={transition:.3f}:"
            f"offset={max(0.0, offset):.3f}{out}"
        )
        prev = out

    filter_path = workdir / f"xfade_{output_path.stem}.txt"
    filter_path.write_text(";\n".join(scale_parts + xfade_parts), encoding="utf-8")

    cmd += [
        "-filter_complex_script", str(filter_path),
        "-map", "[vout]",
        "-r", str(fps),
        *encoder_args(encoder, encode_quality),
        str(output_path),
    ]
    report_progress(progress_callback, encode_base, f"Hiệu ứng {n} scene...")
    run(
        cmd,
        log_callback=log_callback,
        process_controller=process_controller,
        progress_callback=progress_callback,
        progress_duration=total_dur,
        progress_base=encode_base,
        progress_span=encode_span,
    )


def _run_scene_concat_pass(
    pairs, workdir, fps, width, height, zoom_level,
    encoder, encode_quality, output_path,
    log_callback=None, process_controller=None,
    progress_callback=None, progress_duration=None,
):
    """Encode từng scene (nhanh, có progress) rồi ghép copy."""
    scene_durs = [max(0.1, end - start) for _, start, end in pairs]
    n = len(pairs)
    log_msg(log_callback, f"Zoom: {n} scene — encode lần lượt (ổn định, có %)")
    clips = _render_zoom_scene_clips(
        pairs, scene_durs, workdir, fps, width, height, zoom_level,
        encoder, encode_quality, progress_callback, 5, 65,
        log_callback, process_controller,
    )
    report_progress(progress_callback, 72, f"Ghép {n} scene...")
    _concat_clips_copy(clips, output_path, log_callback, process_controller)


def _mux_burn_filter_chain(
    width, height, subtitle=None, watermark_input_idx=None, watermark_opacity=0.7,
    workdir=None, subtitle_style=None, log_callback=None,
):
    """Burn subtitle/watermark onto an already-encoded video (no re-scale geometry)."""
    parts = ["setsar=1"]
    if subtitle:
        parts.append(subtitle_burn_chain(
            subtitle, workdir, width, height, subtitle_style, log_callback, reset_pts=True,
        ))
    if watermark_input_idx is None:
        parts.append("format=yuv420p")
        return ",".join(parts), None

    chain = ",".join(parts)
    filters = [f"[0:v]{chain}[vbase]"]
    filters.extend(watermark_overlay_parts(watermark_input_idx, width, watermark_opacity))
    return None, ";".join(filters)


def mux_audio_video(
    video_path, audio_path, output_path, encoder, encode_quality,
    width, height, fps, zoom_level="off",
    audio_volume=1.0, duration_limit=None, subtitle=None, watermark=None,
    watermark_opacity=0.7, workdir=None, subtitle_style=None, progress_callback=None, progress_duration=None,
    progress_base=82, progress_span=15, log_callback=None, process_controller=None,
):
    video_path = Path(video_path)
    audio_path = Path(audio_path)
    output_path = Path(output_path)
    subtitle_path = Path(subtitle) if subtitle else None
    watermark_path = Path(watermark) if watermark else None
    needs_reencode = bool(subtitle_path or watermark_path)

    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path)]
    wm_idx = None
    if watermark_path:
        cmd += ["-i", str(watermark_path)]
        wm_idx = 2

    if needs_reencode:
        vf_chain, filter_complex = _mux_burn_filter_chain(
            width, height,
            subtitle=subtitle_path, watermark_input_idx=wm_idx,
            watermark_opacity=watermark_opacity, workdir=workdir, subtitle_style=subtitle_style,
            log_callback=log_callback,
        )
        if filter_complex:
            cmd += ["-filter_complex", filter_complex, "-map", "[vout]"]
        else:
            cmd += ["-vf", vf_chain, "-map", "0:v"]
    else:
        cmd += ["-map", "0:v"]

    cmd += ["-map", "1:a"]
    if audio_volume is not None and abs(audio_volume - 1.0) > 0.001:
        cmd += ["-af", f"volume={audio_volume:.3f}"]
    if duration_limit and duration_limit > 0:
        cmd += ["-t", f"{duration_limit:.3f}"]

    if needs_reencode:
        cmd += [*encoder_args(encoder, encode_quality), "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]

    cmd += ["-shortest", str(output_path)]
    run(
        cmd,
        progress_callback=progress_callback,
        progress_duration=progress_duration,
        progress_base=progress_base,
        progress_span=progress_span,
        log_callback=log_callback,
        process_controller=process_controller,
    )


def build_video_with_transitions(
    pairs, audio, output, workdir, fps, width, height, zoom_level, transition, transition_type,
    progress_callback=None, encoder="libx264", encode_quality="fast", transition_seed=None,
    audio_volume=1.0, duration_limit=None, subtitle=None, watermark=None, watermark_opacity=0.7,
    subtitle_style=None, log_callback=None, process_controller=None,
):
    n = len(pairs)
    last_idx = n - 1
    durations = []
    for i, (_, start, end) in enumerate(pairs):
        scene_dur = max(0.1, end - start)
        durations.append(scene_dur + transition if i < last_idx else scene_dur)

    total_duration = sum(durations) - transition * max(0, n - 1)
    boundary_types = build_boundary_transitions(n - 1, transition_type, transition_seed)
    if transition_type == "random":
        used = ", ".join(sorted(set(boundary_types)))
        log_msg(log_callback, f"Random transitions: {used}")

    chunk_size = 20

    if n <= chunk_size:
        report_progress(progress_callback, 5, f"Hiệu ứng {n} scene...")
        temp_video = workdir / "video_noaudio.mp4"
        _run_xfade_pass(
            pairs, durations, workdir, fps, width, height, zoom_level,
            transition, boundary_types, encoder, encode_quality, temp_video,
            log_callback=log_callback, process_controller=process_controller,
            progress_callback=progress_callback, progress_base=5, progress_span=73,
        )
    else:
        chunks = [pairs[i:i + chunk_size] for i in range(0, n, chunk_size)]
        chunk_files = []
        report_progress(progress_callback, 5, f"Hiệu ứng {n} scene ({len(chunks)} đợt)...")
        offset = 0
        for idx, chunk_pairs in enumerate(chunks):
            if process_controller:
                process_controller.raise_if_cancelled()
            chunk_durations = []
            for j, (_, start, end) in enumerate(chunk_pairs):
                scene_dur = max(0.1, end - start)
                global_i = offset + j
                chunk_durations.append(scene_dur + transition if global_i < last_idx else scene_dur)

            chunk_boundaries = boundary_types[offset:offset + len(chunk_pairs) - 1]
            chunk_out = workdir / f"chunk_{idx:03d}.mp4"
            pct = 5 + (idx / len(chunks)) * 70
            report_progress(progress_callback, pct, f"Đợt {idx + 1}/{len(chunks)}...")
            _run_xfade_pass(
                chunk_pairs, chunk_durations, workdir, fps, width, height, zoom_level,
                transition, chunk_boundaries, encoder, encode_quality, chunk_out,
                log_callback=log_callback, process_controller=process_controller,
                progress_callback=progress_callback,
                progress_base=pct, progress_span=70 / len(chunks),
            )
            chunk_files.append(chunk_out)
            offset += len(chunk_pairs)

        concat_path = workdir / "chunks.txt"
        concat_path.write_text(
            "\n".join(f"file '{p.resolve().as_posix()}'" for p in chunk_files),
            encoding="utf-8",
        )
        temp_video = workdir / "video_noaudio.mp4"
        report_progress(progress_callback, 78, "Ghép các đợt (không encode lại)...")
        run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path), "-c", "copy", str(temp_video)],
            log_callback=log_callback, process_controller=process_controller,
        )

    report_progress(progress_callback, 82, "Gắn audio...")
    mux_duration = duration_limit if duration_limit and duration_limit > 0 else max(total_duration, 1.0)
    mux_audio_video(
        temp_video, audio, output, encoder, encode_quality,
        width, height, fps, zoom_level="off",
        audio_volume=audio_volume, duration_limit=duration_limit,
        subtitle=subtitle, watermark=watermark, watermark_opacity=watermark_opacity,
        workdir=workdir, subtitle_style=subtitle_style, log_callback=log_callback,
        progress_callback=progress_callback, progress_duration=mux_duration,
        progress_base=82, progress_span=15,
        process_controller=process_controller,
    )
    report_progress(progress_callback, PROGRESS_RENDER_MAX, "Render xong, hoàn tất...")


def _start_stderr_reader(proc):
    holder = []
    if proc.stderr is None:
        return holder, None

    def reader():
        assert proc.stderr is not None
        for line in proc.stderr:
            holder.append(line.rstrip("\n"))

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    return holder, thread


def run(
    cmd, progress_callback=None, progress_duration=None, progress_wall_budget=None,
    progress_base=0, progress_span=100,
    log_callback=None, process_controller=None,
):
    cmd_str = " ".join(str(x) for x in cmd)
    log_msg(log_callback, f"> {cmd_str}")

    if process_controller:
        process_controller.raise_if_cancelled()

    use_progress = progress_callback is not None and "ffmpeg" in str(cmd[0])
    if not use_progress and process_controller is None:
        try:
            proc = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if proc.stdout:
                for line in proc.stdout.strip().splitlines()[-5:]:
                    log_msg(log_callback, line)
        except subprocess.CalledProcessError as err:
            detail = (err.stderr or err.stdout or "").strip()
            if detail:
                for line in detail.splitlines()[-20:]:
                    log_msg(log_callback, line, "error")
            log_msg(log_callback, f"FFmpeg lỗi (mã {err.returncode})", "error")
            raise
        return

    full_cmd = list(cmd)
    if use_progress:
        full_cmd += ["-progress", "pipe:1", "-nostats"]

    proc = subprocess.Popen(full_cmd, **_popen_kwargs())
    if process_controller:
        process_controller.attach(proc)

    stderr_holder, stderr_thread = _start_stderr_reader(proc)
    rc = 1
    try:
        if use_progress and progress_callback and progress_span > 0:
            progress_callback(progress_base + progress_span * 0.02, "Đang encode... 0.0%")
        if use_progress:
            encode_t0 = time.time()
            last_sent = [progress_base - 1.0]
            last_tick_t = [encode_t0]
            budget_holder = [
                float(progress_wall_budget)
                if progress_wall_budget
                else _encode_wall_budget(progress_duration, progress_span)
            ]
            stop_ticker = threading.Event()

            def tick_encode_progress(ratio=None, *, force=False):
                if not progress_callback or progress_span <= 0:
                    return
                now = time.time()
                elapsed = now - encode_t0
                budget = budget_holder[0]
                if elapsed > budget * 0.72:
                    budget_holder[0] = max(budget, elapsed / 0.75)
                    budget = budget_holder[0]
                wall_ratio = min(0.998, elapsed / budget)

                if ratio is None:
                    ratio = wall_ratio
                else:
                    ratio = max(ratio, wall_ratio * 0.97)

                ratio = min(1.0, max(0.0, ratio))
                pct = progress_base + ratio * progress_span
                cap = PROGRESS_FINALE_MAX if progress_base >= 97.0 else PROGRESS_RENDER_MAX
                pct = min(max(last_sent[0], pct), cap)
                min_step = max(0.004, progress_span * 0.015)
                if not force and pct - last_sent[0] < min_step and (now - last_tick_t[0]) < 0.05:
                    return
                last_sent[0] = pct
                last_tick_t[0] = now
                if progress_base >= 97.0:
                    progress_callback(pct, f"Hoàn tất... {pct:.1f}%")
                else:
                    progress_callback(pct, f"Đang encode... {ratio * 100:.1f}%")

            def progress_ticker():
                while not stop_ticker.is_set():
                    tick_encode_progress()
                    stop_ticker.wait(0.06)

            ticker_thread = threading.Thread(target=progress_ticker, daemon=True)
            ticker_thread.start()
            try:
                for line in _iter_process_stdout(proc, process_controller):
                    if line.startswith("out_time_ms=") and progress_duration and progress_duration > 0:
                        try:
                            current = int(line.strip().split("=", 1)[1]) / 1_000_000
                            tick_encode_progress(min(1.0, current / progress_duration))
                        except ValueError:
                            pass
            finally:
                stop_ticker.set()
                tick_encode_progress(1.0, force=True)
                ticker_thread.join(timeout=1.0)
        else:
            for _line in _iter_process_stdout(proc, process_controller):
                pass

        rc = _wait_process(proc, process_controller)
    finally:
        if stderr_thread is not None:
            stderr_thread.join(timeout=5)
        if process_controller:
            process_controller.detach()

    stderr_lines = stderr_holder

    if process_controller and process_controller.cancelled:
        process_controller.raise_if_cancelled()

    if rc != 0:
        for line in stderr_lines[-25:]:
            log_msg(log_callback, line, "error")
        log_msg(log_callback, f"FFmpeg lỗi (mã {rc})", "error")
        raise subprocess.CalledProcessError(rc, cmd)


def parse_timeline_input(value: str):
    value = (value or "").strip()
    if not value:
        return None
    if ":" in value:
        return parse_time_to_seconds(value)
    return float(value)


def log_msg(callback, message, level="info"):
    text = str(message).strip()
    if not text:
        return
    print(text)
    if callback:
        callback(text, level)


def parse_time_to_seconds(t: str) -> float:
    """
    Accept:
    00:04
    01:23
    00:01:23
    """
    t = t.strip()
    parts = t.split(":")
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    raise ValueError(f"Bad time format: {t}")


def parse_mmss_dot(mm: str, ss: str) -> float:
    return int(mm) * 60 + int(ss)


def parse_scene_bracket_time(mm: str, sep: str, ss: str, frac: str | None = None) -> float:
    """MM.SS (legacy) hoặc MM:SS[.fff] — giây lẻ từ SRT."""
    base = int(mm) * 60 + int(ss)
    if frac:
        base += int(frac) / (10 ** len(frac))
    return base


SCENE_LINE_RE = re.compile(
    r"^(\d{3})_\["
    r"(\d{2})([:.])(\d{2})(?:\.(\d{1,3}))?"
    r"\s*[–-]\s*"
    r"(\d{2})([:.])(\d{2})(?:\.(\d{1,3}))?"
    r"\]",
    re.IGNORECASE,
)

BRACKET_RANGE_RE = re.compile(
    r"\[(\d{2}:\d{2}(?:\.\d{1,3})?(?::\d{2})?)\s*[–-]\s*(\d{2}:\d{2}(?:\.\d{1,3})?(?::\d{2})?)\]",
)


def _format_time_short(seconds: float) -> str:
    seconds = max(0.0, seconds)
    whole = int(seconds)
    if whole >= 3600:
        h = whole // 3600
        m = (whole % 3600) // 60
        s = whole % 60
        return f"{h:02d}:{m:02d}:{s:02d}"
    m = whole // 60
    s = whole % 60
    return f"{m:02d}:{s:02d}"


class MissingSceneImagesError(RuntimeError):
    """Thiếu ảnh cho một hoặc nhiều scene — message liệt kê chi tiết."""

    def __init__(self, missing: list[int], scenes, images_dir: Path):
        self.missing = list(missing)
        self.scenes = list(scenes)
        self.images_dir = Path(images_dir)
        super().__init__(format_missing_images_message(missing, scenes, images_dir))


def format_missing_images_message(missing: list[int], scenes, images_dir: Path) -> str:
    times = {n: (s, e) for n, s, e in scenes}
    lines = [
        f"Thiếu {len(missing)} ảnh:",
        "",
    ]
    for num in sorted(missing):
        if num in times:
            s, e = times[num]
            tr = f" [{_format_time_short(s)}–{_format_time_short(e)}]"
        else:
            tr = ""
        if num == 1:
            hint = f"{num:03d}_...jpg hoặc CHARACTER REFERENCE"
        else:
            hint = f"{num:03d}_...jpg"
        lines.append(f"  • Scene {num:03d}{tr}  →  {hint}")
    lines.append("")
    lines.append(f"Thư mục:\n{images_dir.resolve()}")
    return "\n".join(lines)


def index_images_by_scene(images_dir: Path):
    images = list_images(images_dir)
    by_scene: dict[int, Path] = {}
    for img in images:
        if is_reference_image(img):
            continue
        scene_num = parse_image_scene_num(img)
        if scene_num is not None:
            by_scene[scene_num] = img
    references = sorted(
        [p for p in images if is_reference_image(p)],
        key=image_sort_key,
    )
    return by_scene, references


def resolve_scene_image(scene_num: int, by_scene: dict, references: list):
    if scene_num == 1 and references:
        return references[0]
    return by_scene.get(scene_num)


def find_missing_scene_images(scenes, images_dir: Path) -> list[int]:
    by_scene, references = index_images_by_scene(images_dir)
    missing: list[int] = []
    seen: set[int] = set()
    for scene_num, _start, _end in scenes:
        if scene_num in seen:
            continue
        seen.add(scene_num)
        if resolve_scene_image(scene_num, by_scene, references) is None:
            missing.append(scene_num)
    return sorted(missing)


def validate_scene_images(scenes, images_dir: Path):
    missing = find_missing_scene_images(scenes, images_dir)
    if missing:
        raise MissingSceneImagesError(missing, scenes, images_dir)


def parse_bracket_time_range(text: str):
    pattern = r"\[(\d{2}:\d{2}(?::\d{2})?)\s*[–-]\s*(\d{2}:\d{2}(?::\d{2})?)\]"
    match = re.search(pattern, text)
    if not match or "CHARACTER REFERENCE" in text:
        return None
    start = parse_time_to_seconds(match.group(1))
    end = parse_time_to_seconds(match.group(2))
    if end > start:
        return start, end
    return None


def parse_prompt_scenes(prompt_file: Path, audio_duration: float):
    text = prompt_file.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    scenes = []
    has_scene_lines = any(SCENE_LINE_RE.match(ln.strip()) for ln in lines)

    if has_scene_lines:
        for line in lines:
            line = line.strip()
            if not line or re.match(r"^\d{3}_\[CHARACTER\s+REFERENCE\]", line, re.I):
                continue
            match = SCENE_LINE_RE.match(line)
            if match:
                scene_num = int(match.group(1))
                start = parse_scene_bracket_time(
                    match.group(2), match.group(3), match.group(4), match.group(5),
                )
                end = parse_scene_bracket_time(
                    match.group(6), match.group(7), match.group(8), match.group(9),
                )
                if end > start:
                    scenes.append((scene_num, start, end))
        if scenes:
            scenes.sort(key=lambda x: (x[1], x[0]))
            return scenes

    ranges = []
    for start, end in BRACKET_RANGE_RE.findall(text):
        s = parse_time_to_seconds(start)
        e = parse_time_to_seconds(end)
        if e > s:
            ranges.append((s, e))

    if ranges:
        return [(i + 1, s, e) for i, (s, e) in enumerate(ranges)]

    raise RuntimeError("Không tìm thấy scene trong file prompt.")


def scenes_to_timeline_pairs(scenes, images_dir: Path, total_duration: float):
    """Map scene markers to contiguous absolute (image, start, end) on [0, total_duration]."""
    sorted_scenes = sorted(scenes, key=lambda x: (x[1], x[0]))
    missing = find_missing_scene_images(sorted_scenes, images_dir)
    if missing:
        raise MissingSceneImagesError(missing, sorted_scenes, images_dir)

    by_scene, references = index_images_by_scene(images_dir)
    pairs = []
    cursor = 0.0

    for scene_num, start, end in sorted_scenes:
        img = resolve_scene_image(scene_num, by_scene, references)
        if img is None:
            raise MissingSceneImagesError([scene_num], sorted_scenes, images_dir)

        start = max(0.0, float(start))
        end = min(float(end), total_duration)
        if end <= start + 0.001:
            continue

        if start < cursor - 0.001:
            start = cursor
        if end <= start + 0.001:
            continue

        if not pairs and start > 0.001:
            start = 0.0

        if start > cursor + 0.001:
            if pairs:
                last_img, last_start, _ = pairs[-1]
                pairs[-1] = (last_img, last_start, start)
            cursor = start

        pairs.append((img, start, end))
        cursor = end

    if not pairs:
        raise RuntimeError("Không có scene nào để ghép.")

    if cursor < total_duration - 0.001:
        last_img, last_start, _ = pairs[-1]
        pairs[-1] = (last_img, last_start, total_duration)

    return pairs


def validate_contiguous_pairs(pairs, total_duration, log_callback=None):
    """Ensure absolute segments are back-to-back and span the full output duration."""
    if not pairs:
        raise RuntimeError("Timeline rỗng.")
    if pairs[0][1] > 0.05:
        raise RuntimeError(f"Timeline không bắt đầu từ 0 (bắt đầu {pairs[0][1]:.2f}s).")

    span = 0.0
    for i, (img, start, end) in enumerate(pairs):
        if end <= start + 0.001:
            raise RuntimeError(f"Segment {i} ({img.name}) có độ dài không hợp lệ.")
        if i > 0 and abs(start - pairs[i - 1][2]) > 0.05:
            raise RuntimeError(
                f"Lệch timeline tại {img.name}: {start:.2f}s != {pairs[i - 1][2]:.2f}s"
            )
        span += end - start

    if abs(span - total_duration) > 0.15:
        log_msg(
            log_callback,
            f"Cảnh báo: tổng timeline {span:.2f}s, mục tiêu {total_duration:.2f}s",
            "warn",
        )
    return span


def list_images(images_dir: Path):
    images_dir = Path(images_dir)
    images = [p for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS]
    if not images:
        images = [p for p in images_dir.rglob("*") if p.suffix.lower() in IMAGE_EXTS]
    if not images:
        raise RuntimeError(f"Không tìm thấy ảnh trong thư mục: {images_dir}")
    return images


def parse_image_scene_num(path: Path):
    match = re.match(r"^(\d{3})_", path.name)
    if match:
        return int(match.group(1))
    match = re.match(r"^(\d+)", path.name)
    return int(match.group(1)) if match else None


def is_reference_image(path: Path):
    upper = path.name.upper()
    return "CHARACTER REFERENCE" in upper or "CHARACTER-REFERENCE" in upper


def build_scene_pairs(images_dir: Path, scenes, audio_duration: float):
    return scenes_to_timeline_pairs(scenes, images_dir, audio_duration)


def detect_resolution_from_images(images_dir: Path) -> tuple[int, int]:
    for img in sorted(list_images(images_dir), key=image_sort_key):
        if is_reference_image(img):
            continue
        return get_image_size(img)
    raise RuntimeError("Không có ảnh scene để tự nhận độ phân giải.")


def get_image_size(path: Path) -> tuple[int, int]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        str(path),
    ]
    out = subprocess.check_output(
        cmd,
        text=True,
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    ).strip()
    if "x" not in out:
        raise RuntimeError(f"Không đọc được kích thước: {path.name}")
    width, height = (int(v) for v in out.split("x", 1))
    width -= width % 2
    height -= height % 2
    return max(2, width), max(2, height)


def image_sort_key(path: Path):
    match = re.match(r"(\d+)", path.name)
    return int(match.group(1)) if match else 0


def scale_vf(
    width, height, fps, zoom_level="off", subtitle=None, workdir=None,
    subtitle_style=None, log_callback=None,
):
    parts = list(scale_geometry_parts(width, height, zoom_level))
    parts.append("setsar=1")
    if subtitle:
        parts.append(subtitle_burn_chain(
            subtitle, workdir, width, height, subtitle_style, log_callback,
            fps=fps, reset_pts=True,
        ))
    parts.append("format=yuv420p")
    return ",".join(parts)


def build_fast_video(
    pairs, audio, output, workdir, fps, width, height, zoom_level, encoder, encode_quality,
    audio_volume=1.0, duration_limit=None, subtitle=None, watermark=None, watermark_opacity=0.7,
    subtitle_style=None, progress_callback=None, progress_duration=None, log_callback=None, process_controller=None,
):
    subtitle_path = Path(subtitle) if subtitle else None
    watermark_path = Path(watermark) if watermark else None
    used = len(pairs)

    if zoom_level and zoom_level != "off":
        report_progress(progress_callback, 5, "Zoom Ken Burns — encode từng scene...")
        temp_video = workdir / "video_zoom_noaudio.mp4"
        _run_scene_concat_pass(
            pairs, workdir, fps, width, height, zoom_level,
            encoder, encode_quality, temp_video,
            log_callback=log_callback, process_controller=process_controller,
            progress_callback=progress_callback, progress_duration=progress_duration,
        )
        report_progress(progress_callback, 70, "Ghép audio + phụ đề...")
        mux_audio_video(
            temp_video, audio, output, encoder, encode_quality,
            width, height, fps, zoom_level="off",
            audio_volume=audio_volume, duration_limit=duration_limit,
            subtitle=subtitle_path, watermark=watermark_path, watermark_opacity=watermark_opacity,
            workdir=workdir, subtitle_style=subtitle_style,
            progress_callback=progress_callback, progress_duration=progress_duration,
            progress_base=70, progress_span=27,
            log_callback=log_callback, process_controller=process_controller,
        )
        return used

    concat_file, used, video_span = create_concat_file(pairs, workdir)
    cmd = ["ffmpeg", "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0", "-i", str(concat_file)]
    if watermark_path:
        cmd += ["-i", str(watermark_path)]
    cmd += ["-i", str(audio)]
    audio_idx = 2 if watermark_path else 1

    if watermark_path:
        base_vf = scale_vf(
            width, height, fps, zoom_level, subtitle_path, workdir, subtitle_style, log_callback,
        )
        base_vf = base_vf.replace(",format=yuv420p", "")
        wm_parts = watermark_overlay_parts(1, width, watermark_opacity)
        filter_complex = f"[0:v]{base_vf}[vbase];{wm_parts[0]};{wm_parts[1]}"
        cmd += ["-filter_complex", filter_complex, "-map", "[vout]", "-map", f"{audio_idx}:a"]
    else:
        cmd += [
            "-vf", scale_vf(
                width, height, fps, zoom_level, subtitle_path, workdir, subtitle_style, log_callback,
            ),
            "-map", "0:v", "-map", f"{audio_idx}:a",
        ]

    if audio_volume is not None and abs(audio_volume - 1.0) > 0.001:
        cmd += ["-af", f"volume={audio_volume:.3f}"]
    if duration_limit and duration_limit > 0:
        cmd += ["-t", f"{duration_limit:.3f}"]

    cmd += [
        "-r", str(fps),
        *encoder_args(encoder, encode_quality),
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(output),
    ]
    run(
        cmd,
        progress_callback=progress_callback,
        progress_duration=progress_duration,
        progress_base=5,
        progress_span=92,
        log_callback=log_callback,
        process_controller=process_controller,
    )
    return used


def strip_metadata_flags():
    return [
        "-map_metadata", "-1",
        "-metadata", "title=",
        "-metadata", "artist=",
        "-metadata", "album=",
        "-metadata", "comment=",
        "-metadata", "description=",
        "-metadata", "encoder=",
        "-metadata:s:v:0", "handler_name=",
        "-metadata:s:v:0", "encoder=",
        "-metadata:s:a:0", "handler_name=",
        "-metadata:s:a:0", "encoder=",
    ]


def get_media_duration(path: Path) -> float:
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file: {path}")

    try:
        from videobuilder.core.ffmpeg_setup import ensure_ffmpeg_on_path, resolve_ffprobe
        ensure_ffmpeg_on_path()
        ffprobe = resolve_ffprobe()
    except ImportError:
        ffprobe = shutil.which("ffprobe")

    if not ffprobe:
        raise RuntimeError("Chưa có ffprobe — cài FFmpeg trước.")

    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

    def probe(extra: list[str]) -> str:
        return subprocess.check_output(
            [ffprobe, "-v", "error", *extra, str(path)],
            text=True,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        ).strip()

    last_err: Exception | None = None
    for extra in (
        ["-show_entries", "format=duration", "-of", "default=nk=1:nw=1"],
        ["-select_streams", "a:0", "-show_entries", "stream=duration", "-of", "default=nk=1:nw=1"],
    ):
        try:
            out = probe(extra)
            if not out or out.lower() in ("n/a", "nan"):
                continue
            duration = float(out.splitlines()[0].strip())
            if duration > 0:
                return duration
        except (subprocess.CalledProcessError, ValueError) as err:
            last_err = err

    try:
        raw = probe(["-show_format", "-show_streams", "-of", "json"])
        data = json.loads(raw)
        fmt = data.get("format", {}).get("duration")
        if fmt and float(fmt) > 0:
            return float(fmt)
        for stream in data.get("streams", []):
            if stream.get("codec_type") != "audio":
                continue
            val = stream.get("duration")
            if val and float(val) > 0:
                return float(val)
    except (subprocess.CalledProcessError, ValueError, json.JSONDecodeError) as err:
        last_err = err

    raise RuntimeError(f"Không đọc được độ dài audio: {path.name}") from last_err


def create_concat_file(pairs, workdir: Path):
    concat_path = workdir / "concat.txt"

    lines = []
    for img, start, end in pairs:
        img = img.resolve()
        dur = max(0.1, end - start)
        lines.append(f"file '{img.as_posix()}'")
        lines.append(f"duration {dur:.3f}")

    lines.append(f"file '{pairs[-1][0].resolve().as_posix()}'")

    concat_path.write_text("\n".join(lines), encoding="utf-8")
    total = sum(max(0.1, end - start) for _, start, end in pairs)
    return concat_path, len(pairs), total


def build_video(
    audio,
    prompts,
    images_dir,
    output,
    fps=30,
    width=1920,
    height=1080,
    zoom=False,
    zoom_level="off",
    transition=0.0,
    transition_type="fade",
    timeline_duration=None,
    progress_callback=None,
    transition_seed=None,
    encode_quality="fast",
    encoder_override=None,
    audio_volume=DEFAULT_AUDIO_VOLUME,
    watermark=None,
    watermark_opacity=DEFAULT_WATERMARK_OPACITY,
    subtitle=None,
    subtitle_style=None,
    preview_seconds=None,
    log_callback=None,
    process_controller=None,
):
    audio = Path(audio)
    prompts = Path(prompts)
    images_dir = Path(images_dir)
    output = Path(output)
    zoom_level = resolve_zoom_level(zoom=zoom, zoom_level=zoom_level)
    reset_progress_floor()

    try:
        from videobuilder.core.ffmpeg_setup import ensure_ffmpeg_on_path
        ensure_ffmpeg_on_path()
    except ImportError:
        pass

    subtitle_path = Path(subtitle) if subtitle else None
    watermark_path = Path(watermark) if watermark else None
    if subtitle_path and not subtitle_path.is_file():
        raise ValueError(f"Không tìm thấy file phụ đề: {subtitle_path}")
    if watermark_path and not watermark_path.is_file():
        raise ValueError(f"Không tìm thấy watermark: {watermark_path}")
    if subtitle_path:
        style = subtitle_style or DEFAULT_SUBTITLE_STYLE
        font_px = style.font_size or subtitle_font_size(height)
        offset = f", lệch {style.offset_sec:+.2f}s" if abs(style.offset_sec) > 0.001 else ""
        log_msg(
            log_callback,
            f"Phụ đề: {subtitle_path.name} (font {font_px}px, lề {style.margin_v}px{offset})",
        )
    if watermark_path:
        wm_w = max(48, min(320, int(width * 0.18)))
        log_msg(log_callback, f"Watermark: {watermark_path.name} (~{wm_w}px, góc phải dưới)")

    audio_duration = get_media_duration(audio)
    report_progress(progress_callback, 1.2, "Đọc audio & timeline...")
    output_duration = audio_duration
    if timeline_duration and timeline_duration > 0:
        output_duration = min(timeline_duration, audio_duration)

    report_progress(progress_callback, 2.0, "Đọc file prompt...")
    scenes = parse_prompt_scenes(prompts, output_duration)
    report_progress(progress_callback, 2.6, f"Map {len(scenes)} scene...")
    pairs = build_scene_pairs(images_dir, scenes, output_duration)
    report_progress(progress_callback, 3.2, "Kiểm tra ảnh...")
    validate_scene_images(scenes, images_dir)
    timeline_span = validate_contiguous_pairs(pairs, output_duration, log_callback)
    report_progress(progress_callback, 3.8, f"Chuẩn bị {len(pairs)} scene...")

    duration_limit = None
    if preview_seconds and preview_seconds > 0:
        pairs = trim_pairs_preview(pairs, preview_seconds)
        duration_limit = preview_seconds
        output_duration = preview_seconds
        timeline_span = sum(max(0.1, end - start) for _, start, end in pairs)

    log_msg(log_callback, f"Audio duration: {audio_duration:.2f}s")
    log_msg(log_callback, f"Output duration: {output_duration:.2f}s (mốc tuyệt đối)")
    log_msg(log_callback, f"Video timeline span: {timeline_span:.2f}s")
    if duration_limit:
        log_msg(log_callback, f"Preview mode: {duration_limit:.2f}s")
        if subtitle_path:
            try:
                cues = parse_srt_file(subtitle_path)
                cue_in = sum(1 for s, e, _ in cues if s < duration_limit)
                log_msg(
                    log_callback,
                    f"  Phụ đề trong preview: {cue_in}/{len(cues)} cue (render full để thấy hết)",
                )
            except ValueError:
                pass
    log_msg(log_callback, f"Prompt scenes: {len(scenes)}")
    log_msg(log_callback, f"Timeline segments: {len(pairs)}")
    if pairs:
        log_msg(log_callback, f"First segment: {pairs[0][1]:.2f}-{pairs[0][2]:.2f}s -> {pairs[0][0].name}")
        log_msg(log_callback, f"Total video span: {pairs[-1][2]:.2f}s")
    if transition > 0:
        min_scene = min(max(0.1, end - start) for _, start, end in pairs)
        if transition >= min_scene:
            log_msg(
                log_callback,
                f"Cảnh báo: hiệu ứng {transition:.2f}s dài hơn scene ngắn nhất ({min_scene:.2f}s) — có thể lệch.",
                "warn",
            )
        log_msg(log_callback, f"Transition: {transition}s ({transition_type})")
    else:
        if zoom_level and zoom_level != "off":
            log_msg(log_callback, "Mode: concat + Zoom (encode từng scene)")
        else:
            log_msg(log_callback, "Mode: concat nhanh (1 lần encode GPU)")
    log_msg(log_callback, f"Resolution: {width}x{height} @ {fps}fps")
    if is_portrait_output(width, height):
        if zoom_level and zoom_level != "off":
            log_msg(log_callback, "Shorts 9:16 + Zoom: crop fill, zoom in mượt từng scene")
        else:
            log_msg(log_callback, "Shorts 9:16: fit toàn ảnh (letterbox), không crop")
    if zoom_level and zoom_level != "off":
        z = ZOOM_LEVELS.get(zoom_level, 1.0)
        log_msg(log_callback, f"Zoom: {zoom_level} (~{int((z - 1) * 100)}% vào, ease mượt)")
    else:
        log_msg(log_callback, f"Zoom: {zoom_level}")
    log_msg(log_callback, f"Quality: {encode_quality}")
    if audio_volume != 1.0:
        log_msg(log_callback, f"Audio volume: {audio_volume:.2f}")

    encoder, encoder_label = resolve_encoder(encoder_override)
    log_msg(log_callback, f"Encoder: {encoder} ({encoder_label})")
    mode_msg = "Crossfade (nhiều đợt)" if transition > 0 else "Nhanh: 1 lần encode"
    report_progress(progress_callback, 4.0, f"{encoder_label} — {mode_msg}")

    encode_duration = duration_limit if duration_limit else audio_duration

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)

        if transition > 0:
            build_video_with_transitions(
                pairs, audio, output, workdir, fps, width, height,
                zoom_level, transition, transition_type, progress_callback, encoder, encode_quality,
                transition_seed, audio_volume, duration_limit, subtitle_path, watermark_path, watermark_opacity,
                subtitle_style, log_callback, process_controller,
            )
            used = len(pairs)
        else:
            report_progress(progress_callback, 5, f"1 lần encode — {encoder_label}...")
            used = build_fast_video(
                pairs, audio, output, workdir, fps, width, height, zoom_level, encoder, encode_quality,
                audio_volume, duration_limit, subtitle_path, watermark_path, watermark_opacity,
                subtitle_style, progress_callback, encode_duration, log_callback, process_controller,
            )
            report_progress(progress_callback, PROGRESS_RENDER_MAX, "Render xong, hoàn tất...")

    log_msg(log_callback, f"Done: {output}", "success")
    log_msg(log_callback, f"Used images/scenes: {used}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, help="Path to audio mp3/wav")
    parser.add_argument("--prompts", required=True, help="Path to prompt txt with [00:00–00:04] ranges")
    parser.add_argument("--images", required=True, help="Folder containing generated images")
    parser.add_argument("--output", default="final_video.mp4", help="Output mp4")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--zoom", action="store_true", help="Add very light zoompan effect")
    parser.add_argument("--zoom-level", default="off", choices=list(ZOOM_LEVELS.keys()), help="off/light/medium/strong")
    parser.add_argument("--transition", type=float, default=0, help="Crossfade seconds (e.g. 0.5)")
    parser.add_argument("--transition-type", default="fade", help="xfade: fade, smoothleft, random, ...")
    parser.add_argument("--transition-seed", type=int, default=None, help="Seed cho random transitions")
    parser.add_argument("--encode-quality", default="fast", choices=list(ENCODE_QUALITY_OPTIONS.keys()))
    parser.add_argument("--encoder", default="auto", choices=list(ENCODER_OVERRIDE_OPTIONS.keys()))
    parser.add_argument("--audio-volume", type=float, default=DEFAULT_AUDIO_VOLUME, help="1.0 = 100%")
    parser.add_argument("--watermark", default="", help="PNG watermark")
    parser.add_argument("--watermark-opacity", type=float, default=DEFAULT_WATERMARK_OPACITY)
    parser.add_argument("--subtitle", default="", help="SRT/ASS subtitle file")
    parser.add_argument("--preview-seconds", type=float, default=0, help="Preview length (0 = full)")
    parser.add_argument("--timeline", default="", help="Timeline MM:SS or seconds (default: audio length)")
    args = parser.parse_args()

    build_video(
        audio=args.audio,
        prompts=args.prompts,
        images_dir=args.images,
        output=args.output,
        fps=args.fps,
        width=args.width,
        height=args.height,
        zoom=args.zoom,
        zoom_level=args.zoom_level,
        transition=args.transition,
        transition_type=args.transition_type,
        timeline_duration=parse_timeline_input(args.timeline),
        transition_seed=args.transition_seed,
        encode_quality=args.encode_quality,
        encoder_override=args.encoder,
        audio_volume=args.audio_volume,
        watermark=args.watermark or None,
        watermark_opacity=args.watermark_opacity,
        subtitle=args.subtitle or None,
        preview_seconds=args.preview_seconds if args.preview_seconds > 0 else None,
    )


if __name__ == "__main__":
    main()
