#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Danh sách model Groq (theo console limits) + cache model đang dùng + fallback."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from videobuilder.core.env_config import env_api_key
from videobuilder.core.user_config import get_groq_model_cache_file, get_legacy_groq_model_cache_file

GROQ_LLM_MODEL_ENV = "GROQ_LLM_MODEL"
GROQ_WHISPER_MODEL_ENV = "GROQ_WHISPER_MODEL"

# —— Chat Completions (tạo timeline) ——
GROQ_LLM_DEFAULT_MODEL = "llama-3.3-70b-versatile"
GROQ_LLM_FALLBACK_MODELS: tuple[str, ...] = (
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
    "qwen/qwen3-32b",
    "qwen/qwen2-32b",
    "qwen/qwen2.5-27b",
    "allam-2-7b",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "groq/compound",
    "groq/compound-mini",
)

# —— Speech to Text (tạo SRT) ——
GROQ_WHISPER_TURBO = "whisper-large-v3-turbo"
GROQ_WHISPER_LARGE = "whisper-large-v3"
GROQ_WHISPER_MODELS: tuple[str, ...] = (
    GROQ_WHISPER_TURBO,
    GROQ_WHISPER_LARGE,
)

WHISPER_CACHE_AUTO_KEY = "_auto"

_active_llm_model: str | None = None
_active_whisper_by_lang: dict[str, str] = {}
_cache_loaded = False


def _groq_model_cache_path() -> Path:
    return get_groq_model_cache_file()


def _whisper_lang_key(language: str) -> str:
    """Chuẩn hóa key cache Whisper theo ngôn ngữ STT."""
    lang = (language or "").strip()
    return lang if lang else WHISPER_CACHE_AUTO_KEY


def _whisper_lang_from_key(key: str) -> str:
    return "" if key == WHISPER_CACHE_AUTO_KEY else key


def _parse_whisper_cache(data: dict[str, Any]) -> dict[str, str]:
    """Đọc cache Whisper (dict theo ngôn ngữ; tương thích chuỗi cũ)."""
    raw = data.get("whisper")
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            model = str(value or "").strip()
            if model in GROQ_WHISPER_MODELS:
                out[str(key)] = model
    elif isinstance(raw, str):
        model = raw.strip()
        if model in GROQ_WHISPER_MODELS:
            out[WHISPER_CACHE_AUTO_KEY] = model
    return out


def _read_model_cache() -> dict[str, Any]:
    path = _groq_model_cache_path()
    legacy_path = get_legacy_groq_model_cache_file()
    if not path.is_file() and legacy_path.is_file() and legacy_path != path:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(legacy_path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            path = legacy_path
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_model_cache(**updates: str) -> None:
    data = _read_model_cache()
    now = round(time.time(), 3)
    if "llm" in updates:
        model = updates["llm"].strip()
        if model:
            data["llm"] = model
            data["llm_updated_at"] = now
    path = _groq_model_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _write_whisper_model_cache(language: str, model: str) -> None:
    data = _read_model_cache()
    whisper_map = _parse_whisper_cache(data)
    whisper_map[_whisper_lang_key(language)] = model
    data["whisper"] = whisper_map
    data["whisper_updated_at"] = round(time.time(), 3)
    path = _groq_model_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _all_known_llm_models() -> set[str]:
    return {GROQ_LLM_DEFAULT_MODEL, *GROQ_LLM_FALLBACK_MODELS}


def _dedupe_chain(preferred: str, *others: str) -> list[str]:
    chain: list[str] = []
    for model in (preferred, *others):
        model = (model or "").strip()
        if model and model not in chain:
            chain.append(model)
    return chain


def _rotate_chain_to_active(chain: list[str], active: str | None) -> list[str]:
    if active and active in chain:
        idx = chain.index(active)
        return chain[idx:] + chain[:idx]
    return chain


def groq_llm_env_model() -> str:
    """Model mặc định từ .env / built-in (không tính cache)."""
    return (env_api_key(GROQ_LLM_MODEL_ENV) or GROQ_LLM_DEFAULT_MODEL).strip()


def groq_llm_model_chain() -> list[str]:
    """Model LLM ưu tiên + fallback; bắt đầu từ model đã cache nếu có."""
    chain = _dedupe_chain(groq_llm_env_model(), *GROQ_LLM_FALLBACK_MODELS) or [GROQ_LLM_DEFAULT_MODEL]
    load_cached_groq_models()
    return _rotate_chain_to_active(chain, _active_llm_model)


def groq_llm_primary_model() -> str:
    """Model sẽ gọi đầu tiên (cache → env default)."""
    return groq_llm_model_chain()[0]


def load_cached_groq_models(*, force_reload: bool = False) -> None:
    """Nạp model đang dùng từ file cache (giữ giữa các lần mở app)."""
    global _active_llm_model, _active_whisper_by_lang, _cache_loaded
    if _cache_loaded and not force_reload:
        return
    data = _read_model_cache()
    cached_llm = str(data.get("llm") or "").strip()
    if cached_llm and cached_llm in _all_known_llm_models():
        _active_llm_model = cached_llm
    else:
        _active_llm_model = None
    _active_whisper_by_lang = {
        _whisper_lang_from_key(key): model
        for key, model in _parse_whisper_cache(data).items()
    }
    _cache_loaded = True


def groq_llm_active_model() -> str:
    if not _cache_loaded:
        load_cached_groq_models()
    return _active_llm_model or groq_llm_primary_model()


def groq_llm_using_cached_model() -> bool:
    load_cached_groq_models()
    env_model = groq_llm_env_model()
    return bool(_active_llm_model) and _active_llm_model != env_model


def reset_active_llm_model() -> None:
    """Chỉ xóa bộ nhớ phiên (test); lần sau đọc lại file cache."""
    global _active_llm_model, _cache_loaded
    _active_llm_model = None
    _cache_loaded = False


def clear_groq_model_cache() -> None:
    """Xóa cache file + bộ nhớ."""
    global _active_llm_model, _active_whisper_by_lang, _cache_loaded
    _active_llm_model = None
    _active_whisper_by_lang = {}
    _cache_loaded = False
    try:
        _groq_model_cache_path().unlink(missing_ok=True)
    except OSError:
        pass


def set_active_llm_model(model: str) -> None:
    global _active_llm_model, _cache_loaded
    model = model.strip()
    if not model:
        return
    _active_llm_model = model
    _cache_loaded = True
    _write_model_cache(llm=model)


def groq_whisper_default_for_language(language: str) -> str:
    """vi → large-v3; auto/khác → turbo."""
    if language == "vi":
        return GROQ_WHISPER_LARGE
    return GROQ_WHISPER_TURBO


def groq_whisper_env_model(language: str = "") -> str:
    override = (env_api_key(GROQ_WHISPER_MODEL_ENV) or "").strip()
    return override or groq_whisper_default_for_language(language)


def groq_whisper_model_chain(language: str = "") -> list[str]:
    """Model Whisper ưu tiên + fallback; bắt đầu từ model đã cache theo ngôn ngữ."""
    chain = _dedupe_chain(
        groq_whisper_env_model(language),
        *GROQ_WHISPER_MODELS,
    ) or list(GROQ_WHISPER_MODELS)
    load_cached_groq_models()
    cached = _active_whisper_by_lang.get((language or "").strip())
    return _rotate_chain_to_active(chain, cached)


def groq_whisper_primary_model(language: str = "") -> str:
    return groq_whisper_model_chain(language)[0]


def _cached_whisper_for_language(language: str) -> str | None:
    if not _cache_loaded:
        load_cached_groq_models()
    lang = (language or "").strip()
    model = _active_whisper_by_lang.get(lang)
    if model in GROQ_WHISPER_MODELS:
        return model
    return None


def groq_whisper_active_model(language: str = "") -> str:
    return _cached_whisper_for_language(language) or groq_whisper_primary_model(language)


def groq_whisper_using_cached_model(language: str = "") -> bool:
    cached = _cached_whisper_for_language(language)
    return bool(cached) and cached != groq_whisper_env_model(language)


def reset_active_whisper_model() -> None:
    global _active_whisper_by_lang
    _active_whisper_by_lang = {}


def set_active_whisper_model(model: str, *, language: str = "") -> None:
    global _active_whisper_by_lang, _cache_loaded
    model = model.strip()
    if model not in GROQ_WHISPER_MODELS:
        return
    lang = (language or "").strip()
    _active_whisper_by_lang[lang] = model
    _cache_loaded = True
    _write_whisper_model_cache(language, model)


def groq_llm_chain_label() -> str:
    load_cached_groq_models()
    active = groq_llm_active_model()
    env_model = groq_llm_env_model()
    if _active_llm_model and active != env_model:
        return f"{active} (cache)"
    chain = _dedupe_chain(groq_llm_env_model(), *GROQ_LLM_FALLBACK_MODELS)
    if len(chain) <= 1:
        return chain[0]
    return f"{env_model} (+{len(chain) - 1} fallback)"


def groq_whisper_chain_label(language: str = "") -> str:
    load_cached_groq_models()
    active = groq_whisper_active_model(language)
    env_model = groq_whisper_env_model(language)
    if _cached_whisper_for_language(language) and active != env_model:
        return f"{active} (cache)"
    chain = _dedupe_chain(env_model, *GROQ_WHISPER_MODELS)
    if len(chain) <= 1:
        return chain[0]
    return " / ".join(chain)
