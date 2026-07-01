#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prompt-first production: topic ideas -> script -> TTS audio -> SRT/prompts."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import subprocess
import sys
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from videobuilder.core.audio_pipeline import apply_env_api_keys, run_audio_pipeline
from videobuilder.core.create_srt import DEFAULT_LANGUAGE, DEFAULT_SRT_SPLIT, groq_api_key, groq_client_available
from videobuilder.core.env_config import GROQ_API_KEY_ENV
from videobuilder.core.ffmpeg_setup import get_app_dir, get_bundle_dir, is_frozen_app
from videobuilder.core.generate_prompts import GeneratePromptsError
from videobuilder.core.groq_models import groq_llm_model_chain, load_cached_groq_models
from videobuilder.core.progress import report_progress
from videobuilder.core.timeline_paths import timeline_filename

BUNDLED_AUTOMATION_PROMPT = get_bundle_dir() / "template" / "v1-base-vietnam-2D.txt"
BUNDLED_FALLBACK_PROMPT = get_bundle_dir() / "public" / "templates" / "automation_prompt_template.txt"
USER_AUTOMATION_PROMPT = get_app_dir() / "public" / "templates" / "automation_prompt_template.txt"
DEFAULT_AUTOMATION_PROMPT = BUNDLED_AUTOMATION_PROMPT
FALLBACK_AUTOMATION_PROMPT = USER_AUTOMATION_PROMPT
DEFAULT_TTS_VOICE = "vi-VN-HoaiMyNeural"
DEFAULT_TTS_RATE = "+0%"
TTS_VOICE_OPTIONS: tuple[str, ...] = (
    "vi-VN-HoaiMyNeural",
    "vi-VN-NamMinhNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "en-US-AriaNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
    "ko-KR-SunHiNeural",
    "ko-KR-InJoonNeural",
)

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
    "edge-tts": "edge_tts",
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
    groq_ok = _auto_package_installed("groq")
    edge_ok = _auto_package_installed("edge-tts")
    ytdlp_ok = _auto_package_installed("yt-dlp")
    missing: list[str] = []
    if not groq_ok:
        missing.append("groq")
    if not edge_ok:
        missing.append("edge-tts")
    if not ytdlp_ok:
        missing.append("yt-dlp")
    _auto_packages_cache = {
        "groq_key": groq_key,
        "groq_ok": groq_ok,
        "edge_tts_ok": edge_ok,
        "yt_dlp_ok": ytdlp_ok,
        "missing": missing,
        "needs_install": bool(missing),
        "ready_for_topics": groq_key and groq_ok,
        "ready_for_pipeline": groq_key and groq_ok and edge_ok,
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
    """Cài groq + edge-tts + yt-dlp khi user bấm Cài đặt."""
    if is_frozen_app():
        raise AutomationError(
            "Bản .exe không cài pip được. Build lại app hoặc chạy bản dev: pip install groq edge-tts yt-dlp"
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
    minutes: str = "7-12",
    log_callback=None,
    progress_callback=None,
) -> Path:
    _auto_report(progress_callback, 8, "Groq viết script audio...")
    prompt = read_optional_text_auto(production_prompt_path)
    folder = project_folder_for_topic(topic, output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    script_path = folder / f"audio_script_{slugify_topic(topic)}.txt"
    data = _groq_json(
        _base_system_prompt(prompt),
        (
            f"Chủ đề đã chọn: {topic}\n"
            f"Viết script audio hoàn chỉnh độ dài khoảng {minutes} phút. "
            "Script chỉ chứa lời đọc thuần, không tiêu đề, không nhãn, không bullet, không ghi chú. "
            "Trả JSON đúng schema: {\"script\":\"...\"}."
        ),
        max_tokens=8192,
        log_callback=log_callback,
        progress_callback=progress_callback,
    )
    script = str(data.get("script") or "").strip()
    if len(script) < 500:
        raise AutomationError("Script quá ngắn hoặc Groq không trả trường script.")
    script_path.write_text(script + "\n", encoding="utf-8")
    if log_callback:
        log_callback(f"Đã tạo script: {script_path.name}", "success")
    _auto_report(progress_callback, 35, "Script xong")
    return script_path


def check_edge_tts_available() -> bool:
    try:
        import edge_tts  # noqa: F401

        return True
    except ImportError:
        return False


def ensure_edge_tts_available(*, auto_install: bool = True, log_callback=None) -> None:
    if check_edge_tts_available():
        return
    if is_frozen_app():
        raise AutomationError(
            "Thiếu edge-tts trong bản .exe. Cập nhật VideoBuilder hoặc chạy bản dev: pip install edge-tts"
        )
    if not auto_install:
        raise AutomationError("Chưa cài edge-tts để tạo audio.mp3. Chạy: pip install edge-tts")
    if log_callback:
        log_callback("Chưa có edge-tts → tự cài để tạo audio.mp3...", "info")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "edge-tts"])
    except subprocess.CalledProcessError as err:
        raise AutomationError("Không cài được edge-tts. Chạy thủ công: pip install edge-tts") from err
    if not check_edge_tts_available():
        raise AutomationError("Đã cài edge-tts nhưng Python hiện tại chưa import được.")


def synthesize_audio_edge_tts(
    script_path: str | Path,
    audio_path: str | Path | None = None,
    *,
    voice: str = DEFAULT_TTS_VOICE,
    rate: str = DEFAULT_TTS_RATE,
    log_callback=None,
    progress_callback=None,
) -> Path:
    _auto_report(progress_callback, 38, "TTS edge-tts...")
    script = Path(script_path)
    if not script.is_file():
        raise FileNotFoundError(f"Không tìm thấy script: {script}")
    ensure_edge_tts_available(log_callback=log_callback)
    out = Path(audio_path) if audio_path else script.with_name("audio.mp3")
    out.parent.mkdir(parents=True, exist_ok=True)
    text = script.read_text(encoding="utf-8-sig", errors="replace").strip()
    if not text:
        raise AutomationError("Script rỗng, không tạo được audio.")

    async def run() -> None:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
        await communicate.save(str(out))

    if log_callback:
        log_callback(f"TTS edge-tts: {voice} → {out.name}", "info")
    asyncio.run(run())
    if log_callback:
        log_callback(f"Đã tạo audio: {out.name}", "success")
    _auto_report(progress_callback, 48, "Audio xong")
    return out


def run_full_auto_pipeline(
    production_prompt_path: str | Path | None,
    topic: str,
    output_dir: str | Path | None = None,
    *,
    voice: str = DEFAULT_TTS_VOICE,
    rate: str = DEFAULT_TTS_RATE,
    language: str = DEFAULT_LANGUAGE,
    split_mode: str = DEFAULT_SRT_SPLIT,
    progress_callback=None,
    log_callback=None,
    process_controller=None,
) -> AutoProductionResult:
    _auto_report(progress_callback, 2, "Bắt đầu pipeline tự động...")
    script = create_script_file(
        production_prompt_path,
        topic,
        output_dir,
        log_callback=log_callback,
        progress_callback=_scaled_progress(progress_callback, 2, 33),
    )
    audio = synthesize_audio_edge_tts(
        script,
        script.with_name("audio.mp3"),
        voice=voice,
        rate=rate,
        log_callback=log_callback,
        progress_callback=_scaled_progress(progress_callback, 35, 15),
    )
    srt = script.with_name("subtitle.srt")
    prompts = script.with_name(timeline_filename(slugify_topic(topic)))
    try:
        srt_path, prompts_path = run_audio_pipeline(
            audio,
            srt_output=srt,
            prompts_output=prompts,
            language=language,
            split_mode=split_mode,
            generate_prompts=True,
            progress_callback=_scaled_progress(progress_callback, 50, 50),
            log_callback=log_callback,
            process_controller=process_controller,
        )
    except GeneratePromptsError as err:
        raise AutomationError(str(err)) from err
    _auto_report(progress_callback, 100, "Hoàn thành!")
    return AutoProductionResult(
        topic=topic,
        folder=script.parent,
        script_path=script,
        audio_path=audio,
        srt_path=srt_path,
        prompts_path=prompts_path,
    )
