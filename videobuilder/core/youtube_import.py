#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tải audio/phụ đề YouTube bằng yt-dlp → SRT + prompt ảnh."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from videobuilder.core.audio_pipeline import run_audio_pipeline, run_prompts_from_srt
from videobuilder.core.automation import (
    AutoProductionResult,
    AutomationError,
    _auto_report,
    slugify_topic,
)
from videobuilder.core.create_srt import DEFAULT_LANGUAGE, DEFAULT_SRT_SPLIT
from videobuilder.core.pipeline import ProcessController, parse_srt_file

_YOUTUBE_URL_RE = re.compile(
    r"^(?:https?://)?(?:"
    r"(?:www\.)?youtube\.com/(?:watch\?[^#\s]*v=|shorts/|embed/|live/)"
    r"|youtu\.be/"
    r")[\w-]{6,}",
    re.I,
)

_SUBTITLE_LANG_ORDER = ("vi", "vie", "vi-vn", "en", "eng", "en-us", "en-gb")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def normalize_youtube_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        raise AutomationError("Thiếu URL YouTube.")
    if not text.startswith(("http://", "https://")):
        text = "https://" + text.lstrip("/")
    if not _YOUTUBE_URL_RE.match(text):
        raise AutomationError("URL YouTube không hợp lệ.")
    return text


def check_yt_dlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401

        return True
    except ImportError:
        return False


def ensure_yt_dlp_available(*, auto_install: bool = True, log_callback=None) -> None:
    if check_yt_dlp_available():
        return
    if not auto_install:
        raise AutomationError("Chưa cài yt-dlp. Chạy: pip install yt-dlp")
    if log_callback:
        log_callback("Chưa có yt-dlp → tự cài...", "info")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "yt-dlp"])
    except subprocess.CalledProcessError as err:
        raise AutomationError("Không cài được yt-dlp. Chạy thủ công: pip install yt-dlp") from err
    if not check_yt_dlp_available():
        raise AutomationError("Đã cài yt-dlp nhưng Python hiện tại chưa import được.")


@dataclass
class YouTubeDownloadResult:
    video_id: str
    title: str
    folder: Path
    audio_path: Path
    subtitle_path: Path | None = None
    description: str = ""


def _subtitle_rank(path: Path) -> tuple[int, str]:
    name = path.name.lower()
    for idx, lang in enumerate(_SUBTITLE_LANG_ORDER):
        token = f".{lang}."
        if token in name or name.endswith(f".{lang}.srt"):
            return idx, name
    return len(_SUBTITLE_LANG_ORDER), name


def _pick_subtitle_file(folder: Path) -> Path | None:
    candidates = [p for p in folder.glob("*.srt") if p.is_file() and p.stat().st_size > 0]
    if not candidates:
        return None
    return sorted(candidates, key=_subtitle_rank)[0]


def _pick_audio_file(folder: Path, video_id: str) -> Path | None:
    preferred = folder / f"{video_id}.mp3"
    if preferred.is_file():
        return preferred
    for ext in (".mp3", ".m4a", ".opus", ".webm", ".wav"):
        for path in folder.glob(f"*{ext}"):
            if path.is_file() and path.stat().st_size > 0:
                return path
    return None


def _write_script_from_srt(script_path: Path, title: str, srt_path: Path) -> None:
    lines: list[str] = []
    for _start, _end, text in parse_srt_file(srt_path):
        chunk = re.sub(r"\s+", " ", text).strip()
        if chunk:
            lines.append(chunk)
    body = "\n".join(lines).strip()
    if not body:
        body = title
    script_path.write_text(body + "\n", encoding="utf-8")


def _write_script_from_meta(script_path: Path, title: str, description: str) -> None:
    desc = re.sub(r"\s+", " ", (description or "").strip())
    if desc:
        text = f"{title}\n\n{desc[:4000]}"
    else:
        text = title
    script_path.write_text(text.strip() + "\n", encoding="utf-8")


def _clean_ytdlp_error(err: BaseException) -> str:
    return _ANSI_ESCAPE_RE.sub("", str(err)).strip()


def _base_ydl_opts(staging: Path) -> dict:
    return {
        "outtmpl": str(staging / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_color": True,
        "noprogress": True,
    }


def _extract_video_info(ydl, url: str) -> dict:
    info = ydl.extract_info(url, download=True)
    if not info:
        raise AutomationError("yt-dlp không trả metadata video.")
    return info


def _download_youtube_audio(url: str, staging: Path, *, log_callback=None) -> dict:
    import yt_dlp

    opts = {
        **_base_ydl_opts(staging),
        "format": "ba/b",
        "writeinfojson": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "keepvideo": False,
    }
    if log_callback:
        log_callback("yt-dlp tải audio...", "info")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return _extract_video_info(ydl, url)
    except Exception as err:
        raise AutomationError(f"yt-dlp không tải được audio: {_clean_ytdlp_error(err)}") from err


def _try_download_youtube_subtitles(
    url: str,
    staging: Path,
    *,
    log_callback=None,
) -> Path | None:
    import yt_dlp

    lang_groups = (["en"], ["vi"], ["en", "vi"])
    last_err: str | None = None
    for langs in lang_groups:
        opts = {
            **_base_ydl_opts(staging),
            "skip_download": True,
            "writeinfojson": False,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": langs,
            "subtitlesformat": "srt/best",
            "ignoreerrors": True,
            "sleep_interval_subtitles": 1,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as err:
            last_err = _clean_ytdlp_error(err)
            continue

        picked = _pick_subtitle_file(staging)
        if picked is not None:
            if log_callback:
                log_callback(f"Phụ đề YouTube: {picked.name}", "info")
            return picked

    if log_callback:
        hint = f" ({last_err})" if last_err else ""
        log_callback(
            f"Không tải được phụ đề YouTube{hint} — bỏ qua, dùng Groq STT.",
            "warn",
        )
    return None


def download_youtube(
    url: str,
    output_dir: str | Path,
    *,
    log_callback=None,
    progress_callback=None,
) -> YouTubeDownloadResult:
    ensure_yt_dlp_available(log_callback=log_callback)
    _auto_report(progress_callback, 3, "yt-dlp tải audio...")
    normalized = normalize_youtube_url(url)
    staging = Path(output_dir) / "_youtube_download"
    staging.mkdir(parents=True, exist_ok=True)

    if log_callback:
        log_callback(f"YouTube: {normalized}", "info")

    info = _download_youtube_audio(normalized, staging, log_callback=log_callback)
    _auto_report(progress_callback, 12, "Audio xong — thử phụ đề...")
    _try_download_youtube_subtitles(normalized, staging, log_callback=log_callback)

    video_id = str(info.get("id") or "").strip()
    title = str(info.get("title") or video_id or "youtube_video").strip()
    description = str(info.get("description") or "").strip()
    if not video_id:
        raise AutomationError("Không lấy được video id từ YouTube.")

    info_path = staging / f"{video_id}.info.json"
    if info_path.is_file():
        try:
            meta = json.loads(info_path.read_text(encoding="utf-8"))
            title = str(meta.get("title") or title).strip()
            description = str(meta.get("description") or description).strip()
        except (OSError, json.JSONDecodeError):
            pass

    audio_path = _pick_audio_file(staging, video_id)
    if audio_path is None:
        raise AutomationError("Không tải được audio từ video (cần FFmpeg).")

    subtitle_path = _pick_subtitle_file(staging)
    folder = Path(output_dir) / slugify_topic(title, fallback=video_id)
    folder.mkdir(parents=True, exist_ok=True)

    target_audio = folder / "audio.mp3"
    if audio_path.resolve() != target_audio.resolve():
        if target_audio.is_file():
            target_audio.unlink()
        shutil.move(str(audio_path), str(target_audio))
    else:
        target_audio = audio_path

    target_sub: Path | None = None
    if subtitle_path is not None:
        target_sub = folder / "subtitle.srt"
        shutil.copy2(subtitle_path, target_sub)
        if subtitle_path.parent == staging:
            subtitle_path.unlink(missing_ok=True)

    for leftover in staging.glob(f"{video_id}*"):
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        staging.rmdir()
    except OSError:
        pass

    if log_callback:
        log_callback(f"Đã tải: {title}", "success")
        if target_sub:
            log_callback(f"Phụ đề YouTube: {target_sub.name}", "info")
        else:
            log_callback("Không có phụ đề — sẽ nhận dạng audio bằng Groq.", "warn")
    _auto_report(progress_callback, 28, "Tải YouTube xong")

    return YouTubeDownloadResult(
        video_id=video_id,
        title=title,
        folder=folder,
        audio_path=target_audio,
        subtitle_path=target_sub,
        description=description,
    )


def analyze_youtube_to_prompts(
    url: str,
    output_dir: str | Path,
    *,
    production_prompt_path: str | Path | None = None,
    language: str = DEFAULT_LANGUAGE,
    split_mode: str = DEFAULT_SRT_SPLIT,
    progress_callback=None,
    log_callback=None,
    process_controller: ProcessController | None = None,
) -> AutoProductionResult:
    del production_prompt_path  # reserved for future prompt-aware analysis
    _auto_report(progress_callback, 1, "Phân tích YouTube...")
    downloaded = download_youtube(
        url, output_dir, log_callback=log_callback, progress_callback=progress_callback,
    )
    topic = downloaded.title
    slug = slugify_topic(topic, fallback=downloaded.video_id)
    script_path = downloaded.folder / f"audio_script_{slug}.txt"
    prompts_path = downloaded.folder / f"image_prompts_{slug}.txt"

    if downloaded.subtitle_path and downloaded.subtitle_path.is_file():
        cues = parse_srt_file(downloaded.subtitle_path)
    else:
        cues = []

    if cues:
        srt_path = downloaded.subtitle_path
        assert srt_path is not None
        _write_script_from_srt(script_path, topic, srt_path)
        if log_callback:
            log_callback("Dùng phụ đề YouTube → tạo prompt ảnh...", "info")
        out_prompts = run_prompts_from_srt(
            srt_path,
            prompts_path,
            log_callback=log_callback,
            progress_callback=lambda p, m: _auto_report(progress_callback, 30 + p * 0.7, m),
        )
    else:
        if downloaded.subtitle_path and log_callback:
            log_callback("Phụ đề YouTube rỗng — chuyển sang Groq STT.", "warn")
        _write_script_from_meta(script_path, topic, downloaded.description)
        if log_callback:
            log_callback("Không có phụ đề → Groq STT + prompt ảnh...", "info")
        srt_path, out_prompts = run_audio_pipeline(
            downloaded.audio_path,
            srt_output=downloaded.folder / "subtitle.srt",
            prompts_output=prompts_path,
            language=language,
            split_mode=split_mode,
            generate_prompts=True,
            progress_callback=lambda p, m: _auto_report(progress_callback, 30 + p * 0.7, m),
            log_callback=log_callback,
            process_controller=process_controller,
        )

    _auto_report(progress_callback, 100, "Hoàn thành!")
    return AutoProductionResult(
        topic=topic,
        folder=downloaded.folder,
        script_path=script_path,
        audio_path=downloaded.audio_path,
        srt_path=srt_path,
        prompts_path=out_prompts,
    )
