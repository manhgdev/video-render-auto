#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

from videobuilder.core.automation import AUTO_DURATION_KEY_TO_LABEL, normalize_auto_duration
from videobuilder.core.create_srt import SRT_SPLIT_KEY_TO_LABEL, normalize_srt_split
from videobuilder.core.ffmpeg_setup import check_ffmpeg, ensure_ffmpeg_on_path
from videobuilder.core.pipeline import DEFAULT_2D_TRANSITION_DURATION, get_media_duration
from videobuilder.gui.constants import (
    C,
    EFFECT_LABEL_TO_KEY,
    EFFECT_NONE,
    OUTPUT_STEM,
    STRIP_METADATA_UI,
    TELEGRAM_HANDLE,
    TELEGRAM_URL,
)
from videobuilder.gui.paths import (
    default_output_folder,
    default_output_path,
    get_settings_file,
    is_writable_output_dir,
    normalize_output_path,
)


class ProjectTabMixin:
        def _open_telegram(self):
            webbrowser.open(TELEGRAM_URL)

        def _copy_telegram(self):
            self.clipboard_clear()
            self.clipboard_append(TELEGRAM_HANDLE)
            self._log(f"Đã sao chép {TELEGRAM_HANDLE}.", "info")

        def _build_contact_tab(self, parent):
            parent.columnconfigure(0, weight=1)

            tk.Label(
                parent, text="Liên hệ & hỗ trợ", font=self._font(12, "bold"),
                bg=C["card"], fg=C["text"],
            ).grid(row=0, column=0, sticky="w", pady=(0, 2))

            tk.Label(
                parent,
                text="Gặp lỗi, cần tính năng mới hoặc hỗ trợ sử dụng — nhắn Telegram.",
                font=self._font(9), bg=C["card"], fg=C["muted"], wraplength=560, justify=tk.LEFT,
            ).grid(row=1, column=0, sticky="w", pady=(0, 10))

            card, body = self._section_panel(parent, "Telegram")
            card.grid(row=2, column=0, sticky="ew")
            body.columnconfigure(0, weight=1)

            link = tk.Label(
                body, text=TELEGRAM_HANDLE, font=self._font(11, "bold"),
                bg=C["card"], fg=C["accent"], cursor="hand2", anchor="w",
            )
            link.grid(row=0, column=0, sticky="w", pady=(0, 8))
            link.bind("<Button-1>", lambda _e: self._open_telegram())
            link.bind("<Enter>", lambda _e: link.configure(fg=C["accent_hover"]))
            link.bind("<Leave>", lambda _e: link.configure(fg=C["accent"]))

            btn_row = ttk.Frame(body, style="Card.TFrame")
            btn_row.grid(row=1, column=0, sticky="w")
            self._pill_button(
                btn_row, "Mở Telegram", self._open_telegram, kind="primary",
            ).pack(side=tk.LEFT, padx=(0, 8))
            self._pill_button(
                btn_row, "Sao chép", self._copy_telegram, kind="secondary",
            ).pack(side=tk.LEFT)

            tk.Label(
                parent,
                text="© 2026 manhgdev · VideoBuilder",
                font=self._font(9), bg=C["card"], fg=C["muted"],
            ).grid(row=3, column=0, sticky="w", pady=(14, 0))

        def _build_log_panel(self, parent):
            wrap = tk.Frame(parent, bg=C["log_bg"], highlightbackground=C["border"], highlightthickness=1)
            wrap.pack(fill=tk.X, padx=16, pady=(0, 8))
            wrap.columnconfigure(0, weight=1)

            top = tk.Frame(wrap, bg=C["log_bg"], padx=10, pady=3)
            top.grid(row=0, column=0, columnspan=2, sticky="ew")
            tk.Label(top, text="Log", font=self._font(9, "bold"), bg=C["log_bg"], fg=C["log_muted"]).pack(side=tk.LEFT)
            ttk.Button(top, text="Xóa", command=self._clear_log, style="Small.TButton", width=5).pack(side=tk.RIGHT, padx=(4, 0))
            ttk.Button(top, text="Copy", command=self._copy_log, style="Small.TButton", width=5).pack(side=tk.RIGHT)

            self.log_text = tk.Text(
                wrap, wrap=tk.WORD, font=("Menlo" if sys.platform == "darwin" else "Consolas", 9), height=4,
                bg=C["log_bg"], fg=C["log_fg"], insertbackground=C["log_fg"],
                relief=tk.FLAT, padx=10, pady=5, state=tk.DISABLED,
            )
            log_scroll = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=self.log_text.yview)
            self.log_text.configure(yscrollcommand=log_scroll.set)
            self.log_text.grid(row=1, column=0, sticky="nsew", padx=1, pady=(0, 1))
            log_scroll.grid(row=1, column=1, sticky="ns")

            self.log_text.tag_configure("info", foreground=C["log_fg"])
            self.log_text.tag_configure("error", foreground=C["log_error"])
            self.log_text.tag_configure("warn", foreground=C["log_warn"])
            self.log_text.tag_configure("success", foreground=C["log_success"])

        def _log(self, message, level="info"):
            text = str(message).strip()
            if not text:
                return
            stamp = datetime.now().strftime("%H:%M:%S")
            line = f"[{stamp}] {text}\n"
            tag = level if level in ("info", "error", "warn", "success") else "info"

            def append():
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, line, tag)
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)

            self._run_on_ui_thread(append)

        def _clear_log(self):
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.delete("1.0", tk.END)
            self.log_text.configure(state=tk.DISABLED)

        def _copy_log(self):
            content = self.log_text.get("1.0", tk.END).strip()
            if content:
                self.clipboard_clear()
                self.clipboard_append(content)
                self._log("Đã sao chép log.", "info")

        def _get_effect_key(self):
            return EFFECT_LABEL_TO_KEY.get(self.effect_var.get().strip(), EFFECT_NONE)

        def _on_effect_changed(self, _event=None):
            effect = self._get_effect_key()
            if effect == EFFECT_NONE:
                self.transition_var.set("0")
            else:
                try:
                    current = float(self.transition_var.get().strip() or "0")
                except ValueError:
                    current = 0.0
                if current <= 0:
                    self.transition_var.set(f"{DEFAULT_2D_TRANSITION_DURATION:.2f}")

        def _format_duration(self, seconds: float) -> str:
            total = max(0, int(round(seconds)))
            if total >= 3600:
                h = total // 3600
                m = (total % 3600) // 60
                s = total % 60
                return f"{h}:{m:02d}:{s:02d}"
            m = total // 60
            s = total % 60
            return f"{m:02d}:{s:02d}"

        def _sync_duration_from_audio(self):
            path = self.audio_var.get().strip()
            if not path:
                self.duration_var.set("—")
                return
            audio_path = Path(path)
            if not audio_path.is_file():
                self.duration_var.set("—")
                return
            try:
                ensure_ffmpeg_on_path()
                if not check_ffmpeg()["ok"]:
                    self.duration_var.set("—")
                    return
                duration = get_media_duration(audio_path)
                self.duration_var.set(self._format_duration(duration))
            except Exception:
                self.duration_var.set("—")

        def _pick_images(self):
            path = filedialog.askdirectory(parent=self, title="Chọn thư mục ảnh")
            if not path:
                return
            from videobuilder.core.pipeline import count_valid_images, resolve_images_dir

            resolved = resolve_images_dir(path)
            self.images_var.set(str(resolved))
            valid, total = count_valid_images(resolved)
            invalid = total - valid
            if valid == 0 and total > 0:
                self._show_warning(
                    "Ảnh không hợp lệ",
                    f"Thư mục có {total} file .png/.jpg nhưng không phải ảnh thật.\n"
                    "Thường gặp khi bulk Gemini tải lỗi (file HTML ~300 byte).\n"
                    "Hãy tải lại hoặc chọn thư mục veo-folder có ảnh .jpg.",
                )
            elif invalid > 0:
                self._show_warning(
                    "Ảnh tải lỗi",
                    f"Chỉ {valid}/{total} file là ảnh hợp lệ; {invalid} file lỗi (HTML/thiếu dữ liệu).\n"
                    "Bulk Gemini thường tải hỏng — hãy xuất lại hoặc dùng veo-folder.",
                )
            elif str(resolved) != str(Path(path).resolve()):
                self._show_info("Thư mục ảnh", f"Đã dùng thư mục con:\n{resolved}")

        def _pick_audio(self):
            path = filedialog.askopenfilename(
                parent=self,
                title="Chọn file audio",
                filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac"), ("Tất cả", "*.*")],
            )
            if path:
                self.audio_var.set(path)
                self._sync_duration_from_audio()

        def _pick_prompts(self):
            saved = self.prompts_var.get().strip()
            if saved:
                saved_path = Path(saved)
                initialdir = saved_path.parent if saved_path.parent.exists() else default_output_folder()
                initialfile = saved_path.name if saved_path.suffix else ""
            else:
                audio = self.audio_var.get().strip()
                if audio and Path(audio).is_file():
                    default_txt = Path(audio).with_suffix(".txt")
                    initialdir = default_txt.parent
                    initialfile = default_txt.name
                else:
                    initialdir = default_output_folder()
                    initialfile = ""
            if not initialdir.exists():
                initialdir = default_output_folder()
            dialog_kwargs = {
                "parent": self,
                "title": "Chọn file timeline (.txt)",
                "filetypes": [("File tạo ảnh", "*.txt"), ("Tất cả", "*.*")],
                "initialdir": str(initialdir),
            }
            if initialfile:
                dialog_kwargs["initialfile"] = initialfile
            path = filedialog.askopenfilename(**dialog_kwargs)
            if path:
                self.prompts_var.set(path)
                self._sync_prompts_display(from_output_var=True)

        def _apply_prompts_name(self):
            self._sync_prompts_display()

        def _reset_prompts_path(self):
            """Xóa timeline — không gán lại từ file audio."""
            self.prompts_var.set("")
            self.prompts_dir_var.set("")
            self.prompts_name_var.set("")

        def _maybe_discover_images_dir(self):
            if self.images_var.get().strip():
                return
            from videobuilder.core.pipeline import discover_images_dir
            from videobuilder.core.timeline_paths import resolve_timeline_path

            timeline = resolve_timeline_path(
                self.prompts_var.get().strip() or None,
                audio_path=self.audio_var.get().strip() or None,
            )
            discovered = discover_images_dir(
                timeline_path=timeline,
                audio_path=self.audio_var.get().strip() or None,
            )
            if discovered is not None:
                self.images_var.set(str(discovered))

        def _sync_prompts_display(self, from_output_var=False):
            self._sync_file_export_display(
                self.prompts_var,
                self.prompts_dir_var,
                self.prompts_name_var,
                ".txt",
                from_output_var=from_output_var,
                fallback_stem="timeline",
                audio_path=self.audio_var.get().strip(),
            )
            self._maybe_discover_images_dir()

        def _pick_watermark(self):
            path = filedialog.askopenfilename(
                parent=self,
                title="Chọn watermark PNG",
                filetypes=[("PNG", "*.png"), ("Ảnh", "*.png *.webp"), ("Tất cả", "*.*")],
            )
            if path:
                self.watermark_var.set(path)

        def _pick_subtitle(self):
            path = filedialog.askopenfilename(
                parent=self,
                title="Chọn file phụ đề",
                filetypes=[("Phụ đề", "*.srt *.ass"), ("Tất cả", "*.*")],
            )
            if path:
                self.subtitle_var.set(path)

        def _sanitize_output_name(self, name: str) -> str:
            text = (name or "").strip() or OUTPUT_STEM
            text = Path(text.replace("\\", "/")).name
            if text.lower().endswith(".mp4"):
                text = Path(text).stem
            for ch in '<>:"/\\|?*':
                text = text.replace(ch, "")
            return text.strip() or OUTPUT_STEM

        def _build_output_path(self, folder: str, name: str) -> str:
            folder_text = (folder or "").strip().rstrip("/\\") or str(default_output_folder())
            stem = self._sanitize_output_name(name)
            return str(normalize_output_path(Path(folder_text) / f"{stem}.mp4"))

        def _format_output_dir(self, folder: str) -> str:
            text = (folder or "").strip() or str(default_output_folder())
            if not text.endswith(("/", "\\")):
                text += "\\" if os.name == "nt" else "/"
            return text

        def _sync_output_display(self, from_output_var=False):
            """Đồng bộ output_var ↔ thư mục + tên hiển thị."""
            if from_output_var:
                saved = self.output_var.get().strip() or str(default_output_path())
                path = normalize_output_path(saved)
                self.output_dir_var.set(self._format_output_dir(str(path.parent)))
                self.output_name_var.set(path.stem)
            full = self._build_output_path(self.output_dir_var.get(), self.output_name_var.get())
            path = Path(full)
            self.output_var.set(str(path))
            self.output_dir_var.set(self._format_output_dir(str(path.parent)))
            self.output_name_var.set(path.stem)

        def _apply_output_name(self):
            self._sync_output_display()

        def _reset_output_path(self):
            self.output_var.set(str(default_output_path()))
            self._sync_output_display(from_output_var=True)

        def _pick_output(self):
            current = normalize_output_path(self.output_var.get().strip() or default_output_path())
            initial = current.parent if current.parent.exists() else default_output_folder()
            path = filedialog.askdirectory(
                parent=self,
                title="Chọn thư mục lưu video",
                initialdir=str(initial),
            )
            if path:
                self.output_dir_var.set(self._format_output_dir(path))
                self._sync_output_display()

        def _open_path(self, path):
            path = Path(path)
            if not path.exists():
                self._show_warning("Không tìm thấy", f"Chưa có file:\n{path}")
                return
            try:
                if sys.platform == "win32":
                    os.startfile(path)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(path)])
                else:
                    subprocess.Popen(["xdg-open", str(path)])
            except OSError as err:
                self._show_error("Không mở được", f"Không mở được file:\n{path}\n\n{err}")

        def _open_folder_path(self, path):
            path = Path(path)
            folder = path if path.is_dir() else path.parent
            if not folder.exists():
                self._show_warning("Không tìm thấy", f"Chưa có thư mục:\n{folder}")
                return
            try:
                if sys.platform == "win32":
                    os.startfile(folder)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", str(folder)])
                else:
                    subprocess.Popen(["xdg-open", str(folder)])
            except OSError as err:
                self._show_error("Không mở được", f"Không mở được thư mục:\n{folder}\n\n{err}")

        def _open_output_path(self):
            target = self.last_output or self.output_var.get().strip()
            if not target:
                self._show_info("Chưa có file", "Chọn file xuất trước.")
                return
            self._open_path(target)

        def _open_srt(self):
            target = self.last_srt_output or self.srt_output_var.get().strip()
            if not target or not Path(target).is_file():
                self._show_info("Chưa có file", "Chưa có file SRT.")
                return
            self._show_srt_viewer(target)

        def _open_prompts(self):
            target = self.last_prompts_output or self.srt_prompts_output_var.get().strip()
            if not target or not Path(target).is_file():
                self._show_info("Chưa có file", "Chưa có file tạo ảnh (.txt).")
                return
            self._show_prompts_viewer(target)

        def _open_video(self):
            if self.last_output:
                self._open_path(self.last_output)
            else:
                self._open_output_path()

        def _open_folder(self):
            mode = getattr(self, "_footer_mode", "render")
            if mode in ("srt", "auto", "image"):
                if mode == "image":
                    img_dir = self.img_output_dir_var.get().strip() or self.images_var.get().strip()
                    if img_dir and Path(img_dir).exists():
                        self._open_folder_path(img_dir)
                        return
                target = self.last_srt_output or self.subtitle_var.get().strip() or self.srt_output_var.get().strip()
                if not target:
                    target = (
                        self.last_prompts_output
                        or self.prompts_var.get().strip()
                        or self.srt_prompts_output_var.get().strip()
                        or self.img_prompts_var.get().strip()
                    )
                if not target and mode == "auto":
                    auto_dir = self.auto_output_dir_var.get().strip()
                    if auto_dir and Path(auto_dir).is_dir():
                        self._open_folder_path(auto_dir)
                        return
            else:
                target = self.last_output or self.output_var.get().strip()
            if not target:
                self._show_info("Chưa có file", "Chọn file xuất trước.")
                return
            self._open_folder_path(target)

        def _collect_settings(self):
            return {
                "images_dir": self.images_var.get(),
                "audio": self.audio_var.get(),
                "prompts": self.prompts_var.get(),
                "output": self.output_var.get(),
                "transition": self.transition_var.get(),
                "effect": self.effect_var.get(),
                "resolution": self.resolution_var.get(),
                "fps": self.fps_var.get(),
                "quality": self.quality_var.get(),
                "zoom": self.zoom_var.get(),
                "encoder": self.encoder_var.get(),
                "speed": self.speed_var.get(),
                "volume": self.volume_var.get(),
                "strip_metadata": "Bật" if self.strip_metadata_var.get().strip() == "Bật" else "Tắt",
                "watermark_opacity": self.watermark_opacity_var.get(),
                "watermark": self.watermark_var.get(),
                "subtitle": self.subtitle_var.get(),
                "subtitle_font": self.subtitle_font_var.get(),
                "subtitle_offset": self.subtitle_offset_var.get(),
                "subtitle_margin": self.subtitle_margin_var.get(),
                "subtitle_outline": self.subtitle_outline_var.get(),
                "preview": self.preview_var.get(),
                "groq_api_key": self.groq_api_key_var.get(),
                "gemini_api_key": self.gemini_api_key_var.get(),
                "elevenlabs_api_key": self.elevenlabs_api_key_var.get(),
                "tts_output": self.tts_output_var.get(),
                "tts_engine": self.tts_engine_var.get(),
                "tts_voice": self.tts_voice_var.get(),
                "tts_say_voice": self.tts_say_voice_var.get(),
                "tts_enhance": self.tts_enhance_var.get(),
                "srt_prompts_output": self.srt_prompts_output_var.get(),
                "srt_gen_prompts": self.srt_gen_prompts_var.get(),
                "srt_audio": self.srt_audio_var.get(),
                "srt_output": self.srt_output_var.get(),
                "srt_model": self.srt_model_var.get(),
                "srt_language": self.srt_language_var.get(),
                "srt_split": self._get_srt_split_mode(),
                "auto_prompt_file": self.auto_prompt_file_var.get(),
                "auto_output_dir": self.auto_output_dir_var.get(),
                "auto_youtube_url": self.auto_youtube_url_var.get(),
                "auto_seed": self.auto_seed_var.get(),
                "auto_script": self.auto_script_var.get(),
                "auto_voice": self.auto_voice_var.get(),
                "auto_rate": self.auto_rate_var.get(),
                "auto_duration": normalize_auto_duration(self.auto_duration_var.get()),
                "auto_topic_history": getattr(self, "auto_topic_history", []),
                "img_prompts": self.img_prompts_var.get(),
                "img_output_dir": self.img_output_dir_var.get(),
                "img_aspect": self.img_aspect_var.get(),
                "img_skip_existing": self.img_skip_existing_var.get(),
            }

        def _load_settings(self):
            settings_file = get_settings_file()
            if not settings_file.exists():
                return
            try:
                data = json.loads(settings_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return
            mapping = {
                "images_dir": self.images_var,
                "audio": self.audio_var,
                "prompts": self.prompts_var,
                "output": self.output_var,
                "transition": self.transition_var,
                "effect": self.effect_var,
                "resolution": self.resolution_var,
                "fps": self.fps_var,
                "quality": self.quality_var,
                "zoom": self.zoom_var,
                "encoder": self.encoder_var,
                "speed": self.speed_var,
                "volume": self.volume_var,
                "strip_metadata": self.strip_metadata_var,
                "watermark_opacity": self.watermark_opacity_var,
                "watermark": self.watermark_var,
                "subtitle": self.subtitle_var,
                "subtitle_font": self.subtitle_font_var,
                "subtitle_offset": self.subtitle_offset_var,
                "subtitle_margin": self.subtitle_margin_var,
                "subtitle_outline": self.subtitle_outline_var,
                "preview": self.preview_var,
                "groq_api_key": self.groq_api_key_var,
                "gemini_api_key": self.gemini_api_key_var,
                "elevenlabs_api_key": self.elevenlabs_api_key_var,
                "tts_output": self.tts_output_var,
                "tts_engine": self.tts_engine_var,
                "tts_voice": self.tts_voice_var,
                "tts_say_voice": self.tts_say_voice_var,
                "srt_prompts_output": self.srt_prompts_output_var,
                "srt_gen_prompts": self.srt_gen_prompts_var,
                "srt_audio": self.srt_audio_var,
                "srt_output": self.srt_output_var,
                "srt_model": self.srt_model_var,
                "srt_language": self.srt_language_var,
                "srt_split": self.srt_split_var,
                "auto_prompt_file": self.auto_prompt_file_var,
                "auto_output_dir": self.auto_output_dir_var,
                "auto_youtube_url": self.auto_youtube_url_var,
                "auto_seed": self.auto_seed_var,
                "auto_script": self.auto_script_var,
                "auto_voice": self.auto_voice_var,
                "auto_rate": self.auto_rate_var,
                "auto_duration": self.auto_duration_var,
                "img_prompts": self.img_prompts_var,
                "img_output_dir": self.img_output_dir_var,
                "img_aspect": self.img_aspect_var,
            }
            for key, var in mapping.items():
                if key not in data:
                    continue
                if key == "auto_topic_history":
                    continue
                if key == "strip_metadata" and isinstance(data[key], bool):
                    var.set("Bật" if data[key] else "Tắt")
                elif key == "strip_metadata" and data[key] in STRIP_METADATA_UI:
                    var.set(data[key])
                elif key == "srt_split":
                    split_key = normalize_srt_split(str(data[key]))
                    var.set(SRT_SPLIT_KEY_TO_LABEL.get(split_key, SRT_SPLIT_KEY_TO_LABEL["normal"]))
                elif key == "auto_duration":
                    dur_key = normalize_auto_duration(str(data[key]))
                    var.set(AUTO_DURATION_KEY_TO_LABEL.get(dur_key, AUTO_DURATION_KEY_TO_LABEL["full"]))
                elif key == "srt_gen_prompts":
                    self.srt_gen_prompts_var.set(bool(data[key]))
                elif key == "img_skip_existing":
                    self.img_skip_existing_var.set(bool(data[key]))
                elif key == "tts_enhance":
                    self.tts_enhance_var.set(bool(data[key]))
                else:
                    var.set(data[key])
            if not self.groq_api_key_var.get().strip():
                legacy = data.get("srt_groq_api_key", "")
                if legacy:
                    self.groq_api_key_var.set(str(legacy))
            if not self.gemini_api_key_var.get().strip():
                for legacy_key in ("img_gemini_api_key", "srt_gemini_api_key", "gemini_api_key"):
                    legacy = data.get(legacy_key, "")
                    if legacy:
                        self.gemini_api_key_var.set(str(legacy))
                        break
            self._sync_output_display(from_output_var=True)
            self._sync_prompts_display(from_output_var=True)
            self._sync_srt_output_display(from_output_var=True)
            self._sync_srt_prompts_output_display(from_output_var=True)
            history = data.get("auto_topic_history", [])
            if isinstance(history, list):
                self.auto_topic_history = [str(item).strip() for item in history if str(item).strip()][-200:]
            self.after(200, self._deferred_auto_settings_fixup)
            if not self.img_prompts_var.get().strip() and self.prompts_var.get().strip():
                self.img_prompts_var.set(self.prompts_var.get())
            if not self.img_output_dir_var.get().strip() and self.images_var.get().strip():
                self.img_output_dir_var.set(self.images_var.get())

        def _save_settings(self):
            try:
                get_settings_file().write_text(
                    json.dumps(self._collect_settings(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass

        def _on_close(self):
            if self.process_controller and (
                self.rendering or self.srt_running or self.auto_running or self.img_running
            ):
                self.process_controller.cancel()
            self._save_settings()
            self.destroy()
