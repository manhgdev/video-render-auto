#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Đường dẫn file timeline (.txt) — hỗ trợ tên mới timeline_* và legacy image_prompts_*."""

from __future__ import annotations

import re
from pathlib import Path

_SCENE_HEAD_RE = re.compile(r"^\d{3}_\[[^\]]+[–-]", re.MULTILINE)


def _looks_like_timeline_file(path: Path) -> bool:
    """File có dòng scene 001_[...] — không nhầm với audio script thuần text."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        chunk = path.read_text(encoding="utf-8", errors="ignore")[:8192]
    except OSError:
        return False
    if _SCENE_HEAD_RE.search(chunk):
        return True
    return bool(re.search(r"^\[\d{2}:\d{2}", chunk, re.MULTILINE))


def timeline_filename(slug: str) -> str:
    slug = (slug or "").strip() or "video"
    return f"timeline_{slug}.txt"


def legacy_prompt_filename(slug: str) -> str:
    slug = (slug or "").strip() or "video"
    return f"image_prompts_{slug}.txt"


def _alias_path(path: Path) -> Path | None:
    name = path.name
    if name.startswith("image_prompts_"):
        alt = path.with_name(name.replace("image_prompts_", "timeline_", 1))
        return alt if alt.is_file() else None
    if name.startswith("timeline_"):
        alt = path.with_name(name.replace("timeline_", "image_prompts_", 1))
        return alt if alt.is_file() else None
    return None


def _newest_nonempty_txt(files: list[Path]) -> Path | None:
    existing = [p for p in files if p.is_file() and p.stat().st_size > 0]
    if not existing:
        return None
    with_scenes = [p for p in existing if _looks_like_timeline_file(p)]
    pool = with_scenes or existing
    return max(pool, key=lambda p: p.stat().st_mtime)


def _scan_folder_for_timeline(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    return _newest_nonempty_txt([
        *folder.glob("timeline*.txt"),
        *folder.glob("image_prompts*.txt"),
    ])


def default_timeline_path(audio_path: Path, *, slug: str | None = None) -> Path:
    """Path timeline mặc định cạnh audio — ưu tiên file đã có."""
    audio_path = Path(audio_path)
    folder = audio_path.parent
    if slug:
        candidates = [
            folder / timeline_filename(slug),
            folder / legacy_prompt_filename(slug),
        ]
    else:
        stem = audio_path.stem
        candidates = [
            folder / f"timeline_{stem}.txt",
            folder / f"image_prompts_{stem}.txt",
        ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    scanned = _scan_folder_for_timeline(folder)
    if scanned is not None:
        return scanned.resolve()
    sibling = audio_path.with_suffix(".txt")
    if sibling.is_file() and _looks_like_timeline_file(sibling):
        return sibling.resolve()
    if slug:
        return (folder / timeline_filename(slug)).resolve()
    return (folder / f"timeline_{audio_path.stem}.txt").resolve()


def _stem_variants(stem: str) -> list[str]:
    """Các tên file .txt có thể — sau đổi image_prompts_* ↔ timeline_*."""
    text = (stem or "").strip()
    if not text:
        return []
    variants: list[str] = []

    def add(value: str) -> None:
        if value and value not in variants:
            variants.append(value)

    add(text)
    if text.startswith("image_prompts_"):
        slug = text[len("image_prompts_") :]
        add(f"timeline_{slug}")
        add(slug)
    elif text.startswith("timeline_"):
        slug = text[len("timeline_") :]
        add(f"image_prompts_{slug}")
        add(slug)
    else:
        add(f"timeline_{text}")
        add(f"image_prompts_{text}")
    return variants


def resolve_timeline_path(
    configured: str | Path | None = None,
    *,
    audio_path: str | Path | None = None,
    folder: str | Path | None = None,
    stem: str | None = None,
) -> Path | None:
    """Tìm file timeline thực tế — sau khi user đổi tên image_prompts → timeline."""
    candidates: list[Path] = []

    if configured:
        path = Path(configured)
        candidates.append(path)
        alias = _alias_path(path)
        if alias is not None:
            candidates.append(alias)

    if folder and stem:
        base = Path(folder)
        stem_text = (stem or "").strip()
        if stem_text:
            for variant in _stem_variants(stem_text):
                candidates.append(base / f"{variant}.txt")
            if stem_text in ("timeline", "subtitle", "prompts", "image_prompts"):
                scanned = _scan_folder_for_timeline(base)
                if scanned is not None:
                    candidates.append(scanned)

    if audio_path:
        audio = Path(audio_path)
        if audio.is_file():
            scanned = _scan_folder_for_timeline(audio.parent)
            if scanned is not None:
                candidates.append(scanned)
            candidates.append(default_timeline_path(audio))
            sibling = audio.with_suffix(".txt")
            if sibling.is_file() and _looks_like_timeline_file(sibling):
                candidates.append(sibling)

    seen: set[str] = set()
    ranked: list[Path] = []
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                ranked.append(candidate.resolve())
        except OSError:
            continue
    if not ranked:
        return None
    with_scenes = [p for p in ranked if _looks_like_timeline_file(p)]
    if with_scenes:
        return with_scenes[0]
    return ranked[0]
