#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audio → SRT (Groq Whisper API, fallback faster-whisper local)."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import threading
import unicodedata
from pathlib import Path

from videobuilder.core.pipeline import ProcessController, RenderCancelled, get_media_duration, parse_srt_file, write_srt_from_cues
from videobuilder.core.env_config import GROQ_API_KEY_ENV

WHISPER_MODELS = ("tiny", "base", "small", "medium", "large-v3")
DEFAULT_MODEL = "small"
DEFAULT_LANGUAGE = "auto"
SRT_SPLIT_MODES = ("very_few", "few", "normal", "many", "medium", "short")
DEFAULT_SRT_SPLIT = "normal"
SRT_SPLIT_OPTIONS = (
    ("very_few", "Rất ít ngắt"),
    ("few", "Ít ngắt"),
    ("normal", "Bình thường"),
    ("many", "Nhiều ngắt"),
    ("medium", "Khá ngắt"),
    ("short", "Rất ngắt"),
)
SRT_SPLIT_LABEL_TO_KEY = {label: key for key, label in SRT_SPLIT_OPTIONS}
SRT_SPLIT_KEY_TO_LABEL = {key: label for key, label in SRT_SPLIT_OPTIONS}
SRT_SPLIT_PARAMS = {
    "very_few": {"max_chars": 200, "max_duration": 22.0, "merge_gap": 2.5},
    "few": {"max_chars": 140, "max_duration": 14.0, "merge_gap": 1.2},
    "many": {
        "max_chars": 52,
        "max_duration": 8.0,
        "min_gap": 0.05,
        "pause_split": 0.42,
        "min_chars": 12,
        "merge_gap": 2.5,
    },
    "medium": {
        "max_chars": 45,
        "max_duration": 5.5,
        "min_gap": 0.05,
        "pause_split": 0.35,
        "min_chars": 10,
        "merge_gap": 1.2,
    },
    "short": {
        "max_chars": 38,
        "max_duration": 4.0,
        "min_gap": 0.05,
        "pause_split": 0.30,
        "min_chars": 8,
        "merge_gap": 0.45,
    },
}
WHISPER_NUMPY_SPEC = "numpy<2"
from videobuilder.core.groq_models import (
    GROQ_WHISPER_LARGE,
    GROQ_WHISPER_TURBO,
    groq_whisper_active_model,
    groq_whisper_chain_label,
    groq_whisper_model_chain,
    groq_whisper_primary_model,
    groq_whisper_using_cached_model,
    load_cached_groq_models,
    set_active_whisper_model,
)

GROQ_WHISPER_MODEL = GROQ_WHISPER_TURBO
GROQ_WHISPER_MODEL_VI = GROQ_WHISPER_LARGE
GROQ_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
GROQ_CHUNK_SECONDS = 18 * 60
GROQ_TRANSCRIBE_CHUNK_SECONDS = 8 * 60
GROQ_PROMPT_API_CHAR_MAX = 896
GROQ_PROMPT_MAX_CHARS = 840
GROQ_PROMPT_MAX_TOKENS = 200
GROQ_PROMPT_MAX_CUES = 10
GROQ_MAX_SEGMENT_DURATION = 12.0
GROQ_PROMPT_ECHO_MARKERS = (
    "nội dung tự nhiên",
    "câu hoàn chỉnh",
    "phụ đề tiếng việt",
    "có dấu đầy đủ",
    "hãy subscribe",
    "subscribe cho kênh",
    "để không bỏ lỡ",
    "ghiền mì gõ",
)
GROQ_GAP_RECOVER_MAX_SEC = 45.0
# Ngưỡng hole timeline — khớp generate_prompts (tránh import vòng)
_SRT_OPENING_DENSE_SEC = 30.0
_SRT_GAP_OPENING_SEC = 4.5
_SRT_GAP_BODY_SEC = 7.0

_VN_TONE_RE = re.compile(
    r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]",
    re.I,
)


def _guess_language_from_cues(cues: list[tuple[float, float, str]]) -> str:
    """Heuristic: chọn 'vi' nếu thấy nhiều dấu tiếng Việt, ngược lại 'en'.

    Mục tiêu là giúp chế độ auto của Groq ít rớt cue trên audio dài/nhiễu.
    """
    sample = " ".join((t or "").strip() for *_r, t in cues[-12:])
    if not sample.strip():
        return ""
    if _VN_TONE_RE.search(sample):
        return "vi"
    # Nếu có chữ cái Latin mà không có dấu VN, ưu tiên en
    if re.search(r"[a-z]", sample, re.I):
        return "en"
    return ""


def _groq_response_language(result) -> str:
    """Ngôn ngữ Groq tự detect — dùng cho chunk/gap-fill khi user chọn auto."""
    lang = getattr(result, "language", None)
    if lang is None and isinstance(result, dict):
        lang = result.get("language")
    text = (lang or "").strip().lower()
    if text in ("vi", "en", "ja", "ko", "zh", "fr", "de", "es"):
        return text
    if text.startswith("zh"):
        return "zh"
    return ""


def _split_time_range(start: float, end: float, *, max_span: float) -> list[tuple[float, float]]:
    if end - start <= max_span:
        return [(start, end)]
    out: list[tuple[float, float]] = []
    cursor = start
    while cursor < end - 0.05:
        seg_end = min(end, cursor + max_span)
        out.append((cursor, seg_end))
        cursor = seg_end
    return out


WHISPER_MODEL_INFO = {
    "tiny": ("~75 MB", "Nhanh nhất, độ chính xác thấp"),
    "base": ("~145 MB", "Nhanh, phù hợp audio rõ"),
    "small": ("~466 MB", "Cân bằng — khuyên dùng"),
    "medium": ("~1.5 GB", "Chính xác hơn, chậm hơn"),
    "large-v3": ("~3.1 GB", "Chính xác nhất, nên dùng GPU"),
}
TORCH_CU121_PACKAGES = (
    "torch==2.2.1",
    "torchvision==0.17.1",
    "torchaudio==2.2.1",
)
TORCH_CU121_INDEX = "https://download.pytorch.org/whl/cu121"

_GPU_ERROR_MARKERS = (
    "cublas",
    "cudnn",
    "cudart",
    "cuda",
    "dll is not found",
    "cannot be loaded",
    "out of memory",
    "no cuda",
)


class CreateSrtError(Exception):
    pass


class CreateSrtCancelled(CreateSrtError):
    pass


class GroqRateLimitError(CreateSrtError):
    """Groq API vượt giới hạn — chuyển faster-whisper local."""
    pass


def _huggingface_hub_dir() -> Path:
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home) / "hub"
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "huggingface" / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _whisper_model_cache_folders(model: str) -> tuple[str, ...]:
    return (
        f"models--Systran--faster-whisper-{model}",
        f"models--guillaumekln--faster-whisper-{model}",
    )


def normalize_srt_split(mode: str | None) -> str:
    text = (mode or "").strip()
    if not text:
        return DEFAULT_SRT_SPLIT
    if text in SRT_SPLIT_MODES:
        return text
    if text in SRT_SPLIT_LABEL_TO_KEY:
        return SRT_SPLIT_LABEL_TO_KEY[text]
    return DEFAULT_SRT_SPLIT


def _ends_sentence(text: str) -> bool:
    text = text.rstrip()
    return bool(text) and text[-1] in ".!?…"


def _ends_clause(text: str) -> bool:
    text = text.rstrip()
    return bool(text) and text[-1] in ",.;:!?…、，。！？"


def _word_text(word) -> str:
    return (word.word or "").strip()


def _join_cue_text(parts: list[str]) -> str:
    out = ""
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if not out:
            out = piece
        elif piece[0] in ".,!?;:…、，。！？":
            out += piece
        elif out[-1].isspace() or piece[0].isspace():
            out += piece
        else:
            out += f" {piece}"
    return out.strip()


def _pause_before(words, index: int) -> float:
    if index <= 0:
        return 0.0
    return max(0.0, float(words[index].start) - float(words[index - 1].end))


def _starts_upper_word(word: str) -> bool:
    text = word.strip()
    if not text:
        return False
    ch = text[0]
    return ch.isupper() and ch != ch.lower()


def _split_at_capitals(text: str) -> list[str]:
    """Tách khi gặp từ viết hoa đầu dòng/cụm (phụ đề kiểu Shorts)."""
    words = text.split()
    if not words:
        return []
    chunks: list[str] = [words[0]]
    for word in words[1:]:
        if _starts_upper_word(word):
            chunks.append(word)
        else:
            chunks[-1] = f"{chunks[-1]} {word}"
    return [c.strip() for c in chunks if c.strip()]


def _split_many_text(text: str, max_chars: int) -> list[str]:
    """Nhiều ngắt: hết câu (. ! ?) + cụm viết hoa đầu dòng."""
    del max_chars  # giữ nguyên cụm dài nếu không có chữ hoa bên trong
    text = text.strip()
    if not text:
        return []
    result: list[str] = []
    for sentence in _split_into_sentences(text):
        result.extend(_split_at_capitals(sentence))
    return result if result else [text]


def _split_medium_text(text: str, max_chars: int) -> list[str]:
    """Khá ngắt: hết câu + viết hoa + dấu phẩy (không cắt giữa cụm)."""
    del max_chars
    text = text.strip()
    if not text:
        return []
    result: list[str] = []
    for sentence in _split_into_sentences(text):
        for cap_part in _split_at_capitals(sentence):
            result.extend(_split_into_phrases(cap_part))
    return result if result else [text]


def _split_into_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    return parts if parts else [text]


def _merge_incomplete_sentence_cues(
    cues: list[tuple[float, float, str]],
    *,
    merge_gap: float = 2.5,
) -> list[tuple[float, float, str]]:
    """Gộp cue liền kề nếu câu trước chưa kết thúc (. ! ?)."""
    if not cues:
        return cues
    merged: list[tuple[float, float, str]] = [cues[0]]
    for start, end, text in cues[1:]:
        ps, pe, pt = merged[-1]
        gap = start - pe
        if not _ends_sentence(pt) and gap <= merge_gap:
            merged[-1] = (ps, end, _join_cue_text([pt, text]))
        else:
            merged.append((start, end, text))
    return merged


def _chunk_text_by_words(text: str, max_chars: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        add = len(word) if not current else len(word) + 1
        if current and length + add > max_chars:
            chunks.append(" ".join(current))
            current = [word]
            length = len(word)
        else:
            current.append(word)
            length += add
    if current:
        chunks.append(" ".join(current))
    return chunks


def _split_into_phrases(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?…,;])\s+", text) if p.strip()]
    return parts if parts else [text]


def _chunk_text_for_split(text: str, max_chars: int) -> list[str]:
    """Chỉ tách theo hết câu; không cắt giữa câu theo số ký tự."""
    text = text.strip()
    if not text:
        return []
    sentences = _split_into_sentences(text)
    if len(sentences) <= 1:
        return [text]
    return _merge_small_chunks(sentences, max_chars)


def _merge_small_chunks(parts: list[str], max_chars: int) -> list[str]:
    merged: list[str] = []
    for part in parts:
        if merged and len(merged[-1]) + 1 + len(part) <= max_chars:
            merged[-1] = f"{merged[-1]} {part}"
        elif merged and len(part) < 12:
            candidate = f"{merged[-1]} {part}"
            if len(candidate) <= max_chars:
                merged[-1] = candidate
                continue
            merged.append(part)
        else:
            merged.append(part)
    return merged


def _assign_chunk_times(
    start: float,
    end: float,
    chunks: list[str],
    min_gap: float = 0.12,
) -> list[tuple[float, float, str]]:
    if not chunks:
        return []
    if len(chunks) == 1:
        return [(start, max(start + min_gap, end), chunks[0])]

    weights = [max(1, len(chunk.split())) for chunk in chunks]
    total_weight = sum(weights) or 1
    duration = max(end - start, min_gap * len(chunks))
    t = start
    result: list[tuple[float, float, str]] = []
    for i, chunk in enumerate(chunks):
        frac = weights[i] / total_weight
        chunk_end = end if i == len(chunks) - 1 else min(end, t + duration * frac)
        chunk_end = max(t + min_gap, chunk_end)
        result.append((t, chunk_end, chunk))
        t = chunk_end
    result[-1] = (result[-1][0], end, result[-1][2])
    return result


def _split_long_cue(
    start: float,
    end: float,
    text: str,
    max_chars: int,
    max_duration: float,
) -> list[tuple[float, float, str]]:
    text = text.strip()
    if not text:
        return []
    sentences = _split_into_sentences(text)
    if len(sentences) <= 1:
        return [(start, end, text)]
    return _assign_chunk_times(start, end, sentences)


def _merge_cues_few(
    cues: list[tuple[float, float, str]],
    *,
    max_chars: int,
    max_duration: float,
    merge_gap: float,
) -> list[tuple[float, float, str]]:
    if not cues:
        return []
    merged: list[tuple[float, float, str]] = [cues[0]]
    for start, end, text in cues[1:]:
        ps, pe, pt = merged[-1]
        gap = start - pe
        combined = f"{pt} {text}".strip()
        duration = end - ps
        if gap <= merge_gap and len(combined) <= max_chars and duration <= max_duration:
            merged[-1] = (ps, end, combined)
        else:
            merged.append((start, end, text))
    return merged


def _merge_tiny_cues(
    cues: list[tuple[float, float, str]],
    *,
    min_chars: int,
    min_duration: float,
    max_gap: float,
) -> list[tuple[float, float, str]]:
    if len(cues) <= 1:
        return cues
    merged: list[tuple[float, float, str]] = [cues[0]]
    for start, end, text in cues[1:]:
        ps, pe, pt = merged[-1]
        gap = start - pe
        prev_short = len(pt.strip()) < min_chars or (pe - ps) < min_duration
        if prev_short and gap <= max_gap:
            merged[-1] = (ps, end, _join_cue_text([pt, text]))
        else:
            merged.append((start, end, text))
    return merged


def _merge_fragment_cues(
    cues: list[tuple[float, float, str]],
    *,
    max_chars: int,
    max_duration: float,
    merge_gap: float,
) -> list[tuple[float, float, str]]:
    """Gộp cue còn dấu phẩy cuối hoặc mảnh quá ngắn (liệt kê, cụm từ)."""
    if len(cues) <= 1:
        return cues
    merged: list[tuple[float, float, str]] = [cues[0]]
    for start, end, text in cues[1:]:
        ps, pe, pt = merged[-1]
        gap = start - pe
        combined = _join_cue_text([pt, text])
        duration = end - ps
        pt_tail = pt.rstrip()
        continues = pt_tail.endswith(",") or pt_tail.endswith("、")
        tiny = len(pt_tail) < 22 or len(text.strip()) < 14
        if (
            gap <= merge_gap
            and len(combined) <= max_chars
            and duration <= max_duration
            and (continues or (tiny and gap <= merge_gap * 0.7))
        ):
            merged[-1] = (ps, end, combined)
        else:
            merged.append((start, end, text))
    return merged


def _group_words_to_cues_sentence(
    words,
    *,
    max_chars: int,
    max_duration: float,
    min_gap: float,
    pause_split: float = 0.30,
    min_chars: int = 5,
) -> list[tuple[float, float, str]]:
    clean = [w for w in words if _word_text(w)]
    if not clean:
        return []

    cues: list[tuple[float, float, str]] = []
    buf: list = []
    buf_start: float | None = None
    buf_end: float | None = None

    def text_of(ws) -> str:
        return "".join(w.word for w in ws).strip()

    def flush():
        nonlocal buf, buf_start, buf_end
        text = text_of(buf)
        if text and buf_start is not None:
            cues.append(
                (buf_start, max(buf_start + min_gap, buf_end or buf_start + min_gap), text)
            )
        buf = []
        buf_start = None
        buf_end = None

    def should_break_before(index: int, word) -> bool:
        if not buf or buf_start is None:
            return False
        if not _ends_sentence(text_of(buf)):
            return False
        cand_dur = float(word.end) - buf_start
        return cand_dur > max_duration * 2.0

    for index, word in enumerate(clean):
        w = (word.word or "").strip()
        if buf and w and _starts_upper_word(w):
            flush()
            buf_start = float(word.start)
        elif should_break_before(index, word):
            flush()
            buf_start = float(word.start)
        elif buf_start is None:
            buf_start = float(word.start)
        buf.append(word)
        buf_end = float(word.end)
        if _ends_sentence(text_of(buf)):
            flush()

    flush()
    return _merge_incomplete_sentence_cues(
        cues,
        merge_gap=max(pause_split * 4, 2.0),
    )


def _group_words_to_cues_short(
    words,
    *,
    max_chars: int,
    max_duration: float,
    min_gap: float,
    pause_split: float = 0.30,
    min_chars: int = 8,
) -> list[tuple[float, float, str]]:
    clean = [w for w in words if _word_text(w)]
    if not clean:
        return []

    cues: list[tuple[float, float, str]] = []
    buf: list = []
    buf_start: float | None = None
    buf_end: float | None = None

    def text_of(ws) -> str:
        return "".join(w.word for w in ws).strip()

    def flush():
        nonlocal buf, buf_start, buf_end
        text = text_of(buf)
        if text and buf_start is not None:
            cues.append(
                (buf_start, max(buf_start + min_gap, buf_end or buf_start + min_gap), text)
            )
        buf = []
        buf_start = None
        buf_end = None

    def should_break_before(index: int, word) -> bool:
        if not buf or buf_start is None:
            return False
        current = text_of(buf)
        candidate = text_of(buf + [word])
        cand_dur = float(word.end) - buf_start
        if len(candidate) > max_chars or cand_dur > max_duration:
            return True
        if index > 0 and _pause_before(clean, index) >= pause_split and len(current) >= min_chars:
            return True
        return False

    for index, word in enumerate(clean):
        if should_break_before(index, word):
            flush()
            buf_start = float(word.start)
        elif buf_start is None:
            buf_start = float(word.start)
        buf.append(word)
        buf_end = float(word.end)
        if _ends_clause(text_of(buf)) and len(text_of(buf)) >= min_chars:
            flush()

    flush()
    params = SRT_SPLIT_PARAMS["short"]
    return _merge_fragment_cues(
        cues,
        max_chars=max_chars,
        max_duration=max_duration,
        merge_gap=params.get("merge_gap", 0.45),
    )


def _cues_from_words_many(words) -> list[tuple[float, float, str]]:
    params = SRT_SPLIT_PARAMS["many"]
    return _group_words_to_cues_sentence(
        words,
        max_chars=params["max_chars"],
        max_duration=params["max_duration"],
        min_gap=params["min_gap"],
        pause_split=params["pause_split"],
        min_chars=params["min_chars"],
    )


def _cues_from_words_short(words) -> list[tuple[float, float, str]]:
    params = SRT_SPLIT_PARAMS["short"]
    return _group_words_to_cues_short(
        words,
        max_chars=params["max_chars"],
        max_duration=params["max_duration"],
        min_gap=params["min_gap"],
        pause_split=params["pause_split"],
        min_chars=params["min_chars"],
    )


def refine_srt_cues(
    cues: list[tuple[float, float, str]],
    split_mode: str,
    *,
    words=None,
) -> list[tuple[float, float, str]]:
    mode = normalize_srt_split(split_mode)
    if mode == "normal" or not cues:
        return cues
    if mode == "very_few":
        params = SRT_SPLIT_PARAMS["very_few"]
        return _merge_cues_few(
            cues,
            max_chars=params["max_chars"],
            max_duration=params["max_duration"],
            merge_gap=params["merge_gap"],
        )
    if mode == "few":
        params = SRT_SPLIT_PARAMS["few"]
        return _merge_cues_few(
            cues,
            max_chars=params["max_chars"],
            max_duration=params["max_duration"],
            merge_gap=params["merge_gap"],
        )
    if mode == "short":
        if words:
            grouped = _cues_from_words_short(words)
            if grouped:
                return grouped
        return _refine_short_from_cues(cues)
    if mode == "medium":
        return _refine_medium_from_cues(cues)
    if mode == "many":
        if words:
            grouped = _cues_from_words_many(words)
            if grouped:
                return grouped
        return _refine_many_from_cues(cues)
    return cues


def _refine_short_from_cues(cues: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Dòng ngắn kiểu Shorts — tách theo dấu phẩy / độ dài."""
    params = SRT_SPLIT_PARAMS["short"]
    result: list[tuple[float, float, str]] = []
    for start, end, text in cues:
        text = text.strip()
        if not text:
            continue
        phrases: list[str] = []
        for phrase in _split_into_phrases(text):
            if len(phrase) <= params["max_chars"]:
                phrases.append(phrase)
            else:
                phrases.extend(_chunk_text_by_words(phrase, params["max_chars"]))
        if len(phrases) <= 1:
            result.append((start, end, text))
        else:
            result.extend(_assign_chunk_times(start, end, phrases))
    return _merge_fragment_cues(
        result,
        max_chars=params["max_chars"],
        max_duration=params["max_duration"],
        merge_gap=params["merge_gap"],
    )


def _refine_medium_from_cues(cues: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Khá ngắt: hết câu + viết hoa + dấu phẩy (không cắt giữa cụm như Rất ngắt)."""
    params = SRT_SPLIT_PARAMS["medium"]
    merged = _merge_incomplete_sentence_cues(
        cues,
        merge_gap=params.get("merge_gap", 1.2),
    )
    result: list[tuple[float, float, str]] = []
    for start, end, text in merged:
        text = text.strip()
        if not text:
            continue
        segments = _split_medium_text(text, params["max_chars"])
        if len(segments) <= 1:
            result.append((start, end, text))
        else:
            result.extend(_assign_chunk_times(start, end, segments))
    return _merge_fragment_cues(
        result,
        max_chars=params["max_chars"],
        max_duration=params["max_duration"],
        merge_gap=params.get("merge_gap", 1.2),
    )


def _refine_many_from_cues(cues: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Ngắt theo hết câu, cụm viết hoa, và độ dài tối đa."""
    params = SRT_SPLIT_PARAMS["many"]
    merged = _merge_incomplete_sentence_cues(
        cues,
        merge_gap=params.get("merge_gap", 2.5),
    )
    result: list[tuple[float, float, str]] = []
    for start, end, text in merged:
        text = text.strip()
        if not text:
            continue
        segments = _split_many_text(text, params["max_chars"])
        if len(segments) <= 1:
            result.append((start, end, text))
        else:
            result.extend(_assign_chunk_times(start, end, segments))
    return result


def resplit_srt(
    srt_path: str | Path,
    *,
    split_mode: str = DEFAULT_SRT_SPLIT,
    output: str | Path | None = None,
    log_callback=None,
    process_controller: ProcessController | None = None,
) -> tuple[Path, int, int]:
    """Re-apply split mode to an existing SRT (timing có sẵn, không Whisper lại)."""
    _check_controller(process_controller)
    path = Path(srt_path)
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy file SRT: {path}")

    cues = parse_srt_file(path)
    if not cues:
        raise CreateSrtError(f"Không đọc được cue nào trong {path.name}")

    before = len(cues)
    if log_callback:
        _log(log_callback, f"Ngắt lại {before} cue (giữ timing SRT)...", "info")
    refined = refine_srt_cues(cues, split_mode)
    if not refined:
        raise CreateSrtError("Không tạo được cue sau khi ngắt câu.")

    dest = Path(output) if output else path
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_srt_from_cues(dest, refined)
    return dest, before, len(refined)


def _collect_words_from_segments(
    segments,
    process_controller: ProcessController | None = None,
    max_seconds: float | None = None,
) -> list:
    words: list = []
    try:
        for seg in segments:
            _check_controller(process_controller)
            if max_seconds is not None and float(seg.start) >= max_seconds:
                break
            for word in getattr(seg, "words", None) or []:
                if not _word_text(word):
                    continue
                if max_seconds is not None and float(word.start) >= max_seconds:
                    return words
                words.append(word)
    except CreateSrtCancelled:
        _close_segments(segments)
        raise
    return words


def _extract_words_on_device(
    audio_path: Path,
    *,
    model: str,
    language: str,
    device: str,
    process_controller: ProcessController | None = None,
    max_seconds: float | None = None,
) -> list:
    _check_controller(process_controller)
    whisper = _load_whisper_model(model, device, process_controller)
    _check_controller(process_controller)
    segments, _info = _whisper_transcribe_cancellable(
        whisper,
        audio_path,
        process_controller,
        language=language or None,
        vad_filter=True,
        beam_size=5,
        word_timestamps=True,
    )
    return _collect_words_from_segments(segments, process_controller, max_seconds=max_seconds)


def extract_whisper_words(
    audio_path: str | Path,
    *,
    model: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANGUAGE,
    log_callback=None,
    process_controller: ProcessController | None = None,
    max_seconds: float | None = None,
) -> list:
    """Word timestamps from audio (for rhythm-accurate resplit, no full SRT rebuild)."""
    ensure_whisper()
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy audio: {audio_path}")
    if model not in WHISPER_MODELS:
        raise ValueError(f"Model không hợp lệ: {model}")
    if language == "auto":
        language = ""

    devices: list[str] = []
    if cuda_available():
        devices.append("cuda")
    if "cpu" not in devices:
        devices.append("cpu")

    last_err: Exception | None = None
    for i, device in enumerate(devices):
        try:
            _check_controller(process_controller)
            words = _extract_words_on_device(
                audio_path,
                model=model,
                language=language,
                device=device,
                process_controller=process_controller,
                max_seconds=max_seconds,
            )
            if not words:
                raise CreateSrtError("Không lấy được timing từng từ từ audio.")
            _log(log_callback, f"Đã căn {len(words)} từ theo audio.")
            return words
        except CreateSrtCancelled:
            raise
        except Exception as err:
            last_err = err
            if device == "cuda" and i + 1 < len(devices) and is_gpu_runtime_error(err):
                _log(log_callback, f"GPU lỗi — chuyển CPU để căn nhịp: {err}", "warn")
                continue
            raise
    raise CreateSrtError(f"Không căn được nhịp từ audio: {last_err}") from last_err


def whisper_model_cache_dir(model: str) -> Path | None:
    hub = _huggingface_hub_dir()
    for folder in _whisper_model_cache_folders(model):
        root = hub / folder
        snapshots = root / "snapshots"
        if snapshots.is_dir() and any(snapshots.iterdir()):
            return root
    return None


def whisper_model_cached(model: str) -> bool:
    return whisper_model_cache_dir(model) is not None


def whisper_model_status_line(model: str) -> str:
    size, desc = WHISPER_MODEL_INFO.get(model, ("", model))
    if whisper_model_cached(model):
        cache = "đã có trên máy"
    else:
        cache = "chưa tải — bấm «Cài đặt» hoặc tự tải khi Tạo SRT"
    return f"{desc} · {size} · {cache}"


def whisper_runtime_device_label() -> str:
    return "GPU (CUDA)" if cuda_available() else "CPU"


_groq_api_key_override: str | None = None


def set_groq_api_key(key: str | None) -> None:
    """Đặt API key Groq từ UI (ưu tiên hơn biến môi trường)."""
    global _groq_api_key_override
    text = (key or "").strip()
    _groq_api_key_override = text or None


def groq_api_key() -> str | None:
    if _groq_api_key_override:
        return _groq_api_key_override
    key = os.environ.get(GROQ_API_KEY_ENV, "").strip()
    return key or None


def groq_client_available() -> bool:
    try:
        from groq import Groq  # noqa: F401

        return True
    except ImportError:
        return False


def _faster_whisper_import_ok() -> bool:
    try:
        from faster_whisper import WhisperModel  # noqa: F401

        return True
    except ImportError:
        return False
    except Exception:
        return False


def srt_packages_status() -> dict:
    """Package groq + faster-whisper (không tải model)."""
    numpy_msg = whisper_numpy_message()
    groq_ok = groq_client_available()
    whisper_ok = _faster_whisper_import_ok() if numpy_msg is None else False
    missing: list[str] = []
    if numpy_msg:
        missing.append("numpy")
    if not groq_ok:
        missing.append("groq")
    if not whisper_ok:
        missing.append("faster-whisper")
    return {
        "groq_ok": groq_ok,
        "whisper_ok": whisper_ok,
        "numpy_ok": numpy_msg is None,
        "numpy_message": numpy_msg,
        "needs_install": bool(missing),
        "missing": missing,
    }


def install_srt_packages(*, log_callback=None) -> None:
    """Cài groq + faster-whisper + dotenv — gọi từ GUI."""
    _log(log_callback, "Đang cài gói nhận dạng (Groq + Whisper)...")
    cmd = [
        sys.executable, "-m", "pip", "install",
        "groq", WHISPER_NUMPY_SPEC, "faster-whisper", "edge-tts",
        "python-dotenv",
    ]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as err:
        raise CreateSrtError(f"Không cài được gói nhận dạng (mã {err.returncode}).") from err
    _log(log_callback, "Đã cài gói nhận dạng.", "success")


def is_groq_rate_limit(exc: BaseException) -> bool:
    if type(exc).__name__ == "RateLimitError":
        return True
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate limit" in msg
        or "rate_limit" in msg
        or "quota" in msg
        or "insufficient" in msg
        or "exceeded" in msg
    )


def _check_local_whisper(model: str | None = None) -> dict:
    """Trạng thái faster-whisper local (cho GUI / fallback)."""
    numpy_msg = whisper_numpy_message()
    if numpy_msg:
        return {"ok": False, "message": numpy_msg, "model_cached": None}
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError:
        return {
            "ok": False,
            "message": "Chưa cài Whisper — bấm «Cài đặt» trong tab Tạo SRT.",
            "model_cached": None,
        }
    except Exception as exc:
        numpy_msg = whisper_numpy_message()
        return {
            "ok": False,
            "message": numpy_msg or f"Lỗi faster-whisper: {exc}",
            "model_cached": None,
        }

    device = whisper_runtime_device_label()
    if not model:
        return {
            "ok": True,
            "message": f"faster-whisper sẵn sàng · {device}",
            "model_cached": None,
            "device": device,
        }

    size, _desc = WHISPER_MODEL_INFO.get(model, ("", model))
    if whisper_model_cached(model):
        return {
            "ok": True,
            "model_cached": True,
            "device": device,
            "message": f"faster-whisper sẵn sàng · {model} · {device} · đã có trên máy",
        }
    return {
        "ok": True,
        "model_cached": False,
        "device": device,
        "message": (
            f"Model {model} chưa tải ({size}) — chỉ cần khi Groq giới hạn · {device}"
        ),
    }


def check_whisper(model: str | None = None, *, language: str = "") -> dict:
    """Trạng thái nhận dạng SRT: Groq trước, faster-whisper fallback."""
    local = _check_local_whisper(model)
    pkg = srt_packages_status()
    key = groq_api_key()
    groq_lib = pkg["groq_ok"]
    needs_install = pkg["needs_install"]

    if key and groq_lib:
        load_cached_groq_models()
        groq_msg = f"Groq STT {groq_whisper_chain_label(language)} · free tier"
        if local["ok"]:
            message = f"{groq_msg} · Whisper {local['message']}"
        else:
            message = f"{groq_msg} · Whisper chưa sẵn sàng — {local['message']}"
        return {
            "ok": True,
            "groq": True,
            "local_ok": local["ok"],
            "needs_install": needs_install,
            "model_cached": local.get("model_cached"),
            "device": local.get("device"),
            "message": message,
        }

    if key and not groq_lib:
        message = "Có API key Groq — cần cài gói nhận dạng"
        if local["ok"]:
            message = f"{message} · tạm dùng Whisper local"
            return {
                "ok": True,
                "groq": False,
                "local_ok": True,
                "needs_install": True,
                "model_cached": local.get("model_cached"),
                "device": local.get("device"),
                "message": message,
            }
        return {
            "ok": False,
            "groq": False,
            "local_ok": False,
            "needs_install": True,
            "model_cached": local.get("model_cached"),
            "message": message,
        }

    if local["ok"]:
        local = dict(local)
        local["message"] = f"Thiếu API key Groq — {local['message']}"
        local["groq"] = False
        local["local_ok"] = True
        local["needs_install"] = needs_install
        return local

    return {
        "ok": False,
        "groq": False,
        "local_ok": False,
        "needs_install": True,
        "model_cached": local.get("model_cached"),
        "message": "Nhập API key Groq và bấm «Cài đặt» để cài gói nhận dạng.",
    }


def numpy_major_version() -> int | None:
    try:
        import numpy as np

        return int(str(np.__version__).split(".")[0])
    except Exception:
        return None


def whisper_numpy_message() -> str | None:
    major = numpy_major_version()
    if major is not None and major >= 2:
        return (
            f"NumPy {major}.x không tương thích — bấm «Cài đặt» trong tab Tạo SRT."
        )
    return None


def ensure_whisper():
    numpy_msg = whisper_numpy_message()
    if numpy_msg:
        raise CreateSrtError(numpy_msg)
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError as exc:
        raise CreateSrtError(
            "Chưa cài gói Whisper.\n"
            "Bấm «Cài đặt» trong tab Tạo SRT."
        ) from exc
    except Exception as exc:
        numpy_msg = whisper_numpy_message()
        if numpy_msg:
            raise CreateSrtError(numpy_msg) from exc
        raise CreateSrtError(f"Lỗi tải faster-whisper: {exc}") from exc


def cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def pick_device() -> str:
    return "cuda" if cuda_available() else "cpu"


def pick_compute_type(device: str) -> str:
    return "float16" if device == "cuda" else "int8"


def download_whisper_model(
    model: str,
    *,
    log_callback=None,
    progress_callback=None,
    process_controller: ProcessController | None = None,
) -> None:
    """Tải model Whisper về cache Hugging Face (không cần file audio)."""
    ensure_whisper()
    if model not in WHISPER_MODELS:
        raise ValueError(f"Model không hợp lệ: {model}")
    if whisper_model_cached(model):
        _log(log_callback, f"Model {model} đã có trên máy.", "info")
        return

    devices: list[str] = []
    if cuda_available():
        devices.append("cuda")
    if "cpu" not in devices:
        devices.append("cpu")

    size, _desc = WHISPER_MODEL_INFO.get(model, ("", model))
    last_err: Exception | None = None
    for i, device in enumerate(devices):
        try:
            _check_controller(process_controller)
            _log(log_callback, f"Đang tải model {model} ({size}) · {device}...")
            report = _progress_with_cancel(process_controller, progress_callback)
            report(15, f"Đang tải model {model}...")
            _load_whisper_model(model, device, process_controller)
            _log(log_callback, f"Đã tải model {model} — sẵn sàng dùng.", "success")
            report(100, "Đã tải model")
            return
        except CreateSrtCancelled:
            raise
        except Exception as err:
            last_err = err
            can_fallback = device == "cuda" and i + 1 < len(devices)
            if can_fallback and is_gpu_runtime_error(err):
                _log(log_callback, f"GPU lỗi khi tải model — thử CPU...", "warn")
                if progress_callback:
                    progress_callback(8, "Chuyển sang CPU...")
                continue
            break

    raise CreateSrtError(f"Không tải được model {model}: {last_err}") from last_err


def is_gpu_runtime_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _GPU_ERROR_MARKERS)


def is_cublas_dll_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "cublas" in msg and (
        "dll" in msg or "not found" in msg or "cannot be loaded" in msg
    )


def install_torch_cuda121(*, log_callback=None) -> bool:
    """Cài torch CUDA 12.1 — sửa lỗi cublas64_12.dll."""
    cmd = [
        sys.executable, "-m", "pip", "install",
        WHISPER_NUMPY_SPEC,
        *TORCH_CU121_PACKAGES,
        "--index-url", TORCH_CU121_INDEX,
    ]
    _log(
        log_callback,
        "Thiếu cublas DLL — đang cài torch 2.2.1 (CUDA 12.1)...",
        "warn",
    )
    try:
        subprocess.check_call(cmd)
        _log(log_callback, "Đã cài torch CUDA 12.1.", "success")
        return True
    except subprocess.CalledProcessError as err:
        _log(log_callback, f"Không cài được torch CUDA 12.1 (mã {err.returncode}).", "error")
        return False


def _check_controller(process_controller) -> None:
    if not process_controller:
        return
    try:
        process_controller.wait_if_paused()
    except RenderCancelled as err:
        raise CreateSrtCancelled("Đã hủy tạo SRT") from err


def _close_segments(segments) -> None:
    close = getattr(segments, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _progress_with_cancel(process_controller, progress_callback):
    def report(pct, message=""):
        _check_controller(process_controller)
        if progress_callback:
            progress_callback(pct, message)

    return report


def _run_cancellable(callable_fn, process_controller, poll: float = 0.2):
    """Chạy callable trong thread phụ, kiểm tra hủy/tạm dừng định kỳ."""
    err: list[BaseException] = []
    result: list = []

    def worker():
        try:
            result.append(callable_fn())
        except BaseException as exc:
            err.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            _check_controller(process_controller)
            thread.join(timeout=poll)
    except CreateSrtCancelled:
        thread.join(timeout=0.05)
        raise
    thread.join()
    if err:
        raise err[0]
    if not result:
        raise CreateSrtError("Tác vụ Whisper không trả kết quả.")
    return result[0]


def _whisper_transcribe_cancellable(
    whisper,
    audio_path: Path,
    process_controller: ProcessController | None,
    **kwargs,
):
    """transcribe() có thể block lúc VAD — poll hủy trong lúc chờ."""
    holder: dict = {}
    err: list[BaseException] = []

    def run():
        try:
            segments, info = whisper.transcribe(str(audio_path), **kwargs)
            holder["segments"] = segments
            holder["info"] = info
        except BaseException as exc:
            err.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            _check_controller(process_controller)
            thread.join(timeout=0.2)
    except CreateSrtCancelled:
        thread.join(timeout=0.05)
        raise
    thread.join()
    if err:
        raise err[0]
    if "segments" not in holder:
        raise CreateSrtError("Whisper không trả kết quả.")
    return holder["segments"], holder.get("info")


def _load_whisper_model(model: str, device: str, process_controller: ProcessController | None):
    def load():
        from faster_whisper import WhisperModel

        compute_type = pick_compute_type(device)
        return WhisperModel(model, device=device, compute_type=compute_type)

    if process_controller:
        return _run_cancellable(load, process_controller)
    return load()


def _poll_ffmpeg_proc(
    proc: subprocess.Popen,
    process_controller: ProcessController | None,
    *,
    poll: float = 0.2,
) -> None:
    """Chờ FFmpeg xong; poll để hỗ trợ hủy/tạm dừng (timeout= poll là sleep, không phải giới hạn tổng)."""
    while proc.poll() is None:
        if process_controller:
            process_controller.wait_if_paused()
            if process_controller.cancelled:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise CreateSrtCancelled("Đã hủy tạo SRT")
        try:
            proc.wait(timeout=poll)
        except subprocess.TimeoutExpired:
            continue


def _extract_audio_clip(
    audio_path: Path,
    seconds: float,
    *,
    log_callback=None,
    process_controller: ProcessController | None = None,
) -> Path:
    from videobuilder.core.ffmpeg_setup import ensure_ffmpeg_on_path, resolve_ffmpeg

    ensure_ffmpeg_on_path()
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise CreateSrtError("Cần FFmpeg để preview SRT (cắt đoạn audio đầu).")

    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    dest = Path(tmp)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(audio_path),
        "-t",
        str(seconds),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(dest),
    ]
    _log(log_callback, f"Preview: cắt {seconds:.0f}s đầu audio...")
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        if process_controller:
            process_controller.attach(proc)
        _poll_ffmpeg_proc(proc, process_controller)
        if proc.returncode != 0:
            err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", errors="replace")
            raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=err)
    except CreateSrtCancelled:
        dest.unlink(missing_ok=True)
        raise
    except subprocess.CalledProcessError as err:
        dest.unlink(missing_ok=True)
        raise CreateSrtError(f"Không cắt được audio preview (mã {err.returncode}).") from err
    finally:
        if process_controller:
            process_controller.detach()
    return dest


def _trim_cues(cues: list[tuple[float, float, str]], max_seconds: float) -> list[tuple[float, float, str]]:
    trimmed: list[tuple[float, float, str]] = []
    for start, end, text in cues:
        if start >= max_seconds:
            break
        trimmed.append((start, min(end, max_seconds), text))
    return trimmed


class _GroqWord:
    __slots__ = ("word", "start", "end")

    def __init__(self, word: str, start: float, end: float):
        self.word = word
        self.start = start
        self.end = end


def groq_model_for_language(language: str) -> str:
    """Model Groq STT đang dùng (ưu tiên cache theo ngôn ngữ)."""
    load_cached_groq_models()
    return groq_whisper_active_model(language)


def _groq_model_for_language(language: str) -> str:
    return groq_model_for_language(language)


def _groq_prompt_tail(text: str) -> str:
    return _groq_trim_prompt(text)


def _groq_prompt_token_count(text: str) -> int | None:
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text or ""))
    except Exception:
        return None


def _groq_trim_prompt(text: str, *, max_chars: int | None = None, max_tokens: int | None = None) -> str:
    """Groq Whisper: prompt ≤896 ký tự (và ~224 token). Luôn cắt an toàn trước khi gửi."""
    text = unicodedata.normalize("NFC", (text or "").strip())
    if not text:
        return ""
    char_limit = min(max_chars or GROQ_PROMPT_MAX_CHARS, GROQ_PROMPT_API_CHAR_MAX)
    token_limit = max_tokens or GROQ_PROMPT_MAX_TOKENS

    def _fit_chars(value: str) -> str:
        if len(value) <= char_limit:
            return value
        chunk = value[-char_limit:]
        space = chunk.find(" ")
        if 0 < space < min(96, char_limit // 4):
            chunk = chunk[space + 1 :]
        return chunk

    trimmed = _fit_chars(text)
    tokens = _groq_prompt_token_count(trimmed)
    if tokens is not None and tokens > token_limit:
        low, high = 0, len(trimmed)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate = _fit_chars(trimmed[-mid:] if mid else "")
            count = _groq_prompt_token_count(candidate)
            if count is not None and count <= token_limit:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        trimmed = best or trimmed[:char_limit]
    if len(trimmed) > GROQ_PROMPT_API_CHAR_MAX:
        trimmed = trimmed[-GROQ_PROMPT_API_CHAR_MAX:]
    return trimmed


def _groq_prompt_too_long_error(err: Exception) -> bool:
    msg = str(err).lower()
    return "prompt length" in msg or "prompt contains" in msg


def _groq_fold_text(text: str) -> str:
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "", text)


def _srt_gap_warn_sec(at_sec: float) -> float:
    if at_sec < _SRT_OPENING_DENSE_SEC:
        return _SRT_GAP_OPENING_SEC
    return _SRT_GAP_BODY_SEC


def _groq_text_is_hallucination(
    text: str,
    *,
    duration: float = 0.0,
    strict: bool = True,
) -> bool:
    """Phát hiện ảo giác Whisper: echo prompt, nhạc nền, chữ vô nghĩa."""
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if not compact:
        return True
    folded = _groq_fold_text(compact)
    for marker in GROQ_PROMPT_ECHO_MARKERS:
        if _groq_fold_text(marker) in folded:
            return True
    if "ndung" in folded and "nhi" in folded and len(folded) < 90:
        return True
    if not strict:
        return False

    words = compact.split()
    word_count = len(words)
    # Chỉ lọc đoạn rất thưa — không coi tiếng Anh dài là ảo giác.
    if duration >= 30.0 and word_count <= 2:
        return True
    if word_count >= 6 and duration >= 12.0:
        tiny = sum(1 for w in words if len(w) <= 2)
        if tiny / word_count > 0.72:
            return True
    return False


def _groq_prompt_from_cues(cues: list[tuple[float, float, str]]) -> str:
    """Chỉ dùng vài cue cuối chunk làm prompt — tránh vượt giới hạn Groq."""
    parts: list[str] = []
    for _, _, text in cues:
        if _groq_text_is_hallucination(text, strict=False):
            continue
        cleaned = text.strip()
        if cleaned:
            parts.append(cleaned)
    if not parts:
        return ""
    tail_parts = parts[-GROQ_PROMPT_MAX_CUES:]
    return _groq_trim_prompt(" ".join(tail_parts))


def _groq_prompt_for_chunk(prompt_tail: str = "") -> str | None:
    tail = _groq_trim_prompt(prompt_tail)
    return tail or None


def _groq_segment_text_duration(seg) -> tuple[str, float]:
    if isinstance(seg, dict):
        start = float(seg.get("start", 0))
        end = float(seg.get("end", start))
        text = (seg.get("text") or "").strip()
    else:
        start = float(getattr(seg, "start", 0))
        end = float(getattr(seg, "end", start))
        text = (getattr(seg, "text", "") or "").strip()
    return text, max(end - start, 0.0)


def _groq_skip_segment(seg, *, strict: bool = True) -> bool:
    """Bỏ segment Whisper hay ảo giác trên nhạc nền / không có lời."""
    text, duration = _groq_segment_text_duration(seg)
    if _groq_text_is_hallucination(text, duration=duration, strict=strict):
        return True
    if not strict:
        return False
    if isinstance(seg, dict):
        nsp = seg.get("no_speech_prob")
        cr = seg.get("compression_ratio")
    else:
        nsp = getattr(seg, "no_speech_prob", None)
        cr = getattr(seg, "compression_ratio", None)
    if nsp is not None and float(nsp) > 0.85:
        return True
    if duration >= 25.0 and nsp is not None and float(nsp) > 0.65:
        return True
    if (
        cr is not None
        and float(cr) > 2.4
        and nsp is not None
        and float(nsp) > 0.45
    ):
        return True
    return False


def _groq_filter_cues(
    cues: list[tuple[float, float, str]],
    *,
    strict: bool = True,
) -> list[tuple[float, float, str]]:
    out: list[tuple[float, float, str]] = []
    for start, end, text in cues:
        if _groq_text_is_hallucination(
            text, duration=max(end - start, 0.0), strict=strict,
        ):
            continue
        out.append((start, end, text))
    return out


def _groq_seg_words(seg, chunk_offset: float) -> list[_GroqWord]:
    seg_words = seg.get("words") if isinstance(seg, dict) else getattr(seg, "words", None)
    out: list[_GroqWord] = []
    for w in seg_words or []:
        if isinstance(w, dict):
            word = (w.get("word") or "").strip()
            start = float(w.get("start", 0))
            end = float(w.get("end", start))
        else:
            word = (getattr(w, "word", "") or "").strip()
            start = float(getattr(w, "start", 0))
            end = float(getattr(w, "end", start))
        if word:
            out.append(
                _GroqWord(
                    word,
                    chunk_offset + start,
                    chunk_offset + max(start + 0.05, end),
                )
            )
    return out


def _groq_collect_words(
    result,
    offset: float = 0.0,
    *,
    strict: bool = True,
) -> list[_GroqWord]:
    words: list[_GroqWord] = []
    raw_words = getattr(result, "words", None)
    if raw_words is None and isinstance(result, dict):
        raw_words = result.get("words")
    if raw_words:
        for w in raw_words:
            if isinstance(w, dict):
                word = (w.get("word") or "").strip()
                start = float(w.get("start", 0))
                end = float(w.get("end", start))
            else:
                word = (getattr(w, "word", "") or "").strip()
                start = float(getattr(w, "start", 0))
                end = float(getattr(w, "end", start))
            if word:
                words.append(_GroqWord(word, offset + start, offset + max(start + 0.05, end)))
        return words

    segments = getattr(result, "segments", None)
    if segments is None and isinstance(result, dict):
        segments = result.get("segments")
    for seg in segments or []:
        if _groq_skip_segment(seg, strict=strict):
            continue
        words.extend(_groq_seg_words(seg, offset))
    return words


def _find_cue_timeline_gaps(
    cues: list[tuple[float, float, str]],
    audio_duration: float,
) -> list[tuple[float, float]]:
    """Đoạn audio không có cue — thường do STT bỏ sót hoặc lọc ảo giác quá gắt."""
    if audio_duration <= 0:
        return []
    sorted_cues = sorted(cues, key=lambda c: c[0])
    gaps: list[tuple[float, float]] = []
    if not sorted_cues:
        if audio_duration > _srt_gap_warn_sec(0.0):
            return [(0.0, audio_duration)]
        return gaps

    if sorted_cues[0][0] > _srt_gap_warn_sec(0.0):
        gaps.append((0.0, sorted_cues[0][0]))

    for i in range(1, len(sorted_cues)):
        prev_end = sorted_cues[i - 1][1]
        next_start = sorted_cues[i][0]
        if next_start - prev_end > _srt_gap_warn_sec(prev_end):
            gaps.append((prev_end, next_start))

    tail = audio_duration - sorted_cues[-1][1]
    if tail > _srt_gap_warn_sec(sorted_cues[-1][1]):
        gaps.append((sorted_cues[-1][1], audio_duration))
    return gaps


def _merge_cues_timeline(
    *cue_lists: list[tuple[float, float, str]],
) -> list[tuple[float, float, str]]:
    merged: list[tuple[float, float, str]] = []
    for part in cue_lists:
        merged.extend(part)
    merged.sort(key=lambda c: (c[0], c[1]))
    return merged


def _warn_srt_timeline_gaps(
    cues: list[tuple[float, float, str]],
    audio_duration: float,
    *,
    log_callback=None,
) -> list[tuple[float, float]]:
    gaps = _find_cue_timeline_gaps(cues, audio_duration)
    for gap_start, gap_end in gaps:
        _log(
            log_callback,
            (
                f"SRT thiếu transcript {gap_start:.2f}s → {gap_end:.2f}s "
                f"(im lặng {gap_end - gap_start:.1f}s)."
            ),
            "warn",
        )
    if gaps:
        _log(
            log_callback,
            "SRT chưa phủ hết audio — kiểm tra STT hoặc chèn cue thủ công.",
            "warn",
        )
    return gaps


def _groq_split_long_segment(
    seg_words: list[_GroqWord],
    *,
    max_duration: float = GROQ_MAX_SEGMENT_DURATION,
) -> list[tuple[float, float, str]]:
    if not seg_words:
        return []
    params = SRT_SPLIT_PARAMS["many"]
    return _group_words_to_cues_sentence(
        seg_words,
        max_chars=params["max_chars"],
        max_duration=min(max_duration, params["max_duration"]),
        min_gap=params["min_gap"],
        pause_split=params["pause_split"],
        min_chars=params["min_chars"],
    )


def _groq_response_to_cues(
    result,
    offset: float = 0.0,
    *,
    strict: bool = True,
) -> list[tuple[float, float, str]]:
    segments = getattr(result, "segments", None)
    if segments is None and isinstance(result, dict):
        segments = result.get("segments")
    if not segments:
        text = getattr(result, "text", None)
        if text is None and isinstance(result, dict):
            text = result.get("text")
        text = (text or "").strip()
        if text and not _groq_text_is_hallucination(text, strict=strict):
            return [(offset, offset + 0.05, text)]
        return []

    cues: list[tuple[float, float, str]] = []
    for seg in segments:
        if _groq_skip_segment(seg, strict=strict):
            continue
        if isinstance(seg, dict):
            start = float(seg.get("start", 0))
            end = float(seg.get("end", start))
            text = (seg.get("text") or "").strip()
        else:
            start = float(getattr(seg, "start", 0))
            end = float(getattr(seg, "end", start))
            text = (getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        abs_start = offset + start
        abs_end = offset + max(start + 0.05, end)
        duration = abs_end - abs_start
        if duration > GROQ_MAX_SEGMENT_DURATION:
            seg_words = _groq_seg_words(seg, offset)
            if seg_words:
                split = _groq_split_long_segment(seg_words)
                if split:
                    cues.extend(split)
                    continue
        cues.append((abs_start, abs_end, text))
    return cues


def _run_ffmpeg_convert(
    cmd: list[str],
    *,
    process_controller: ProcessController | None = None,
) -> None:
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    if process_controller:
        process_controller.attach(proc)
    try:
        _poll_ffmpeg_proc(proc, process_controller)
        if proc.returncode != 0:
            err = (proc.stderr.read() if proc.stderr else b"").decode("utf-8", errors="replace")
            raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=err)
    finally:
        if process_controller:
            process_controller.detach()


def _ffmpeg_to_flac_mono16k(
    audio_path: Path,
    dest: Path,
    *,
    start: float = 0.0,
    duration: float | None = None,
    process_controller: ProcessController | None = None,
) -> None:
    from videobuilder.core.ffmpeg_setup import ensure_ffmpeg_on_path, resolve_ffmpeg

    ensure_ffmpeg_on_path()
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise CreateSrtError("Cần FFmpeg để chuẩn bị audio cho Groq.")

    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if start > 0:
        cmd.extend(["-ss", str(start)])
    cmd.extend(["-i", str(audio_path)])
    if duration is not None and duration > 0:
        cmd.extend(["-t", str(duration)])
    # Groq giới hạn upload ~25MB. Nếu dùng FLAC lossless, file có thể phình to dù audio gốc (mp3) nhỏ.
    # Dùng mp3 mono 16k bitrate thấp để giữ file nhỏ và ổn định upload.
    cmd.extend(
        [
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "64k",
            str(dest),
        ]
    )
    _run_ffmpeg_convert(cmd, process_controller=process_controller)


def _format_bytes(n: int) -> str:
    try:
        return f"{n / (1024 * 1024):.2f} MB ({n:,} bytes)"
    except Exception:
        return f"{n} bytes"


def _groq_prepare_chunks(
    audio_path: Path,
    *,
    max_seconds: float | None = None,
    log_callback=None,
    process_controller: ProcessController | None = None,
) -> tuple[list[tuple[Path, float]], list[Path]]:
    """Chuẩn bị chunk FLAC cho Groq (≤25MB, ~8 phút/request để giảm ảo giác)."""
    try:
        duration = get_media_duration(audio_path)
    except Exception:
        duration = 0.0
    if max_seconds is not None:
        if duration > 0:
            duration = min(duration, max_seconds)
        else:
            duration = max_seconds

    # Nếu không đọc được duration audio gốc (ffprobe fail), không được tự ý cắt 8 phút đầu.
    # Thay vào đó: cắt tuần tự theo chunk_step cho tới khi chunk gần như rỗng (đã hết audio).
    total = duration if duration > 0 else 0.0
    temps: list[Path] = []
    chunks: list[tuple[Path, float]] = []
    offset = 0.0
    chunk_step = float(GROQ_TRANSCRIBE_CHUNK_SECONDS)
    need_split = (total > 0 and total > chunk_step + 0.5) or (total <= 0)

    if need_split:
        _log(
            log_callback,
            (
                f"Chia ~{chunk_step / 60:.0f} phút/request cho Groq"
                + (f" ({total / 60:.1f} phút)..." if total > 0 else "...")
            ),
            "info",
        )

    def reached_limit() -> bool:
        return bool(max_seconds is not None and offset >= float(max_seconds) - 0.01)

    while True:
        if total > 0:
            if offset >= total - 0.01:
                break
            seg_dur = min(chunk_step, total - offset)
        else:
            if reached_limit():
                break
            seg_dur = chunk_step
        fd, tmp = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        chunk_path = Path(tmp)
        temps.append(chunk_path)
        _ffmpeg_to_flac_mono16k(
            audio_path,
            chunk_path,
            start=offset,
            duration=seg_dur,
            process_controller=process_controller,
        )
        # Nếu duration gốc không rõ, dùng duration chunk để biết đã hết audio chưa.
        if total <= 0:
            try:
                chunk_dur = get_media_duration(chunk_path)
            except Exception:
                chunk_dur = 0.0
            if chunk_dur <= 0.25:
                chunk_path.unlink(missing_ok=True)
                temps.remove(chunk_path)
                break
            if max_seconds is not None:
                chunk_dur = min(chunk_dur, max(0.0, float(max_seconds) - offset))
                if chunk_dur <= 0.25:
                    chunk_path.unlink(missing_ok=True)
                    temps.remove(chunk_path)
                    break
        size = chunk_path.stat().st_size
        if size > GROQ_MAX_UPLOAD_BYTES:
            chunk_path.unlink(missing_ok=True)
            temps.remove(chunk_path)
            raise CreateSrtError(
                (
                    f"Đoạn audio {offset / 60:.1f} phút vượt giới hạn upload của Groq "
                    f"({ _format_bytes(size) } > { _format_bytes(GROQ_MAX_UPLOAD_BYTES) }) — "
                    "thử rút ngắn preview."
                )
            )
        chunks.append((chunk_path, offset))
        # Nếu duration gốc không rõ, offset vẫn tăng theo chunk_step để tiến về cuối file.
        offset += seg_dur

    if not chunks:
        raise CreateSrtError("Không chuẩn bị được audio cho Groq.")
    return chunks, temps


def _groq_transcribe_chunk(
    client,
    chunk_path: Path,
    language: str,
    offset: float,
    process_controller: ProcessController | None,
    *,
    prompt_tail: str = "",
    log_callback=None,
    strict: bool = True,
):
    chain = groq_whisper_model_chain(language)

    kwargs_base = {
        "file": chunk_path,
        "response_format": "verbose_json",
        "temperature": 0.0,
        "timestamp_granularities": ["word", "segment"],
    }
    if language:
        kwargs_base["language"] = language
    prompt = _groq_prompt_for_chunk(prompt_tail)

    def call(model: str, extra_prompt: str | None = prompt):
        kwargs = {**kwargs_base, "model": model}
        if extra_prompt:
            kwargs["prompt"] = _groq_trim_prompt(extra_prompt)
        return client.audio.transcriptions.create(**kwargs)

    last_err: BaseException | None = None
    for i, model in enumerate(chain):
        try:
            if process_controller:
                try:
                    result = _run_cancellable(lambda m=model: call(m, prompt), process_controller)
                except Exception as err:
                    if prompt and _groq_prompt_too_long_error(err):
                        _log(
                            log_callback,
                            "Prompt chunk vượt giới hạn Groq — thử lại không dùng ngữ cảnh.",
                            "warn",
                        )
                        result = _run_cancellable(
                            lambda m=model: call(m, None), process_controller,
                        )
                    else:
                        raise
            else:
                try:
                    result = call(model, prompt)
                except Exception as err:
                    if prompt and _groq_prompt_too_long_error(err):
                        _log(
                            log_callback,
                            "Prompt chunk vượt giới hạn Groq — thử lại không dùng ngữ cảnh.",
                            "warn",
                        )
                        result = call(model, None)
                    else:
                        raise
        except Exception as err:
            if is_groq_rate_limit(err) and i < len(chain) - 1:
                last_err = err
                if log_callback:
                    _log(
                        log_callback,
                        f"Groq {model} rate limit → thử {chain[i + 1]}...",
                        "warn",
                    )
                continue
            raise

        if groq_whisper_active_model(language) != model and log_callback:
            _log(log_callback, f"Groq STT: dùng {model}", "info")
        set_active_whisper_model(model, language=language)
        detected = _groq_response_language(result)
        cues = _groq_filter_cues(
            _groq_response_to_cues(result, offset, strict=strict),
            strict=strict,
        )
        words = _groq_collect_words(result, offset, strict=strict)
        return cues, words, detected

    raise GroqRateLimitError(
        "Groq STT rate limit — đã thử hết model: "
        + ", ".join(groq_whisper_model_chain(language))
        + (f". Chi tiết: {last_err}" if last_err else "")
    ) from last_err


def _groq_recover_missing_cues(
    client,
    audio_path: Path,
    cues: list[tuple[float, float, str]],
    *,
    language: str,
    audio_duration: float,
    detected_language: str = "",
    process_controller: ProcessController | None,
    log_callback=None,
) -> tuple[list[tuple[float, float, str]], list[_GroqWord]]:
    """Transcribe lại đoạn STT thiếu — chia nhỏ gap, dùng ngôn ngữ Groq detect nếu auto."""
    gaps = _find_cue_timeline_gaps(cues, audio_duration)
    if not gaps:
        return cues, []

    recovered: list[tuple[float, float, str]] = []
    extra_words: list[_GroqWord] = []
    temps: list[Path] = []
    sorted_cues = sorted(cues, key=lambda c: c[0])
    recover_lang = language or detected_language or _guess_language_from_cues(sorted_cues)

    try:
        for gap_start, gap_end in gaps:
            sub_gaps = _split_time_range(
                gap_start, gap_end, max_span=GROQ_GAP_RECOVER_MAX_SEC,
            )
            for sub_start, sub_end in sub_gaps:
                gap_dur = sub_end - sub_start
                if gap_dur < 1.0:
                    continue
                _log(
                    log_callback,
                    (
                        f"STT thiếu {sub_start:.1f}s→{sub_end:.1f}s ({gap_dur:.1f}s) "
                        "— transcribe lại..."
                    ),
                    "warn",
                )
                fd, tmp = tempfile.mkstemp(suffix=".mp3")
                os.close(fd)
                chunk_path = Path(tmp)
                temps.append(chunk_path)
                _ffmpeg_to_flac_mono16k(
                    audio_path,
                    chunk_path,
                    start=sub_start,
                    duration=gap_dur,
                    process_controller=process_controller,
                )
                prior = [c for c in sorted_cues if c[1] <= sub_start + 0.08]
                prompt_tail = _groq_prompt_from_cues(
                    prior[-GROQ_PROMPT_MAX_CUES:] if prior else sorted_cues[:3]
                )
                gap_cues, gap_words, sub_detected = _groq_transcribe_chunk(
                    client,
                    chunk_path,
                    recover_lang or language,
                    sub_start,
                    process_controller,
                    prompt_tail=prompt_tail,
                    log_callback=log_callback,
                    strict=False,
                )
                if not recover_lang and sub_detected:
                    recover_lang = sub_detected
                gap_cues = _groq_filter_cues(gap_cues, strict=False)
                if gap_cues:
                    recovered.extend(gap_cues)
                    extra_words.extend(gap_words)
                    sorted_cues = _merge_cues_timeline(sorted_cues, gap_cues)
                    _log(log_callback, f"  → bổ sung {len(gap_cues)} cue", "info")
                else:
                    _log(
                        log_callback,
                        "  → vẫn không có lời (im lặng thật hoặc cần sửa SRT thủ công)",
                        "warn",
                    )
    finally:
        for path in temps:
            path.unlink(missing_ok=True)

    if not recovered:
        return cues, []
    return _merge_cues_timeline(cues, recovered), extra_words


def _transcribe_with_groq(
    audio_path: Path,
    *,
    language: str,
    split_mode: str = DEFAULT_SRT_SPLIT,
    progress_callback=None,
    log_callback=None,
    process_controller: ProcessController | None = None,
    max_seconds: float | None = None,
    refine: bool = True,
) -> list[tuple[float, float, str]] | tuple[list[tuple[float, float, str]], list[_GroqWord]]:
    key = groq_api_key()
    if not key:
        raise CreateSrtError(f"Thiếu {GROQ_API_KEY_ENV}")
    if not groq_client_available():
        raise CreateSrtError("Chưa cài Groq — bấm «Cài đặt» trong tab Tạo SRT.")

    from groq import Groq

    load_cached_groq_models()
    lang_label = language or "auto"
    split_key = normalize_srt_split(split_mode)
    split_label = SRT_SPLIT_KEY_TO_LABEL[split_key]
    groq_model = groq_whisper_active_model(language)
    cache_note = " (cache)" if groq_whisper_using_cached_model(language) else ""
    _log(
        log_callback,
        f"Groq {groq_model}{cache_note} · ngôn ngữ {lang_label} · ngắt câu {split_label}",
    )
    # Không spam warning khi auto. Chỉ cảnh báo nếu bên dưới phát hiện SRT bị "đứt".

    _check_controller(process_controller)
    report = _progress_with_cancel(process_controller, progress_callback)
    report(5, "Chuẩn bị audio cho Groq...")

    client = Groq(api_key=key)
    chunks, temps = _groq_prepare_chunks(
        audio_path,
        max_seconds=max_seconds,
        log_callback=log_callback,
        process_controller=process_controller,
    )
    all_cues: list[tuple[float, float, str]] = []
    all_words: list[_GroqWord] = []
    prompt_tail = ""
    detected_language = ""
    try:
        total = len(chunks)
        for i, (chunk_path, offset) in enumerate(chunks):
            _check_controller(process_controller)
            pct = 10.0 + ((i + 1) / total) * 80.0
            report(pct, f"Groq nhận dạng ({i + 1}/{total})...")
            chunk_lang = language or detected_language
            try:
                cues, words, chunk_detected = _groq_transcribe_chunk(
                    client,
                    chunk_path,
                    chunk_lang,
                    offset,
                    process_controller,
                    prompt_tail=prompt_tail,
                    log_callback=log_callback,
                )
            except GroqRateLimitError:
                raise
            except Exception as err:
                if is_groq_rate_limit(err):
                    raise GroqRateLimitError(str(err)) from err
                raise
            if chunk_detected:
                if not detected_language:
                    detected_language = chunk_detected
                    if not language and log_callback:
                        _log(
                            log_callback,
                            f"Groq auto detect: «{detected_language}»",
                            "info",
                        )
            all_cues.extend(cues)
            all_words.extend(words)
            chunk_prompt = _groq_prompt_from_cues(cues)
            if chunk_prompt:
                prompt_tail = chunk_prompt
    finally:
        for path in temps:
            path.unlink(missing_ok=True)

    if not all_cues:
        raise CreateSrtError("Groq không nhận dạng được lời thoại trong audio.")
    filtered = _groq_filter_cues(all_cues)
    if not filtered:
        raise CreateSrtError("Groq không nhận dạng được lời thoại trong audio.")

    try:
        audio_duration = get_media_duration(audio_path)
    except Exception:
        audio_duration = 0.0
    if max_seconds is not None:
        if audio_duration > 0:
            audio_duration = min(audio_duration, max_seconds)
        else:
            audio_duration = max_seconds
    if audio_duration > 0:
        filtered, gap_words = _groq_recover_missing_cues(
            client,
            audio_path,
            filtered,
            language=language,
            audio_duration=audio_duration,
            detected_language=detected_language,
            process_controller=process_controller,
            log_callback=log_callback,
        )
        all_words.extend(gap_words)
        _warn_srt_timeline_gaps(filtered, audio_duration, log_callback=log_callback)

    if not refine:
        report(95, f"Groq xong — {len(filtered)} segment")
        return filtered, all_words
    use_words = split_key in ("short", "many") and all_words
    all_cues = refine_srt_cues(
        filtered,
        split_key,
        words=all_words if use_words else None,
    )
    if not all_cues:
        raise CreateSrtError("Groq không nhận dạng được lời thoại trong audio.")
    report(95, f"Groq xong — {len(all_cues)} cue")
    return all_cues


def transcribe_groq_strict(
    audio_path: Path,
    *,
    language: str = DEFAULT_LANGUAGE,
    progress_callback=None,
    log_callback=None,
    process_controller: ProcessController | None = None,
    max_seconds: float | None = None,
) -> tuple[list[tuple[float, float, str]], list[_GroqWord]]:
    """Chỉ Groq STT — trả segment thô (đã lọc ảo giác) + word timestamps."""
    if not groq_api_key():
        raise CreateSrtError(f"Thiếu {GROQ_API_KEY_ENV}")
    if not groq_client_available():
        raise CreateSrtError("Chưa cài Groq — bấm «Cài đặt» trong tab Tạo SRT.")
    if language == "auto":
        language = ""
    result = _transcribe_with_groq(
        Path(audio_path),
        language=language,
        split_mode="normal",
        progress_callback=progress_callback,
        log_callback=log_callback,
        process_controller=process_controller,
        max_seconds=max_seconds,
        refine=False,
    )
    if not isinstance(result, tuple):
        raise CreateSrtError("Groq không trả segment.")
    return result


def default_srt_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(".srt")


def normalize_output_path(audio_path: Path, output: str | Path | None) -> Path:
    audio_path = Path(audio_path)
    if output:
        out = Path(output)
        if out.suffix.lower() != ".srt":
            out = out.with_suffix(".srt")
        return out
    return default_srt_path(audio_path)


def _collect_segments(
    segments,
    duration: float,
    progress_callback=None,
    process_controller=None,
    max_seconds: float | None = None,
    *,
    collect_words: bool = False,
) -> tuple[list[tuple[float, float, str]], list]:
    cues: list[tuple[float, float, str]] = []
    words: list = []
    try:
        for seg in segments:
            _check_controller(process_controller)
            text = (seg.text or "").strip()
            if not text:
                continue
            start = max(0.0, float(seg.start))
            if max_seconds is not None and start >= max_seconds:
                break
            end = max(start + 0.05, float(seg.end))
            if max_seconds is not None:
                end = min(end, max_seconds)
            cues.append((start, end, text))
            if collect_words and getattr(seg, "words", None):
                for word in seg.words:
                    if (word.word or "").strip():
                        words.append(word)
            if progress_callback and duration > 0:
                pct = min(95.0, 12.0 + (end / duration) * 83.0)
                progress_callback(pct, f"Đang nghe... {end:.1f}s / {duration:.1f}s")
            elif progress_callback:
                progress_callback(min(95.0, 12.0 + len(cues) * 0.5), f"Đã có {len(cues)} câu...")
            if max_seconds is not None and end >= max_seconds:
                break
    except CreateSrtCancelled:
        _close_segments(segments)
        raise

    return cues, words


def _transcribe_on_device(
    audio_path: Path,
    *,
    model: str,
    language: str,
    device: str,
    split_mode: str = DEFAULT_SRT_SPLIT,
    progress_callback=None,
    log_callback=None,
    process_controller: ProcessController | None = None,
    max_seconds: float | None = None,
) -> list[tuple[float, float, str]]:
    lang_label = language or "auto"
    split_key = normalize_srt_split(split_mode)
    split_label = SRT_SPLIT_KEY_TO_LABEL[split_key]
    _log(log_callback, f"Whisper {model} · {device} · ngôn ngữ {lang_label} · ngắt câu {split_label}")

    _check_controller(process_controller)
    report = _progress_with_cancel(process_controller, progress_callback)
    report(5, f"Tải model Whisper ({device})...")

    whisper = _load_whisper_model(model, device, process_controller)

    _check_controller(process_controller)
    try:
        duration = get_media_duration(audio_path)
    except Exception:
        duration = 0.0
    if max_seconds is not None and duration > 0:
        duration = min(duration, max_seconds)
    elif max_seconds is not None:
        duration = max_seconds

    report(12, "Đang nhận dạng giọng nói...")

    use_words = split_key == "short"
    segments, _info = _whisper_transcribe_cancellable(
        whisper,
        audio_path,
        process_controller,
        language=language or None,
        vad_filter=True,
        beam_size=5,
        word_timestamps=use_words,
    )

    cues, words = _collect_segments(
        segments,
        duration,
        report,
        process_controller,
        max_seconds=max_seconds,
        collect_words=use_words,
    )
    if not cues:
        raise CreateSrtError("Không nhận dạng được lời thoại trong audio.")
    cues = refine_srt_cues(cues, split_key, words=words if use_words else None)
    if not cues:
        raise CreateSrtError("Không nhận dạng được lời thoại trong audio.")
    return cues


def transcribe_audio(
    audio_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANGUAGE,
    split_mode: str = DEFAULT_SRT_SPLIT,
    progress_callback=None,
    log_callback=None,
    process_controller: ProcessController | None = None,
    max_seconds: float | None = None,
) -> list[tuple[float, float, str]]:
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy audio: {audio_path}")

    if language == "auto":
        language = ""

    groq_ready = bool(groq_api_key()) and groq_client_available()
    if groq_ready:
        try:
            return _transcribe_with_groq(
                audio_path,
                language=language,
                split_mode=split_mode,
                progress_callback=progress_callback,
                log_callback=log_callback,
                process_controller=process_controller,
                max_seconds=max_seconds,
            )
        except CreateSrtCancelled:
            raise
        except GroqRateLimitError as err:
            _log(
                log_callback,
                f"Groq rate limit / hết quota — chuyển faster-whisper local. ({err})",
                "warn",
            )
        except Exception as err:
            if is_groq_rate_limit(err):
                _log(
                    log_callback,
                    f"Groq rate limit / hết quota — chuyển faster-whisper local. ({err})",
                    "warn",
                )
            else:
                _log(
                    log_callback,
                    f"Groq lỗi ({err}) — thử faster-whisper local.",
                    "warn",
                )
    elif groq_api_key() and not groq_client_available():
        _log(
            log_callback,
            "Có API key nhưng chưa cài Groq — bấm «Cài đặt» hoặc đợi cài tự động.",
            "warn",
        )
    else:
        _log(log_callback, f"Không có {GROQ_API_KEY_ENV} — dùng faster-whisper local.", "info")

    return _transcribe_with_whisper_local(
        audio_path,
        model=model,
        language=language,
        split_mode=split_mode,
        progress_callback=progress_callback,
        log_callback=log_callback,
        process_controller=process_controller,
        max_seconds=max_seconds,
    )


def _transcribe_with_whisper_local(
    audio_path: Path,
    *,
    model: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANGUAGE,
    split_mode: str = DEFAULT_SRT_SPLIT,
    progress_callback=None,
    log_callback=None,
    process_controller: ProcessController | None = None,
    max_seconds: float | None = None,
) -> list[tuple[float, float, str]]:
    ensure_whisper()

    if model not in WHISPER_MODELS:
        raise ValueError(f"Model không hợp lệ: {model}")

    devices: list[str] = []
    if cuda_available():
        devices.append("cuda")
    if "cpu" not in devices:
        devices.append("cpu")

    last_err: Exception | None = None
    for i, device in enumerate(devices):
        retried_cuda = False
        while True:
            try:
                _check_controller(process_controller)
                return _transcribe_on_device(
                    audio_path,
                    model=model,
                    language=language,
                    device=device,
                    split_mode=split_mode,
                    progress_callback=progress_callback,
                    log_callback=log_callback,
                    process_controller=process_controller,
                    max_seconds=max_seconds,
                )
            except CreateSrtCancelled:
                raise
            except Exception as err:
                last_err = err
                if (
                    device == "cuda"
                    and not retried_cuda
                    and is_cublas_dll_error(err)
                    and install_torch_cuda121(log_callback=log_callback)
                ):
                    retried_cuda = True
                    if progress_callback:
                        progress_callback(8, "Thử lại GPU...")
                    continue
                break

        can_fallback = device == "cuda" and i + 1 < len(devices)
        if can_fallback and last_err and is_gpu_runtime_error(last_err):
            _log(
                log_callback,
                f"GPU lỗi ({last_err}) — tự chuyển sang CPU (chậm hơn nhưng ổn định).",
                "warn",
            )
            if progress_callback:
                progress_callback(8, "Chuyển sang CPU...")
            continue
        if last_err:
            raise last_err

    raise CreateSrtError(f"Không chạy được Whisper: {last_err}") from last_err


def create_srt(
    audio: str | Path,
    output: str | Path | None = None,
    *,
    model: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANGUAGE,
    split_mode: str = DEFAULT_SRT_SPLIT,
    progress_callback=None,
    log_callback=None,
    process_controller: ProcessController | None = None,
    preview_seconds: float | None = None,
) -> Path:
    audio_path = Path(audio)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy audio: {audio_path}")

    output_path = normalize_output_path(audio_path, output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _log(log_callback, f"Audio: {audio_path.name}")
    _log(log_callback, f"Xuất: {output_path}")

    max_seconds = None
    if preview_seconds and preview_seconds > 0:
        max_seconds = float(preview_seconds)
        _log(log_callback, f"Preview SRT: {max_seconds:.0f}s đầu audio")

    clip_path: Path | None = None
    audio_for_whisper = audio_path
    try:
        if max_seconds is not None:
            _check_controller(process_controller)
            clip_path = _extract_audio_clip(
                audio_path,
                max_seconds,
                log_callback=log_callback,
                process_controller=process_controller,
            )
            audio_for_whisper = clip_path

        cues = transcribe_audio(
            audio_for_whisper,
            model=model,
            language=language,
            split_mode=split_mode,
            progress_callback=progress_callback,
            log_callback=log_callback,
            process_controller=process_controller,
            max_seconds=max_seconds,
        )
        if max_seconds is not None:
            cues = _trim_cues(cues, max_seconds)
    except CreateSrtCancelled:
        if output_path.is_file():
            output_path.unlink(missing_ok=True)
        raise
    finally:
        if clip_path and clip_path.is_file():
            clip_path.unlink(missing_ok=True)

    try:
        audio_duration = get_media_duration(audio_path)
        if max_seconds is not None and audio_duration > 0:
            audio_duration = min(audio_duration, max_seconds)
    except Exception:
        audio_duration = max_seconds or 0.0
    if audio_duration > 0:
        _warn_srt_timeline_gaps(cues, audio_duration, log_callback=log_callback)

    write_srt_from_cues(output_path, cues)
    _log(log_callback, f"Xong: {len(cues)} cue → {output_path}", "success")
    if progress_callback:
        progress_callback(100, "Hoàn thành!")
    return output_path


def _log(callback, message, level="info"):
    text = str(message).strip()
    if not text:
        return
    print(text)
    if callback:
        callback(text, level)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tạo file SRT từ audio (Groq → faster-whisper)")
    parser.add_argument("--audio", "-a", required=True, help="File audio (.mp3, .wav, ...)")
    parser.add_argument("--output", "-o", default="", help="File SRT xuất (mặc định: cùng tên audio)")
    parser.add_argument("--model", "-m", default=DEFAULT_MODEL, choices=WHISPER_MODELS)
    parser.add_argument("--language", "-l", default=DEFAULT_LANGUAGE, help="Mã ngôn ngữ (auto, vi, en, ...)")
    parser.add_argument(
        "--split",
        default=DEFAULT_SRT_SPLIT,
        choices=SRT_SPLIT_MODES,
        help="Cách ngắt câu: very_few, few, normal, many, short",
    )
    args = parser.parse_args(argv)

    try:
        create_srt(
            args.audio,
            args.output or None,
            model=args.model,
            language=args.language,
            split_mode=args.split,
        )
    except (CreateSrtError, FileNotFoundError, ValueError) as err:
        print(f"Lỗi: {err}", file=sys.stderr)
        return 1
    except Exception as err:
        print(f"Lỗi: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
