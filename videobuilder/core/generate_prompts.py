#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Groq LLM visual beat → image_prompts.txt."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from videobuilder.core.create_srt import (
    groq_api_key,
    groq_client_available,
    is_groq_rate_limit,
)
from videobuilder.core.env_config import GROQ_API_KEY_ENV
from videobuilder.core.groq_models import (
    GROQ_LLM_DEFAULT_MODEL,
    GROQ_LLM_MODEL_ENV,
    groq_llm_active_model,
    groq_llm_model_chain,
    groq_llm_primary_model,
    groq_llm_using_cached_model,
    load_cached_groq_models,
    set_active_llm_model,
)

# Alias tương thích import cũ
GROQ_LLM_MODEL = GROQ_LLM_DEFAULT_MODEL
GROQ_LLM_MAX_OUTPUT_TOKENS = 4096
GROQ_LLM_MAX_INPUT_TOKENS = 8_500
GROQ_LLM_EST_CHARS_PER_TOKEN = 2.0
GROQ_LLM_TOKEN_SAFETY_MARGIN = 1.12
GROQ_LLM_CHUNK_MIN_INTERVAL_SEC = 2.0
GROQ_LLM_MAX_RETRY_WAIT_SEC = 120.0
GROQ_LLM_RETRY_PER_MODEL = 4

DEFAULT_CHARACTER_STYLE = (
    "Nhân vật chính là người que 2D đầu tròn màu trắng, tóc cam dựng nhọn, mắt chấm đen, "
    "lông mày mảnh biểu cảm, miệng nét đơn giản, tay chân que đen, bàn tay nhỏ màu đen. "
    "Người cổ đại là người que tóc nâu rối, mặc da thú đơn giản, có thể cầm giáo gỗ, "
    "đá sắc, giỏ hái lượm hoặc dây buộc thô sơ."
)

DEFAULT_ART_STYLE = (
    "Tranh minh họa giáo dục 2D dạng người que, vẽ tay, màu phẳng, "
    "nét viền đen đậm hơi rung như bút marker, bố cục sạch, dễ hiểu, không ảnh thật, "
    "không 3D, không anime, không khuôn mặt thật, không texture ảnh chụp, "
    "không đổ bóng phức tạp, khung hình ngang 16:9."
)

DEFAULT_LABELS = "Không dùng chữ trong ảnh."

HOOK_MIN_SEC = 3.0
HOOK_MAX_SEC = 10.0
HOOK_RENDER_MAX_SEC = 10.0
HOOK_CHAIN_MIN = 2
HOOK_CHAIN_MAX = 3
OPENING_DENSE_SEC = 30.0
MAX_GAP_OPENING_SEC = 4.5
MAX_GAP_BODY_SEC = 7.0
MIN_BEAT_SPAN_SEC = 1.5
TARGET_BEAT_SPAN_OPENING_SEC = 3.0
TARGET_BEAT_SPAN_BODY_SEC = 5.0
DENSITY_FILL_RATIO = 0.7
LONG_VIDEO_MIN_SEC = 8 * 60
LONG_VIDEO_BEATS_MIN = 80
LONG_VIDEO_BEATS_MAX = 110
BEATS_PER_MINUTE_MIN = 10.0
BEATS_PER_MINUTE_MAX = 13.75
HOOK_TYPES = (
    "nguy hiểm sắp xảy ra",
    "câu hỏi gây tò mò",
    "tình huống sốc",
    "nghịch lý bất ngờ",
    "bí mật chưa được tiết lộ",
    "thử thách sinh tồn",
    "cảnh báo trực tiếp với người xem",
)

class GeneratePromptsError(Exception):
    pass


class GroqLlmRateLimitError(GeneratePromptsError):
    pass


class GroqLlmPayloadTooLargeError(GeneratePromptsError):
    """Request vượt giới hạn kích thước (413) — cần tách chunk, không phải TPM/TPD."""
    pass


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


@dataclass
class VisualBeat:
    start: float
    end: float
    audio_quote: str
    character_desc: str
    scene_intent: str
    camera: str
    background: str
    visual: str
    labels: str
    style: str
    is_hook: bool = False
    hook_type: str = ""
    scene_bridge: str = ""


def check_prompt_llm(*, api_key: str | None = None) -> dict:
    """Trạng thái Groq LLM (visual beat) — dùng chung GROQ_API_KEY với STT."""
    key = (api_key or groq_api_key() or "").strip()
    pkg_ok = groq_client_available()
    if not key:
        return {
            "ok": False,
            "llm": False,
            "needs_install": not pkg_ok,
            "message": f"Chưa có {GROQ_API_KEY_ENV}",
        }
    if not pkg_ok:
        return {
            "ok": False,
            "llm": False,
            "needs_install": True,
            "message": "Có API key — cần cài groq (bấm «Cài đặt»)",
        }
    load_cached_groq_models()
    active = groq_llm_active_model()
    fallbacks = [m for m in groq_llm_model_chain()[1:] if m != active]
    fb_hint = f" (+{len(fallbacks)} fallback)" if fallbacks else ""
    cache_hint = " (cache)" if groq_llm_using_cached_model() else ""
    return {
        "ok": True,
        "llm": True,
        "needs_install": False,
        "message": f"Groq LLM {active}{cache_hint}{fb_hint} ✓",
        "model": active,
        "fallback_models": fallbacks,
    }


def verify_groq_llm_api_key(*, api_key: str | None = None) -> tuple[bool, str]:
    """Kiểm tra key Groq LLM bằng gọi chat completion nhẹ."""
    status = check_prompt_llm(api_key=api_key)
    if not status["ok"]:
        return False, status["message"]
    key = (api_key or groq_api_key() or "").strip()
    last_err: Exception | None = None
    for model in groq_llm_model_chain():
        try:
            from groq import Groq

            client = Groq(api_key=key)
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ok"}],
                max_tokens=8,
            )
        except Exception as err:
            if is_groq_rate_limit(err) or _is_rate_limit(err):
                last_err = err
                continue
            return False, f"Groq LLM ({model}): {err}"
        else:
            return True, f"Groq LLM API key hợp lệ · {model}"
    if last_err is not None:
        return True, f"Groq LLM rate limit toàn bộ chain (key hợp lệ)"
    return False, "Groq LLM: không kiểm tra được"


def segments_from_cues(cues: list[tuple[float, float, str]]) -> list[TranscriptSegment]:
    out: list[TranscriptSegment] = []
    for start, end, text in cues:
        text = (text or "").strip()
        if not text:
            continue
        out.append(TranscriptSegment(float(start), float(end), text))
    return out


def parse_prompt_timecode_token(token: str) -> float:
    """Đọc timecode trong [00.00.01.92] — chuẩn file tạo ảnh (hỗ trợ legacy 00.10.80, 00:06.33)."""
    text = (token or "").strip()
    colon = re.match(r"^(\d{2}):(\d{2})(?:\.(\d{1,3}))?$", text)
    if colon:
        base = int(colon.group(1)) * 60 + int(colon.group(2))
        frac = colon.group(3)
        if frac:
            base += int(frac) / (10 ** len(frac))
        return float(base)
    parts = [p.strip() for p in text.split(".") if p.strip()]
    if len(parts) == 2:
        return float(int(parts[0]) * 60 + int(parts[1]))
    if len(parts) < 3:
        raise ValueError(f"timecode không hợp lệ: {token}")
    nums = [int(p) for p in parts]
    if len(nums) == 3:
        a, b, c = nums
        if a == 0 and b == 0:
            return float(c)
        if c == 0:
            return float(b * 60)
        if c >= 60:
            # Legacy: 00.10.80 → 10.8 giây (phần thập phân ≥ 60)
            return float(b) + c / (10 ** len(parts[2]))
        # 00.MM.SS — ví dụ 00.01.30 = 1 phút 30 giây
        return float(b * 60 + c)
    if len(nums) == 4:
        a, b, c, d = nums
        frac = d / (10 ** len(parts[3]))
        if a == 0 and b == 0:
            return c + frac
        if a == 0:
            return b * 60 + c + frac
        return a * 3600 + b * 60 + c + frac
    raise ValueError(f"timecode không hợp lệ: {token}")


def format_prompt_timecode(seconds: float) -> str:
    """Chuẩn FORMAT: 00.00.SS.CS (<1 phút), 00.MM.SS[.CS] (≥1 phút)."""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    rem = seconds - h * 3600
    m = int(rem // 60)
    s = rem - m * 60
    si = int(s)
    cs = int(round((s - si) * 100)) % 100

    if h > 0:
        return f"{h:02d}.{m:02d}.{si:02d}.{cs:02d}"
    if m > 0:
        if cs == 0:
            return f"00.{m:02d}.{si:02d}"
        return f"00.{m:02d}.{si:02d}.{cs:02d}"
    if cs == 0:
        return f"00.00.{si:02d}"
    return f"00.00.{si:02d}.{cs:02d}"


def format_time_range(start: float, end: float) -> str:
    return f"{format_prompt_timecode(start)}-{format_prompt_timecode(end)}"


def format_beat_block(index: int, beat: VisualBeat) -> str:
    """Một prompt = một dòng liền; các prompt cách nhau bằng dòng trống khi ghi file."""
    tr = format_time_range(beat.start, beat.end)
    quote = beat.audio_quote.replace('"', "'").strip()
    intent = beat.scene_intent.strip()
    if beat.is_hook and beat.hook_type and "hook" not in intent.lower():
        intent = f"Tạo hook giữ chân ({beat.hook_type}): {intent}"
    parts = [
        f"{index:03d}_[{tr}]",
        f"CHARACTER BIBLE: {beat.character_desc.strip()}",
        f'Câu audio bám sát: "{quote}"',
        f"Ý cảnh: {intent}",
        f"Hình ảnh cần thể hiện: {beat.visual.strip()}",
    ]
    bridge = beat.scene_bridge.strip()
    if beat.is_hook or bridge:
        parts.append(f"Điểm nối chuyển cảnh: {bridge or 'Giữ cùng nhân vật/bối cảnh/vật thể cho beat kế.'}")
    parts.extend([
        f"Góc máy: {beat.camera.strip()}",
        f"Bối cảnh: {beat.background.strip()}",
        f"Nhãn trong ảnh: {beat.labels.strip()}",
        f"Phong cách: {beat.style.strip()}",
    ])
    return " ".join(parts)


def format_beat_line(index: int, beat: VisualBeat) -> str:
    """Một beat = một dòng prompt."""
    return format_beat_block(index, beat)


def _normalize_hook_type(value: str) -> str:
    text = (value or "").strip().lower()
    for known in HOOK_TYPES:
        if known in text or text in known:
            return known
    return HOOK_TYPES[1]


def _opening_segments_for_hook(segments: list[TranscriptSegment]) -> list[TranscriptSegment]:
    """Segment SRT bắt đầu trong 0–10s (hook window)."""
    if not segments:
        return []
    start = segments[0].start
    cap = start + HOOK_RENDER_MAX_SEC
    return [seg for seg in segments if seg.start < cap - 0.02]


def estimate_hook_window(segments: list[TranscriptSegment]) -> tuple[float, float]:
    """Ước lượng cửa sổ HOOK IMAGE RENDER: 0–10 giây đầu (gom chuỗi 2–3 prompt)."""
    opening = _opening_segments_for_hook(segments)
    if not opening:
        return 0.0, min(HOOK_MIN_SEC, HOOK_RENDER_MAX_SEC)
    start = opening[0].start
    end = min(opening[-1].end, start + HOOK_RENDER_MAX_SEC)
    end = max(end, start + min(HOOK_MIN_SEC, HOOK_RENDER_MAX_SEC))
    return start, end


def merge_segment_texts(
    segments: list[TranscriptSegment],
    start: float,
    end: float,
) -> str:
    texts: list[str] = []
    for seg in segments:
        if seg.end <= start + 0.02 or seg.start >= end - 0.02:
            continue
        if seg.start < end and seg.end > start:
            text = seg.text.strip()
            if text:
                texts.append(text)
    return " ".join(texts).strip()


def _extract_beat_quote_field(item: dict[str, Any]) -> str:
    for key in ("audio_quote", "quote", "transcript", "audio_text", "text"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _infer_audio_quote(
    segments: list[TranscriptSegment],
    start: float,
    end: float,
) -> str:
    """Suy ra audio_quote từ transcript khi LLM bỏ trường này."""
    if not segments:
        return ""
    quote = merge_segment_texts(segments, start, end)
    if quote:
        return quote
    quote = merge_segment_texts(segments, max(0.0, start - 1.0), end + 1.0)
    if quote:
        return quote
    mid = (start + end) / 2.0
    best: TranscriptSegment | None = None
    best_dist = float("inf")
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        center = (seg.start + seg.end) / 2.0
        dist = abs(center - mid)
        if dist < best_dist:
            best_dist = dist
            best = seg
    return best.text.strip() if best else ""


def _snap_beat_times_to_segments(
    start: float,
    end: float,
    segments: list[TranscriptSegment],
) -> tuple[float, float]:
    """Neo start/end beat vào cue SRT gần nhất — tránh timestamp LLM lệch audio."""
    if not segments:
        return start, end
    overlapping = [
        seg for seg in segments
        if seg.end > start + 0.02 and seg.start < end - 0.02
    ]
    if overlapping:
        return overlapping[0].start, overlapping[-1].end
    mid = (start + end) / 2.0
    nearest = min(
        segments,
        key=lambda seg: abs((seg.start + seg.end) / 2.0 - mid),
    )
    return nearest.start, nearest.end


def _beat_overlap_sec(beat: VisualBeat, seg: TranscriptSegment) -> float:
    return max(0.0, min(beat.end, seg.end) - max(beat.start, seg.start))


def _assign_segment_to_beat_index(
    seg: TranscriptSegment,
    beats: list[VisualBeat],
) -> int:
    if not beats:
        return 0
    best_i = 0
    best_overlap = -1.0
    for i, beat in enumerate(beats):
        overlap = _beat_overlap_sec(beat, seg)
        if overlap > best_overlap:
            best_overlap = overlap
            best_i = i
    if best_overlap > 0.05:
        return best_i
    seg_mid = (seg.start + seg.end) / 2.0
    best_i = 0
    best_dist = float("inf")
    for i, beat in enumerate(beats):
        beat_mid = (beat.start + beat.end) / 2.0
        dist = abs(seg_mid - beat_mid)
        if dist < best_dist:
            best_dist = dist
            best_i = i
    return best_i


def _split_segment_subgroups(
    segs: list[TranscriptSegment],
    max_span: float,
    *,
    max_segs: int = 3,
) -> list[list[TranscriptSegment]]:
    if not segs:
        return []
    groups: list[list[TranscriptSegment]] = []
    current = [segs[0]]
    for seg in segs[1:]:
        gap = seg.start - current[-1].end
        span = seg.end - current[0].start
        if gap > 1.2 or span > max_span or len(current) >= max_segs:
            groups.append(current)
            current = [seg]
        else:
            current.append(seg)
    if current:
        groups.append(current)
    return groups


def _visual_for_resynced_beat(beat: VisualBeat, new_quote: str) -> str:
    """Giữ visual LLM nếu còn khớp quote; nếu quote đổi nhiều thì mô tả lại theo audio."""
    old = beat.audio_quote.strip()
    new = new_quote.strip()
    vis = beat.visual.strip()
    if not new:
        return vis or f"Tranh người que 2D minh họa: {old[:160]}"
    if old == new:
        return vis or f"Tranh người que 2D minh họa đúng lời: {new[:180]}"
    if old and len(old) >= 12 and (old in new or new in old):
        return vis or f"Tranh người que 2D minh họa đúng lời: {new[:180]}"
    generic = vis.startswith("Minh họa người que") or vis.startswith("Tranh người que")
    if vis and not generic:
        return vis
    return f"Tranh người que 2D minh họa đúng lời: {new[:180]}"


def _resync_beat_quote_and_visual(
    beat: VisualBeat,
    segments: list[TranscriptSegment],
    start: float,
    end: float,
) -> VisualBeat:
    quote = merge_segment_texts(segments, start, end) or beat.audio_quote
    visual = _visual_for_resynced_beat(beat, quote)
    intent = beat.scene_intent.strip()
    if quote and quote != beat.audio_quote.strip():
        if not intent or intent == "Minh họa đúng lời audio.":
            intent = f"Minh họa đúng lời audio đang nói ({start:.1f}–{end:.1f}s)."
    return VisualBeat(
        start=start,
        end=end,
        audio_quote=quote,
        character_desc=beat.character_desc,
        scene_intent=intent,
        camera=beat.camera,
        background=beat.background,
        visual=visual,
        labels=beat.labels,
        style=beat.style,
        is_hook=beat.is_hook,
        hook_type=beat.hook_type,
        scene_bridge=beat.scene_bridge,
    )


def _realign_body_beats_to_segments(
    body: list[VisualBeat],
    segments: list[TranscriptSegment],
    hook_end: float,
) -> list[VisualBeat]:
    """Gán mỗi cue SRT vào beat LLM gần nhất; timeline liền mạch, quote/visual khớp audio."""
    post_segs = _segments_after_hook(segments, hook_end)
    if not post_segs:
        return []

    body_sorted = sorted(body, key=lambda b: b.start)
    if not body_sorted:
        groups = _group_uncovered_segments(post_segs, [])
        body_sorted = [_synthesize_beat_from_segment_group(g) for g in groups]

    seg_groups: list[list[TranscriptSegment]] = [[] for _ in body_sorted]
    for seg in post_segs:
        seg_groups[_assign_segment_to_beat_index(seg, body_sorted)].append(seg)

    chunks: list[tuple[VisualBeat, list[TranscriptSegment]]] = []
    in_opening = hook_end < OPENING_DENSE_SEC
    max_span = (
        TARGET_BEAT_SPAN_OPENING_SEC * 1.5
        if in_opening else TARGET_BEAT_SPAN_BODY_SEC * 1.4
    )
    max_segs = 2 if in_opening else 3
    for beat, segs in zip(body_sorted, seg_groups):
        if not segs:
            continue
        for subgroup in _split_segment_subgroups(segs, max_span, max_segs=max_segs):
            chunks.append((beat, subgroup))

    if not chunks:
        groups = _group_uncovered_segments(post_segs, [])
        chunks = [(_synthesize_beat_from_segment_group(g), g) for g in groups]

    realigned: list[VisualBeat] = []
    cursor = hook_end
    for beat, segs in chunks:
        start = cursor
        end = max(segs[-1].end, start + MIN_BEAT_SPAN_SEC)
        quote = merge_segment_texts(segs, segs[0].start, segs[-1].end)
        synced = _resync_beat_quote_and_visual(beat, segments, start, end)
        realigned.append(VisualBeat(
            start=start,
            end=end,
            audio_quote=quote or synced.audio_quote,
            character_desc=synced.character_desc,
            scene_intent=synced.scene_intent,
            camera=synced.camera,
            background=synced.background,
            visual=_visual_for_resynced_beat(beat, quote or synced.audio_quote),
            labels=synced.labels,
            style=synced.style,
            is_hook=False,
            hook_type="",
            scene_bridge=synced.scene_bridge,
        ))
        cursor = end
    return [_normalize_beat_defaults(b) for b in realigned]


def transcript_duration(segments: list[TranscriptSegment]) -> float:
    if not segments:
        return 0.0
    return max(float(segments[-1].end), 0.0)


def estimate_beat_density(segments: list[TranscriptSegment]) -> dict[str, Any]:
    """Tham khảo nhịp (log/UI) — LLM không bị ép đạt số beat cố định."""
    duration = transcript_duration(segments)
    minutes = duration / 60.0 if duration > 0 else 0.0

    if duration >= LONG_VIDEO_MIN_SEC:
        total_min, total_max = LONG_VIDEO_BEATS_MIN, LONG_VIDEO_BEATS_MAX
    elif minutes > 0:
        total_min = max(10, round(minutes * BEATS_PER_MINUTE_MIN))
        total_max = max(total_min + 8, round(minutes * BEATS_PER_MINUTE_MAX))
    else:
        total_min, total_max = 10, 16

    if duration <= OPENING_DENSE_SEC:
        opening_min = max(3, round(total_min * 0.35))
        opening_max = max(opening_min + 1, min(total_max, round(total_max * 0.5)))
    else:
        opening_min = max(8, round(total_min * 0.13))
        opening_max = max(opening_min + 2, min(18, round(total_max * 0.17)))

    after_duration = max(1.0, duration - OPENING_DENSE_SEC)
    after_beats_min = max(0, total_min - opening_max)
    after_beats_max = max(after_beats_min + 1, total_max - opening_min)

    return {
        "duration_sec": round(duration, 2),
        "duration_min": round(minutes, 2),
        "total_beats_min": total_min,
        "total_beats_max": total_max,
        "opening_0_30s_beats_min": opening_min,
        "opening_0_30s_beats_max": opening_max,
        "after_30s_beats_min": after_beats_min,
        "after_30s_beats_max": after_beats_max,
        "avg_sec_per_beat_after_30s_min": round(
            after_duration / max(1, after_beats_max), 1,
        ),
        "avg_sec_per_beat_after_30s_max": round(
            after_duration / max(1, after_beats_min), 1,
        ),
    }


def _build_visual_beat_system_prompt() -> str:
    hook_types = "\n".join(f"  - {t}" for t in HOOK_TYPES)
    return (
        "Bạn là đạo diễn hình ảnh cho video giáo dục tiếng Việt dạng người que.\n"
        "Nhiệm vụ: từ transcript audio (segments có start/end/text giây), "
        "tạo hook_chain (0–10s) rồi đủ VISUAL BEAT phủ kín timeline audio.\n\n"
        "=== THỨ TỰ ƯU TIÊN (BẮT BUỘC) ===\n"
        "1. Khớp audio — start_sec/end_sec bám timestamp segments; beat phủ đúng lời đang nói.\n"
        "2. Khớp SRT — audio_quote trích nguyên văn transcript, không bịa, không cắt sai nghĩa.\n"
        "3. Phủ kín timeline — đạt TỐI THIỂU target_beats_min trong pacing_hints/video_density; "
        "KHÔNG bỏ trống audio > max_uncovered_gap_sec; mọi segment phải thuộc ít nhất một beat.\n\n"
        "QUAN TRỌNG: quy tắc «không 1 subtitle = 1 ảnh» CHỈ áp dụng 0–10s (hook_chain). "
        "Sau hook vẫn cần ĐỦ ảnh — gộp chỉ khi chắc chắn cùng khung hình.\n\n"
        "=== BƯỚC 1 — HOOK IMAGE RENDER RULE (0–10 GIÂY ĐẦU, BẮT BUỘC) ===\n"
        "Trong 0–10 giây đầu: KHÔNG render từng subtitle rời rạc (1 cue = 1 ảnh).\n"
        "Phải gom thành chuỗi 2–3 prompt hook (hook_chain) có nối cảnh liên tục — "
        "đây là prompt 001, 002, (003) khi ghép video.\n\n"
        "Mỗi prompt trong hook_chain BẮT BUỘC có đủ:\n"
        "- audio_quote — Câu audio bám sát (trích nguyên văn transcript)\n"
        "- scene_intent — Ý cảnh\n"
        "- visual — Hình ảnh cần thể hiện\n"
        "- scene_bridge — Điểm nối chuyển cảnh (cùng nhân vật / bối cảnh / vật thể / "
        "hướng nhìn / hành động tiếp diễn sang prompt kế)\n"
        "Và: start_sec, end_sec, character_desc, camera, background, labels, style.\n\n"
        "Vai trò từng prompt hook:\n"
        "- Prompt 001 (hook_chain[0]): cú mở cảnh.\n"
        "- Prompt 002 (hook_chain[1]): làm rõ điều bất thường.\n"
        "- Prompt 003 (hook_chain[2], nếu có): đẩy sang nguy hiểm hoặc xung đột chính.\n\n"
        "Các prompt hook giữ cùng nhân vật, cùng bối cảnh hoặc cùng vật thể nối cảnh "
        "để dựng video mượt (camera tiến gần, lia, đổi góc, phát hiện chi tiết trong cùng tình huống).\n"
        "KHÔNG nhảy cảnh quá xa nếu audio chưa chuyển ý.\n"
        "KHÔNG nhân vật/đạo cụ xuất hiện rồi biến mất ở prompt kế trừ khi audio chuyển cảnh rõ.\n"
        "hook_chain gồm 2 hoặc 3 phần tử; tổng thời gian ≤ 10 giây; bám timestamp segments.\n"
        "hook_type (tùy chọn, một trong):\n"
        f"{hook_types}\n"
        "KHÔNG đặt hook trong mảng beats — chỉ hook_chain.\n\n"
        "=== BƯỚC 2 — 0–30 GIÂY (SAU CHUỖI HOOK): NHỊP NHANH ===\n"
        "Sau hook_chain (sau ~10 giây đầu) đến 30 giây: đổi cảnh NHANH hơn phần còn lại (~2–4 giây/beat).\n"
        "Tách beat khi có: chuyển cảm xúc, câu hỏi tu từ, hành động mới, chi tiết sốc nhỏ.\n"
        "KHÔNG gộp nhiều ý khác nhau vào một ảnh chỉ vì các câu liền kề.\n\n"
        "=== BƯỚC 3 — SAU 30 GIÂY: VISUAL BEAT ĐỦ MẬT ĐỘ ===\n"
        "Sau 30 giây: chia beat theo nội dung thật nhưng PHẢI phủ liên tục timeline.\n"
        "- Mục tiêu ~5–8 giây/beat (xem video_density); không nhảy cóc 15–30 giây không ảnh.\n"
        "- GỘP nhiều câu liên tiếp CHỈ KHI chắc chắn cùng khung hình "
        "(cùng bối cảnh + hành động + cảm xúc + ý giải thích).\n"
        "- TÁCH beat khi có thay đổi rõ: bối cảnh, hành động chính, cảm xúc mạnh, "
        "nhân vật/đối tượng mới, kể chuyện↔giải thích, ví dụ mới, khái niệm, cao trào.\n"
        "Beat đầu trong mảng beats KHÔNG được trùng hook_chain — hook chỉ nằm trong hook_chain.\n\n"
        "=== ĐA DẠNG GÓC MÁY & BỐ CỤC (SAU CHUỖI HOOK 001–003) ===\n"
        "Sau hook_chain (từ beat thứ 4 / sau ~10 giây): hai beat liên tiếp KHÔNG được lặp:\n"
        "  • cùng góc máy (cận / trung / rộng / toàn cảnh / góc cao / góc thấp / nghiêng)\n"
        "  • cùng bố cục (nhân vật cùng vị trí, cùng nền tĩnh, cùng cách sắp xếp)\n"
        "  • cảnh gần như trùng nhau — phải đổi góc, scale, hoặc hành động rõ ràng.\n"
        "Luân phiên góc máy và bố cục để video sống động.\n"
        "Ngoại lệ: trong hook_chain (001–003) được đổi góc có chủ đích trong cùng tình huống.\n\n"
        "=== KHÔNG GỘP QUÁ NHIỀU Ý ===\n"
        "Mỗi beat = một ý / một khung hình chính.\n"
        "Không nhét nhiều ví dụ, nhân vật, hoặc luận điểm khác nhau vào cùng một ảnh.\n\n"
        "QUY TẮC CHUNG:\n"
        "- character_desc PHẢI dùng CHARACTER BIBLE người que 2D chuẩn (không viết tắt «Người chính»).\n"
        "- style PHẢI là phong cách tranh người que 2D chuẩn; labels = «Không dùng chữ trong ảnh».\n"
        "- visual mô tả CỤ THỂ hành động/bối cảnh cho ĐÚNG audio_quote trong start_sec–end_sec; "
        "không mô tả cảnh khác thời điểm.\n"
        "- start_sec/end_sec khớp timeline; beats sau hook_chain bắt đầu ≥ hook_chain[-1].end_sec.\n"
        "- Prompt 001–003 LUÔN là hook_chain (2–3 phần tử).\n"
        "- Trả JSON thuần: { hook_chain: [...], beats: [...] }, không markdown.\n"
        "- Vẫn chấp nhận legacy { hook: {...} } — parser sẽ tách thành chuỗi 2–3.\n"
    )


def _build_continuation_system_prompt() -> str:
    return (
        "Chia VISUAL BEAT cho đoạn transcript (chunk) video người que tiếng Việt.\n"
        "CHỈ JSON: {\"beats\":[...]} — không hook_chain.\n"
        "character_desc/style/labels dùng mặc định người que 2D (CHARACTER BIBLE chuẩn).\n"
        "Beat: start_sec,end_sec,audio_quote,character_desc,scene_intent,camera,background,"
        "visual,labels,style.\n\n"
        "ƯU TIÊN: khớp audio timeline > khớp SRT nguyên văn > đủ số beat tối thiểu (pacing_hints).\n"
        "PHẢI phủ mọi segment trong chunk — không bỏ sót đoạn audio dài.\n"
        "Đạt target_beats_min; max_uncovered_gap_sec là giới hạn khoảng trống cho phép.\n"
        "Nếu chunk bắt đầu ngay sau hook_chain (~10s đầu): beat đầu tiên phải nối mạch "
        "với prompt 003 (cùng tình huống nếu audio chưa chuyển ý).\n"
        "Nếu chunk nằm trong 0–30 giây (xem pacing_hints): nhịp đổi cảnh nhanh, tách nhiều hơn.\n"
        "Sau 30 giây: ~5–8 giây/beat, phủ liên tục — gộp khi cùng khung hình.\n"
        "Không gộp quá nhiều ý vào một ảnh.\n"
        "Hai beat liên tiếp không lặp góc máy, bố cục, hoặc cảnh gần trùng.\n"
        "audio_quote nguyên văn; start_sec/end_sec bám segments.\n"
    )


def _estimate_llm_tokens(text: str) -> int:
    return max(1, int(len(text) / GROQ_LLM_EST_CHARS_PER_TOKEN))


def _chunk_pacing_hints(
    chunk_segments: list[TranscriptSegment],
    all_segments: list[TranscriptSegment],
) -> dict[str, Any]:
    """Gợi ý nhịp — tham khảo, không ép LLM đạt số beat cố định."""
    full = estimate_beat_density(all_segments)
    if not chunk_segments:
        return {"chunk_scope": "empty", "priority": ["audio", "srt", "count_reference_only"]}
    chunk_start = chunk_segments[0].start
    chunk_end = chunk_segments[-1].end
    in_opening = chunk_start < OPENING_DENSE_SEC
    after_opening = chunk_end > OPENING_DENSE_SEC
    hints: dict[str, Any] = {
        "chunk_start_sec": round(chunk_start, 2),
        "chunk_end_sec": round(chunk_end, 2),
        "priority": [
            "1_khop_audio_timeline",
            "2_khop_srt_nguyen_van",
            "3_so_luong_anh_tham_chieu_khong_ep",
        ],
        "opening_dense_window_sec": OPENING_DENSE_SEC,
        "in_opening_0_30s": in_opening,
        "spans_after_30s": after_opening and not in_opening,
    }
    if in_opening:
        hints["hook_image_render_rule"] = (
            "0–10s đầu: gom 2–3 prompt hook_chain liền mạch; KHÔNG 1 subtitle = 1 ảnh; "
            "mỗi hook có audio_quote, scene_intent, visual, scene_bridge."
        )
        hints["hook_chain_roles"] = (
            "001=cú mở cảnh; 002=điều bất thường; 003=nguy hiểm/xung đột chính; "
            "cùng nhân vật/bối cảnh/vật thể; không nhảy cảnh xa."
        )
        hints["opening_pacing"] = (
            "Sau hook_chain (~10s) đến 30s: nhịp ~2–4 giây/beat; "
            "tách khi đổi cảm xúc/hành động/ý; không gộp nhiều ý."
        )
        hints["reference_opening_beats_soft"] = (
            f"{full['opening_0_30s_beats_min']}–{full['opening_0_30s_beats_max']} "
            "(tham khảo, không bắt buộc)"
        )
    if after_opening and (not in_opening or chunk_end > OPENING_DENSE_SEC):
        hints["after_30s_pacing"] = (
            "Visual beat tự nhiên kiểu Gemini: gộp khi cùng khung hình, "
            "không đặt trước số lượng ảnh."
        )
        hints["reference_avg_sec_per_beat_soft"] = (
            f"{full['avg_sec_per_beat_after_30s_min']}–"
            f"{full['avg_sec_per_beat_after_30s_max']}s (tham khảo)"
        )
    hints["visual_variety"] = (
        "Không lặp góc máy, bố cục, hoặc cảnh liên tiếp giữa 2 beat."
    )
    chunk_dur = max(0.5, chunk_end - chunk_start)
    if chunk_start < OPENING_DENSE_SEC:
        avg_span = TARGET_BEAT_SPAN_OPENING_SEC
        max_gap = MAX_GAP_OPENING_SEC
    else:
        avg_span = TARGET_BEAT_SPAN_BODY_SEC
        max_gap = MAX_GAP_BODY_SEC
    hints["target_beats_min"] = max(2, round(chunk_dur / avg_span * 0.9))
    hints["target_beats_max"] = max(
        hints["target_beats_min"] + 1,
        round(chunk_dur / max(MIN_BEAT_SPAN_SEC, avg_span * 0.6)),
    )
    hints["max_uncovered_gap_sec"] = max_gap
    hints["coverage"] = "Mọi segment trong chunk phải thuộc ít nhất một beat."
    hints["video_total_beats_min"] = full["total_beats_min"]
    hints["video_total_beats_max"] = full["total_beats_max"]
    return hints


def _build_visual_beat_user_payload(
    segments: list[TranscriptSegment],
    *,
    all_segments: list[TranscriptSegment] | None = None,
    include_hook: bool = True,
    chunk_index: int = 0,
    chunk_total: int = 1,
) -> str:
    all_segments = all_segments or segments
    hook_start, hook_end = estimate_hook_window(all_segments)
    pacing = _chunk_pacing_hints(segments, all_segments)
    payload: dict[str, Any] = {
        "segments": [s.to_dict() for s in segments],
        "pacing_hints": pacing,
        "video_density": estimate_beat_density(all_segments),
        "chunk_index": chunk_index,
        "chunk_total": chunk_total,
    }
    if include_hook:
        payload["hook_render_window"] = {
            "start_sec": round(hook_start, 3),
            "end_sec": round(hook_end, 3),
            "max_sec": HOOK_RENDER_MAX_SEC,
            "chain_size": f"{HOOK_CHAIN_MIN}-{HOOK_CHAIN_MAX}",
            "note": (
                "HOOK IMAGE RENDER RULE: 0–10s gom hook_chain 2–3 prompt liền mạch; "
                "không tách từng subtitle; sau đó nhịp nhanh đến 30s."
            ),
        }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _request_token_estimate(
    segments: list[TranscriptSegment],
    *,
    all_segments: list[TranscriptSegment],
    include_hook: bool,
    chunk_index: int,
    chunk_total: int,
) -> int:
    system = (
        _build_visual_beat_system_prompt()
        if include_hook
        else _build_continuation_system_prompt()
    )
    user = (
        "Dữ liệu transcript:\n"
        + _build_visual_beat_user_payload(
            segments,
            all_segments=all_segments,
            include_hook=include_hook,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
        )
    )
    return int(
        (_estimate_llm_tokens(system) + _estimate_llm_tokens(user))
        * GROQ_LLM_TOKEN_SAFETY_MARGIN
    )


def split_segments_for_groq_llm(
    segments: list[TranscriptSegment],
    *,
    max_input_tokens: int = GROQ_LLM_MAX_INPUT_TOKENS,
) -> list[tuple[list[TranscriptSegment], bool]]:
    """Chia transcript — Groq free tier ~12k token/request (input)."""
    if not segments:
        return []
    if _request_token_estimate(
        segments, all_segments=segments, include_hook=True, chunk_index=0, chunk_total=1,
    ) <= max_input_tokens:
        return [(segments, True)]

    raw_chunks: list[list[TranscriptSegment]] = []
    i = 0
    while i < len(segments):
        chunk: list[TranscriptSegment] = []
        is_first = len(raw_chunks) == 0
        while i < len(segments):
            trial = chunk + [segments[i]]
            tokens = _request_token_estimate(
                trial,
                all_segments=segments,
                include_hook=is_first,
                chunk_index=len(raw_chunks),
                chunk_total=max(3, len(segments) // 25),
            )
            if chunk and tokens > max_input_tokens:
                break
            chunk = trial
            i += 1
        if not chunk:
            chunk = [segments[i]]
            i += 1
        raw_chunks.append(chunk)

    return [(chunk, idx == 0) for idx, chunk in enumerate(raw_chunks)]


def _groq_chat_json(
    client,
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int = GROQ_LLM_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=max_tokens,
        )
    except Exception as err:
        if _is_payload_too_large(err):
            raise GroqLlmPayloadTooLargeError(str(err)) from err
        if is_groq_rate_limit(err) or _is_rate_limit(err):
            raise GroqLlmRateLimitError(str(err)) from err
        raise GeneratePromptsError(f"Groq LLM lỗi: {err}") from err

    text = ""
    try:
        text = response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        text = ""
    return _parse_json_response(text)


def _groq_chat_json_with_fallback(
    client,
    *,
    system: str,
    user: str,
    log_callback=None,
    max_tokens: int = GROQ_LLM_MAX_OUTPUT_TOKENS,
) -> dict[str, Any]:
    """Gọi Groq JSON; tự chuyển model khi rate limit (giữ model đã chọn cho các chunk sau)."""
    chain = groq_llm_model_chain()

    last_err: BaseException | None = None
    for i, model in enumerate(chain):
        attempt = 0
        while attempt < GROQ_LLM_RETRY_PER_MODEL:
            try:
                data = _groq_chat_json(
                    client, model=model, system=system, user=user, max_tokens=max_tokens,
                )
            except GroqLlmPayloadTooLargeError:
                raise
            except GroqLlmRateLimitError as err:
                wait = _parse_groq_retry_after_seconds(err)
                if wait is not None and wait <= GROQ_LLM_MAX_RETRY_WAIT_SEC:
                    attempt += 1
                    if log_callback:
                        log_callback(
                            f"Groq {model} rate limit — chờ {wait:.1f}s "
                            f"(thử lại {attempt}/{GROQ_LLM_RETRY_PER_MODEL})...",
                            "warn",
                        )
                    time.sleep(wait + 0.25)
                    continue
                last_err = err
                break
            except GeneratePromptsError as err:
                last_err = err
                if log_callback and i < len(chain) - 1:
                    log_callback(
                        f"Groq {model} lỗi → thử {chain[i + 1]}...",
                        "warn",
                    )
                break
            else:
                if groq_llm_active_model() != model:
                    if log_callback:
                        log_callback(f"Groq LLM: dùng {model}", "info")
                    set_active_llm_model(model)
                return data
        if last_err is not None and i < len(chain) - 1 and log_callback:
            log_callback(
                f"Groq {model} rate limit → thử {chain[i + 1]}...",
                "warn",
            )

    raise GeneratePromptsError(
        "Groq LLM rate limit — đã thử hết model: "
        + ", ".join(groq_llm_model_chain())
        + (f". Chi tiết: {last_err}" if last_err else "")
    ) from last_err


def _merge_chunked_beats(
    hook_chain: list[VisualBeat] | None,
    body_beats: list[VisualBeat],
    segments: list[TranscriptSegment],
) -> list[VisualBeat]:
    body_beats.sort(key=lambda b: b.start)
    if not hook_chain:
        if not body_beats:
            return parse_visual_beat_response({"beats": []}, segments)
        hook_chain = _synthesize_hook_chain(segments, seed=body_beats[0])
        rest = _beats_after_hook(body_beats, hook_chain[-1].end, segments)
        return hook_chain + rest
    hook_end = hook_chain[-1].end
    rest = _beats_after_hook(body_beats, hook_end, segments)
    merged = list(hook_chain) + rest
    merged.sort(key=lambda b: b.start)
    for i, beat in enumerate(merged[: len(hook_chain)]):
        beat.is_hook = True
    return merged


def _is_payload_too_large(exc: BaseException) -> bool:
    """413 / request too large — KHÔNG nhầm với TPM (tokens per minute) rate limit."""
    text = str(exc).lower()
    if "413" in text or "request too large" in text:
        return True
    if "payload too large" in text or "context length" in text:
        return True
    if "too large" in text and "tokens per" not in text and "rate limit" not in text:
        return True
    return False


def _is_request_too_large(exc: BaseException) -> bool:
    """Alias — chỉ payload 413, không phải TPM."""
    return _is_payload_too_large(exc)


def _parse_groq_retry_after_seconds(exc: BaseException) -> float | None:
    text = str(exc)
    match = re.search(
        r"try again in\s+"
        r"(?:(\d+)\s*h(?:ours?)?)?\s*"
        r"(?:(\d+)\s*m(?:in(?:ute)?s?)?)?\s*"
        r"([\d.]+)\s*s(?:ec(?:ond)?s?)?",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = float(match.group(3) or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def _is_rate_limit(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in ("429", "rate limit", "quota", "resource exhausted", "too many requests")
    )


def _parse_json_response(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise GeneratePromptsError("LLM trả về rỗng.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise GeneratePromptsError("LLM không trả JSON hợp lệ.") from None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as err:
            raise GeneratePromptsError(f"JSON LLM lỗi: {err}") from err


def _normalize_labels(labels: str) -> str:
    text = (labels or "").strip()
    if not text or (text.startswith("[") and text.endswith("]")):
        return DEFAULT_LABELS
    if text.lower() in ("[]", "none", "null"):
        return DEFAULT_LABELS
    return text


def _normalize_character_desc(desc: str) -> str:
    d = (desc or "").strip()
    short_roles = {
        "người chính", "nhóm người", "con vật", "nhân vật chính",
        "người que", "character", "main character",
    }
    if len(d) < 50 or d.lower() in short_roles:
        return DEFAULT_CHARACTER_STYLE
    return d


def _normalize_style(style: str) -> str:
    s = (style or "").strip()
    if not s or s.lower() in ("tối giản", "minimal", "minimalist", "simple"):
        return DEFAULT_ART_STYLE
    return s


def _normalize_beat_defaults(beat: VisualBeat) -> VisualBeat:
    beat.character_desc = _normalize_character_desc(beat.character_desc)
    beat.style = _normalize_style(beat.style)
    beat.labels = _normalize_labels(beat.labels)
    if not beat.scene_intent.strip():
        beat.scene_intent = "Minh họa đúng lời audio."
    if not beat.visual.strip():
        beat.visual = f"Minh họa người que 2D: {beat.audio_quote[:120]}"
    if not beat.background.strip():
        beat.background = "Bối cảnh phù hợp lời audio, nét vẽ đơn giản."
    return beat


def _segment_covered(seg: TranscriptSegment, beats: list[VisualBeat], margin: float = 0.08) -> bool:
    for beat in beats:
        if beat.start < seg.end - margin and beat.end > seg.start + margin:
            return True
    return False


def _synthesize_beat_from_segment_group(
    group: list[TranscriptSegment],
    *,
    is_hook: bool = False,
) -> VisualBeat:
    start = group[0].start
    end = group[-1].end
    quote = merge_segment_texts(group, start, end) or group[0].text.strip()
    cameras = (
        "Cận hoặc trung bình, nhấn biểu cảm.",
        "Trung bình, nhìn ngang.",
        "Góc rộng, thấy bối cảnh.",
        "Góc thấp hoặc nghiêng, tạo chiều sâu.",
    )
    cam_idx = int(start) % len(cameras)
    return VisualBeat(
        start=start,
        end=end,
        audio_quote=quote,
        character_desc=DEFAULT_CHARACTER_STYLE,
        scene_intent="Minh họa đúng lời audio đang nói.",
        camera=cameras[cam_idx],
        background="Bối cảnh khớp nội dung câu thoại, vẽ đơn giản.",
        visual=f"Tranh người que 2D minh họa: {quote[:160]}",
        labels=DEFAULT_LABELS,
        style=DEFAULT_ART_STYLE,
        is_hook=is_hook,
        hook_type=HOOK_TYPES[1] if is_hook else "",
        scene_bridge=_default_scene_bridge(0, 1) if is_hook else "",
    )


def _group_uncovered_segments(
    segments: list[TranscriptSegment],
    beats: list[VisualBeat],
) -> list[list[TranscriptSegment]]:
    uncovered = [
        seg for seg in segments
        if not _segment_covered(seg, beats)
    ]
    if not uncovered:
        return []

    groups: list[list[TranscriptSegment]] = []
    current = [uncovered[0]]
    for seg in uncovered[1:]:
        gap = seg.start - current[-1].end
        span = seg.end - current[0].start
        in_opening = current[0].start < OPENING_DENSE_SEC
        max_span = TARGET_BEAT_SPAN_OPENING_SEC * 2.2 if in_opening else TARGET_BEAT_SPAN_BODY_SEC * 1.6
        max_segs = 2 if in_opening else 3
        if gap > 1.2 or span > max_span or len(current) >= max_segs:
            groups.append(current)
            current = [seg]
        else:
            current.append(seg)
    if current:
        groups.append(current)
    return groups


def fill_timeline_gaps(
    beats: list[VisualBeat],
    segments: list[TranscriptSegment],
    *,
    coverage_segments: list[TranscriptSegment] | None = None,
) -> list[VisualBeat]:
    """Bổ sung beat cho segment/audio chưa được LLM phủ."""
    if not segments:
        return [_normalize_beat_defaults(b) for b in beats]
    normalized = [_normalize_beat_defaults(b) for b in beats]
    segs = coverage_segments if coverage_segments is not None else segments
    groups = _group_uncovered_segments(segs, normalized)
    if not groups:
        return sorted(normalized, key=lambda b: b.start)
    extra = [_synthesize_beat_from_segment_group(g) for g in groups]
    merged = normalized + extra
    merged.sort(key=lambda b: b.start)
    return merged


def _beat_from_dict(
    item: dict[str, Any],
    *,
    segments: list[TranscriptSegment] | None = None,
    is_hook: bool = False,
    hook_type: str = "",
) -> VisualBeat:
    try:
        start = float(item["start_sec"])
        end = float(item["end_sec"])
    except (KeyError, TypeError, ValueError) as err:
        raise GeneratePromptsError("Beat thiếu start_sec/end_sec.") from err
    if end <= start:
        raise GeneratePromptsError(f"Beat thời gian không hợp lệ: {start}–{end}")
    if segments:
        start, end = _snap_beat_times_to_segments(start, end, segments)
    quote = _extract_beat_quote_field(item)
    if not quote and segments:
        quote = _infer_audio_quote(segments, start, end)
    if not quote:
        raise GeneratePromptsError("Beat thiếu audio_quote.")
    bridge = str(
        item.get("scene_bridge")
        or item.get("transition_bridge")
        or item.get("dien_noi_chuyen_canh")
        or ""
    ).strip()
    return _normalize_beat_defaults(VisualBeat(
        start=start,
        end=end,
        audio_quote=quote,
        character_desc=str(item.get("character_desc") or DEFAULT_CHARACTER_STYLE).strip(),
        scene_intent=str(item.get("scene_intent") or "").strip(),
        camera=str(item.get("camera") or "Trung bình, nhìn ngang.").strip(),
        background=str(item.get("background") or "").strip(),
        visual=str(item.get("visual") or "").strip(),
        labels=str(item.get("labels") or DEFAULT_LABELS).strip(),
        style=str(item.get("style") or DEFAULT_ART_STYLE).strip(),
        is_hook=is_hook,
        hook_type=hook_type,
        scene_bridge=bridge,
    ))


HOOK_CHAIN_ROLES = (
    "Cú mở cảnh — bám audio mở đầu.",
    "Làm rõ điều bất thường trong cùng tình huống.",
    "Đẩy sang nguy hiểm hoặc xung đột chính.",
)


def _default_scene_bridge(index: int, total: int) -> str:
    if index >= total - 1:
        return "Chuyển sang nội dung chính — giữ continuity nếu audio chưa đổi cảnh."
    return (
        "Giữ cùng nhân vật/bối cảnh/vật thể cho prompt kế — "
        "camera tiếp tục trong cùng tình huống."
    )


def _partition_segments(segments: list[TranscriptSegment], parts: int) -> list[list[TranscriptSegment]]:
    if not segments or parts <= 1:
        return [segments] if segments else [[]]
    parts = max(1, min(parts, len(segments)))
    chunk_size = max(1, (len(segments) + parts - 1) // parts)
    groups: list[list[TranscriptSegment]] = []
    for i in range(0, len(segments), chunk_size):
        groups.append(segments[i : i + chunk_size])
    while len(groups) > parts:
        groups[-2].extend(groups.pop())
    while len(groups) < parts and len(groups) > 0:
        groups.append([groups[-1][-1]])
    return groups


def _non_empty_groups(groups: list[list[TranscriptSegment]]) -> list[list[TranscriptSegment]]:
    return [g for g in groups if g]


def _hook_contiguous_window(
    groups: list[list[TranscriptSegment]],
    index: int,
    window_start: float,
    opening: list[TranscriptSegment],
) -> tuple[float, float]:
    """Cửa sổ hook liền mạch: end[i] = start[i+1], không để khoảng trống SRT."""
    cap = window_start + HOOK_RENDER_MAX_SEC
    total_end = min(opening[-1].end, cap) if opening else cap
    group = groups[index]
    w_start = window_start if index == 0 else group[0].start
    if index < len(groups) - 1:
        w_end = groups[index + 1][0].start
    else:
        w_end = total_end
    if w_end <= w_start:
        w_end = min(max(group[-1].end, w_start + MIN_BEAT_SPAN_SEC), cap)
    return w_start, w_end


def _stitch_beats_contiguous(
    beats: list[VisualBeat],
    segments: list[TranscriptSegment],
    *,
    chain_start: float | None = None,
) -> list[VisualBeat]:
    """Ảnh kế tiếp: start[i+1] == end[i] — không gap giữa các beat."""
    ordered = sorted(beats, key=lambda b: b.start)
    if len(ordered) < 2:
        if ordered:
            return [_normalize_beat_defaults(ordered[0])]
        return []
    stitched: list[VisualBeat] = []
    for i, beat in enumerate(ordered):
        if i == 0:
            start = chain_start if chain_start is not None else ordered[0].start
        else:
            start = stitched[-1].end
        if i < len(ordered) - 1:
            end = ordered[i + 1].start
            if end <= start + 0.02:
                end = max(beat.end, start + MIN_BEAT_SPAN_SEC)
        else:
            end = max(beat.end, start + MIN_BEAT_SPAN_SEC)
        quote = merge_segment_texts(segments, start, end) or beat.audio_quote
        stitched.append(VisualBeat(
            start=start,
            end=end,
            audio_quote=quote,
            character_desc=beat.character_desc,
            scene_intent=beat.scene_intent,
            camera=beat.camera,
            background=beat.background,
            visual=beat.visual,
            labels=beat.labels,
            style=beat.style,
            is_hook=beat.is_hook,
            hook_type=beat.hook_type,
            scene_bridge=beat.scene_bridge,
        ))
    return [_normalize_beat_defaults(b) for b in stitched]


def _segments_after_hook(
    segments: list[TranscriptSegment],
    hook_end: float,
) -> list[TranscriptSegment]:
    """Segment cần body beat phủ — bỏ phần đã nằm trong hook_chain."""
    return [seg for seg in segments if seg.end > hook_end + 0.05]


def _ensure_hook_chain_quality(hooks: list[VisualBeat]) -> list[VisualBeat]:
    """Giữ chuỗi hook 0–10s: đủ vai trò, hook_type, nối cảnh — giữ chân người xem."""
    polished: list[VisualBeat] = []
    total = min(len(hooks), HOOK_CHAIN_MAX)
    for i, beat in enumerate(hooks[:HOOK_CHAIN_MAX]):
        role = HOOK_CHAIN_ROLES[min(i, len(HOOK_CHAIN_ROLES) - 1)]
        intent = beat.scene_intent.strip()
        if not intent or intent == "Minh họa đúng lời audio.":
            intent = role
        visual = beat.visual.strip()
        if not visual or visual.startswith("Minh họa người que"):
            visual = (
                "Khung hình dễ hiểu trong 1 giây: nhân vật chính trong tình huống hook, "
                "bố cục rõ, không tiết lộ kết cục."
                if i == 0 else
                "Tiếp nối hook — cùng nhân vật/bối cảnh, phát hiện thêm chi tiết."
            )
        polished.append(_normalize_beat_defaults(VisualBeat(
            start=beat.start,
            end=beat.end,
            audio_quote=beat.audio_quote,
            character_desc=beat.character_desc,
            scene_intent=intent,
            camera=beat.camera,
            background=beat.background,
            visual=visual,
            labels=beat.labels,
            style=beat.style,
            is_hook=True,
            hook_type=beat.hook_type.strip() or HOOK_TYPES[1],
            scene_bridge=beat.scene_bridge.strip() or _default_scene_bridge(i, total),
        )))
    return polished


def _hook_chain_beat_from_dict(
    item: dict[str, Any],
    *,
    segments: list[TranscriptSegment],
    role_index: int,
    chain_total: int,
    hook_type: str = "",
) -> VisualBeat:
    beat = _beat_from_dict(
        item,
        segments=segments,
        is_hook=True,
        hook_type=hook_type,
    )
    role = HOOK_CHAIN_ROLES[role_index] if role_index < len(HOOK_CHAIN_ROLES) else HOOK_CHAIN_ROLES[-1]
    if not beat.scene_intent.strip():
        beat.scene_intent = role
    elif role_index == 0 and "hook" not in beat.scene_intent.lower():
        beat.scene_intent = f"{role} {beat.scene_intent}".strip()
    if not beat.scene_bridge.strip():
        beat.scene_bridge = _default_scene_bridge(role_index, chain_total)
    if not beat.visual.strip():
        beat.visual = (
            "Khung hình dễ hiểu trong 1 giây: nhân vật chính trong tình huống hook, "
            "bố cục rõ, không tiết lộ kết cục."
        )
    return beat


def _reanchor_hook_chain_to_segments(
    chain: list[VisualBeat],
    segments: list[TranscriptSegment],
    hook_type: str = "",
) -> list[VisualBeat]:
    """Gán lại timestamp hook 0–10s từ SRT — liền mạch, không nhảy như LLM."""
    if not chain:
        return _synthesize_hook_chain(segments)
    window_start, window_end = estimate_hook_window(segments)
    opening = _opening_segments_for_hook(segments)
    if not opening:
        return _expand_hook_chain(chain, segments, hook_type)

    count = max(HOOK_CHAIN_MIN, min(len(chain), HOOK_CHAIN_MAX))
    if len(opening) >= 3 and count < HOOK_CHAIN_MAX:
        count = HOOK_CHAIN_MAX
    groups = _partition_segments(opening, count)
    nonempty = _non_empty_groups(groups)
    reanchored: list[VisualBeat] = []

    for i, group in enumerate(nonempty):
        g_start, g_end = _hook_contiguous_window(nonempty, i, window_start, opening)
        src = chain[min(i, len(chain) - 1)]
        quote = merge_segment_texts(segments, g_start, g_end) or src.audio_quote
        role = HOOK_CHAIN_ROLES[min(i, len(HOOK_CHAIN_ROLES) - 1)]
        reanchored.append(VisualBeat(
            start=g_start,
            end=g_end,
            audio_quote=quote,
            character_desc=src.character_desc,
            scene_intent=src.scene_intent.strip() or role,
            camera=src.camera,
            background=src.background,
            visual=src.visual,
            labels=src.labels,
            style=src.style,
            is_hook=True,
            hook_type=hook_type or src.hook_type or HOOK_TYPES[1],
            scene_bridge=src.scene_bridge or _default_scene_bridge(i, len(nonempty)),
        ))

    if reanchored:
        cap = window_start + HOOK_RENDER_MAX_SEC
        last = reanchored[-1]
        last.end = min(max(last.end, opening[-1].end), cap)

    if len(reanchored) >= HOOK_CHAIN_MIN:
        return [_normalize_beat_defaults(b) for b in reanchored]
    return _synthesize_hook_chain(segments, seed=chain[0])


def _normalize_hook_chain(
    chain: list[VisualBeat],
    segments: list[TranscriptSegment],
    hook_type: str = "",
) -> list[VisualBeat]:
    if not chain:
        return _synthesize_hook_chain(segments)
    chain = sorted(chain, key=lambda b: b.start)[:HOOK_CHAIN_MAX]
    window_start = max(0.0, chain[0].start)
    window_end = min(window_start + HOOK_RENDER_MAX_SEC, max(b.end for b in chain))
    if window_end - window_start < HOOK_MIN_SEC:
        _, window_end = estimate_hook_window(segments)
    for i, beat in enumerate(chain):
        beat.is_hook = True
        beat.hook_type = hook_type or beat.hook_type or HOOK_TYPES[1]
        if not beat.scene_bridge.strip():
            beat.scene_bridge = _default_scene_bridge(i, len(chain))
    if len(chain) < HOOK_CHAIN_MIN:
        return _expand_hook_chain(chain, segments, hook_type)
    # Gộp beat hook trùng thời gian 0–10s nếu LLM tách quá mảnh
    if len(chain) > HOOK_CHAIN_MAX:
        opening = _opening_segments_for_hook(segments)
        groups = _partition_segments(opening, HOOK_CHAIN_MAX)
        nonempty = _non_empty_groups(groups)
        merged: list[VisualBeat] = []
        for gi, group in enumerate(nonempty):
            g_start, g_end = _hook_contiguous_window(nonempty, gi, window_start, opening)
            quote = merge_segment_texts(segments, g_start, g_end)
            src = chain[min(gi, len(chain) - 1)]
            merged.append(VisualBeat(
                start=g_start,
                end=g_end,
                audio_quote=quote or src.audio_quote,
                character_desc=src.character_desc,
                scene_intent=src.scene_intent or HOOK_CHAIN_ROLES[min(gi, len(HOOK_CHAIN_ROLES) - 1)],
                camera=src.camera,
                background=src.background,
                visual=src.visual,
                labels=src.labels,
                style=src.style,
                is_hook=True,
                hook_type=hook_type or src.hook_type,
                scene_bridge=src.scene_bridge or _default_scene_bridge(gi, len(nonempty)),
            ))
        chain = merged
    return _reanchor_hook_chain_to_segments(chain, segments, hook_type)


def _expand_hook_chain(
    chain: list[VisualBeat],
    segments: list[TranscriptSegment],
    hook_type: str = "",
) -> list[VisualBeat]:
    """Tách 1 hook LLM thành chuỗi 2–3 prompt render."""
    if not chain:
        return _synthesize_hook_chain(segments)
    base = chain[0]
    start = max(0.0, base.start)
    end = min(base.end, start + HOOK_RENDER_MAX_SEC)
    opening = _opening_segments_for_hook(segments)
    target = HOOK_CHAIN_MAX if len(opening) >= 3 else HOOK_CHAIN_MIN
    if end - start < HOOK_MIN_SEC:
        _, end = estimate_hook_window(segments)
    groups = _partition_segments(opening or segments[:target], target)
    nonempty = _non_empty_groups(groups)
    expanded: list[VisualBeat] = []
    for i, group in enumerate(nonempty):
        g_start, g_end = _hook_contiguous_window(nonempty, i, start, opening or segments[:target])
        quote = merge_segment_texts(segments, g_start, g_end) or base.audio_quote
        expanded.append(VisualBeat(
            start=g_start,
            end=g_end,
            audio_quote=quote,
            character_desc=base.character_desc,
            scene_intent=base.scene_intent if i == 0 else HOOK_CHAIN_ROLES[min(i, len(HOOK_CHAIN_ROLES) - 1)],
            camera=base.camera,
            background=base.background,
            visual=base.visual,
            labels=base.labels,
            style=base.style,
            is_hook=True,
            hook_type=hook_type or base.hook_type or HOOK_TYPES[1],
            scene_bridge=_default_scene_bridge(i, len(nonempty)),
        ))
    return expanded if len(expanded) >= HOOK_CHAIN_MIN else _synthesize_hook_chain(segments, seed=base)


def _synthesize_hook_chain(
    segments: list[TranscriptSegment],
    *,
    seed: VisualBeat | None = None,
) -> list[VisualBeat]:
    """Fallback: gom cue 0–10s thành chuỗi 2–3 hook prompt liền mạch."""
    start, end = estimate_hook_window(segments)
    opening = _opening_segments_for_hook(segments)
    if not opening:
        opening = segments[:1]
    target = HOOK_CHAIN_MAX if len(opening) >= 3 else HOOK_CHAIN_MIN
    groups = _partition_segments(opening, target)
    nonempty = _non_empty_groups(groups)
    chain: list[VisualBeat] = []
    for i, group in enumerate(nonempty):
        g_start, g_end = _hook_contiguous_window(nonempty, i, start, opening)
        quote = merge_segment_texts(segments, g_start, g_end)
        if not quote and seed:
            quote = seed.audio_quote
        if not quote and segments:
            quote = segments[0].text.strip()
        chain.append(VisualBeat(
            start=g_start,
            end=g_end,
            audio_quote=quote or "Mở đầu",
            character_desc=(seed.character_desc if seed else DEFAULT_CHARACTER_STYLE),
            scene_intent=HOOK_CHAIN_ROLES[min(i, len(HOOK_CHAIN_ROLES) - 1)],
            camera=(seed.camera if seed and i == 0 else "Cận hoặc trung bình, nhấn biểu cảm."),
            background=(seed.background if seed and i == 0 else "Nền đơn giản, bố cục rõ."),
            visual=(
                seed.visual if seed and i == 0 else
                "Tiếp nối hook — cùng nhân vật/bối cảnh, phát hiện thêm chi tiết."
            ),
            labels=DEFAULT_LABELS,
            style=(seed.style if seed else DEFAULT_ART_STYLE),
            is_hook=True,
            hook_type=(seed.hook_type if seed else HOOK_TYPES[1]),
            scene_bridge=_default_scene_bridge(i, len(nonempty)),
        ))
    if len(chain) < HOOK_CHAIN_MIN:
        quote = merge_segment_texts(segments, start, end) or (segments[0].text.strip() if segments else "Mở đầu")
        mid = start + (end - start) / 2
        chain = [
            VisualBeat(
                start=start, end=mid, audio_quote=quote,
                character_desc=DEFAULT_CHARACTER_STYLE,
                scene_intent=HOOK_CHAIN_ROLES[0],
                camera="Cận hoặc trung bình.",
                background="Nền đơn giản.",
                visual="Nhân vật chính — cú mở cảnh hook.",
                labels=DEFAULT_LABELS, style=DEFAULT_ART_STYLE,
                is_hook=True, hook_type=HOOK_TYPES[1],
                scene_bridge=_default_scene_bridge(0, 2),
            ),
            VisualBeat(
                start=mid, end=end, audio_quote=quote,
                character_desc=DEFAULT_CHARACTER_STYLE,
                scene_intent=HOOK_CHAIN_ROLES[1],
                camera="Lia hoặc tiến gần trong cùng bối cảnh.",
                background="Nền đơn giản.",
                visual="Làm rõ điều bất thường — cùng tình huống.",
                labels=DEFAULT_LABELS, style=DEFAULT_ART_STYLE,
                is_hook=True, hook_type=HOOK_TYPES[1],
                scene_bridge=_default_scene_bridge(1, 2),
            ),
        ]
    return chain


def _parse_hook_chain(
    data: dict[str, Any],
    segments: list[TranscriptSegment],
) -> list[VisualBeat]:
    hook_type = ""
    raw_chain = data.get("hook_chain")
    if isinstance(raw_chain, list) and raw_chain:
        hook_type = _normalize_hook_type(str(data.get("hook_type") or ""))
        chain = [
            _hook_chain_beat_from_dict(
                item,
                segments=segments,
                role_index=i,
                chain_total=min(len(raw_chain), HOOK_CHAIN_MAX),
                hook_type=hook_type,
            )
            for i, item in enumerate(raw_chain[:HOOK_CHAIN_MAX])
            if isinstance(item, dict)
        ]
        return _normalize_hook_chain(chain, segments, hook_type)

    hook_raw = data.get("hook")
    if isinstance(hook_raw, dict):
        single = _hook_beat_from_dict(hook_raw, segments)
        hook_type = single.hook_type
        return _expand_hook_chain([single], segments, hook_type)

    return _synthesize_hook_chain(segments)


def _hook_beat_from_dict(
    item: dict[str, Any],
    segments: list[TranscriptSegment],
) -> VisualBeat:
    hint_start, hint_end = estimate_hook_window(segments)
    try:
        start = float(item.get("start_sec", hint_start))
        end = float(item.get("end_sec", hint_end))
    except (TypeError, ValueError) as err:
        raise GeneratePromptsError("Hook thiếu start_sec/end_sec hợp lệ.") from err
    start = max(0.0, start)
    end = min(max(end, start + 0.5), start + HOOK_MAX_SEC)
    if end - start < HOOK_MIN_SEC:
        end = min(start + HOOK_MAX_SEC, max(hint_end, start + HOOK_MIN_SEC))
    hook_type = _normalize_hook_type(str(item.get("hook_type") or ""))
    quote = _extract_beat_quote_field(item)
    if not quote:
        quote = merge_segment_texts(segments, start, end)
    if not quote:
        quote = _infer_audio_quote(segments, start, end)
    if not quote:
        raise GeneratePromptsError("Hook thiếu audio_quote bám transcript.")
    beat = _beat_from_dict(
        {
            **item,
            "start_sec": start,
            "end_sec": end,
            "audio_quote": quote,
            "camera": item.get("camera") or "Cận hoặc trung bình, nhấn biểu cảm và vấn đề.",
            "background": item.get("background") or "Nền đơn giản, ít chi tiết phụ.",
        },
        is_hook=True,
        hook_type=hook_type,
    )
    if not beat.visual.strip():
        beat.visual = (
            "Một khung hình dễ hiểu trong 1 giây: nhân vật chính đối mặt vấn đề lớn, "
            "căng thẳng/tò mò, không tiết lộ kết cục."
        )
    return beat


def _synthesize_hook_beat(
    beats: list[VisualBeat],
    segments: list[TranscriptSegment],
) -> VisualBeat:
    """Fallback: gộp beat/cue mở đầu thành hook khi LLM không trả object hook."""
    start, end = estimate_hook_window(segments)
    quote = merge_segment_texts(segments, start, end)
    if not quote and beats:
        opening = [b for b in beats if b.start < end + 0.05]
        if opening:
            start = opening[0].start
            end = max(b.end for b in opening)
            end = min(end, start + HOOK_MAX_SEC)
            quote = " ".join(b.audio_quote for b in opening).strip()
    if not quote:
        quote = segments[0].text.strip() if segments else "Mở đầu"
    visual = ""
    scene = "Căng thẳng, tò mò — giữ chân người xem ngay giây đầu."
    if beats:
        visual = beats[0].visual
        scene = beats[0].scene_intent or scene
    return VisualBeat(
        start=start,
        end=end,
        audio_quote=quote,
        character_desc=DEFAULT_CHARACTER_STYLE,
        scene_intent=scene,
        camera="Cận hoặc trung bình, nhấn biểu cảm.",
        background="Nền đơn giản, bố cục rõ.",
        visual=visual or (
            "Nhân vật chính đối mặt vấn đề lớn — hiểu ngay trong 1 giây, không tiết lộ kết."
        ),
        labels=DEFAULT_LABELS,
        style=DEFAULT_ART_STYLE,
        is_hook=True,
        hook_type=HOOK_TYPES[1],
    )


def _beats_after_hook(
    beats: list[VisualBeat],
    hook_end: float,
    segments: list[TranscriptSegment] | None = None,
) -> list[VisualBeat]:
    rest: list[VisualBeat] = []
    for beat in beats:
        if beat.is_hook:
            continue
        if beat.end <= hook_end + 0.05:
            continue
        if beat.start < hook_end - 0.05:
            trimmed_start = hook_end
            quote = merge_segment_texts(segments, trimmed_start, beat.end) if segments else beat.audio_quote
            beat = VisualBeat(
                start=trimmed_start,
                end=beat.end,
                audio_quote=quote or beat.audio_quote,
                character_desc=beat.character_desc,
                scene_intent=beat.scene_intent,
                camera=beat.camera,
                background=beat.background,
                visual=_visual_for_resynced_beat(beat, quote or beat.audio_quote),
                labels=beat.labels,
                style=beat.style,
                is_hook=False,
                hook_type="",
                scene_bridge=beat.scene_bridge,
            )
        if beat.end > beat.start + 0.05:
            rest.append(beat)
    return rest


def parse_visual_beat_response(
    data: dict[str, Any],
    segments: list[TranscriptSegment],
) -> list[VisualBeat]:
    """Parse hook_chain (2–3 prompt 0–10s) + beats; prompt 001–003 là chuỗi hook render."""
    raw_beats = data.get("beats")
    body_beats: list[VisualBeat] = []
    if isinstance(raw_beats, list) and raw_beats:
        body_beats = [
            _beat_from_dict(item, segments=segments)
            for item in raw_beats
            if isinstance(item, dict)
        ]
        body_beats.sort(key=lambda b: b.start)

    hook_chain = _parse_hook_chain(data, segments)
    if not hook_chain:
        hook_chain = _synthesize_hook_chain(segments)
    hook_end = hook_chain[-1].end
    rest = _beats_after_hook(body_beats, hook_end, segments)
    merged = hook_chain + rest
    merged.sort(key=lambda b: b.start)
    if not merged:
        raise GeneratePromptsError("Không có beat hợp lệ sau khi áp hook_chain.")
    for i, beat in enumerate(merged[: len(hook_chain)]):
        beat.is_hook = True
        if not beat.hook_type:
            beat.hook_type = HOOK_TYPES[1]
        if not beat.scene_bridge.strip():
            beat.scene_bridge = _default_scene_bridge(i, len(hook_chain))
    return merged


def _log_beat_density(
    beats: list[VisualBeat],
    segments: list[TranscriptSegment],
    log_callback,
) -> None:
    if not log_callback or not beats:
        return
    density = estimate_beat_density(segments)
    total = len(beats)
    opening = sum(1 for b in beats if b.start < OPENING_DENSE_SEC)
    t_min = density["total_beats_min"]
    t_max = density["total_beats_max"]
    o_min = density["opening_0_30s_beats_min"]
    o_max = density["opening_0_30s_beats_max"]
    log_callback(
        f"Visual beat: {total} beat / {density['duration_min']} phút "
        f"(0–30s: {opening} beat; tham khảo mật độ {t_min}–{t_max}, "
        f"0–30s {o_min}–{o_max} — ưu tiên khớp audio).",
        "info",
    )


def parse_llm_beats(
    data: dict[str, Any],
    segments: list[TranscriptSegment] | None = None,
) -> list[VisualBeat]:
    raw = data.get("beats")
    if not isinstance(raw, list) or not raw:
        raise GeneratePromptsError("JSON thiếu mảng beats.")
    beats = [
        _beat_from_dict(item, segments=segments)
        for item in raw
        if isinstance(item, dict)
    ]
    if not beats:
        raise GeneratePromptsError("Không có beat hợp lệ trong JSON.")
    beats.sort(key=lambda b: b.start)
    return beats


def _process_groq_chunk(
    client,
    chunk_segs: list[TranscriptSegment],
    all_segments: list[TranscriptSegment],
    *,
    include_hook: bool,
    chunk_index: int,
    chunk_total: int,
    log_callback=None,
) -> tuple[list[VisualBeat], list[VisualBeat]]:
    """Gọi Groq cho một chunk; tự tách đôi nếu 413 request too large."""
    system = (
        _build_visual_beat_system_prompt()
        if include_hook
        else _build_continuation_system_prompt()
    )
    user = (
        "Dữ liệu transcript:\n"
        + _build_visual_beat_user_payload(
            chunk_segs,
            all_segments=all_segments,
            include_hook=include_hook,
            chunk_index=chunk_index,
            chunk_total=chunk_total,
        )
    )
    try:
        data = _groq_chat_json_with_fallback(
            client, system=system, user=user, log_callback=log_callback,
        )
    except GroqLlmPayloadTooLargeError:
        if len(chunk_segs) <= 1:
            raise
        mid = max(1, len(chunk_segs) // 2)
        if log_callback:
            log_callback(
                f"Chunk {chunk_index + 1} quá lớn ({len(chunk_segs)} segment) "
                f"→ tách {mid}+{len(chunk_segs) - mid}...",
                "warn",
            )
        left, right = chunk_segs[:mid], chunk_segs[mid:]
        hook_l, body_l = _process_groq_chunk(
            client, left, all_segments,
            include_hook=include_hook, chunk_index=chunk_index, chunk_total=chunk_total,
            log_callback=log_callback,
        )
        hook_r, body_r = _process_groq_chunk(
            client, right, all_segments,
            include_hook=False, chunk_index=chunk_index, chunk_total=chunk_total,
            log_callback=log_callback,
        )
        return (hook_l or hook_r), body_l + body_r

    if include_hook:
        chunk_beats = parse_visual_beat_response(data, all_segments)
        hook_n = sum(1 for b in chunk_beats if b.is_hook)
        if hook_n < HOOK_CHAIN_MIN:
            hook_n = min(len(chunk_beats), HOOK_CHAIN_MAX)
        hook_n = max(HOOK_CHAIN_MIN, min(hook_n, HOOK_CHAIN_MAX, len(chunk_beats)))
        return chunk_beats[:hook_n], chunk_beats[hook_n:]
    return [], parse_llm_beats(data, all_segments)


def finalize_timeline_coverage(
    beats: list[VisualBeat],
    segments: list[TranscriptSegment],
) -> list[VisualBeat]:
    """Neo hook_chain 0–10s (giữ chân) + lấp gap body sau hook."""
    if not segments:
        return beats
    hooks = [b for b in beats if b.is_hook]
    body = [b for b in beats if not b.is_hook]
    if hooks:
        hooks = _reanchor_hook_chain_to_segments(hooks[:HOOK_CHAIN_MAX], segments)
    else:
        hooks = _synthesize_hook_chain(segments)
    if len(hooks) < HOOK_CHAIN_MIN:
        hooks = _synthesize_hook_chain(segments, seed=hooks[0] if hooks else None)
    hooks = _stitch_beats_contiguous(hooks, segments)
    hooks = [
        _resync_beat_quote_and_visual(h, segments, h.start, h.end)
        for h in hooks
    ]
    hooks = _ensure_hook_chain_quality(hooks)
    hook_end = hooks[-1].end
    body = _beats_after_hook(body, hook_end, segments)
    post_hook_segs = _segments_after_hook(segments, hook_end)
    body = fill_timeline_gaps(body, segments, coverage_segments=post_hook_segs)
    body = _realign_body_beats_to_segments(body, segments, hook_end)
    return hooks + body


def call_groq_visual_beats(
    segments: list[TranscriptSegment],
    *,
    api_key: str | None = None,
    log_callback=None,
) -> list[VisualBeat]:
    if not segments:
        raise GeneratePromptsError("Transcript rỗng — không phân tích visual beat.")
    key = (api_key or groq_api_key() or "").strip()
    if not key:
        raise GeneratePromptsError(f"Thiếu {GROQ_API_KEY_ENV} trong .env hoặc UI.")
    if not groq_client_available():
        raise GeneratePromptsError(
            "Chưa cài groq — bấm «Cài đặt» hoặc: pip install groq"
        )

    from groq import Groq

    load_cached_groq_models()
    chunks = split_segments_for_groq_llm(segments)
    active = groq_llm_active_model()
    if log_callback:
        model_note = f" (cache)" if groq_llm_using_cached_model() else ""
        if len(chunks) == 1:
            log_callback(
                f"Groq {active}{model_note}: phân tích hook + {len(segments)} segment...",
                "info",
            )
        else:
            log_callback(
                f"Groq {active}{model_note}: {len(segments)} segment → {len(chunks)} chunk "
                f"(≤~{GROQ_LLM_MAX_INPUT_TOKENS} token input/request)...",
                "info",
            )

    client = Groq(api_key=key)
    hook_chain: list[VisualBeat] = []
    body_beats: list[VisualBeat] = []
    last_call_at = 0.0

    for idx, (chunk_segs, include_hook) in enumerate(chunks):
        if idx > 0 and GROQ_LLM_CHUNK_MIN_INTERVAL_SEC > 0:
            wait = GROQ_LLM_CHUNK_MIN_INTERVAL_SEC - (time.monotonic() - last_call_at)
            if wait > 0:
                time.sleep(wait)

        if log_callback and len(chunks) > 1:
            t0, t1 = chunk_segs[0].start, chunk_segs[-1].end
            log_callback(
                f"Groq chunk {idx + 1}/{len(chunks)}: {len(chunk_segs)} segment "
                f"[{t0:.0f}s–{t1:.0f}s]...",
                "info",
            )

        hook_part, body_part = _process_groq_chunk(
            client,
            chunk_segs,
            segments,
            include_hook=include_hook,
            chunk_index=idx,
            chunk_total=len(chunks),
            log_callback=log_callback,
        )
        last_call_at = time.monotonic()

        if hook_part and not hook_chain:
            hook_chain = hook_part
        body_beats.extend(body_part)

    beats = _merge_chunked_beats(hook_chain or None, body_beats, segments)
    before_fill = len(beats)
    beats = finalize_timeline_coverage(beats, segments)
    if log_callback and len(beats) > before_fill:
        log_callback(
            f"Bổ sung {len(beats) - before_fill} beat cho đoạn audio LLM bỏ sót "
            f"(tổng {len(beats)} beat).",
            "warn",
        )
    if log_callback and beats:
        hook_items = [b for b in beats if b.is_hook]
        hook_end = hook_items[-1].end if hook_items else beats[0].end
        hook_label = hook_items[0].hook_type if hook_items else "hook"
        log_callback(
            f"Groq LLM ({groq_llm_active_model()}): hook_chain {len(hook_items)} prompt "
            f"[{beats[0].start:.1f}–{hook_end:.1f}s] «{hook_label}» + "
            f"{max(0, len(beats) - len(hook_items))} beat sau hook"
            + (f" ({len(chunks)} chunk)." if len(chunks) > 1 else "."),
            "success",
        )
        _log_beat_density(beats, segments, log_callback)
    return beats


# Alias tương thích test / import cũ
parse_gemini_response = parse_visual_beat_response
parse_gemini_beats = parse_llm_beats
call_gemini_visual_beats = call_groq_visual_beats


def write_image_prompts_txt(beats: list[VisualBeat], output: Path) -> Path:
    output = Path(output)
    if output.suffix.lower() != ".txt":
        output = output.with_suffix(".txt")
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [format_beat_line(i + 1, beat) for i, beat in enumerate(beats)]
    output.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    return output


def default_prompts_path(audio_path: Path) -> Path:
    return Path(audio_path).with_suffix(".txt")


def generate_image_prompts_from_segments(
    segments: list[TranscriptSegment],
    output: Path,
    *,
    api_key: str | None = None,
    log_callback=None,
) -> Path:
    beats = call_groq_visual_beats(
        segments,
        api_key=api_key,
        log_callback=log_callback,
    )
    path = write_image_prompts_txt(beats, output)
    if log_callback:
        log_callback(f"Đã ghi {len(beats)} prompt → {path.name}", "success")
    return path
