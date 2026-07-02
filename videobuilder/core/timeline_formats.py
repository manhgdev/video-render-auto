#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""7 định dạng timestamp timeline — đọc/ghi thống nhất.

1) SRT gốc (input subtitle):
   - 00:00:00,000 --> 00:00:09,000
   - 00:00:00.000 --> 00:00:09.000

2) Timestamp trung gian / chuẩn kỹ thuật (normalize):
   - [00:00:00:000-00:00:09:000]
   - [00:00:00.000-00:00:09.000]

3) Timestamp dễ đọc cho prompt:
   - [00:00.000-00:09.000]

4) Prompt ảnh có số thứ tự:
   - 001_[00:00.000-00:09.000]

5) Prompt ảnh có chia visual beat (output chính khi nhiều ảnh/đoạn):
   - 001_[00:00.000-00:09.000]_VISUAL_01_03
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# —— SRT gốc ——
SRT_ARROW_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
)

# —— Prompt line: 001_[...] hoặc 001_[...]_VISUAL_01_03 ——
PROMPT_SCENE_LINE_RE = re.compile(
    r"^(\d{3})_\[([^\]–-]+?)\s*[–-]\s*([^\]]+?)\]"
    r"(?:_VISUAL_(\d{2})_(\d{2}))?",
    re.IGNORECASE,
)

# —— Bracket range (3 format giữa) ——
_BRACKET_TECH_COLON_RE = re.compile(
    r"\[(\d{2}:\d{2}:\d{2}:\d{3})\s*[–-]\s*(\d{2}:\d{2}:\d{2}:\d{3})\]"
)
_BRACKET_TECH_DOT_RE = re.compile(
    r"\[(\d{2}:\d{2}:\d{2}\.\d{3})\s*[–-]\s*(\d{2}:\d{2}:\d{2}\.\d{3})\]"
)
_BRACKET_READABLE_RE = re.compile(
    r"\[(\d{2}:\d{2}\.\d{3})\s*[–-]\s*(\d{2}:\d{2}\.\d{3})\]"
)
_BRACKET_LEGACY_RE = re.compile(
    r"\[(\d{2}:\d{2}(?:\.\d{1,3})?(?::\d{2}(?:\.\d{1,3})?)?)\s*[–-]\s*"
    r"(\d{2}:\d{2}(?:\.\d{1,3})?(?::\d{2}(?:\.\d{1,3})?)?)\]"
)

SINGLE_TIMESTAMP_LINE_RE = re.compile(
    r"^\[(\d{2}:\d{2}(?:\.\d{1,3})?(?::\d{2}(?:\.\d{1,3})?)?)\]",
)

# Backward compat — pipeline/generate_images import tên cũ
SCENE_LINE_RE = PROMPT_SCENE_LINE_RE
BRACKET_RANGE_RE = _BRACKET_LEGACY_RE


@dataclass(frozen=True)
class TimelineSceneEntry:
    scene_num: int
    start: float
    end: float
    visual_index: int = 1
    visual_total: int = 1
    line: str = ""


def _hms_ms(h: int, m: int, s: int, ms: int) -> float:
    return h * 3600 + m * 60 + s + ms / 1000.0


def _parse_legacy_dot_timecode(text: str) -> float:
    """Legacy dot-separated: 00.00.01.92, 00.02.00, 00.10.80, …"""
    parts = [p.strip() for p in text.split(".") if p.strip()]
    if len(parts) == 2:
        return float(int(parts[0]) * 60 + int(parts[1]))
    if len(parts) < 3:
        raise ValueError(f"timecode không hợp lệ: {text}")
    nums = [int(p) for p in parts]
    if len(nums) == 3:
        a, b, c = nums
        frac_len = len(parts[2])
        frac = c / (10 ** frac_len)
        if a == 0 and b == 0:
            if frac_len >= 3:
                return float(frac)
            return float(c)
        if a == 0:
            if frac_len >= 3:
                return float(b) + frac
            if c == 0:
                return float(b * 60)
            if c >= 60:
                return float(b) + frac
            return float(b * 60 + c)
        if frac_len >= 3:
            return float(a * 60 + b) + frac
        if c == 0:
            return float(a * 60 + b)
        if c >= 60:
            return float(a * 60 + b) + frac
        return float(a * 60 + b * 60 + c)
    if len(nums) == 4:
        a, b, c, d = nums
        frac = d / (10 ** len(parts[3]))
        if a == 0 and b == 0:
            return c + frac
        if a == 0:
            return b * 60 + c + frac
        return a * 3600 + b * 60 + c + frac
    raise ValueError(f"timecode không hợp lệ: {text}")


def parse_time_token(token: str) -> float:
    """Đọc một mốc thời gian — hỗ trợ cả 7 định dạng + legacy."""
    text = (token or "").strip()
    if not text:
        raise ValueError("timecode rỗng")

    match = re.fullmatch(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})", text)
    if match:
        return _hms_ms(*(int(g) for g in match.groups()))

    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}):(\d{3})", text)
    if match:
        return _hms_ms(*(int(g) for g in match.groups()))

    match = re.fullmatch(r"(\d{2}):(\d{2})\.(\d{3})", text)
    if match:
        mm, ss, ms = (int(g) for g in match.groups())
        return mm * 60 + ss + ms / 1000.0

    match = re.fullmatch(r"(\d{2}):(\d{2})(?:\.(\d{1,3}))?", text)
    if match:
        mm = int(match.group(1))
        ss = int(match.group(2))
        base = mm * 60 + ss
        frac = match.group(3)
        if frac:
            base += int(frac) / (10 ** len(frac))
        return float(base)

    parts = text.split(":")
    if len(parts) == 3:
        h, m, s = parts
        if "." not in s and s.isdigit() and m.isdigit() and h.isdigit():
            return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        if s.replace(".", "", 1).isdigit() and m.isdigit():
            return int(m) * 60 + float(s)

    if "." in text and ":" not in text:
        return _parse_legacy_dot_timecode(text)

    raise ValueError(f"timecode không hợp lệ: {token}")


def parse_prompt_timecode_token(token: str) -> float:
    """Alias tương thích — generate_prompts / pipeline gọi tên cũ."""
    return parse_time_token(token)


def parse_srt_arrow_line(line: str) -> tuple[float, float] | None:
    """Parse dòng SRT gốc: HH:MM:SS,mmm --> HH:MM:SS,mmm."""
    match = SRT_ARROW_RE.match((line or "").strip())
    if not match:
        return None
    start = _hms_ms(*(int(g) for g in match.groups()[:4]))
    end = _hms_ms(*(int(g) for g in match.groups()[4:]))
    if end <= start:
        return None
    return start, end


def _parse_bracket_pair(start_tok: str, end_tok: str) -> tuple[float, float] | None:
    try:
        start = parse_time_token(start_tok)
        end = parse_time_token(end_tok)
    except ValueError:
        return None
    if end > start:
        return start, end
    return None


def parse_bracket_time_range(text: str) -> tuple[float, float] | None:
    """Parse [start-end] — 3 format ngoặc + legacy."""
    if not text or "CHARACTER REFERENCE" in text:
        return None
    for pattern in (
        _BRACKET_TECH_COLON_RE,
        _BRACKET_TECH_DOT_RE,
        _BRACKET_READABLE_RE,
        _BRACKET_LEGACY_RE,
    ):
        match = pattern.search(text)
        if match:
            return _parse_bracket_pair(match.group(1), match.group(2))
    return None


def find_all_bracket_ranges(text: str) -> list[tuple[float, float]]:
    """Tìm mọi cặp [start-end] trong text."""
    ranges: list[tuple[float, float]] = []
    for pattern in (
        _BRACKET_TECH_COLON_RE,
        _BRACKET_TECH_DOT_RE,
        _BRACKET_READABLE_RE,
        _BRACKET_LEGACY_RE,
    ):
        for start_tok, end_tok in pattern.findall(text):
            pair = _parse_bracket_pair(start_tok, end_tok)
            if pair:
                ranges.append(pair)
    return ranges


def parse_prompt_scene_line(line: str) -> TimelineSceneEntry | None:
    """Parse dòng prompt: 001_[...] hoặc 001_[...]_VISUAL_01_03."""
    text = (line or "").strip()
    if not text or re.match(r"^\d{3}_\[CHARACTER\s+REFERENCE\]", text, re.I):
        return None
    match = PROMPT_SCENE_LINE_RE.match(text)
    if not match:
        return None
    scene_num = int(match.group(1))
    try:
        start = parse_time_token(match.group(2))
        end = parse_time_token(match.group(3))
    except ValueError:
        return None
    if end <= start:
        return None
    visual_index = int(match.group(4)) if match.group(4) else 1
    visual_total = int(match.group(5)) if match.group(5) else 1
    if visual_total > 1 and 1 <= visual_index <= visual_total:
        duration = end - start
        slice_len = duration / visual_total
        start = start + (visual_index - 1) * slice_len
        end = start + slice_len
    return TimelineSceneEntry(
        scene_num=scene_num,
        start=start,
        end=end,
        visual_index=visual_index,
        visual_total=visual_total,
        line=text,
    )


def format_readable_timecode(seconds: float) -> str:
    """Chuẩn prompt dễ đọc: MM:SS.mmm (vd. 00:09.000)."""
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    ms = int(round((seconds - whole) * 1000)) % 1000
    mm = whole // 60
    ss = whole % 60
    return f"{mm:02d}:{ss:02d}.{ms:03d}"


def format_readable_time_range(start: float, end: float) -> str:
    return f"{format_readable_timecode(start)}-{format_readable_timecode(end)}"


def format_prompt_line_prefix(
    index: int,
    start: float,
    end: float,
    *,
    visual_index: int = 1,
    visual_total: int = 1,
) -> str:
    """001_[00:00.000-00:09.000] hoặc kèm _VISUAL_01_03 khi nhiều ảnh/đoạn."""
    tr = format_readable_time_range(start, end)
    prefix = f"{index:03d}_[{tr}]"
    if visual_total > 1:
        prefix += f"_VISUAL_{visual_index:02d}_{visual_total:02d}"
    return prefix


def scenes_from_timeline_text(text: str, audio_duration: float) -> list[tuple[int, float, float]]:
    """Đọc file timeline — ưu tiên prompt line, bracket, SRT arrow, single timestamp."""
    lines = text.splitlines()
    has_prompt_lines = any(
        PROMPT_SCENE_LINE_RE.match(ln.strip()) for ln in lines if ln.strip()
    )

    if has_prompt_lines:
        scenes: list[tuple[int, float, float]] = []
        for line in lines:
            entry = parse_prompt_scene_line(line)
            if entry:
                scenes.append((entry.scene_num, entry.start, entry.end))
        if scenes:
            scenes.sort(key=lambda x: (x[1], x[0]))
            return scenes

    ranges = find_all_bracket_ranges(text)
    if ranges:
        return [(i + 1, s, e) for i, (s, e) in enumerate(ranges)]

    srt_scenes: list[tuple[int, float, float]] = []
    for line in lines:
        pair = parse_srt_arrow_line(line)
        if pair:
            srt_scenes.append((len(srt_scenes) + 1, pair[0], pair[1]))
    if srt_scenes:
        return srt_scenes

    marks: list[tuple[int, float]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        match = SINGLE_TIMESTAMP_LINE_RE.match(line)
        if not match:
            continue
        try:
            start = parse_time_token(match.group(1))
        except ValueError:
            continue
        marks.append((len(marks) + 1, start))
    if not marks:
        return []
    duration = max(float(audio_duration or 0), marks[-1][1] + 0.5)
    single_scenes: list[tuple[int, float, float]] = []
    for index, (scene_num, start) in enumerate(marks):
        end = marks[index + 1][1] if index + 1 < len(marks) else duration
        if end > start:
            single_scenes.append((scene_num, start, end))
    return single_scenes
