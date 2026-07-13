#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tab Tạo audio — text → mp3 (ElevenLabs hoặc macOS say)."""

from __future__ import annotations

import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

from videobuilder.core.automation import (
    DEFAULT_MACOS_SAY_VOICE,
    DEFAULT_TTS_ENGINE,
    DEFAULT_TTS_VOICE,
    TTS_ENGINE_MACOS_SAY,
    TTS_ENGINE_OPTIONS,
    TTS_VOICE_OPTIONS,
    AutomationError,
    elevenlabs_api_keys,
    list_macos_say_voice_names,
    macos_say_available,
    synthesize_text_elevenlabs,
    synthesize_text_macos_say,
)
from videobuilder.core.env_config import ELEVENLABS_API_KEY_ENV
from videobuilder.core.progress import reset_progress_floor
from videobuilder.gui.constants import C
from videobuilder.gui.paths import default_output_folder

TTS_FIELD_LABEL_WIDTH = 13


class TtsTabMixin:
        def _tts_is_say(self) -> bool:
            return "say" in self.tts_engine_var.get().lower()

        def _tts_voice_var(self):
            return self.tts_say_voice_var if self._tts_is_say() else self.tts_voice_var

        def _tts_busy_reason(self) -> str:
            if getattr(self, "tts_running", False):
                return "Đang tạo audio — chờ xong."
            if self.auto_running:
                return "Pipeline tự động đang chạy."
            if self.srt_running:
                return "Đang chạy tạo SRT."
            if getattr(self, "img_running", False):
                return "Đang tạo ảnh."
            if self.rendering:
                return "Đang render video."
            return ""

        def _default_tts_output_path(self) -> Path:
            folder = default_output_folder()
            folder.mkdir(parents=True, exist_ok=True)
            return folder / ("audio_say.mp3" if self._tts_is_say() else "audio_adam.mp3")

        def _pick_tts_output(self):
            initial = self.tts_output_var.get().strip()
            initialdir = str(Path(initial).parent) if initial else str(default_output_folder())
            path = filedialog.asksaveasfilename(
                title="Lưu audio",
                initialdir=initialdir,
                defaultextension=".mp3",
                filetypes=[("MP3", "*.mp3"), ("All", "*.*")],
            )
            if path:
                self.tts_output_var.set(path)

        def _use_tts_audio_for_project(self):
            path = self.tts_output_var.get().strip()
            if not path or not Path(path).is_file():
                self._show_warning("Tạo audio", "Tạo audio trước, rồi mới gán cho Dự án.")
                return
            self.audio_var.set(path)
            if hasattr(self, "_sync_duration_from_audio"):
                self._sync_duration_from_audio()
            self._show_info("Tạo audio", f"Đã gán audio cho tab Dự án:\n{path}")

        def _open_tts_audio(self):
            path = (getattr(self, "last_tts_output", None) or self.tts_output_var.get() or "").strip()
            if not path:
                self._show_warning("Tạo audio", "Chưa có file audio để mở.")
                return
            self._open_path(path)

        def _load_tts_text_from_file(self):
            path = filedialog.askopenfilename(
                title="Mở script",
                filetypes=[("Text", "*.txt"), ("All", "*.*")],
            )
            if not path:
                return
            try:
                text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
            except OSError as err:
                self._show_error("Tạo audio", str(err))
                return
            self.tts_text_widget.delete("1.0", tk.END)
            self.tts_text_widget.insert("1.0", text)
            self.tts_output_var.set(str(Path(path).with_suffix(".mp3")))

        def _refresh_tts_status(self):
            if self._tts_is_say():
                ok = macos_say_available()
                msg = (
                    f"Sẵn sàng · say · {self.tts_say_voice_var.get() or DEFAULT_MACOS_SAY_VOICE}"
                    if ok
                    else "macOS say chỉ có trên macOS"
                )
            else:
                n = len(elevenlabs_api_keys())
                ok = n > 0
                msg = (
                    f"Sẵn sàng · {n} key ElevenLabs"
                    if ok
                    else f"Thiếu {ELEVENLABS_API_KEY_ENV} — tab API key / .env"
                )
            bg, fg = (C["ok_bg"], C["ok_fg"]) if ok else (C["warn_bg"], C["warn_fg"])
            self.tts_status_var.set(msg)
            if getattr(self, "_tts_status_inner", None) is not None:
                self._tts_status_inner.configure(bg=bg)
                self._tts_status_msg.configure(bg=bg, fg=fg)

        def _on_tts_engine_change(self, *_):
            combo = getattr(self, "_tts_voice_combo", None)
            if combo is None:
                return
            say = self._tts_is_say()
            if say:
                voices = list_macos_say_voice_names()
                if self.tts_say_voice_var.get() not in voices and voices:
                    pick = DEFAULT_MACOS_SAY_VOICE if DEFAULT_MACOS_SAY_VOICE in voices else voices[0]
                    self.tts_say_voice_var.set(pick)
                combo.configure(values=voices, textvariable=self.tts_say_voice_var)
                self._tts_enhance_check.pack_forget()
            else:
                if self.tts_voice_var.get() not in TTS_VOICE_OPTIONS:
                    self.tts_voice_var.set(DEFAULT_TTS_VOICE)
                combo.configure(values=TTS_VOICE_OPTIONS, textvariable=self.tts_voice_var)
                self._tts_enhance_check.pack(side=tk.LEFT, padx=(12, 0))
            self._refresh_tts_status()

        def _start_tts_generate(self):
            busy = self._tts_busy_reason()
            if busy:
                self._show_warning("Tạo audio", busy)
                return
            text = self.tts_text_widget.get("1.0", tk.END).strip()
            if not text:
                self._show_warning("Tạo audio", "Nhập văn bản cần đọc.")
                return
            use_say = self._tts_is_say()
            if use_say and not macos_say_available():
                self._show_warning("Tạo audio", "macOS say chỉ có trên macOS.")
                return
            if not use_say and not elevenlabs_api_keys():
                self._show_warning(
                    "Tạo audio",
                    f"Thiếu {ELEVENLABS_API_KEY_ENV} — tab API key hoặc .env.",
                )
                return

            out = self.tts_output_var.get().strip() or str(self._default_tts_output_path())
            self.tts_output_var.set(out)
            voice = self._tts_voice_var().get().strip()
            enhance = bool(self.tts_enhance_var.get())

            self.tts_running = True
            self._apply_footer_mode("tts")
            reset_progress_floor(self)
            self._set_progress(0, "TTS...")
            self._log(f"Tạo audio {'say' if use_say else 'Adam'} → {Path(out).name}", "info")

            def worker():
                err_msg = ""
                result_path: Path | None = None
                try:
                    if use_say:
                        result_path = synthesize_text_macos_say(
                            text, out, voice=voice or DEFAULT_MACOS_SAY_VOICE,
                            log_callback=self._log, progress_callback=self._set_progress,
                        )
                    else:
                        result_path = synthesize_text_elevenlabs(
                            text, out, voice=voice or DEFAULT_TTS_VOICE, enhance=enhance,
                            log_callback=self._log, progress_callback=self._set_progress,
                        )
                except (AutomationError, OSError) as err:
                    err_msg = str(err)
                except Exception as err:
                    err_msg = f"Lỗi TTS: {err}"

                def done():
                    self.tts_running = False
                    if err_msg:
                        self._set_progress(0, "Lỗi")
                        self._show_error("Tạo audio", err_msg)
                        self._log(err_msg, "error")
                    else:
                        self._set_progress(100, "Xong")
                        self.last_tts_output = str(result_path or out)
                        self._show_info(
                            "Tạo audio",
                            f"Đã tạo:\n{result_path}\n\n"
                            "Bấm «Dùng cho Dự án» để gắn vào File audio.",
                        )
                    self._refresh_tts_status()
                    if self._active_tab == "tts":
                        self._apply_footer_mode("tts")

                self._run_on_ui_thread(done)

            threading.Thread(target=worker, daemon=True).start()

        def _build_tts_tab(self, parent):
            lw = TTS_FIELD_LABEL_WIDTH
            parent.columnconfigure(1, weight=1)
            if not self.tts_output_var.get().strip():
                self.tts_output_var.set(str(self._default_tts_output_path()))

            text_panel, text_body = self._section_panel(parent, "Văn bản → audio")
            text_panel.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))
            text_body.columnconfigure(0, weight=1)

            head = ttk.Frame(text_body, style="Card.TFrame")
            head.grid(row=0, column=0, sticky="ew", pady=(0, 2))
            self._muted_label_with_help(head, "Nội dung", help_key="tts_text").pack(side=tk.LEFT)
            ttk.Button(
                head, text="Mở file .txt", command=self._load_tts_text_from_file,
                style="Small.TButton",
            ).pack(side=tk.RIGHT)

            wrap = tk.Frame(
                text_body, bg=C["entry_bg"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            wrap.grid(row=1, column=0, sticky="ew")
            wrap.columnconfigure(0, weight=1)
            self.tts_text_widget = tk.Text(
                wrap, height=5, wrap=tk.WORD, font=self._font(10),
                bg="#ffffff", fg=C["text"], relief=tk.FLAT,
                highlightthickness=0, padx=6, pady=4,
            )
            self.tts_text_widget.grid(row=0, column=0, sticky="ew")
            scroll = ttk.Scrollbar(wrap, command=self.tts_text_widget.yview)
            scroll.grid(row=0, column=1, sticky="ns")
            self.tts_text_widget.configure(yscrollcommand=scroll.set)

            opts_panel, opts_body = self._section_panel(parent, "Xuất & tùy chọn")
            opts_panel.grid(row=1, column=0, columnspan=3, sticky="ew")
            opts_body.columnconfigure(1, weight=1)

            self._path_field(
                opts_body, 0, "File xuất", self.tts_output_var, self._pick_tts_output,
                label_width=lw, help_key="tts_output",
            )

            self._grid_field_label(opts_body, 1, "Engine", "tts_engine", label_width=lw, col=0, pady=2)
            eng_row = ttk.Frame(opts_body, style="Card.TFrame")
            eng_row.grid(row=1, column=1, sticky="ew", pady=2)
            ttk.Combobox(
                eng_row, textvariable=self.tts_engine_var, values=list(TTS_ENGINE_OPTIONS),
                state="readonly", width=18,
            ).pack(side=tk.LEFT)
            self._tts_voice_combo = ttk.Combobox(
                eng_row, textvariable=self.tts_voice_var,
                values=TTS_VOICE_OPTIONS, state="normal", width=22,
            )
            self._tts_voice_combo.pack(side=tk.LEFT, padx=(8, 0))
            self._tts_enhance_check = ttk.Checkbutton(
                eng_row, text="Cảm xúc", variable=self.tts_enhance_var,
            )
            self._tts_enhance_check.pack(side=tk.LEFT, padx=(8, 0))

            btn_row = ttk.Frame(opts_body, style="Card.TFrame")
            btn_row.grid(row=2, column=1, sticky="ew", pady=(6, 0))
            self.tts_generate_btn = self._make_action_button(
                btn_row, "Tạo audio", self._start_tts_generate, kind="primary",
            )
            self.tts_generate_btn.pack(side=tk.LEFT)
            ttk.Button(
                btn_row, text="Dùng cho Dự án",
                command=self._use_tts_audio_for_project, style="Small.TButton",
            ).pack(side=tk.LEFT, padx=(8, 0))
            self._tts_status_inner = tk.Frame(btn_row, bg=C["entry_bg"])
            self._tts_status_inner.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))
            self._tts_status_msg = tk.Label(
                self._tts_status_inner, textvariable=self.tts_status_var,
                font=self._font(8), bg=C["entry_bg"], fg=C["muted"], anchor="w",
            )
            self._tts_status_msg.pack(fill=tk.X)

            if self.tts_engine_var.get() not in TTS_ENGINE_OPTIONS:
                self.tts_engine_var.set(
                    TTS_ENGINE_MACOS_SAY if self._tts_is_say() else DEFAULT_TTS_ENGINE,
                )
            self.tts_engine_var.trace_add("write", self._on_tts_engine_change)
            self._on_tts_engine_change()
