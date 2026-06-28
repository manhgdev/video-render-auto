#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Chuẩn bị audio trước khi upload Groq (nén nếu quá lớn)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from videobuilder.core.create_srt import CreateSrtError

GROQ_UPLOAD_MAX_BYTES = 25 * 1024 * 1024


def _ffmpeg() -> str:
    from videobuilder.core.ffmpeg_setup import ensure_ffmpeg_on_path, resolve_ffmpeg

    ensure_ffmpeg_on_path()
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise CreateSrtError("Cần FFmpeg để nén audio cho Groq.")
    return ffmpeg


def compress_audio_mp3_64k(
    audio_path: Path,
    *,
    log_callback=None,
) -> Path:
    """Nén audio → mono 16kHz mp3 64kbps (file tạm)."""
    ffmpeg = _ffmpeg()
    fd, tmp = tempfile.mkstemp(suffix=".mp3")
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
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(dest),
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        subprocess.run(cmd, check=True, creationflags=creationflags)
    except subprocess.CalledProcessError as err:
        dest.unlink(missing_ok=True)
        raise CreateSrtError(f"Không nén được audio (mã {err.returncode}).") from err
    if log_callback:
        before = audio_path.stat().st_size / (1024 * 1024)
        after = dest.stat().st_size / (1024 * 1024)
        log_callback(
            f"Đã nén audio {before:.1f}MB → {after:.1f}MB (mp3 64kbps).",
            "info",
        )
    return dest


def prepare_audio_for_groq(
    audio_path: Path,
    *,
    max_bytes: int = GROQ_UPLOAD_MAX_BYTES,
    log_callback=None,
) -> tuple[Path, Path | None]:
    """
  Trả (path_dùng_cho_stt, path_tạm_cần_xóa).
  Nếu không nén, path_tạm là None.
    """
    audio_path = Path(audio_path)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy audio: {audio_path}")
    size = audio_path.stat().st_size
    if size <= max_bytes:
        return audio_path, None
    if log_callback:
        log_callback(
            f"Audio {size / (1024 * 1024):.1f}MB > {max_bytes // (1024 * 1024)}MB — nén cho Groq...",
            "info",
        )
    compressed = compress_audio_mp3_64k(audio_path, log_callback=log_callback)
    if compressed.stat().st_size > max_bytes:
        compressed.unlink(missing_ok=True)
        raise CreateSrtError(
            "Audio vẫn vượt 25MB sau khi nén — rút ngắn file hoặc cắt preview."
        )
    return compressed, compressed
