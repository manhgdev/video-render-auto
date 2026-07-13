#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prompt-first production: topic ideas -> script -> TTS audio -> SRT/prompts."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from videobuilder.core.audio_pipeline import apply_env_api_keys, run_prompts_from_srt
from videobuilder.core.create_srt import (
    DEFAULT_LANGUAGE,
    DEFAULT_SRT_SPLIT,
    cues_from_script_text,
    groq_api_key,
    groq_client_available,
    refine_srt_cues,
)
from videobuilder.core.env_config import ELEVENLABS_API_KEY_ENV, GROQ_API_KEY_ENV, env_api_key
from videobuilder.core.ffmpeg_setup import get_app_dir, get_bundle_dir, is_frozen_app, resolve_ffmpeg
from videobuilder.core.groq_models import groq_llm_model_chain, load_cached_groq_models
from videobuilder.core.pipeline import get_media_duration, write_srt_from_cues
from videobuilder.core.progress import report_progress
from videobuilder.core.timeline_paths import timeline_filename

BUNDLED_AUTOMATION_PROMPT = get_bundle_dir() / "template" / "v1-base-vietnam-2D.txt"
BUNDLED_FALLBACK_PROMPT = get_bundle_dir() / "public" / "templates" / "automation_prompt_template.txt"
USER_AUTOMATION_PROMPT = get_app_dir() / "public" / "templates" / "automation_prompt_template.txt"
DEFAULT_AUTOMATION_PROMPT = BUNDLED_AUTOMATION_PROMPT
FALLBACK_AUTOMATION_PROMPT = USER_AUTOMATION_PROMPT

# ElevenLabs Adam — cùng voice_id StudioVoiceAdamAI (chỉ audio; SRT vẫn qua Groq STT).
DEFAULT_TTS_VOICE = "pNInz6obpgDQGcFmaJgB"
DEFAULT_TTS_RATE = "+0%"  # ponytail: giữ UI; ElevenLabs không dùng rate Edge
TTS_VOICE_OPTIONS: tuple[str, ...] = (
    "pNInz6obpgDQGcFmaJgB",  # Adam
    "EXAVITQu4vr4xnSDxMaL",  # Sarah
    "VR6AewLTigWG4xSOukaG",  # Arnold
)
ELEVENLABS_MODEL_ID = "eleven_v3"  # khớp StudioVoiceAdamAI
ELEVENLABS_MAX_CHARS = 4000  # ponytail: cắt script dài; nâng nếu quota model cho phép

# Tab Tạo audio: ElevenLabs (cloud) hoặc macOS say (AdamVoiceAssistant / local)
TTS_ENGINE_ELEVENLABS = "ElevenLabs Adam"
TTS_ENGINE_MACOS_SAY = "macOS say"
TTS_ENGINE_OPTIONS = (TTS_ENGINE_ELEVENLABS, TTS_ENGINE_MACOS_SAY)
DEFAULT_TTS_ENGINE = TTS_ENGINE_ELEVENLABS
DEFAULT_MACOS_SAY_VOICE = "Linh"
DEFAULT_MACOS_SAY_RATE = 193  # ~AdamVoice; say mặc định ~175

# Độ dài video = độ dài audio. Short = ép script ngắn → TTS/SRT/ảnh tự khớp.
AUTO_DURATION_OPTIONS = (
    ("full", "Dài (7–12 phút)"),
    ("6", "Short 6 giây"),
    ("10", "Short 10 giây"),
)
DEFAULT_AUTO_DURATION = "full"
AUTO_DURATION_LABEL_TO_KEY = {label: key for key, label in AUTO_DURATION_OPTIONS}
AUTO_DURATION_KEY_TO_LABEL = {key: label for key, label in AUTO_DURATION_OPTIONS}


def normalize_auto_duration(value: str | None) -> str:
    text = (value or "").strip()
    if text in AUTO_DURATION_KEY_TO_LABEL:
        return text
    if text in AUTO_DURATION_LABEL_TO_KEY:
        return AUTO_DURATION_LABEL_TO_KEY[text]
    if text in ("6s", "6 giây"):
        return "6"
    if text in ("10s", "10 giây"):
        return "10"
    return DEFAULT_AUTO_DURATION


DEFAULT_AUTOMATION_PROMPT_TEXT = """# Prompt mẫu cho tab Tự động

Mục tiêu: tạo video giáo dục ngắn/dài dạng kể chuyện, dễ hiểu, có hook mạnh.

Phong cách:
- Tiếng Việt tự nhiên, câu ngắn, dễ đọc TTS.
- Mở đầu phải gây tò mò trong 5-10 giây đầu.
- Nội dung có nhịp kể chuyện, ví dụ cụ thể, không lan man.
- Hình ảnh minh họa ưu tiên 2D educational, rõ ý, không quá nhiều chi tiết.

Yêu cầu script:
- Chỉ viết lời đọc thuần.
- Không heading, không bullet, không ghi chú sân khấu.
- Không chèn mô tả hình ảnh vào script audio.

Yêu cầu prompt ảnh:
- Chia theo timeline bám sát audio.
- Mỗi prompt mô tả rõ nhân vật, hành động, bối cảnh, cảm xúc.
- Giữ nhất quán style và nhân vật giữa các cảnh.
"""


_default_prompt_path_cached: Path | None = None


def automation_prompt_path_hint() -> Path:
    """Path prompt mặc định — không tạo/ghi file (dùng khi khởi tạo UI)."""
    global _default_prompt_path_cached
    if _default_prompt_path_cached is not None:
        return _default_prompt_path_cached
    _default_prompt_path_cached = USER_AUTOMATION_PROMPT
    return _default_prompt_path_cached


def ensure_default_automation_prompt() -> Path:
    """Path prompt mặc định ổn định (không lưu _MEIPASS vào settings)."""
    global _default_prompt_path_cached
    if USER_AUTOMATION_PROMPT.is_file():
        _default_prompt_path_cached = USER_AUTOMATION_PROMPT
        return USER_AUTOMATION_PROMPT
    source = BUNDLED_AUTOMATION_PROMPT if BUNDLED_AUTOMATION_PROMPT.is_file() else BUNDLED_FALLBACK_PROMPT
    USER_AUTOMATION_PROMPT.parent.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        USER_AUTOMATION_PROMPT.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        USER_AUTOMATION_PROMPT.write_text(DEFAULT_AUTOMATION_PROMPT_TEXT, encoding="utf-8")
    _default_prompt_path_cached = USER_AUTOMATION_PROMPT
    return USER_AUTOMATION_PROMPT


_AUTO_PACKAGE_MODULES = {
    "groq": "groq",
    "yt-dlp": "yt_dlp",
}


def _auto_package_installed(dist_name: str) -> bool:
    module = _AUTO_PACKAGE_MODULES.get(dist_name, dist_name.replace("-", "_"))
    return importlib.util.find_spec(module) is not None


_auto_packages_cache: dict | None = None


def invalidate_auto_packages_cache() -> None:
    global _auto_packages_cache
    _auto_packages_cache = None


def auto_packages_status(*, force: bool = False) -> dict:
    """Kiểm tra nhanh gói tab Tự động — chỉ find_spec, không import nặng."""
    global _auto_packages_cache
    if not force and _auto_packages_cache is not None:
        return _auto_packages_cache

    from videobuilder.core.create_srt import groq_api_key

    groq_key = bool(groq_api_key())
    eleven_key = bool(elevenlabs_api_keys())
    groq_ok = _auto_package_installed("groq")
    ytdlp_ok = _auto_package_installed("yt-dlp")
    missing: list[str] = []
    if not groq_ok:
        missing.append("groq")
    if not ytdlp_ok:
        missing.append("yt-dlp")
    _auto_packages_cache = {
        "groq_key": groq_key,
        "elevenlabs_key": eleven_key,
        "groq_ok": groq_ok,
        "edge_tts_ok": True,  # legacy UI key — TTS giờ là ElevenLabs
        "yt_dlp_ok": ytdlp_ok,
        "missing": missing,
        "needs_install": bool(missing),
        "ready_for_topics": groq_key and groq_ok,
        "ready_for_pipeline": groq_key and groq_ok and eleven_key,
        "ready_for_youtube": groq_key and groq_ok and ytdlp_ok,
    }
    return _auto_packages_cache


def warmup_auto_defaults() -> None:
    """Cache path/output/gói sau khi UI đã hiện — chạy nền, không block main thread."""

    def work() -> None:
        try:
            ensure_default_automation_prompt()
            _default_auto_output_folder()
            auto_packages_status(force=True)
        except Exception:
            pass

    threading.Thread(target=work, daemon=True).start()


def install_auto_packages(*, log_callback=None) -> None:
    """Cài groq + yt-dlp khi user bấm Cài đặt."""
    if is_frozen_app():
        raise AutomationError(
            "Bản .exe không cài pip được. Build lại app hoặc chạy bản dev: pip install groq yt-dlp"
        )
    missing = auto_packages_status()["missing"]
    if not missing:
        return
    pip_names = [name for name in missing if name in _AUTO_PACKAGE_MODULES]
    if not pip_names:
        return
    if log_callback:
        log_callback(f"Đang cài: {', '.join(pip_names)}...", "info")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", *pip_names])
    except subprocess.CalledProcessError as err:
        raise AutomationError(
            f"Không cài được gói ({', '.join(pip_names)}). Chạy thủ công: pip install {' '.join(pip_names)}"
        ) from err
    invalidate_auto_packages_cache()
    still_missing = auto_packages_status(force=True)["missing"]
    if still_missing:
        raise AutomationError(f"Vẫn thiếu: {', '.join(still_missing)}")


TOOL_PRODUCTION_PROMPT = """
Bạn là engine sản xuất video YouTube audio-first chạy trong app desktop VideoBuilder.
Bạn không thao tác file trực tiếp. App sẽ nhận JSON của bạn và tự ghi file.

Mục tiêu:
- Từ input "start" hoặc một ý tưởng mơ hồ, tạo 5 chủ đề video giáo dục có khả năng viral.
- Sau khi người dùng chọn 1 chủ đề, viết script audio sạch để app đưa vào TTS.
- Script là lời đọc thuần, không tiêu đề, không heading, không bullet, không ghi chú sân khấu.
- Văn phong kể chuyện giáo dục, dễ hiểu, có hook mạnh trong 5-10 giây đầu.
- Câu ngắn, dễ đọc TTS, nhịp cảm xúc rõ.
- Nếu là ngách cổ đại/sinh tồn: dùng ngôi thứ hai "bạn", đưa người xem vào tình huống.
- Kết thúc gợi suy nghĩ hoặc nối lại hook ban đầu.

Style mặc định:
- Video minh họa 2D giáo dục, người que, hình ảnh rõ ý theo audio.
- Ưu tiên chủ đề có nguy hiểm, bí mật, sinh tồn, nghịch lý, lịch sử đời sống con người, khoa học đời thường.

Quy tắc output bắt buộc:
- Luôn trả JSON hợp lệ.
- Không markdown.
- Không giải thích ngoài JSON.
- Với danh sách chủ đề: {"topics":["..."]}.
- Với script: {"script":"..."}.
"""


class AutomationError(Exception):
    pass


@dataclass
class AutoProductionResult:
    topic: str
    folder: Path
    script_path: Path
    audio_path: Path | None = None
    srt_path: Path | None = None
    prompts_path: Path | None = None


def read_text_auto(path: str | Path) -> str:
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"Không tìm thấy file: {src}")
    for enc in ("utf-8-sig", "utf-16", "cp1258", "cp1252"):
        try:
            text = src.read_text(encoding=enc)
        except UnicodeError:
            continue
        if text.strip():
            return text
    return src.read_text(encoding="utf-8", errors="replace")


def read_optional_text_auto(path: str | Path | None) -> str:
    if not path:
        return ""
    src = Path(path)
    if not src.is_file():
        return ""
    return read_text_auto(src)


def slugify_topic(topic: str, *, fallback: str = "video") -> str:
    text = unicodedata.normalize("NFD", topic or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:80].strip("_") or fallback


def normalize_topic_key(topic: str) -> str:
    return slugify_topic(topic, fallback="")


def filter_unique_topics(topics: list[str], excluded: list[str] | tuple[str, ...] | None = None) -> list[str]:
    seen = {normalize_topic_key(item) for item in (excluded or [])}
    out: list[str] = []
    for topic in topics:
        text = str(topic or "").strip()
        key = normalize_topic_key(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def project_folder_for_topic(topic: str, output_dir: str | Path | None = None) -> Path:
    base = Path(output_dir) if output_dir else _default_auto_output_folder()
    return base / slugify_topic(topic)


def discover_existing_topic_hints(output_dir: str | Path | None) -> list[str]:
    if not output_dir:
        return []
    base = Path(output_dir)
    if not base.is_dir():
        return []
    hints: list[str] = []
    for child in base.iterdir():
        if child.is_dir():
            hints.append(child.name)
            for script in child.glob("audio_script_*.txt"):
                hints.append(script.stem.removeprefix("audio_script_"))
        elif child.is_file() and child.name.startswith("audio_script_"):
            hints.append(child.stem.removeprefix("audio_script_"))
    return filter_unique_topics(hints)


_default_auto_output_cached: Path | None = None


def _default_auto_output_folder() -> Path:
    global _default_auto_output_cached
    if _default_auto_output_cached is not None and _default_auto_output_cached.is_dir():
        return _default_auto_output_cached
    for folder in (get_app_dir() / "public" / "auto", Path.home() / "Downloads", Path.home() / "Videos", Path.home() / "Desktop"):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / "._vb_write_test"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
            _default_auto_output_cached = folder.resolve()
            return _default_auto_output_cached
        except OSError:
            continue
    _default_auto_output_cached = (Path.home() / "Desktop").resolve()
    return _default_auto_output_cached


def _ensure_groq_llm_ready() -> str:
    apply_env_api_keys()
    if not groq_api_key():
        raise AutomationError(f"Thiếu {GROQ_API_KEY_ENV} trong .env hoặc ô Groq.")
    if not groq_client_available():
        raise AutomationError("Chưa cài groq. Bấm Cài đặt ở tab Tạo SRT hoặc chạy: pip install groq")
    load_cached_groq_models()
    return groq_api_key() or ""


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise AutomationError("Groq không trả JSON hợp lệ.")
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise AutomationError("Groq trả dữ liệu không đúng dạng object.")
    return data


def _auto_report(progress_callback, pct: float, message: str) -> None:
    report_progress(progress_callback, pct, message)


def _scaled_progress(progress_callback, base: float, span: float):
    def report(pct: float, message: str) -> None:
        _auto_report(progress_callback, base + float(pct) * span / 100.0, message)

    return report


def _groq_json(system: str, user: str, *, max_tokens: int = 4096, log_callback=None, progress_callback=None) -> dict[str, Any]:
    key = _ensure_groq_llm_ready()
    from groq import Groq

    client = Groq(api_key=key)
    last_err: BaseException | None = None
    models = list(groq_llm_model_chain())
    for idx, model in enumerate(models):
        try:
            if log_callback:
                log_callback(f"Groq LLM {model}...", "info")
            if progress_callback and models:
                pct = 20 + (55 * idx / max(1, len(models)))
                _auto_report(progress_callback, pct, f"Groq LLM {model}...")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.35,
                max_tokens=max_tokens,
            )
            return _parse_json_object(response.choices[0].message.content or "")
        except Exception as err:
            last_err = err
            continue
    raise AutomationError(f"Groq LLM lỗi: {last_err}")


def _base_system_prompt(production_prompt: str = "") -> str:
    reference = production_prompt.strip()
    reference_block = (
        "\n\nPROMPT THAM KHẢO CỦA NGƯỜI DÙNG, CHỈ LẤY STYLE/FORMAT PHÙ HỢP:\n"
        f"{reference[:18000]}"
        if reference else ""
    )
    return (
        TOOL_PRODUCTION_PROMPT.strip()
        + reference_block
        + "\n\nNếu prompt tham khảo có yêu cầu kiểu chat như 'tạo file', hãy chuyển thành JSON cho app xử lý."
    )


def suggest_topics(
    production_prompt_path: str | Path | None,
    seed: str = "start",
    *,
    count: int = 5,
    exclude_topics: list[str] | tuple[str, ...] | None = None,
    output_dir: str | Path | None = None,
    log_callback=None,
    progress_callback=None,
) -> list[str]:
    _auto_report(progress_callback, 3, "Groq đề xuất chủ đề...")
    prompt = read_optional_text_auto(production_prompt_path)
    excluded = filter_unique_topics([
        *(exclude_topics or []),
        *discover_existing_topic_hints(output_dir),
    ])
    candidate_count = max(count * 3, count + min(len(excluded), 10))
    avoid_block = (
        "\nCác chủ đề đã dùng hoặc đã có trong thư mục dự án, TUYỆT ĐỐI KHÔNG lặp lại ý này:\n"
        + "\n".join(f"- {item}" for item in excluded[:80])
        if excluded else ""
    )
    data = _groq_json(
        _base_system_prompt(prompt),
        (
            f"Input người dùng: {seed!r}\n"
            f"Hãy đề xuất {candidate_count} chủ đề video có khả năng viral, cụ thể, dễ viết script audio. "
            f"Sau đó tự chọn ra các chủ đề khác biệt nhất. Không lặp lại chủ đề đã dùng.{avoid_block}\n"
            "Trả JSON đúng schema: {\"topics\":[\"...\", \"...\"]}."
        ),
        max_tokens=1800,
        log_callback=log_callback,
        progress_callback=progress_callback,
    )
    topics = filter_unique_topics(
        [str(x).strip() for x in data.get("topics", []) if str(x).strip()],
        excluded=excluded,
    )
    if len(topics) < count:
        raise AutomationError("Groq trả thiếu chủ đề mới không trùng. Bấm Tạo 5 chủ đề lại.")
    _auto_report(progress_callback, 100, "Hoàn thành!")
    return topics[:count]


def create_script_file(
    production_prompt_path: str | Path | None,
    topic: str,
    output_dir: str | Path | None = None,
    *,
    target_duration: str = DEFAULT_AUTO_DURATION,
    minutes: str = "7-12",
    log_callback=None,
    progress_callback=None,
) -> Path:
    _auto_report(progress_callback, 8, "Groq viết script audio...")
    prompt = read_optional_text_auto(production_prompt_path)
    folder = project_folder_for_topic(topic, output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    script_path = folder / f"audio_script_{slugify_topic(topic)}.txt"
    duration_key = normalize_auto_duration(target_duration)
    if duration_key == "6":
        length_rule = (
            "Viết script audio khoảng 6 giây khi đọc (~20–35 từ tiếng Việt). "
            "Một hook mạnh + một ý duy nhất. Không giải thích dài."
        )
        min_len, max_tokens = 40, 512
    elif duration_key == "10":
        length_rule = (
            "Viết script audio khoảng 10 giây khi đọc (~35–55 từ tiếng Việt). "
            "Hook + một twist ngắn. Không lan man."
        )
        min_len, max_tokens = 60, 800
    else:
        length_rule = (
            f"Viết script audio hoàn chỉnh độ dài khoảng {minutes} phút. "
            "Script chỉ chứa lời đọc thuần."
        )
        min_len, max_tokens = 500, 8192
    data = _groq_json(
        _base_system_prompt(prompt),
        (
            f"Chủ đề đã chọn: {topic}\n"
            f"{length_rule} "
            "Không tiêu đề, không nhãn, không bullet, không ghi chú. "
            "Trả JSON đúng schema: {\"script\":\"...\"}."
        ),
        max_tokens=max_tokens,
        log_callback=log_callback,
        progress_callback=progress_callback,
    )
    script = str(data.get("script") or "").strip()
    if len(script) < min_len:
        raise AutomationError(
            f"Script quá ngắn ({len(script)} ký tự, cần ≥{min_len}) "
            "hoặc Groq không trả trường script."
        )
    script_path.write_text(script + "\n", encoding="utf-8")
    if log_callback:
        label = AUTO_DURATION_KEY_TO_LABEL.get(duration_key, duration_key)
        log_callback(f"Đã tạo script ({label}): {script_path.name}", "success")
    _auto_report(progress_callback, 35, "Script xong")
    return script_path


_elevenlabs_api_key_override: str | None = None


def elevenlabs_api_keys() -> list[str]:
    """Keys từ override UI / .env — hỗ trợ nhiều key cách nhau bởi dấu phẩy."""
    if _elevenlabs_api_key_override:
        raw = _elevenlabs_api_key_override
    else:
        raw = (env_api_key(ELEVENLABS_API_KEY_ENV) or "").strip()
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def set_elevenlabs_api_key(key: str | None) -> None:
    global _elevenlabs_api_key_override
    text = (key or "").strip()
    _elevenlabs_api_key_override = text or None


def _detect_tts_language(text: str) -> str:
    """Theo StudioVoiceAdamAI; script Việt → 'en' (không gửi 'vi', bỏ nhánh fr dễ false-positive)."""
    if re.search(r"[\u3040-\u309f\u30a0-\u30ff]", text):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", text):
        return "ko"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    return "en"


def _split_text_for_tts(text: str, max_chars: int = ELEVENLABS_MAX_CHARS) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= max_chars:
            chunks.append(rest)
            break
        window = rest[:max_chars]
        cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "), window.rfind("\n"))
        if cut < max_chars // 3:
            cut = window.rfind(" ")
        if cut < max_chars // 3:
            cut = max_chars
        else:
            cut += 1
        piece = rest[:cut].strip()
        if piece:
            chunks.append(piece)
        rest = rest[cut:].strip()
    return chunks


def _ssl_context() -> ssl.SSLContext:
    """Python.org trên macOS thường thiếu cert.pem → CERTIFICATE_VERIFY_FAILED."""
    candidates: list[str] = []
    try:
        import certifi

        candidates.append(certifi.where())
    except ImportError:
        pass
    candidates.extend(
        [
            "/opt/homebrew/etc/openssl@3/cert.pem",
            "/usr/local/etc/openssl@3/cert.pem",
            "/etc/ssl/cert.pem",
            "/etc/ssl/certs/ca-certificates.crt",
            str(Path(sys.base_prefix) / "etc" / "openssl" / "cert.pem"),
        ]
    )
    for cafile in candidates:
        if cafile and Path(cafile).is_file():
            return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


def _elevenlabs_tts_request(
    text: str,
    *,
    api_key: str,
    voice_id: str,
    language_code: str,
    enhance: bool = False,
) -> bytes:
    body: dict[str, Any] = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.6 if enhance else 0.0,
            "use_speaker_boost": enhance,
        },
    }
    if language_code and language_code != "auto":
        body["language_code"] = language_code
    if enhance:
        body["apply_text_to_speech_enhancement"] = True
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        data=data,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180, context=_ssl_context()) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")[:400]
        raise AutomationError(f"ElevenLabs HTTP {err.code}: {detail}") from err
    except urllib.error.URLError as err:
        raise AutomationError(f"ElevenLabs kết nối lỗi: {err.reason}") from err


def _tts_with_key_rotation(
    text: str,
    *,
    voice_id: str,
    language_code: str,
    enhance: bool = False,
) -> bytes:
    keys = elevenlabs_api_keys()
    if not keys:
        raise AutomationError(
            f"Thiếu {ELEVENLABS_API_KEY_ENV} trong .env (TTS Adam / ElevenLabs)."
        )
    last_err: Exception | None = None
    for api_key in keys:
        try:
            return _elevenlabs_tts_request(
                text,
                api_key=api_key,
                voice_id=voice_id,
                language_code=language_code,
                enhance=enhance,
            )
        except AutomationError as err:
            last_err = err
            msg = str(err).lower()
            if any(tok in msg for tok in ("401", "403", "429", "quota", "limit")):
                continue
            raise
    raise AutomationError(
        f"Tất cả ElevenLabs API key đều lỗi: {last_err}"
    ) from last_err


def _concat_mp3_chunks(chunk_paths: list[Path], out: Path) -> None:
    if len(chunk_paths) == 1:
        out.write_bytes(chunk_paths[0].read_bytes())
        return
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        # ponytail: không ffmpeg → ghép bytes thô (MP3 thường nghe được)
        out.write_bytes(b"".join(p.read_bytes() for p in chunk_paths))
        return
    list_file = out.with_suffix(".concat.txt")
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in chunk_paths),
        encoding="utf-8",
    )
    try:
        subprocess.check_call(
            [
                ffmpeg, "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file), "-c", "copy", str(out),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        list_file.unlink(missing_ok=True)


def synthesize_text_elevenlabs(
    text: str,
    audio_path: str | Path,
    *,
    voice: str = DEFAULT_TTS_VOICE,
    enhance: bool = False,
    log_callback=None,
    progress_callback=None,
) -> Path:
    """TTS ElevenLabs Adam — text → mp3 (tab Tạo audio / pipeline tự động)."""
    _auto_report(progress_callback, 10, "TTS ElevenLabs...")
    out = Path(audio_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = (text or "").strip()
    if not text:
        raise AutomationError("Văn bản rỗng, không tạo được audio.")

    voice_id = (voice or DEFAULT_TTS_VOICE).strip() or DEFAULT_TTS_VOICE
    if voice_id.startswith("vi-VN-") or voice_id.startswith("en-") or voice_id.startswith("ko-"):
        voice_id = DEFAULT_TTS_VOICE
    lang = _detect_tts_language(text)
    chunks = _split_text_for_tts(text)
    if log_callback:
        log_callback(
            f"TTS ElevenLabs Adam/{voice_id[:8]}… lang={lang} "
            f"({len(chunks)} đoạn, {len(text)} ký tự) → {out.name}",
            "info",
        )

    with tempfile.TemporaryDirectory(prefix="vb_tts_") as tmp:
        tmp_dir = Path(tmp)
        parts: list[Path] = []
        for i, chunk in enumerate(chunks):
            audio = _tts_with_key_rotation(
                chunk, voice_id=voice_id, language_code=lang, enhance=enhance,
            )
            part = tmp_dir / f"part_{i:03d}.mp3"
            part.write_bytes(audio)
            parts.append(part)
            pct = 10 + int((i + 1) / len(chunks) * 80)
            _auto_report(progress_callback, pct, f"TTS {i + 1}/{len(chunks)}...")
        _concat_mp3_chunks(parts, out)

    if log_callback:
        log_callback(f"Đã tạo audio: {out.name}", "success")
    _auto_report(progress_callback, 100, "Audio xong")
    return out


def macos_say_available() -> bool:
    return sys.platform == "darwin" and shutil.which("say") is not None


def list_macos_say_voice_names(*, prefer_locale: str = "vi_VN") -> list[str]:
    """Tên giọng từ `say -v ?` — locale ưu tiên lên đầu."""
    if not macos_say_available():
        return []
    try:
        raw = subprocess.check_output(
            ["say", "-v", "?"], text=True, stderr=subprocess.STDOUT, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return [DEFAULT_MACOS_SAY_VOICE]
    preferred: list[str] = []
    rest: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        m = re.match(r"^(.+?)\s+([a-z]{2}_[A-Z]{2})\s+#", line)
        if not m:
            continue
        name = m.group(1).rstrip()
        if name in seen:
            continue
        seen.add(name)
        (preferred if m.group(2) == prefer_locale else rest).append(name)
    return preferred + rest or [DEFAULT_MACOS_SAY_VOICE]


def synthesize_text_macos_say(
    text: str,
    audio_path: str | Path,
    *,
    voice: str = DEFAULT_MACOS_SAY_VOICE,
    rate: int = DEFAULT_MACOS_SAY_RATE,
    log_callback=None,
    progress_callback=None,
) -> Path:
    """TTS macOS `say` — text → wav → mp3."""
    _auto_report(progress_callback, 10, "TTS macOS say...")
    if not macos_say_available():
        raise AutomationError("macOS say chỉ chạy trên macOS.")
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        raise AutomationError("Cần FFmpeg để chuyển WAV → mp3.")

    out = Path(audio_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = (text or "").strip()
    if not text:
        raise AutomationError("Văn bản rỗng, không tạo được audio.")
    voice_name = (voice or DEFAULT_MACOS_SAY_VOICE).strip() or DEFAULT_MACOS_SAY_VOICE
    try:
        rate_i = max(90, min(400, int(rate)))
    except (TypeError, ValueError):
        rate_i = DEFAULT_MACOS_SAY_RATE

    if log_callback:
        log_callback(
            f"TTS say/{voice_name} rate={rate_i} ({len(text)} ký tự) → {out.name}",
            "info",
        )

    with tempfile.TemporaryDirectory(prefix="vb_say_") as tmp:
        tmp_dir = Path(tmp)
        txt, wav = tmp_dir / "input.txt", tmp_dir / "say.wav"
        txt.write_text(text, encoding="utf-8")
        _auto_report(progress_callback, 30, "say đang đọc...")
        try:
            subprocess.check_call(
                [
                    "say", "-v", voice_name, "-r", str(rate_i),
                    "-f", str(txt), "-o", str(wav),
                    "--data-format=LEF32@22050",
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as err:
            raise AutomationError(
                f"say thất bại (giọng «{voice_name}»?). Thoát {err.returncode}."
            ) from err
        if not wav.is_file() or wav.stat().st_size < 100:
            raise AutomationError("say không tạo được file WAV.")
        _auto_report(progress_callback, 70, "Chuyển mp3...")
        try:
            subprocess.check_call(
                [ffmpeg, "-y", "-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "4", str(out)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as err:
            raise AutomationError(f"FFmpeg chuyển mp3 thất bại: {err}") from err

    if not out.is_file() or out.stat().st_size < 100:
        raise AutomationError("File mp3 sau say trống hoặc lỗi.")
    if log_callback:
        log_callback(f"Đã tạo audio (say): {out.name}", "success")
    _auto_report(progress_callback, 100, "Audio xong")
    return out


def synthesize_audio_elevenlabs(
    script_path: str | Path,
    audio_path: str | Path | None = None,
    *,
    voice: str = DEFAULT_TTS_VOICE,
    rate: str = DEFAULT_TTS_RATE,
    enhance: bool = False,
    log_callback=None,
    progress_callback=None,
) -> Path:
    """TTS từ file script — wrapper quanh synthesize_text_elevenlabs."""
    del rate
    script = Path(script_path)
    if not script.is_file():
        raise FileNotFoundError(f"Không tìm thấy script: {script}")
    out = Path(audio_path) if audio_path else script.with_name("audio.mp3")
    text = script.read_text(encoding="utf-8-sig", errors="replace").strip()
    return synthesize_text_elevenlabs(
        text,
        out,
        voice=voice,
        enhance=enhance,
        log_callback=log_callback,
        progress_callback=progress_callback,
    )


# Alias tương thích import cũ
synthesize_audio_edge_tts = synthesize_audio_elevenlabs


def run_full_auto_pipeline(
    production_prompt_path: str | Path | None,
    topic: str,
    output_dir: str | Path | None = None,
    *,
    voice: str = DEFAULT_TTS_VOICE,
    rate: str = DEFAULT_TTS_RATE,
    language: str = DEFAULT_LANGUAGE,
    split_mode: str = DEFAULT_SRT_SPLIT,
    target_duration: str = DEFAULT_AUTO_DURATION,
    progress_callback=None,
    log_callback=None,
    process_controller=None,
) -> AutoProductionResult:
    del language  # TTS path: SRT từ script, không STT
    del process_controller
    _auto_report(progress_callback, 2, "Bắt đầu pipeline tự động...")
    script = create_script_file(
        production_prompt_path,
        topic,
        output_dir,
        target_duration=target_duration,
        log_callback=log_callback,
        progress_callback=_scaled_progress(progress_callback, 2, 33),
    )
    audio = synthesize_audio_elevenlabs(
        script,
        script.with_name("audio.mp3"),
        voice=voice,
        rate=rate,
        log_callback=log_callback,
        progress_callback=_scaled_progress(progress_callback, 35, 15),
    )
    srt = script.with_name("subtitle.srt")
    prompts = script.with_name(timeline_filename(slugify_topic(topic)))
    script_text = script.read_text(encoding="utf-8-sig", errors="replace")
    try:
        duration = get_media_duration(audio)
    except Exception:
        duration = 0.0
    if duration <= 0.5:
        raise AutomationError("Không đọc được độ dài audio sau TTS.")

    _auto_report(progress_callback, 52, "SRT từ script...")
    raw_cues = cues_from_script_text(script_text, duration)
    srt_cues = refine_srt_cues(raw_cues, split_mode)
    if not srt_cues:
        raise AutomationError("Không tạo được cue SRT từ script.")
    write_srt_from_cues(srt, srt_cues)
    if log_callback:
        log_callback(
            f"SRT từ script TTS: {len(srt_cues)} cue / {duration:.1f}s → {srt.name} "
            "(không STT — tránh ảo giác Whisper trên Adam/VI)",
            "success",
        )

    try:
        prompts_path = run_prompts_from_srt(
            srt,
            prompts,
            log_callback=log_callback,
            progress_callback=_scaled_progress(progress_callback, 60, 40),
        )
    except Exception as err:
        # AudioPipelineError / GeneratePromptsError
        raise AutomationError(str(err)) from err
    _auto_report(progress_callback, 100, "Hoàn thành!")
    return AutoProductionResult(
        topic=topic,
        folder=script.parent,
        script_path=script,
        audio_path=audio,
        srt_path=srt,
        prompts_path=prompts_path,
    )
