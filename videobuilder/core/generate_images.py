#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tạo ảnh scene từ file prompt (image_prompts.txt) qua Gemini."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from videobuilder.core.create_srt import CreateSrtCancelled
from videobuilder.core.env_config import GEMINI_API_KEY_ENV, env_api_key, load_env
from videobuilder.core.pipeline import (
    IMAGE_EXTS,
    ProcessController,
    RenderCancelled,
    SCENE_LINE_RE,
    SINGLE_TIMESTAMP_LINE_RE,
    is_reference_image,
    is_valid_image_file,
    parse_image_scene_num,
    parse_time_to_seconds,
)
from videobuilder.core.progress import report_progress, reset_progress_floor

_runtime_gemini_key: str | None = None

IMAGE_MODEL_CHAIN = (
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
)

ASPECT_RATIO_OPTIONS = {
    "16:9": "16:9",
    "9:16": "9:16",
    "1:1": "1:1",
    "auto": "auto",
}


class GenerateImagesError(Exception):
    pass


@dataclass(frozen=True)
class PromptImageEntry:
    scene_num: int
    start: float
    end: float
    line: str


def gemini_api_key() -> str | None:
    if _runtime_gemini_key:
        return _runtime_gemini_key
    return env_api_key(GEMINI_API_KEY_ENV)


def set_gemini_api_key(key: str | None) -> None:
    global _runtime_gemini_key
    _runtime_gemini_key = (key or "").strip() or None


def apply_env_gemini_key() -> None:
    load_env()
    if gemini_api_key() is None:
        value = env_api_key(GEMINI_API_KEY_ENV)
        if value:
            set_gemini_api_key(value)


def genai_client_available() -> bool:
    try:
        import google.genai  # noqa: F401

        return True
    except ImportError:
        return False


def install_genai_package(*, log_callback=None) -> None:
    _log(log_callback, "Đang cài google-genai...", "info")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "google-genai"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as err:
        raise GenerateImagesError(
            "Không cài được google-genai. Chạy thủ công: pip install google-genai"
        ) from err


def ensure_genai_ready(*, log_callback=None) -> None:
    if genai_client_available():
        return
    install_genai_package(log_callback=log_callback)
    if not genai_client_available():
        raise GenerateImagesError("Chưa cài google-genai sau khi cài đặt.")


def check_gemini_image(*, api_key: str | None = None) -> dict:
    key = (api_key or gemini_api_key() or "").strip()
    pkg_ok = genai_client_available()
    if not key:
        return {
            "ok": False,
            "needs_install": not pkg_ok,
            "message": f"Chưa có {GEMINI_API_KEY_ENV}",
        }
    if not pkg_ok:
        return {
            "ok": False,
            "needs_install": True,
            "message": "Có API key — cần cài google-genai (bấm «Cài đặt»)",
        }
    return {
        "ok": True,
        "needs_install": False,
        "message": f"Gemini image · {IMAGE_MODEL_CHAIN[0]} ✓",
        "model": IMAGE_MODEL_CHAIN[0],
    }


def verify_gemini_api_key(*, api_key: str | None = None) -> tuple[bool, str]:
    status = check_gemini_image(api_key=api_key)
    if not status["ok"]:
        return False, status["message"]
    key = (api_key or gemini_api_key() or "").strip()
    try:
        ensure_genai_ready()
        from google import genai

        client = genai.Client(api_key=key)
        client.models.get(model=f"models/{IMAGE_MODEL_CHAIN[0]}")
    except Exception as err:
        err_text = str(err)
        if "401" in err_text or "403" in err_text or "API key" in err_text:
            return False, f"Gemini API key không hợp lệ: {err_text[:160]}"
        if "404" in err_text:
            return True, f"Gemini key hợp lệ · model {IMAGE_MODEL_CHAIN[0]} có thể khác region"
        return True, f"Gemini key có vẻ hợp lệ ({err_text[:100]})"
    return True, f"Gemini API key hợp lệ · {IMAGE_MODEL_CHAIN[0]}"


def parse_prompt_entries(prompt_file: Path) -> list[PromptImageEntry]:
    if not prompt_file.is_file():
        raise FileNotFoundError(f"Không tìm thấy file prompt: {prompt_file}")

    from videobuilder.core.timeline_formats import parse_prompt_scene_line

    entries: list[PromptImageEntry] = []
    lines = prompt_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    raw_entries: list[tuple[int, float, str]] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        entry = parse_prompt_scene_line(line)
        if entry:
            scene_num = entry.scene_num
            start = entry.start
            end = entry.end
            if end <= start:
                end = start + 0.5
            raw_entries.append((scene_num, start, line))
            entries.append(
                PromptImageEntry(scene_num=scene_num, start=start, end=end, line=line)
            )
            continue
        single = SINGLE_TIMESTAMP_LINE_RE.match(line)
        if single:
            try:
                from videobuilder.core.timeline_formats import parse_time_token

                start = parse_time_token(single.group(1))
            except ValueError:
                continue
            scene_num = len(raw_entries) + 1
            raw_entries.append((scene_num, start, line))

    if raw_entries and not entries:
        duration = raw_entries[-1][1] + 0.5
        for index, (scene_num, start, line) in enumerate(raw_entries):
            end = raw_entries[index + 1][1] if index + 1 < len(raw_entries) else duration
            if end <= start:
                end = start + 0.5
            entries.append(
                PromptImageEntry(scene_num=scene_num, start=start, end=end, line=line)
            )

    if entries:
        entries.sort(key=lambda item: (item.scene_num, item.start))
        return entries

    raise GenerateImagesError(
        "Không tìm thấy dòng prompt hợp lệ (001_[...] hoặc [HH:MM:SS] ...)."
    )


def image_output_path(entry: PromptImageEntry, images_dir: Path) -> Path:
    match = SCENE_LINE_RE.match(entry.line.strip())
    if match:
        name = f"{match.group(1)}_[{match.group(2)}-{match.group(3)}].jpg"
    else:
        name = f"{entry.scene_num:03d}.jpg"
    return images_dir / name


def resolve_aspect_ratio(value: str, *, resolution_label: str = "") -> str:
    key = (value or "auto").strip().lower()
    if key in ASPECT_RATIO_OPTIONS and key != "auto":
        return ASPECT_RATIO_OPTIONS[key]
    res = (resolution_label or "").lower()
    if "short" in res or "9:16" in res:
        return "9:16"
    return "16:9"


def _list_scene_images(images_dir: Path) -> list[Path]:
    """Liệt kê ảnh scene — thư mục trống trả [] (không raise như list_images render)."""
    images_dir = Path(images_dir)
    if not images_dir.is_dir():
        return []
    images = [p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    if not images:
        images = [
            p for p in images_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        ]
    images = [p for p in images if is_valid_image_file(p)]
    return images


def _scene_has_image(scene_num: int, images_dir: Path) -> bool:
    references: list[Path] = []
    for img in _list_scene_images(images_dir):
        if is_reference_image(img):
            references.append(img)
            continue
        if parse_image_scene_num(img) == scene_num:
            return True
    if scene_num == 1 and references:
        return True
    return False


def _log(callback, message: str, level: str = "info") -> None:
    if callback:
        callback(message, level)


def _api_error_kind(err: Exception) -> str:
    text = str(err)
    upper = text.upper()
    if "404" in text or "NOT_FOUND" in upper:
        return "not_found"
    if "429" in text or "RESOURCE_EXHAUSTED" in upper or "QUOTA" in upper:
        return "quota"
    if "401" in text or "403" in text or "API KEY" in upper or "PERMISSION" in upper:
        return "auth"
    if "400" in text and "PAID" in upper:
        return "paid_only"
    return "other"


def _format_model_errors(failures: list[tuple[str, str, str]]) -> str:
    if not failures:
        return "Không tạo được ảnh từ Gemini."
    kinds = {kind for _model, kind, _msg in failures}
    if kinds == {"quota"}:
        models = ", ".join(m for m, k, _ in failures if k == "quota")
        return (
            "Gemini hết quota tạo ảnh (free tier / billing).\n"
            f"Đã thử: {models}.\n"
            "Kiểm tra hạn mức tại Google AI Studio hoặc bật billing, rồi thử lại sau."
        )
    if "auth" in kinds:
        for _m, kind, msg in failures:
            if kind == "auth":
                return f"Gemini API key không hợp lệ hoặc không có quyền: {msg[:200]}"
    lines = ["Không tạo được ảnh từ Gemini:"]
    for model, kind, msg in failures:
        hint = {
            "not_found": "model không còn / không hỗ trợ",
            "quota": "hết quota",
            "paid_only": "cần plan trả phí",
            "auth": "lỗi key",
        }.get(kind, "lỗi")
        lines.append(f"  • {model} — {hint}")
    last_msg = failures[-1][2]
    if last_msg and last_msg not in lines[-1]:
        lines.append(f"Chi tiết: {last_msg[:240]}")
    return "\n".join(lines)


def _is_quota_error(err: Exception | str) -> bool:
    text = str(err).lower()
    return "quota" in text or "hết quota" in text or "resource_exhausted" in text


def _generate_gemini_image(
    client,
    *,
    prompt: str,
    aspect_ratio: str,
    models: tuple[str, ...] = IMAGE_MODEL_CHAIN,
    log_callback=None,
):
    from google.genai import types

    failures: list[tuple[str, str, str]] = []
    config = types.GenerateContentConfig(
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(aspect_ratio=aspect_ratio),
    )
    for model in models:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
        except Exception as err:
            kind = _api_error_kind(err)
            failures.append((model, kind, str(err)))
            if kind == "auth":
                raise GenerateImagesError(_format_model_errors(failures)) from err
            if kind in ("not_found", "quota", "paid_only"):
                _log(log_callback, f"Gemini {model}: {kind} — thử model khác...", "warn")
                continue
            raise GenerateImagesError(_format_model_errors(failures)) from err
        for part in getattr(response, "parts", []) or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return part, model
            if hasattr(part, "as_image"):
                try:
                    img = part.as_image()
                    if img is not None:
                        return part, model
                except Exception:
                    pass
        failures.append((model, "other", f"{model}: không có ảnh trong phản hồi"))
        _log(log_callback, f"Gemini {model}: không có ảnh — thử model khác...", "warn")
    raise GenerateImagesError(_format_model_errors(failures))


def _save_generated_part(part, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(part, "as_image"):
        try:
            img = part.as_image()
            if img is not None:
                img.save(dest)
                return
        except Exception:
            pass
    inline = getattr(part, "inline_data", None)
    if inline is not None and getattr(inline, "data", None):
        dest.write_bytes(inline.data)
        return
    raise GenerateImagesError(f"Không lưu được ảnh: {dest.name}")


def generate_images_from_prompts(
    prompts_file: str | Path,
    images_dir: str | Path,
    *,
    aspect_ratio: str = "16:9",
    skip_existing: bool = True,
    scene_from: int | None = None,
    scene_to: int | None = None,
    scene_nums: list[int] | None = None,
    skip_errors: bool = False,
    api_key: str | None = None,
    progress_callback=None,
    log_callback=None,
    process_controller: ProcessController | None = None,
) -> list[Path]:
    """Đọc file prompt, gọi Gemini tạo ảnh theo số scene."""
    apply_env_gemini_key()
    key = (api_key or gemini_api_key() or "").strip()
    if not key:
        raise GenerateImagesError(
            f"Thiếu {GEMINI_API_KEY_ENV} — nhập key hoặc thêm vào .env."
        )

    ensure_genai_ready(log_callback=log_callback)
    from google import genai

    prompt_path = Path(prompts_file)
    out_dir = Path(images_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = parse_prompt_entries(prompt_path)
    if scene_nums is not None:
        wanted = {int(n) for n in scene_nums}
        entries = [e for e in entries if e.scene_num in wanted]
    if scene_from is not None:
        entries = [e for e in entries if e.scene_num >= scene_from]
    if scene_to is not None:
        entries = [e for e in entries if e.scene_num <= scene_to]
    if not entries:
        raise GenerateImagesError("Không có scene nào trong phạm vi chọn.")

    todo: list[tuple[PromptImageEntry, Path]] = []
    for entry in entries:
        dest = image_output_path(entry, out_dir)
        if skip_existing and dest.is_file():
            continue
        if skip_existing and _scene_has_image(entry.scene_num, out_dir):
            continue
        todo.append((entry, dest))

    total = len(entries)
    skipped = total - len(todo)
    if skipped:
        _log(log_callback, f"Bỏ qua {skipped}/{total} scene đã có ảnh.", "info")
    if not todo:
        _log(log_callback, "Tất cả scene đã có ảnh — không cần tạo thêm.", "success")
        report_progress(progress_callback, 100, "Hoàn thành — không có ảnh mới")
        return []

    reset_progress_floor()
    client = genai.Client(api_key=key)
    ratio = resolve_aspect_ratio(aspect_ratio)
    saved: list[Path] = []
    n = len(todo)
    gemini_quota_exhausted = False

    for i, (entry, dest) in enumerate(todo):
        if process_controller:
            process_controller.wait_if_paused()
            try:
                process_controller.raise_if_cancelled()
            except RenderCancelled as err:
                raise CreateSrtCancelled(str(err)) from err

        if gemini_quota_exhausted:
            if skip_errors:
                _log(log_callback, f"Bỏ qua scene {entry.scene_num:03d} — Gemini hết quota.", "warn")
                continue
            raise GenerateImagesError(
                "Gemini hết quota — dừng batch (các scene sau cũng sẽ lỗi tương tự)."
            )

        pct = 5 + (90.0 * i / max(1, n))
        msg = f"Gemini ảnh {entry.scene_num}/{entries[-1].scene_num} ({i + 1}/{n})..."
        report_progress(progress_callback, pct, msg)
        _log(log_callback, f"Scene {entry.scene_num:03d} → {dest.name}", "info")

        try:
            part, model = _generate_gemini_image(
                client,
                prompt=entry.line,
                aspect_ratio=ratio,
                log_callback=log_callback,
            )
        except GenerateImagesError as err:
            if _is_quota_error(err):
                gemini_quota_exhausted = True
            if skip_errors:
                _log(log_callback, f"✗ Scene {entry.scene_num:03d}: {err}", "warn")
                continue
            raise

        try:
            _save_generated_part(part, dest)
        except GenerateImagesError as err:
            if skip_errors:
                _log(log_callback, f"✗ Scene {entry.scene_num:03d}: {err}", "warn")
                continue
            raise
        saved.append(dest)
        _log(log_callback, f"✓ Scene {entry.scene_num:03d} ({model})", "success")

    report_progress(progress_callback, 100, f"Hoàn thành — {len(saved)} ảnh mới")
    _log(log_callback, f"Đã tạo {len(saved)} ảnh trong {out_dir}", "success")
    return saved
