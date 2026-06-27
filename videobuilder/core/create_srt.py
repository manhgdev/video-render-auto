#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audio → SRT (faster-whisper)."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from videobuilder.core.pipeline import ProcessController, RenderCancelled, get_media_duration, parse_srt_file, write_srt_from_cues

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


def check_whisper(model: str | None = None) -> dict:
    """Trạng thái cài đặt faster-whisper (cho GUI)."""
    numpy_msg = whisper_numpy_message()
    if numpy_msg:
        return {"ok": False, "message": numpy_msg, "model_cached": None}
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError:
        return {
            "ok": False,
            "message": "Chưa cài faster-whisper — bấm «Cài Whisper» để bắt đầu.",
            "model_cached": None,
        }
    except Exception as exc:
        numpy_msg = whisper_numpy_message()
        return {
            "ok": False,
            "message": numpy_msg or f"Lỗi Whisper: {exc}",
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
            f"Model {model} chưa tải ({size}) — bấm «Cài đặt» để tải · {device}"
        ),
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
            f"NumPy {major}.x không tương thích torch/Whisper — "
            f'chạy: pip install "{WHISPER_NUMPY_SPEC}"'
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
            "Chưa cài faster-whisper.\n"
            "Chạy: pip install faster-whisper"
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
            proc.wait(timeout=0.2)
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
    parser = argparse.ArgumentParser(description="Tạo file SRT từ audio (Whisper)")
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
