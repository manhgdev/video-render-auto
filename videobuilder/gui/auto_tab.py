#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

from videobuilder.core.create_srt import CreateSrtCancelled
from videobuilder.core.path_checks import path_is_dir_safe, path_is_file_safe
from videobuilder.core.pipeline import ProcessController
from videobuilder.core.progress import reset_progress_floor
from videobuilder.core.automation import (
    AutomationError,
    DEFAULT_TTS_RATE,
    DEFAULT_TTS_VOICE,
    TTS_VOICE_OPTIONS,
    _default_auto_output_folder,
    _ensure_groq_llm_ready,
    auto_packages_status,
    automation_prompt_path_hint,
    ensure_default_automation_prompt,
    install_auto_packages,
    run_full_auto_pipeline,
    suggest_topics,
)
from videobuilder.core.ffmpeg_setup import is_frozen_app
from videobuilder.core.youtube_import import analyze_youtube_to_prompts
from videobuilder.gui.constants import C
from videobuilder.gui.paths import default_output_folder, is_writable_output_dir


class AutoTabMixin:
        def _on_auto_tab_shown(self):
            """Tab đã hiện — chỉ cập nhật cache; kiểm tra nặng chạy nền."""
            self._refresh_auto_tab_status(use_cache=True)
            self._schedule_auto_tab_background_refresh()

        def _schedule_auto_tab_background_refresh(self):
            job = getattr(self, "_auto_tab_refresh_job", None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
            self._auto_tab_refresh_job = self.after(30, self._start_auto_tab_background_refresh)

        def _start_auto_tab_background_refresh(self):
            self._auto_tab_refresh_job = None
            if getattr(self, "_auto_tab_refresh_running", False):
                return
            prompt = self.auto_prompt_file_var.get().strip()
            output = self.auto_output_dir_var.get().strip()
            self._auto_tab_refresh_running = True
            threading.Thread(
                target=self._auto_tab_background_refresh,
                args=(prompt, output),
                daemon=True,
            ).start()

        def _auto_tab_background_refresh(self, prompt: str, output: str):
            path_notes: list[str] = []
            prompt_value = None
            output_value = None
            show_template_btn = False
            try:
                if not prompt:
                    prompt_value = str(automation_prompt_path_hint())
                else:
                    exists = path_is_file_safe(prompt)
                    if exists is False:
                        path_notes.append("File prompt không tồn tại — chọn lại hoặc bấm «Tạo mẫu»")
                        show_template_btn = True
                    elif exists is None:
                        path_notes.append("Đang kiểm tra file prompt...")

                if not output:
                    output_value = self._format_output_dir(str(_default_auto_output_folder()))
                else:
                    exists = path_is_dir_safe(output)
                    if exists is False:
                        output_value = self._format_output_dir(str(_default_auto_output_folder()))
                    elif exists is None:
                        path_notes.append("Đang kiểm tra thư mục xuất...")

                status = auto_packages_status(force=True)
            except Exception:
                status = auto_packages_status()
                path_notes.append("Không kiểm tra đủ trạng thái — thử lại sau.")

            def apply():
                self._auto_tab_refresh_running = False
                if prompt_value is not None:
                    self.auto_prompt_file_var.set(prompt_value)
                if output_value is not None:
                    self.auto_output_dir_var.set(output_value)
                if path_notes:
                    self._auto_path_warning = " · ".join(path_notes)
                elif getattr(self, "_auto_path_warning", ""):
                    self._auto_path_warning = ""
                self._auto_show_template_btn = show_template_btn
                self._refresh_auto_tab_status(status=status, path_warning=self._auto_path_warning)
                if getattr(self, "_footer_mode", "") == "auto":
                    self._update_srt_open_buttons()

            self._run_on_ui_thread(apply)

        def _create_default_auto_prompt_file(self):
            path = ensure_default_automation_prompt()
            self.auto_prompt_file_var.set(str(path))
            self._auto_path_warning = ""
            self._save_settings()
            self._refresh_auto_tab_status()
            self._show_info("Prompt mẫu", f"Đã tạo file:\n{path}")

        def _deferred_auto_settings_fixup(self):
            """Sau load settings — không block khởi động."""
            from videobuilder.core.automation import automation_prompt_path_hint

            prompt_path = self.auto_prompt_file_var.get().strip()
            if not prompt_path:
                self.auto_prompt_file_var.set(str(automation_prompt_path_hint()))
            output_path = self.auto_output_dir_var.get().strip()
            if not output_path:
                self.auto_output_dir_var.set(self._format_output_dir(str(_default_auto_output_folder())))
            if self.auto_seed_var.get().strip().lower() == "start":
                self.auto_seed_var.set("")
            seed = self.auto_seed_var.get().strip()
            if len(seed) > 800 and any(marker in seed for marker in ("STAGE ", "ROLE:", "CORE PRINCIPLE")):
                self.auto_seed_var.set("")
                self._auto_path_warning = "Ô ý tưởng đã xóa prompt dài — dùng «Tạo mẫu» hoặc «Chọn» file."
            self._schedule_auto_tab_background_refresh()

        def _fix_auto_tab_paths(self):
            """Giữ tương thích — chỉ gọi từ background refresh."""
            self._schedule_auto_tab_background_refresh()

        def _format_auto_status_message(self, status: dict, path_warning: str = "") -> str:
            if path_warning:
                return path_warning
            if status["ready_for_pipeline"]:
                return "Sẵn sàng · groq · edge-tts · yt-dlp"
            parts: list[str] = []
            if not status["groq_key"]:
                parts.append("Thiếu Groq API key (tab API key)")
            if not status["groq_ok"]:
                parts.append("thiếu groq")
            if not status["edge_tts_ok"]:
                parts.append("thiếu edge-tts (TTS)")
            if not status["yt_dlp_ok"]:
                parts.append("thiếu yt-dlp (YouTube)")
            if status["needs_install"]:
                parts.append("bấm «Cài đặt»")
            return " · ".join(parts) if parts else "Cần cấu hình"

        def _refresh_auto_tab_status(self, *, status: dict | None = None, use_cache: bool = False, path_warning: str | None = None):
            """Cập nhật banner + nút Cài đặt — không block UI."""
            if status is None:
                status = auto_packages_status() if use_cache else auto_packages_status(force=True)
            self.auto_packages_ok = status["ready_for_topics"]
            warning = path_warning if path_warning is not None else getattr(self, "_auto_path_warning", "")
            message = self._format_auto_status_message(status, warning)
            self.auto_status_var.set(message)
            if getattr(self, "_auto_status_wrap", None) is not None:
                if status["needs_install"]:
                    bg, fg = C["warn_bg"], C["warn_fg"]
                    if not self.auto_installing and not is_frozen_app():
                        self.auto_install_btn.pack(side=tk.LEFT, padx=(8, 0))
                    else:
                        self.auto_install_btn.pack_forget()
                elif status["ready_for_pipeline"]:
                    bg, fg = C["ok_bg"], C["ok_fg"]
                    self.auto_install_btn.pack_forget()
                else:
                    bg, fg = C["warn_bg"], C["warn_fg"]
                    self.auto_install_btn.pack_forget()
                if getattr(self, "_auto_show_template_btn", False) and not is_frozen_app():
                    self.auto_prompt_template_btn.pack(side=tk.LEFT, padx=(4, 0))
                else:
                    self.auto_prompt_template_btn.pack_forget()
                self._auto_status_wrap.configure(bg=bg)
                self._auto_status_inner.configure(bg=bg)
                self._auto_status_msg.configure(bg=bg, fg=fg)
            if getattr(self, "_footer_mode", "") == "auto":
                self.srt_status_var.set(message[:120])
            self._update_auto_action_buttons(status)

        def _update_auto_action_buttons(self, status: dict | None = None):
            status = status or auto_packages_status()
            busy = self.auto_running or self.auto_installing
            topics_ok = status["ready_for_topics"] and not busy
            pipeline_ok = status["ready_for_pipeline"] and not busy
            youtube_ok = status["ready_for_youtube"] and not busy
            self._set_auto_button_enabled(self.auto_topics_btn, topics_ok)
            self._set_auto_button_enabled(self.auto_next_btn, pipeline_ok)
            self._set_auto_button_enabled(self.auto_youtube_btn, youtube_ok)

        def _install_auto_packages(self):
            if self.auto_installing or self.auto_running:
                return
            if is_frozen_app():
                self._show_warning(
                    "Cài đặt",
                    "Bản .exe không cài pip được.\nChạy: pip install groq edge-tts yt-dlp\nhoặc dùng bản exe build mới.",
                )
                return
            status = auto_packages_status()
            if not status["needs_install"]:
                self._refresh_auto_tab_status()
                return
            self.auto_installing = True
            self.auto_install_btn.configure(state=tk.DISABLED, text="Đang cài...")
            self.auto_status_var.set(f"Đang cài: {', '.join(status['missing'])}...")
            self._update_auto_action_buttons()

            def worker():
                err_msg = None
                try:
                    install_auto_packages(log_callback=self._log)
                except Exception as err:
                    err_msg = str(err)

                def done():
                    self.auto_installing = False
                    if getattr(self, "auto_install_btn", None) is not None:
                        self.auto_install_btn.configure(state=tk.NORMAL, text="Cài đặt")
                    self._refresh_auto_tab_status()
                    if err_msg:
                        self._show_error("Cài đặt", err_msg)
                    else:
                        self._show_info("Xong", "Đã cài gói tab Tự động (groq, edge-tts, yt-dlp).")
                        self._log("Đã cài gói tab Tự động.", "success")

                self._run_on_ui_thread(done)

            threading.Thread(target=worker, daemon=True).start()

        def _auto_require_topics(self) -> bool:
            status = auto_packages_status()
            if not status["groq_key"]:
                self._show_warning("Tự động", "Nhập Groq API key ở tab API key.")
                return False
            if not status["groq_ok"]:
                self._show_warning("Tự động", "Thiếu gói groq — bấm «Cài đặt» trên tab này.")
                return False
            return True

        def _auto_require_pipeline(self) -> bool:
            if not self._auto_require_topics():
                return False
            status = auto_packages_status()
            if not status["edge_tts_ok"]:
                self._show_warning("Tự động", "Thiếu edge-tts (TTS) — bấm «Cài đặt».")
                return False
            return True

        def _auto_require_youtube(self) -> bool:
            if not self._auto_require_topics():
                return False
            status = auto_packages_status()
            if not status["yt_dlp_ok"]:
                self._show_warning("Tự động", "Thiếu yt-dlp — bấm «Cài đặt».")
                return False
            return True

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
            if getattr(self, "img_running", False):
                return "Đang tạo ảnh — chờ xong hoặc bấm Hủy."
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
            if not self._auto_require_youtube():
                return
            url = self._auto_widget_value(getattr(self, "auto_youtube_text", None), self.auto_youtube_url_var)
            if not url:
                self._show_warning("Thiếu URL", "Dán URL YouTube trước khi phân tích video.")
                return
            try:
                prompt_file, output_dir = self._auto_validate_common()
            except ValueError as err:
                self._show_warning("Tự động", str(err))
                return

            def task():
                return analyze_youtube_to_prompts(
                    url,
                    output_dir,
                    production_prompt_path=prompt_file,
                    progress_callback=self._set_auto_progress,
                    log_callback=self._log,
                    process_controller=self.process_controller,
                )

            self._run_auto_worker("Phân tích YouTube", task)

        def _auto_panel(self, parent, title: str) -> ttk.Frame:
            return self._section_panel(parent, title)

        def _auto_button(self, parent, text, command, *, kind="primary"):
            btn = self._pill_button(parent, text, command, kind=kind)
            btn._auto_enabled = True
            btn._auto_bg = btn._pill_bg
            btn._auto_fg = btn._pill_fg
            btn._auto_hover = btn._pill_hover
            btn._auto_command = command
            return btn

        def _set_auto_button_enabled(self, button, enabled: bool):
            if button is None:
                return
            button._auto_enabled = enabled
            button._pill_enabled = enabled
            if enabled:
                button.configure(
                    bg=getattr(button, "_auto_bg", getattr(button, "_pill_bg", "#eef2ff")),
                    fg=getattr(button, "_auto_fg", getattr(button, "_pill_fg", C["text"])),
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
            self._refresh_auto_tab_status()
            self._update_render_control_buttons()

        def _set_auto_progress(self, pct, message):
            self._set_srt_progress(pct, message)

        def _run_auto_worker(self, title: str, target):
            busy = self._auto_busy_reason()
            if busy:
                self._show_warning("Đang bận", busy)
                return
            if not self._check_auto_groq_ready():
                return
            reset_progress_floor()
            self._apply_footer_mode("auto")
            self._set_auto_running(True)
            self.srt_running = True
            self.srt_paused = False
            self.process_controller = ProcessController()
            self._update_render_control_buttons()
            if self._srt_tracker is not None:
                self._srt_tracker.reset(0.0)
            self.srt_status_var.set(title)
            self._set_auto_progress(1, title)
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

                self._run_on_ui_thread(done)

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
                "Đã tạo audio, SRT và file tạo ảnh.\n\n"
                "Bạn tạo ảnh theo file .txt, đặt vào thư mục ảnh rồi bấm RENDER.",
            )

        def _auto_suggest_topics(self):
            if not self._auto_require_topics():
                return
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
                    progress_callback=self._set_auto_progress,
                )

            self._run_auto_worker("Gợi ý chủ đề", task)

        def _auto_run_full(self):
            if not self._auto_require_pipeline():
                return
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
                    progress_callback=self._set_auto_progress,
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
            parent.rowconfigure(2, weight=1)

            lw = 11

            self._auto_status_wrap = tk.Frame(parent, bg=C["warn_bg"], highlightbackground=C["border"], highlightthickness=1)
            self._auto_status_wrap.grid(row=0, column=0, sticky="ew", pady=(0, 6))
            self._auto_status_inner = tk.Frame(self._auto_status_wrap, bg=C["warn_bg"])
            self._auto_status_inner.pack(fill=tk.X, padx=10, pady=8)
            self.auto_status_var = tk.StringVar(value="Mở tab để kiểm tra gói...")
            self._auto_status_msg = tk.Label(
                self._auto_status_inner,
                textvariable=self.auto_status_var,
                bg=C["warn_bg"],
                fg=C["warn_fg"],
                font=self._font(9),
                anchor="w",
                justify=tk.LEFT,
                wraplength=900,
            )
            self._auto_status_msg.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self._auto_install_btn_frame = ttk.Frame(self._auto_status_inner, style="Card.TFrame")
            self._auto_install_btn_frame.pack(side=tk.RIGHT)
            self.auto_install_btn = ttk.Button(
                self._auto_install_btn_frame,
                text="Cài đặt",
                command=self._install_auto_packages,
                style="Small.TButton",
                width=9,
            )
            self.auto_prompt_template_btn = ttk.Button(
                self._auto_install_btn_frame,
                text="Tạo mẫu",
                command=self._create_default_auto_prompt_file,
                style="Small.TButton",
                width=8,
            )

            settings = ttk.Frame(parent, style="Card.TFrame")
            settings.grid(row=1, column=0, sticky="ew", pady=(0, 6))
            settings.columnconfigure(1, weight=1)
            settings.columnconfigure(3, weight=1)

            self._grid_field_label(
                settings, 0, "Prompt mẫu", label_width=lw, pady=2, col=0,
                help_key="auto_prompt",
            )
            prompt_row = ttk.Frame(settings, style="Card.TFrame")
            prompt_row.grid(row=0, column=1, sticky="ew", pady=2, padx=(0, 10))
            prompt_row.columnconfigure(0, weight=1)
            ttk.Entry(prompt_row, textvariable=self.auto_prompt_file_var).grid(
                row=0, column=0, sticky="ew", padx=(0, 4),
            )
            ttk.Button(
                prompt_row, text="Chọn", command=self._pick_auto_prompt_file,
                style="Small.TButton", width=5,
            ).grid(row=0, column=1, padx=(0, 2))
            ttk.Button(
                prompt_row, text="Mở", command=self._open_auto_prompt_file,
                style="Small.TButton", width=4,
            ).grid(row=0, column=2)

            self._grid_field_label(
                settings, 0, "Thư mục xuất", label_width=lw, pady=2, col=2,
                help_key="auto_output_dir",
            )
            output_row = ttk.Frame(settings, style="Card.TFrame")
            output_row.grid(row=0, column=3, sticky="ew", pady=2)
            output_row.columnconfigure(0, weight=1)
            ttk.Entry(output_row, textvariable=self.auto_output_dir_var).grid(
                row=0, column=0, sticky="ew", padx=(0, 4),
            )
            ttk.Button(
                output_row, text="Chọn", command=self._pick_auto_output_dir,
                style="Small.TButton", width=5,
            ).grid(row=0, column=1)

            workflows = ttk.Frame(parent, style="Card.TFrame")
            workflows.grid(row=2, column=0, sticky="nsew")
            workflows.columnconfigure(0, weight=1, uniform="auto_flow")
            workflows.columnconfigure(1, weight=1, uniform="auto_flow")
            workflows.rowconfigure(0, weight=1)

            yt_wrap, yt_body = self._auto_panel(workflows, "① Từ URL YouTube")
            yt_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
            yt_body.columnconfigure(0, weight=1)
            yt_label = self._muted_label_with_help(yt_body, "URL YouTube", help_key="auto_youtube")
            yt_label.grid(row=0, column=0, sticky="w", pady=(0, 2))
            youtube_box = ttk.Entry(yt_body, textvariable=self.auto_youtube_url_var)
            youtube_box.grid(row=1, column=0, sticky="ew", pady=(0, 6))
            self.auto_youtube_text = youtube_box
            self.auto_youtube_btn = self._auto_button(
                yt_body, "Phân tích → prompt ảnh", self._auto_analyze_youtube,
                kind="primary",
            )
            self.auto_youtube_btn.grid(row=2, column=0, sticky="w")

            topic_wrap, topic_body = self._auto_panel(workflows, "② Từ ý tưởng / chủ đề")
            topic_wrap.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
            topic_body.columnconfigure(0, weight=1)
            topic_body.rowconfigure(5, weight=1)

            voice_row = ttk.Frame(topic_body, style="Card.TFrame")
            voice_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
            voice_row.columnconfigure(1, weight=1)
            voice_label = self._muted_label_with_help(voice_row, "Giọng", help_key="auto_voice")
            voice_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
            ttk.Combobox(
                voice_row,
                textvariable=self.auto_voice_var,
                values=TTS_VOICE_OPTIONS,
                state="normal",
                height=12,
                width=28,
            ).grid(row=0, column=1, sticky="ew", padx=(0, 8))
            rate_label = self._muted_label_with_help(voice_row, "Tốc độ", help_key="auto_rate")
            rate_label.grid(row=0, column=2, sticky="w", padx=(0, 4))
            ttk.Entry(voice_row, textvariable=self.auto_rate_var, width=6).grid(
                row=0, column=3, sticky="e",
            )

            seed_label = self._muted_label_with_help(
                topic_body, "Ý tưởng / chủ đề", help_key="auto_seed",
            )
            seed_label.grid(row=1, column=0, sticky="w", pady=(0, 2))
            input_box = ttk.Entry(topic_body, textvariable=self.auto_seed_var)
            input_box.grid(row=2, column=0, sticky="ew", pady=(0, 6))
            seed = self.auto_seed_var.get().strip()
            if seed.lower() == "start":
                self.auto_seed_var.set("")
            self.auto_seed_text = input_box

            btn_row = ttk.Frame(topic_body, style="Card.TFrame")
            btn_row.grid(row=3, column=0, sticky="w", pady=(0, 6))
            self.auto_topics_btn = self._auto_button(
                btn_row, "Tạo 5 chủ đề", self._auto_suggest_topics, kind="secondary",
            )
            self.auto_topics_btn.pack(side=tk.LEFT, padx=(0, 6))
            self.auto_next_btn = self._auto_button(
                btn_row, "Chạy đến prompt ảnh", self._auto_next, kind="primary",
            )
            self.auto_next_btn.pack(side=tk.LEFT)

            topics_label = self._muted_label_with_help(
                topic_body, "Danh sách chủ đề (double-click để chạy)", help_key="auto_topics",
            )
            topics_label.grid(row=4, column=0, sticky="w", pady=(0, 2))
            list_frame = ttk.Frame(topic_body, style="Card.TFrame")
            list_frame.grid(row=5, column=0, sticky="nsew")
            list_frame.columnconfigure(0, weight=1)
            list_frame.rowconfigure(0, weight=1)
            topics = tk.Listbox(
                list_frame, height=6, font=self._font(10), activestyle="dotbox",
                bg="#f8fafc", fg=C["text"], selectbackground=C["accent"],
                selectforeground="#ffffff", highlightbackground=C["border"],
                highlightthickness=1, relief=tk.FLAT,
            )
            topics.grid(row=0, column=0, sticky="nsew")
            topics.bind("<Double-Button-1>", lambda _e: self._auto_run_full())
            self.auto_topics_list = topics
