#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

from videobuilder.core.create_srt import CreateSrtCancelled
from videobuilder.core.automation import (
    AutomationError,
    DEFAULT_AUTOMATION_PROMPT,
    DEFAULT_TTS_RATE,
    DEFAULT_TTS_VOICE,
    TTS_VOICE_OPTIONS,
    _ensure_groq_llm_ready,
    run_full_auto_pipeline,
    suggest_topics,
)
from videobuilder.core.pipeline import ProcessController
from videobuilder.gui.constants import C, SRT_FIELD_LABEL_WIDTH
from videobuilder.gui.paths import default_output_folder, is_writable_output_dir


class AutoTabMixin:
        def _auto_widget_value(self, widget, fallback_var=None) -> str:
            if widget is None:
                return fallback_var.get().strip() if fallback_var is not None else ""
            try:
                return widget.get("1.0", tk.END).strip()
            except TypeError:
                return widget.get().strip()

        def _auto_busy_reason(self) -> str:
            if self.auto_running:
                return "Pipeline tự động đang chạy."
            if self.srt_running:
                return "Đang chạy tạo SRT — chờ xong hoặc bấm Hủy."
            if self.rendering:
                return "Đang render video — chờ xong hoặc bấm Hủy."
            return ""

        def _check_auto_groq_ready(self) -> bool:
            try:
                _ensure_groq_llm_ready()
                return True
            except AutomationError as err:
                self._show_warning("Tự động", str(err))
                return False

        def _pick_auto_prompt_file(self):
            initial = self.auto_prompt_file_var.get().strip()
            initialdir = Path(initial).parent if initial and Path(initial).parent.exists() else default_output_folder()
            path = filedialog.askopenfilename(
                parent=self,
                title="Chọn prompt sản xuất",
                initialdir=str(initialdir),
                filetypes=[("Text", "*.txt"), ("Tất cả", "*.*")],
            )
            if path:
                self.auto_prompt_file_var.set(path)
                self._save_settings()

        def _pick_auto_output_dir(self):
            current = self.auto_output_dir_var.get().strip()
            initialdir = Path(current) if current and Path(current).exists() else default_output_folder()
            path = filedialog.askdirectory(
                parent=self,
                title="Chọn thư mục dự án tự động",
                initialdir=str(initialdir),
            )
            if path:
                self.auto_output_dir_var.set(self._format_output_dir(path))
                self._save_settings()

        def _open_auto_prompt_file(self):
            path = self.auto_prompt_file_var.get().strip()
            if not path or not Path(path).is_file():
                self._show_info("Prompt tham khảo", "Chưa chọn file prompt tham khảo.")
                return
            self._open_path(path)

        def _auto_analyze_youtube(self):
            url = self._auto_widget_value(getattr(self, "auto_youtube_text", None), self.auto_youtube_url_var)
            if not url:
                self._show_warning("Thiếu URL", "Dán URL YouTube trước khi phân tích video.")
                return
            self._show_info(
                "Chưa hỗ trợ",
                "Luồng URL YouTube cần backend tải/phân tích video. UI đã tách đúng quy trình, "
                "nhưng phần xử lý URL chưa có trong core hiện tại.",
            )

        def _auto_button(self, parent, text, command, *, kind="primary"):
            palettes = {
                "primary": ("#2563eb", "#ffffff", "#1d4ed8"),
                "secondary": ("#eef2ff", "#1e40af", "#dbeafe"),
                "ghost": ("#f8fafc", "#334155", "#e2e8f0"),
            }
            bg, fg, hover = palettes.get(kind, palettes["ghost"])
            btn = tk.Label(
                parent,
                text=text,
                bg=bg,
                fg=fg,
                font=self._font(10, "bold" if kind == "primary" else "normal"),
                padx=16,
                pady=8,
                cursor="hand2",
                anchor="center",
            )
            btn._auto_enabled = True
            btn._auto_bg = bg
            btn._auto_fg = fg
            btn._auto_hover = hover
            btn._auto_command = command

            def invoke(_event=None):
                if getattr(btn, "_auto_enabled", True):
                    command()

            def enter(_event=None):
                if getattr(btn, "_auto_enabled", True):
                    btn.configure(bg=hover)

            def leave(_event=None):
                if getattr(btn, "_auto_enabled", True):
                    btn.configure(bg=bg)

            btn.bind("<Button-1>", invoke)
            btn.bind("<Enter>", enter)
            btn.bind("<Leave>", leave)
            return btn

        def _set_auto_button_enabled(self, button, enabled: bool):
            if button is None:
                return
            button._auto_enabled = enabled
            if enabled:
                button.configure(
                    bg=getattr(button, "_auto_bg", "#eef2ff"),
                    fg=getattr(button, "_auto_fg", C["text"]),
                    cursor="hand2",
                )
            else:
                button.configure(bg="#e5e7eb", fg="#94a3b8", cursor="arrow")

        def _auto_selected_topic(self) -> str:
            selection = self.auto_topics_list.curselection()
            if selection:
                return self.auto_topics_list.get(selection[0]).strip()
            direct = self._auto_widget_value(getattr(self, "auto_seed_text", None), self.auto_seed_var)
            return direct if direct and direct.lower() not in ("start", "bắt đầu", "lam video", "làm video") else ""

        def _auto_validate_common(self) -> tuple[Path | None, Path]:
            prompt_text = self.auto_prompt_file_var.get().strip()
            prompt_file = Path(prompt_text) if prompt_text else None
            if prompt_file is not None and not prompt_file.is_file():
                prompt_file = None
            output_dir = Path(self.auto_output_dir_var.get().strip() or default_output_folder())
            if not is_writable_output_dir(output_dir):
                raise ValueError(f"Không ghi được vào thư mục:\n{output_dir}")
            return prompt_file, output_dir

        def _remember_auto_topics(self, topics):
            seen = set()
            history = []
            for topic in [*(getattr(self, "auto_topic_history", []) or []), *topics]:
                text = str(topic or "").strip()
                key = text.lower()
                if not text or key in seen:
                    continue
                seen.add(key)
                history.append(text)
            self.auto_topic_history = history[-200:]
            self._save_settings()

        def _auto_excluded_topics(self, output_dir: Path | None = None) -> list[str]:
            items = list(getattr(self, "auto_topic_history", []) or [])
            if getattr(self, "auto_topics_list", None) is not None:
                items.extend(self.auto_topics_list.get(0, tk.END))
            if output_dir and output_dir.is_dir():
                items.extend(child.name for child in output_dir.iterdir() if child.is_dir())
            return [str(item).strip() for item in items if str(item).strip()]

        def _set_auto_running(self, active: bool):
            self.auto_running = active
            enabled = not active
            self._set_auto_button_enabled(self.auto_topics_btn, enabled)
            self._set_auto_button_enabled(self.auto_next_btn, enabled)
            self._set_auto_button_enabled(self.auto_youtube_btn, enabled)
            self._update_render_control_buttons()

        def _run_auto_worker(self, title: str, target):
            busy = self._auto_busy_reason()
            if busy:
                self._show_warning("Đang bận", busy)
                return
            if not self._check_auto_groq_ready():
                return
            self._set_auto_running(True)
            self.srt_running = True
            self.srt_paused = False
            self.process_controller = ProcessController()
            self._update_render_control_buttons()
            if self._srt_tracker is not None:
                self._srt_tracker.reset(0.0)
            self.srt_status_var.set(title)
            self._log(f"——— {title} ———", "info")

            def worker():
                err_msg = None
                result = None
                cancelled = False
                try:
                    result = target()
                except CreateSrtCancelled:
                    cancelled = True
                except Exception as err:
                    err_msg = str(err)

                def done():
                    self._set_auto_running(False)
                    self.srt_running = False
                    self.srt_paused = False
                    self.process_controller = None
                    if cancelled:
                        if self._srt_tracker is not None:
                            self._srt_tracker.reset(0.0)
                        self.srt_status_var.set("Đã hủy")
                        self._log("Đã hủy pipeline tự động.", "warn")
                    elif err_msg:
                        if self._srt_tracker is not None:
                            self._srt_tracker.reset(0.0)
                        self.srt_status_var.set("Lỗi")
                        self._log(err_msg, "error")
                        self._show_error("Tự động", err_msg)
                    else:
                        if self._srt_tracker is not None:
                            self._srt_tracker.report(100.0)
                        self.srt_status_var.set("Hoàn thành")
                        if result:
                            self._auto_apply_result(result)
                    self._update_srt_open_buttons()
                    self._update_render_control_buttons()

                self.after(0, done)

            threading.Thread(target=worker, daemon=True).start()

        def _auto_apply_result(self, result):
            if isinstance(result, list):
                self.auto_topics_list.delete(0, tk.END)
                for topic in result:
                    self.auto_topics_list.insert(tk.END, topic)
                if result:
                    self.auto_topics_list.selection_set(0)
                self._remember_auto_topics(result)
                self._log(f"Đã tạo {len(result)} chủ đề.", "success")
                return

            path = Path(result) if isinstance(result, (str, Path)) else None
            if path and path.is_file():
                self.auto_script_var.set(str(path))
                self._log(f"Script: {path}", "success")
                return

            if getattr(result, "script_path", None):
                self.auto_script_var.set(str(result.script_path))
            if getattr(result, "topic", None):
                self._remember_auto_topics([result.topic])
            if getattr(result, "audio_path", None):
                self.audio_var.set(str(result.audio_path))
                self.srt_audio_var.set(str(result.audio_path))
                self._sync_duration_from_audio()
            if getattr(result, "srt_path", None):
                self.subtitle_var.set(str(result.srt_path))
                self.srt_output_var.set(str(result.srt_path))
                self.last_srt_output = str(result.srt_path)
            if getattr(result, "prompts_path", None):
                self.prompts_var.set(str(result.prompts_path))
                self.srt_prompts_output_var.set(str(result.prompts_path))
                self.last_prompts_output = str(result.prompts_path)
                self._sync_prompts_display(from_output_var=True)
            self._update_srt_open_buttons()
            self._save_settings()
            self._show_info(
                "Tự động xong",
                "Đã tạo script, audio, SRT và file tạo ảnh.\n\n"
                "Bạn tạo ảnh theo file .txt, đặt vào thư mục ảnh rồi bấm RENDER.",
            )

        def _auto_suggest_topics(self):
            try:
                prompt_file, output_dir = self._auto_validate_common()
            except ValueError as err:
                self._show_warning("Tự động", str(err))
                return
            seed = self._auto_widget_value(getattr(self, "auto_seed_text", None), self.auto_seed_var) or "start"

            def task():
                return suggest_topics(
                    prompt_file,
                    seed,
                    output_dir=output_dir,
                    exclude_topics=self._auto_excluded_topics(output_dir),
                    log_callback=self._log,
                )

            self._run_auto_worker("Gợi ý chủ đề", task)

        def _auto_run_full(self):
            try:
                prompt_file, output_dir = self._auto_validate_common()
            except ValueError as err:
                self._show_warning("Tự động", str(err))
                return
            topic = self._auto_selected_topic()
            if not topic:
                self._show_warning("Chưa chọn chủ đề", "Chọn một chủ đề trong danh sách hoặc nhập chủ đề trực tiếp.")
                return

            def task():
                return run_full_auto_pipeline(
                    prompt_file,
                    topic,
                    output_dir,
                    voice=self.auto_voice_var.get().strip() or DEFAULT_TTS_VOICE,
                    rate=self.auto_rate_var.get().strip() or DEFAULT_TTS_RATE,
                    progress_callback=self._set_srt_progress,
                    log_callback=self._log,
                    process_controller=self.process_controller,
                )

            self._run_auto_worker("Tự tạo đến file ảnh", task)

        def _auto_next(self):
            if self.auto_topics_list.size() == 0:
                if self._auto_selected_topic():
                    self._auto_run_full()
                    return
                self._auto_suggest_topics()
                return
            self._auto_run_full()

        def _build_auto_tab(self, parent):
            parent.columnconfigure(0, weight=1)
            parent.columnconfigure(1, weight=1)

            lw = SRT_FIELD_LABEL_WIDTH

            self._grid_field_label(parent, 0, "Prompt mẫu", label_width=lw)
            prompt_row = ttk.Frame(parent, style="Card.TFrame")
            prompt_row.grid(row=0, column=1, columnspan=2, sticky="ew", pady=3)
            prompt_row.columnconfigure(0, weight=1)
            ttk.Entry(prompt_row, textvariable=self.auto_prompt_file_var).grid(row=0, column=0, sticky="ew", padx=(0, 6))
            self._auto_button(prompt_row, "Chọn", self._pick_auto_prompt_file, kind="ghost").grid(row=0, column=1, padx=(0, 4))
            self._auto_button(prompt_row, "Mở", self._open_auto_prompt_file, kind="secondary").grid(row=0, column=2)

            self._path_field(
                parent, 1, "Thư mục xuất", self.auto_output_dir_var,
                self._pick_auto_output_dir, label_width=lw,
            )

            ttk.Separator(parent, orient=tk.HORIZONTAL).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 8))

            tk.Label(
                parent, text="1. Từ URL YouTube", bg=C["card"], fg=C["text"],
                font=self._font(10, "bold"), anchor="w",
            ).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(0, 4))

            self._grid_field_label(parent, 4, "URL YouTube", label_width=lw)
            youtube_box = ttk.Entry(parent, textvariable=self.auto_youtube_url_var)
            youtube_box.grid(row=4, column=1, columnspan=2, sticky="ew", pady=3)
            self.auto_youtube_text = youtube_box

            self.auto_youtube_btn = self._auto_button(
                parent, "Phân tích video → prompt ảnh", self._auto_analyze_youtube,
                kind="primary",
            )
            self.auto_youtube_btn.grid(row=5, column=1, sticky="w", pady=(4, 8))

            ttk.Separator(parent, orient=tk.HORIZONTAL).grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 8))

            tk.Label(
                parent, text="2. Từ ý tưởng / chủ đề", bg=C["card"], fg=C["text"],
                font=self._font(10, "bold"), anchor="w",
            ).grid(row=7, column=0, columnspan=3, sticky="ew", pady=(0, 4))

            self._grid_field_label(parent, 8, "Giọng đọc", label_width=lw)
            voice_row = ttk.Frame(parent, style="Card.TFrame")
            voice_row.grid(row=8, column=1, columnspan=2, sticky="ew", pady=3)
            voice_row.columnconfigure(0, weight=1)
            voice_combo = ttk.Combobox(
                voice_row,
                textvariable=self.auto_voice_var,
                values=TTS_VOICE_OPTIONS,
                state="normal",
                height=9,
            )
            voice_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))
            ttk.Label(voice_row, text="Tốc độ", style="Field.TLabel").grid(row=0, column=1, sticky="w", padx=(0, 6))
            ttk.Entry(voice_row, textvariable=self.auto_rate_var, width=8).grid(row=0, column=2, sticky="ew")

            self._grid_field_label(parent, 9, "Ý tưởng", label_width=lw)
            input_box = ttk.Entry(parent, textvariable=self.auto_seed_var)
            input_box.grid(row=9, column=1, columnspan=2, sticky="ew", pady=3)
            seed = self.auto_seed_var.get().strip()
            if seed.lower() == "start":
                self.auto_seed_var.set("")
            self.auto_seed_text = input_box

            btn_row = ttk.Frame(parent, style="Card.TFrame")
            btn_row.grid(row=10, column=1, columnspan=2, sticky="w", pady=(4, 8))
            self.auto_topics_btn = self._auto_button(btn_row, "Tạo 5 chủ đề", self._auto_suggest_topics, kind="secondary")
            self.auto_topics_btn.pack(side=tk.LEFT, padx=(0, 8))
            self.auto_next_btn = self._auto_button(btn_row, "Chạy đến prompt ảnh", self._auto_next, kind="primary")
            self.auto_next_btn.pack(side=tk.LEFT)

            self._grid_field_label(parent, 11, "Chủ đề", label_width=lw)
            list_frame = ttk.Frame(parent, style="Card.TFrame")
            list_frame.grid(row=11, column=1, columnspan=2, sticky="nsew", pady=3)
            list_frame.columnconfigure(0, weight=1)
            list_frame.rowconfigure(0, weight=1)
            topics = tk.Listbox(
                list_frame, height=5, font=self._font(10), activestyle="dotbox",
                bg="#f8fafc", fg=C["text"], selectbackground=C["accent"],
                selectforeground="#ffffff", highlightbackground=C["border"],
                highlightthickness=1, relief=tk.FLAT,
            )
            topics.grid(row=0, column=0, sticky="nsew")
            topics.bind("<Double-Button-1>", lambda _e: self._auto_run_full())
            self.auto_topics_list = topics
