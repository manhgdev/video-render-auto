#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pipeline: audio → Groq STT → SRT + Groq LLM visual beats → image_prompts.txt."""

from __future__ import annotations

from pathlib import Path

from videobuilder.core.audio_prepare import prepare_audio_for_groq
from videobuilder.core.create_srt import (
    CreateSrtCancelled,
    CreateSrtError,
    DEFAULT_LANGUAGE,
    DEFAULT_SRT_SPLIT,
    _trim_cues,
    create_srt,
    groq_api_key,
    groq_client_available,
    refine_srt_cues,
    set_groq_api_key,
    transcribe_groq_strict,
    write_srt_from_cues,
)
from videobuilder.core.env_config import GROQ_API_KEY_ENV, load_env
from videobuilder.core.generate_prompts import (
    GeneratePromptsError,
    default_prompts_path,
    generate_image_prompts_from_segments,
    segments_from_cues,
)
from videobuilder.core.pipeline import ProcessController


class AudioPipelineError(Exception):
    pass


def _log(callback, message: str, level: str = "info") -> None:
    if callback:
        callback(message, level)


def apply_env_api_keys() -> None:
    load_env()
    if groq_api_key() is None:
        from videobuilder.core.env_config import env_api_key

        g = env_api_key(GROQ_API_KEY_ENV)
        if g:
            set_groq_api_key(g)


def run_audio_pipeline(
    audio: str | Path,
    *,
    srt_output: str | Path | None = None,
    prompts_output: str | Path | None = None,
    language: str = DEFAULT_LANGUAGE,
    split_mode: str = DEFAULT_SRT_SPLIT,
    generate_prompts: bool = True,
    groq_only: bool = True,
    progress_callback=None,
    log_callback=None,
    process_controller: ProcessController | None = None,
    preview_seconds: float | None = None,
) -> tuple[Path, Path | None]:
    """
    Groq STT → .srt; tùy chọn Groq LLM → image_prompts.txt.
    Trả (srt_path, prompts_path hoặc None).
    """
    apply_env_api_keys()
    audio_path = Path(audio)
    if not audio_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy audio: {audio_path}")

    if groq_only and (not groq_api_key() or not groq_client_available()):
        raise AudioPipelineError(
            f"Pipeline cần Groq ({GROQ_API_KEY_ENV}) — nhập key hoặc thêm vào .env."
        )
    if generate_prompts and (not groq_api_key() or not groq_client_available()):
        raise AudioPipelineError(
            f"Cần Groq ({GROQ_API_KEY_ENV}) cho STT + prompt ảnh — thêm vào .env hoặc ô API key."
        )

    from videobuilder.core.create_srt import default_srt_path, normalize_output_path

    srt_path = normalize_output_path(audio_path, srt_output)
    prompts_path = (
        Path(prompts_output)
        if prompts_output
        else default_prompts_path(audio_path)
    )
    if prompts_path.suffix.lower() != ".txt":
        prompts_path = prompts_path.with_suffix(".txt")

    srt_path.parent.mkdir(parents=True, exist_ok=True)

    def report(pct: float, msg: str) -> None:
        if progress_callback:
            progress_callback(pct, msg)

    _log(log_callback, f"Audio: {audio_path.name}")
    _log(log_callback, f"SRT: {srt_path.name}")
    if generate_prompts:
        _log(log_callback, f"Prompt ảnh: {prompts_path.name}")

    max_seconds = float(preview_seconds) if preview_seconds and preview_seconds > 0 else None
    if max_seconds:
        _log(log_callback, f"Preview: {max_seconds:.0f}s đầu audio")

    report(2, "Chuẩn bị audio...")
    prepared, temp_audio = prepare_audio_for_groq(audio_path, log_callback=log_callback)
    clip_path: Path | None = None
    try:
        from videobuilder.core.create_srt import _extract_audio_clip, _check_controller

        audio_for_stt = prepared
        if max_seconds is not None:
            _check_controller(process_controller)
            clip_path = _extract_audio_clip(
                prepared,
                max_seconds,
                log_callback=log_callback,
                process_controller=process_controller,
            )
            audio_for_stt = clip_path

        report(8, "Groq nhận dạng giọng nói...")
        raw_cues, words = transcribe_groq_strict(
            audio_for_stt,
            language=language,
            progress_callback=lambda p, m: report(8 + p * 0.52, m),
            log_callback=log_callback,
            process_controller=process_controller,
            max_seconds=max_seconds,
        )
        if max_seconds is not None:
            raw_cues = _trim_cues(raw_cues, max_seconds)

        srt_cues = refine_srt_cues(
            raw_cues,
            split_mode,
            words=words if split_mode in ("short", "many") else None,
        )
        if not srt_cues:
            raise CreateSrtError("Không tạo được cue SRT.")

        write_srt_from_cues(srt_path, srt_cues)
        _log(log_callback, f"SRT: {len(srt_cues)} cue → {srt_path.name}", "success")
        report(62, f"SRT xong — {len(srt_cues)} cue")

        out_prompts: Path | None = None
        if generate_prompts:
            segments = segments_from_cues(raw_cues)
            report(65, "Groq LLM phân tích visual beat...")
            out_prompts = generate_image_prompts_from_segments(
                segments,
                prompts_path,
                log_callback=log_callback,
            )
            report(95, f"Prompt ảnh: {out_prompts.name}")

        report(100, "Hoàn thành!")
        return srt_path, out_prompts
    except GeneratePromptsError as err:
        raise AudioPipelineError(f"Tạo timeline thất bại (SRT đã xong): {err}") from err
    except CreateSrtCancelled:
        if srt_path.is_file():
            srt_path.unlink(missing_ok=True)
        if prompts_path.is_file():
            prompts_path.unlink(missing_ok=True)
        raise
    finally:
        if clip_path and clip_path.is_file():
            clip_path.unlink(missing_ok=True)
        if temp_audio and temp_audio.is_file():
            temp_audio.unlink(missing_ok=True)


def run_prompts_from_srt(
    srt_path: str | Path,
    prompts_output: str | Path,
    *,
    log_callback=None,
    progress_callback=None,
) -> Path:
    """Groq LLM → image_prompts.txt từ SRT có sẵn (không chạy STT lại)."""
    apply_env_api_keys()
    if not groq_api_key() or not groq_client_available():
        raise AudioPipelineError(
            f"Cần Groq ({GROQ_API_KEY_ENV}) cho prompt ảnh — thêm vào .env hoặc ô API key."
        )

    from videobuilder.core.pipeline import parse_srt_file

    srt = Path(srt_path)
    if not srt.is_file():
        raise FileNotFoundError(f"Không tìm thấy SRT: {srt}")

    prompts_path = Path(prompts_output)
    if prompts_path.suffix.lower() != ".txt":
        prompts_path = prompts_path.with_suffix(".txt")
    prompts_path.parent.mkdir(parents=True, exist_ok=True)

    def report(pct: float, msg: str) -> None:
        if progress_callback:
            progress_callback(pct, msg)

    cues = parse_srt_file(srt)
    if not cues:
        raise AudioPipelineError("SRT rỗng — không tạo được file tạo ảnh.")

    _log(log_callback, f"SRT: {srt.name} ({len(cues)} cue)")
    _log(log_callback, f"File tạo ảnh: {prompts_path.name}")

    segments = segments_from_cues(cues)
    report(15, "Groq LLM phân tích visual beat...")
    try:
        out = generate_image_prompts_from_segments(
            segments,
            prompts_path,
            log_callback=log_callback,
        )
    except GeneratePromptsError as err:
        raise AudioPipelineError(f"Tạo timeline thất bại: {err}") from err
    report(100, "Hoàn thành!")
    return out


def run_srt_only(
    audio: str | Path,
    output: str | Path | None = None,
    **kwargs,
) -> Path:
    """Giữ tương thích — chỉ tạo SRT (dùng create_srt hiện có)."""
    return create_srt(audio, output, **kwargs)
