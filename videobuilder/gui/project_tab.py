#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

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
                parent, text="Liên hệ & hỗ trợ", font=self._font(14, "bold"),
                bg=C["card"], fg=C["text"],
            ).grid(row=0, column=0, sticky="w", pady=(0, 4))

            tk.Label(
                parent,
                text="Gặp lỗi, cần tính năng mới hoặc hỗ trợ sử dụng — nhắn Telegram.",
                font=self._font(10), bg=C["card"], fg=C["muted"], wraplength=520, justify=tk.LEFT,
            ).grid(row=1, column=0, sticky="w", pady=(0, 20))

            card = tk.Frame(
                parent, bg=C["accent_soft"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            card.grid(row=2, column=0, sticky="ew", pady=(0, 12))
            card.columnconfigure(1, weight=1)

            tk.Label(
                card, text="Telegram", font=self._font(10, "bold"),
                bg=C["accent_soft"], fg=C["text"],
            ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 4))

            link = tk.Label(
                card, text=TELEGRAM_HANDLE, font=self._font(11, "bold"),
                bg=C["accent_soft"], fg=C["accent"], cursor="hand2",
            )
            link.grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 12))
            link.bind("<Button-1>", lambda _e: self._open_telegram())
            link.bind("<Enter>", lambda _e: link.configure(fg=C["accent_hover"]))
            link.bind("<Leave>", lambda _e: link.configure(fg=C["accent"]))

            btn_row = tk.Frame(card, bg=C["accent_soft"])
            btn_row.grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 14))

            tk.Button(
                btn_row, text="Mở Telegram", font=self._font(10, "bold"),
                bg=C["accent"], fg="#ffffff", activebackground=C["accent_hover"],
                relief=tk.FLAT, cursor="hand2", padx=14, pady=5,
                command=self._open_telegram,
            ).pack(side=tk.LEFT, padx=(4, 8))

            tk.Button(
                btn_row, text="Sao chép", font=self._font(10),
                bg="#ffffff", fg=C["text"], activebackground="#f3f4f6",
                relief=tk.FLAT, cursor="hand2", padx=12, pady=5,
                command=self._copy_telegram,
            ).pack(side=tk.LEFT)

            tk.Label(
                parent,
                text="© 2026 manhgdev · VideoBuilder",
                font=self._font(9), bg=C["card"], fg=C["muted"],
            ).grid(row=3, column=0, sticky="w", pady=(16, 0))

        def _build_log_panel(self, parent):
            wrap = tk.Frame(parent, bg=C["log_bg"], highlightbackground=C["border"], highlightthickness=1)
            wrap.pack(fill=tk.X, padx=10, pady=4)
            wrap.columnconfigure(0, weight=1)

            top = tk.Frame(wrap, bg=C["log_bg"], padx=8, pady=4)
            top.grid(row=0, column=0, columnspan=2, sticky="ew")
            tk.Label(top, text="Log", font=self._font(9, "bold"), bg=C["log_bg"], fg=C["log_muted"]).pack(side=tk.LEFT)
            ttk.Button(top, text="Xóa", command=self._clear_log, style="Small.TButton", width=5).pack(side=tk.RIGHT, padx=(4, 0))
            ttk.Button(top, text="Copy", command=self._copy_log, style="Small.TButton", width=5).pack(side=tk.RIGHT)

            self.log_text = tk.Text(
                wrap, wrap=tk.WORD, font=("Consolas", 9), height=5,
                bg=C["log_bg"], fg=C["log_fg"], insertbackground=C["log_fg"],
                relief=tk.FLAT, padx=8, pady=4, state=tk.DISABLED,
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

            self.after(0, append)

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
            if path:
                self.images_var.set(path)

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
            path = filedialog.askopenfilename(
                parent=self,
                title="Chọn file prompt",
                filetypes=[("Text", "*.txt"), ("Tất cả", "*.*")],
            )
            if path:
                self.prompts_var.set(path)

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
                os.startfile(path)  # type: ignore[attr-defined]
            except AttributeError:
                subprocess.Popen(["xdg-open", str(path)])

        def _open_folder_path(self, path):
            path = Path(path)
            folder = path if path.is_dir() else path.parent
            if not folder.exists():
                self._show_warning("Không tìm thấy", f"Chưa có thư mục:\n{folder}")
                return
            try:
                os.startfile(folder)  # type: ignore[attr-defined]
            except AttributeError:
                subprocess.Popen(["xdg-open", str(folder)])

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

        def _open_video(self):
            if self.last_output:
                self._open_path(self.last_output)
            else:
                self._open_output_path()

        def _open_folder(self):
            if getattr(self, "_footer_mode", "render") == "srt":
                target = self.last_srt_output or self.srt_output_var.get().strip()
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
                "srt_audio": self.srt_audio_var.get(),
                "srt_output": self.srt_output_var.get(),
                "srt_model": self.srt_model_var.get(),
                "srt_language": self.srt_language_var.get(),
                "srt_split": self._get_srt_split_mode(),
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
                "srt_audio": self.srt_audio_var,
                "srt_output": self.srt_output_var,
                "srt_model": self.srt_model_var,
                "srt_language": self.srt_language_var,
                "srt_split": self.srt_split_var,
            }
            for key, var in mapping.items():
                if key in data:
                    if key == "strip_metadata" and isinstance(data[key], bool):
                        var.set("Bật" if data[key] else "Tắt")
                    elif key == "strip_metadata" and data[key] in STRIP_METADATA_UI:
                        var.set(data[key])
                    elif key == "srt_split":
                        split_key = normalize_srt_split(str(data[key]))
                        var.set(SRT_SPLIT_KEY_TO_LABEL.get(split_key, SRT_SPLIT_KEY_TO_LABEL["normal"]))
                    else:
                        var.set(data[key])
            self._sync_output_display(from_output_var=True)
            self._sync_srt_output_display(from_output_var=True)

        def _save_settings(self):
            try:
                get_settings_file().write_text(
                    json.dumps(self._collect_settings(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass

        def _on_close(self):
            if self.process_controller and (self.rendering or self.srt_running):
                self.process_controller.cancel()
            self._save_settings()
            self.destroy()

