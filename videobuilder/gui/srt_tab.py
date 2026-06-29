#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

from videobuilder.core.create_srt import (
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DEFAULT_SRT_SPLIT,
    SRT_SPLIT_OPTIONS,
    SRT_SPLIT_KEY_TO_LABEL,
    WHISPER_MODELS,
    CreateSrtError,
    CreateSrtCancelled,
    check_whisper,
    create_srt,
    default_srt_path,
    download_whisper_model,
    groq_api_key,
    groq_client_available,
    install_srt_packages,
    normalize_srt_split,
    resplit_srt,
    set_groq_api_key,
    srt_packages_status,
    whisper_model_cached,
    whisper_model_status_line,
)
from videobuilder.core.audio_pipeline import (
    AudioPipelineError,
    apply_env_api_keys,
    run_audio_pipeline,
    run_prompts_from_srt,
)
from videobuilder.core.generate_prompts import (
    check_prompt_llm,
    default_prompts_path,
)
from videobuilder.core.groq_models import (
    groq_llm_chain_label,
    groq_whisper_chain_label,
    load_cached_groq_models,
)
from videobuilder.core.pipeline import ProcessController
from videobuilder.core.ffmpeg_setup import check_ffmpeg
from videobuilder.core.pipeline import DEFAULT_PREVIEW_SECONDS
from videobuilder.gui.constants import C, SRT_FIELD_LABEL_WIDTH, SRT_LANGUAGE_OPTIONS
from videobuilder.gui.paths import default_output_folder, is_writable_output_dir


class SrtTabMixin:
        def _apply_groq_api_key(self, *, silent: bool = False):
            set_groq_api_key(self.srt_groq_api_key_var.get())
            if self._srt_whisper_btn_frame:
                self._refresh_srt_engine_status()
            if not silent:
                self._save_settings()

        def _clear_groq_api_key(self):
            self.srt_groq_api_key_var.set("")
            self._apply_groq_api_key()

        def _check_groq_api_key(self):
            if self.whisper_installing or self.srt_running:
                return
            self._apply_groq_api_key(silent=True)
            self._refresh_srt_engine_status()
            model = self.srt_model_var.get().strip() or DEFAULT_MODEL
            groq_lang = "" if (self.srt_language_var.get().strip() or "auto") == "auto" else self.srt_language_var.get().strip()
            status = check_whisper(model, language=groq_lang)
            msg = self._format_srt_engine_status(status, model)
            if status.get("groq"):
                self._log(f"Groq OK — {msg}", "success")
                self._show_info("Groq", msg)
            elif groq_api_key():
                self._log(msg, "warn")
                self._show_warning("Groq", msg)
            else:
                self._show_warning("Groq", "Chưa có API key Groq.")

        def _toggle_groq_key_visibility(self):
            self._groq_key_hidden = not self._groq_key_hidden
            if self.srt_groq_key_entry:
                self.srt_groq_key_entry.configure(
                    show="*" if self._groq_key_hidden else "",
                )
            if self.srt_groq_key_toggle_btn:
                self.srt_groq_key_toggle_btn.configure(
                    text="Hiện" if self._groq_key_hidden else "Ẩn",
                )

        def _api_key_block(
            self,
            parent,
            column: int,
            label: str,
            textvar,
            *,
            apply_cmd,
            clear_cmd,
            check_cmd,
            toggle_cmd,
        ):
            """Một cột API key: ô nhập + Hiện / Xóa / Kiểm tra (giống field xuất file)."""
            block = ttk.Frame(parent, style="Card.TFrame")
            block.grid(row=0, column=column, sticky="ew", padx=(0, 4) if column == 0 else (4, 0))
            block.columnconfigure(0, weight=1)
            block.columnconfigure(1, weight=0)

            inner = tk.Frame(
                block, bg=C["entry_bg"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            inner.grid(row=0, column=0, sticky="ew", padx=(0, 4))
            inner.columnconfigure(1, weight=1)

            tk.Label(
                inner, text=label, font=self._font(8),
                bg=C["entry_bg"], fg=C["muted"], width=5, anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=(6, 2), pady=3)

            entry = tk.Entry(
                inner, textvariable=textvar,
                font=self._font(9), bg="#ffffff", fg=C["text"],
                relief=tk.FLAT, borderwidth=1, highlightthickness=1,
                highlightbackground=C["border"], highlightcolor=C["accent"],
                show="*",
            )
            entry.grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=3)
            entry.bind("<FocusOut>", lambda _e: apply_cmd(silent=True))
            entry.bind("<Return>", lambda _e: apply_cmd())

            btns = ttk.Frame(block, style="Card.TFrame")
            btns.grid(row=0, column=1, sticky="e")
            toggle_btn = ttk.Button(
                btns, text="Hiện", command=toggle_cmd,
                style="Small.TButton", width=4,
            )
            toggle_btn.pack(side=tk.LEFT, padx=(0, 2))
            ttk.Button(
                btns, text="Xóa", command=clear_cmd,
                style="Small.TButton", width=4,
            ).pack(side=tk.LEFT, padx=(0, 2))
            check_btn = ttk.Button(
                btns, text="Kiểm tra", command=check_cmd,
                style="Small.TButton", width=9,
            )
            check_btn.pack(side=tk.LEFT)
            return entry, toggle_btn, check_btn

        def _srt_output_path_field(self, parent, row, label_width=SRT_FIELD_LABEL_WIDTH):
            return self._export_path_field(
                parent, row, "File SRT xuất", "srt_output",
                label_width=label_width,
                full_var=self.srt_output_var,
                dir_var=self.srt_output_dir_var,
                name_var=self.srt_output_name_var,
                suffix=".srt",
                pick_cmd=self._pick_srt_output,
                reset_cmd=self._reset_srt_output_path,
                apply_cmd=self._apply_srt_output_name,
            )

        def _srt_prompts_output_path_field(self, parent, row, label_width=SRT_FIELD_LABEL_WIDTH):
            return self._export_path_field(
                parent, row, "File tạo ảnh", "srt_prompts_output",
                label_width=label_width,
                full_var=self.srt_prompts_output_var,
                dir_var=self.srt_prompts_output_dir_var,
                name_var=self.srt_prompts_output_name_var,
                suffix=".txt",
                pick_cmd=self._pick_srt_prompts_output,
                reset_cmd=self._reset_srt_prompts_output_path,
                apply_cmd=self._apply_srt_prompts_output_name,
                enable_var=self.srt_gen_prompts_var,
            )

        def _mirror_prompts_export_from_srt(self):
            self.srt_prompts_output_dir_var.set(self.srt_output_dir_var.get())
            self.srt_prompts_output_name_var.set(self.srt_output_name_var.get())
            self._sync_srt_prompts_output_display()

        def _srt_engine_box(self, parent, row, label_width=SRT_FIELD_LABEL_WIDTH):
            box = tk.Frame(
                parent, bg=C["card"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            box.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(0, 6))
            box.columnconfigure(1, weight=1)

            self._grid_field_label(
                box, 0, "API key", "srt_whisper", label_width=label_width, col=0, pady=2,
            )
            keys_col = ttk.Frame(box, style="Card.TFrame")
            keys_col.grid(row=0, column=1, columnspan=2, sticky="ew", pady=2)
            keys_col.columnconfigure(0, weight=1)

            self._groq_key_hidden = True
            (
                self.srt_groq_key_entry,
                self.srt_groq_key_toggle_btn,
                self.srt_groq_recheck_btn,
            ) = self._api_key_block(
                keys_col, 0, "Groq", self.srt_groq_api_key_var,
                apply_cmd=self._apply_groq_api_key,
                clear_cmd=self._clear_groq_api_key,
                check_cmd=self._check_groq_api_key,
                toggle_cmd=self._toggle_groq_key_visibility,
            )

            status_row = ttk.Frame(keys_col, style="Card.TFrame")
            status_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
            status_row.columnconfigure(0, weight=1)
            status_row.columnconfigure(1, weight=0)

            self._srt_whisper_inner = tk.Frame(
                status_row, bg=C["entry_bg"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            self._srt_whisper_inner.grid(row=0, column=0, sticky="ew")
            self._srt_whisper_inner.columnconfigure(0, weight=1)
            self._srt_whisper_msg = tk.Label(
                self._srt_whisper_inner, textvariable=self.whisper_status_var,
                font=self._font(8), bg=C["entry_bg"], fg=C["muted"],
                anchor="w", justify=tk.LEFT,
            )
            self._srt_whisper_msg.grid(row=0, column=0, sticky="ew", padx=6, pady=3)

            self._srt_whisper_btn_frame = ttk.Frame(status_row, style="Card.TFrame")
            self._srt_whisper_btn_frame.grid(row=0, column=1, sticky="e", padx=(4, 0))
            self.srt_install_btn = ttk.Button(
                self._srt_whisper_btn_frame, text="Cài đặt",
                command=self._install_srt_packages, style="Small.TButton", width=7,
            )
            self.srt_recheck_btn = ttk.Button(
                self._srt_whisper_btn_frame, text="Tải model",
                command=self._install_fallback_model, style="Small.TButton", width=9,
            )

            self._grid_field_label(
                box, 1, "Whisper", "srt_model", label_width=label_width, col=0, pady=2,
            )
            fb_col = ttk.Frame(box, style="Card.TFrame")
            fb_col.grid(row=1, column=1, columnspan=2, sticky="ew", pady=2)
            fb_col.columnconfigure(0, weight=2)
            fb_col.columnconfigure(1, weight=1)
            fb_col.columnconfigure(2, weight=1)

            self.srt_model_combo = ttk.Combobox(
                fb_col, textvariable=self.srt_model_var,
                values=list(WHISPER_MODELS), state="readonly",
            )
            self.srt_model_combo.grid(row=0, column=0, sticky="ew", padx=(0, 6))
            self.srt_model_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_srt_model_hint())

            ttk.Combobox(
                fb_col, textvariable=self.srt_language_var,
                values=SRT_LANGUAGE_OPTIONS, state="readonly",
            ).grid(row=0, column=1, sticky="ew", padx=(0, 6))

            split_row = ttk.Frame(fb_col, style="Card.TFrame")
            split_row.grid(row=0, column=2, sticky="ew")
            split_row.columnconfigure(0, weight=1)
            split_row.columnconfigure(1, weight=0)
            ttk.Combobox(
                split_row,
                textvariable=self.srt_split_var,
                values=[label for _key, label in SRT_SPLIT_OPTIONS],
                state="readonly",
            ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
            ttk.Button(
                split_row, text="Áp dụng", command=self._apply_srt_split,
                style="Small.TButton", width=9,
            ).grid(row=0, column=1, sticky="e")

            tk.Label(
                fb_col, textvariable=self.srt_model_hint_var, font=self._font(8),
                bg=C["card"], fg=C["muted"], anchor="w",
            ).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 0))

            self.srt_model_var.trace_add("write", lambda *_: self._sync_srt_model_hint())
            self.srt_language_var.trace_add("write", lambda *_: self._refresh_srt_engine_status())

        def _format_srt_engine_status(self, status: dict, model: str) -> str:
            lang = self.srt_language_var.get().strip() or "auto"
            groq_lang = "" if lang == "auto" else lang
            load_cached_groq_models()
            groq_stt = groq_whisper_chain_label(groq_lang)
            llm_status = check_prompt_llm()
            llm_label = groq_llm_chain_label()
            if status.get("groq") and llm_status.get("llm"):
                groq = f"Groq STT {groq_stt} · LLM {llm_label} ✓"
            elif groq_api_key() and not groq_client_available():
                groq = "Groq: chưa cài — bấm Cài đặt"
            elif groq_api_key():
                groq = f"Groq STT {groq_stt} ✓ · LLM {llm_label}"
            else:
                groq = "Groq: chưa có API key"

            if status.get("local_ok"):
                if status.get("model_cached"):
                    fb = f"Whisper {model} ✓"
                elif status.get("model_cached") is False:
                    fb = f"Whisper {model} chưa tải"
                else:
                    fb = "Whisper ✓"
            else:
                fb = "Whisper chưa cài"
            return f"{groq} · {fb} · limit → Whisper"

        def _sync_srt_model_hint(self):
            model = self.srt_model_var.get().strip() or DEFAULT_MODEL
            self.srt_model_hint_var.set(whisper_model_status_line(model))
            self._refresh_srt_engine_status()

        def _get_srt_split_mode(self) -> str:
            return normalize_srt_split(self.srt_split_var.get())

        def _resolve_srt_file_for_split(self) -> Path | None:
            self._apply_srt_output_name()
            for candidate in (
                self.srt_output_var.get().strip(),
                self.subtitle_var.get().strip(),
            ):
                if candidate and Path(candidate).is_file():
                    return Path(candidate)
            return None

        def _apply_srt_split(self):
            if self.srt_running or self.rendering:
                return

            path = self._resolve_srt_file_for_split()
            if path is None:
                self._show_warning(
                    "Thiếu file SRT",
                    "Chọn file SRT xuất hoặc trỏ «File phụ đề» tab Dự án tới file .srt có sẵn.",
                )
                return

            mode = self._get_srt_split_mode()
            mode_label = SRT_SPLIT_KEY_TO_LABEL[mode]
            try:
                out, before, after = resplit_srt(
                    path,
                    split_mode=mode,
                    log_callback=self._log,
                )
            except (CreateSrtError, FileNotFoundError, OSError) as err:
                self._show_error("Ngắt câu", str(err))
                return
            self._finish_resplit(out, before, after, mode, mode_label)

        def _finish_resplit(
            self,
            out: Path,
            before: int,
            after: int,
            mode: str,
            mode_label: str,
        ):
            self.last_srt_output = str(out)
            if not self.subtitle_var.get().strip():
                self.subtitle_var.set(str(out))
            self._save_settings()
            self._update_srt_open_buttons()
            if self._srt_tracker is not None:
                self._srt_tracker.report(100.0)
            else:
                self.srt_percent_var.set("100%")
            self._update_srt_status(done=True)
            self._log(
                f"Ngắt câu ({mode_label}): {before} → {after} cue · {out.name}",
                "success",
            )
            if mode == "normal":
                self._log(
                    "Bình thường giữ nguyên file — không khôi phục segment gốc nhận dạng.",
                    "info",
                )
            elif before == after:
                self._log(
                    "Số cue không đổi — file có thể đã ở dạng này; vẫn có thể tạo lại file tạo ảnh.",
                    "warn",
                )

        def _build_srt_tab(self, parent):
            lw = SRT_FIELD_LABEL_WIDTH
            parent.columnconfigure(1, weight=1)

            self._srt_engine_box(parent, 0, lw)

            self._path_field(
                parent, 1, "File audio", self.srt_audio_var, self._pick_srt_audio,
                label_width=lw, help_key="srt_audio", on_clear=self._on_srt_audio_cleared,
            )

            use_row = ttk.Frame(parent, style="Card.TFrame")
            use_row.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 4))
            self.srt_use_project_btn = ttk.Button(
                use_row, text="Lấy audio tab Dự án",
                command=self._use_project_audio_for_srt, style="Small.TButton",
            )
            self.srt_use_project_btn.pack(side=tk.LEFT)

            self._srt_output_path_field(parent, 3, lw)
            self._srt_prompts_output_path_field(parent, 4, lw)

        def _sync_srt_prompts_output_display(self, from_output_var=False):
            self._sync_file_export_display(
                self.srt_prompts_output_var,
                self.srt_prompts_output_dir_var,
                self.srt_prompts_output_name_var,
                ".txt",
                from_output_var=from_output_var,
                audio_path=self.srt_audio_var.get().strip(),
            )

        def _apply_srt_prompts_output_name(self):
            self._sync_srt_prompts_output_display()

        def _reset_srt_prompts_output_path(self):
            audio = self.srt_audio_var.get().strip()
            if audio and Path(audio).is_file():
                self._sync_exports_from_audio(audio)
            else:
                self.srt_prompts_output_var.set("")
                self.srt_prompts_output_dir_var.set("")
                self.srt_prompts_output_name_var.set("subtitle")

        def _sync_srt_prompts_output_from_audio(self, audio_path: str):
            path = Path(audio_path)
            if path.is_file():
                self.srt_prompts_output_var.set(str(default_prompts_path(path)))
                self._sync_srt_prompts_output_display(from_output_var=True)

        def _pick_srt_prompts_output(self):
            saved = self.srt_prompts_output_var.get().strip()
            if saved:
                initial = Path(saved).parent
            else:
                audio = self.srt_audio_var.get().strip()
                initial = (
                    default_prompts_path(Path(audio)).parent
                    if audio and Path(audio).is_file()
                    else default_output_folder()
                )
            if not initial.exists():
                initial = default_output_folder()
            path = filedialog.askdirectory(
                parent=self,
                title="Chọn thư mục lưu file tạo ảnh",
                initialdir=str(initial),
            )
            if path:
                self.srt_prompts_output_dir_var.set(self._format_output_dir(path))
                self._sync_srt_prompts_output_display()
                self.srt_output_dir_var.set(self._format_output_dir(path))
                self._sync_srt_output_display()

        def _sync_srt_output_display(self, from_output_var=False):
            self._sync_file_export_display(
                self.srt_output_var,
                self.srt_output_dir_var,
                self.srt_output_name_var,
                ".srt",
                from_output_var=from_output_var,
                audio_path=self.srt_audio_var.get().strip(),
            )

        def _apply_srt_output_name(self):
            self._sync_srt_output_display()
            self._mirror_prompts_export_from_srt()

        def _reset_srt_output_path(self):
            audio = self.srt_audio_var.get().strip()
            if audio and Path(audio).is_file():
                self._sync_exports_from_audio(audio)
            else:
                self.srt_output_var.set("")
                self.srt_output_dir_var.set("")
                self.srt_output_name_var.set("subtitle")

        def _sync_exports_from_audio(self, audio_path: str):
            path = Path(audio_path)
            if not path.is_file():
                return
            self.srt_output_var.set(str(default_srt_path(path)))
            self.srt_prompts_output_var.set(str(default_prompts_path(path)))
            self._sync_srt_output_display(from_output_var=True)
            self._sync_srt_prompts_output_display(from_output_var=True)

        def _sync_srt_output_from_audio(self, audio_path: str):
            self._sync_exports_from_audio(audio_path)

        def _pick_srt_audio(self):
            path = filedialog.askopenfilename(
                parent=self,
                title="Chọn file audio",
                filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac"), ("Tất cả", "*.*")],
            )
            if path:
                self.srt_audio_var.set(path)
                self._sync_exports_from_audio(path)

        def _pick_srt_output(self):
            saved = self.srt_output_var.get().strip()
            if saved:
                initial = Path(saved).parent
            else:
                audio = self.srt_audio_var.get().strip()
                initial = (
                    default_srt_path(Path(audio)).parent
                    if audio and Path(audio).is_file()
                    else default_output_folder()
                )
            if not initial.exists():
                initial = default_output_folder()
            path = filedialog.askdirectory(
                parent=self,
                title="Chọn thư mục lưu file SRT",
                initialdir=str(initial),
            )
            if path:
                self.srt_output_dir_var.set(self._format_output_dir(path))
                self.srt_prompts_output_dir_var.set(self._format_output_dir(path))
                self._sync_srt_output_display()
                self._mirror_prompts_export_from_srt()

        def _update_srt_open_buttons(self):
            mode = getattr(self, "_footer_mode", "render")
            if mode not in ("srt", "auto"):
                return
            if mode == "auto":
                srt_target = (
                    self.last_srt_output
                    or self.subtitle_var.get().strip()
                    or self.srt_output_var.get().strip()
                )
                prompts_target = (
                    self.last_prompts_output
                    or self.prompts_var.get().strip()
                    or self.srt_prompts_output_var.get().strip()
                )
            else:
                srt_target = self.last_srt_output or self.srt_output_var.get().strip()
                prompts_target = self.last_prompts_output or self.srt_prompts_output_var.get().strip()
            srt_ok = bool(srt_target and Path(srt_target).is_file())
            prompts_ok = bool(prompts_target and Path(prompts_target).is_file())
            folder_ok = srt_ok or prompts_ok
            if mode == "auto" and not folder_ok:
                auto_dir = self.auto_output_dir_var.get().strip()
                folder_ok = bool(auto_dir and Path(auto_dir).is_dir())
            state_srt = tk.NORMAL if srt_ok else tk.DISABLED
            self.open_video_btn.configure(state=state_srt)
            self.open_folder_btn.configure(state=tk.NORMAL if folder_ok else tk.DISABLED)
            if hasattr(self, "open_prompts_btn"):
                self.open_prompts_btn.configure(state=tk.NORMAL if prompts_ok else tk.DISABLED)

        def _on_srt_audio_cleared(self):
            self.srt_output_var.set("")
            self.srt_output_dir_var.set("")
            self.srt_output_name_var.set("subtitle")
            self.srt_prompts_output_var.set("")
            self.srt_prompts_output_dir_var.set("")
            self.srt_prompts_output_name_var.set("subtitle")

        def _use_project_audio_for_srt(self):
            audio = self.audio_var.get().strip()
            if not audio or not Path(audio).is_file():
                self._show_warning("Chưa có audio", "Chọn file audio ở tab Dự án trước.")
                return
            self.srt_audio_var.set(audio)
            self._sync_exports_from_audio(audio)

        def _ensure_srt_packages_auto(self):
            """Tự cài groq + faster-whisper lần đầu mở tab SRT."""
            if self.whisper_installing or getattr(self, "_srt_packages_auto_started", False):
                return
            if not srt_packages_status()["needs_install"]:
                return
            self._srt_packages_auto_started = True
            self._install_srt_packages()

        def _refresh_srt_engine_status(self):
            if not self._srt_whisper_btn_frame:
                return
            model = self.srt_model_var.get().strip() or DEFAULT_MODEL
            groq_lang = "" if (self.srt_language_var.get().strip() or "auto") == "auto" else self.srt_language_var.get().strip()
            status = check_whisper(model, language=groq_lang)
            self.whisper_ok = status["ok"]
            self.whisper_status_var.set(self._format_srt_engine_status(status, model))

            groq_ready = bool(status.get("groq"))
            local_ok = bool(status.get("local_ok"))
            model_cached = status.get("model_cached")
            needs_install = bool(status.get("needs_install"))

            if needs_install:
                bg, fg = C["warn_bg"], C["warn_fg"]
                if not self.whisper_installing:
                    self.srt_install_btn.pack(side=tk.LEFT, padx=(0, 2))
                    self.srt_recheck_btn.pack_forget()
            elif not status["ok"]:
                bg, fg = C["warn_bg"], C["warn_fg"]
                if not self.whisper_installing:
                    self.srt_install_btn.pack(side=tk.LEFT, padx=(0, 2))
                    self.srt_recheck_btn.pack_forget()
            elif local_ok and model_cached is False:
                bg, fg = (C["ok_bg"], C["ok_fg"]) if groq_ready else (C["warn_bg"], C["warn_fg"])
                self.srt_install_btn.pack_forget()
                if not self.whisper_installing:
                    self.srt_recheck_btn.pack(side=tk.LEFT)
                    self.srt_recheck_btn.configure(
                        text="Tải model",
                        command=self._install_fallback_model,
                        width=9,
                    )
            elif groq_ready or local_ok:
                bg, fg = C["ok_bg"], C["ok_fg"]
                self.srt_install_btn.pack_forget()
                if not self.whisper_installing:
                    self.srt_recheck_btn.pack(side=tk.LEFT)
                    self.srt_recheck_btn.configure(
                        text="Kiểm tra",
                        command=self._refresh_srt_engine_status,
                        width=9,
                    )
            else:
                bg, fg = C["warn_bg"], C["warn_fg"]
                self.srt_install_btn.pack_forget()
                if not self.whisper_installing:
                    self.srt_recheck_btn.pack(side=tk.LEFT)
                    self.srt_recheck_btn.configure(
                        text="Kiểm tra",
                        command=self._refresh_srt_engine_status,
                        width=9,
                    )

            self._srt_whisper_inner.configure(bg=bg)
            self._srt_whisper_msg.configure(bg=bg, fg=fg)
            self._update_srt_controls_locked()

        def _refresh_whisper_status(self):
            self._refresh_srt_engine_status()

        def _update_srt_controls_locked(self):
            if not self.srt_create_btn:
                return
            if self.srt_running or self.whisper_installing:
                self.srt_create_btn.configure(state=tk.DISABLED)
                self._style_primary_button(self.srt_create_btn, False)
                self.preview_btn.configure(state=tk.DISABLED)
            elif self.whisper_ok:
                self.srt_create_btn.configure(state=tk.NORMAL)
                self._style_primary_button(self.srt_create_btn, True)
                self.preview_btn.configure(state=tk.NORMAL)
            else:
                self.srt_create_btn.configure(state=tk.DISABLED)
                self._style_primary_button(self.srt_create_btn, False)
                self.preview_btn.configure(state=tk.DISABLED)
            self._update_srt_control_buttons()

        def _update_srt_control_buttons(self):
            self._update_render_control_buttons()

        def _install_srt_packages(self):
            if self.srt_running or self.whisper_installing:
                return
            self.whisper_installing = True
            if self._srt_whisper_btn_frame:
                self.srt_install_btn.configure(state=tk.DISABLED, text="Đang cài...")
                self.srt_recheck_btn.pack_forget()
            self.whisper_status_var.set("Đang cài gói nhận dạng (Groq + Whisper)...")
            self._update_srt_status("Đang cài đặt...")
            self._log("Đang cài gói nhận dạng (Groq + Whisper)...", "info")
            self._set_srt_running(True)

            def worker():
                try:
                    install_srt_packages(log_callback=self._log)
                    msg = "Đã cài gói nhận dạng."
                    level = "success"
                except (CreateSrtError, subprocess.CalledProcessError) as err:
                    msg = str(err) if str(err) else "Không cài được gói nhận dạng."
                    level = "error"

                def done():
                    self.whisper_installing = False
                    self._set_srt_running(False)
                    if self._srt_whisper_btn_frame:
                        self.srt_install_btn.configure(state=tk.NORMAL, text="Cài đặt")
                    self._refresh_srt_engine_status()
                    if level == "success":
                        self._update_srt_status("Đã cài đặt")
                        self._show_info("Xong", msg)
                    else:
                        self._srt_status.reset_last()
                        self.srt_status_var.set("Lỗi cài đặt")
                        self._show_error("Cài đặt", msg)
                    self._log(msg, level)

                self.after(0, done)

            threading.Thread(target=worker, daemon=True).start()

        def _install_whisper(self):
            self._install_srt_packages()

        def _install_fallback_model(self):
            if self.srt_running or self.whisper_installing or self.rendering:
                return
            if not self.whisper_ok:
                self._show_warning(
                    "Chưa sẵn sàng",
                    "Nhập Groq API key hoặc bấm «Cài đặt».",
                )
                return

            model = self.srt_model_var.get().strip() or DEFAULT_MODEL
            if whisper_model_cached(model):
                self._refresh_srt_engine_status()
                return

            if self._srt_whisper_btn_frame:
                self.srt_recheck_btn.configure(state=tk.DISABLED, text="Đang tải...")
                self.srt_install_btn.pack_forget()
            self.whisper_status_var.set(f"Đang tải model Whisper {model}...")
            self._update_srt_status(f"Đang tải Whisper {model}...")
            self._log(f"——— Tải model Whisper: {model} ———", "info")
            self._set_srt_running(True)
            self._srt_tracker.reset(0.0)

            def worker():
                err_msg = None
                cancelled = False
                try:
                    download_whisper_model(
                        model,
                        log_callback=self._log,
                        progress_callback=self._set_srt_progress,
                        process_controller=self.process_controller,
                    )
                except CreateSrtCancelled:
                    cancelled = True
                except (CreateSrtError, ValueError) as err:
                    err_msg = str(err)
                except Exception as err:
                    err_msg = str(err)

                def done():
                    self._set_srt_running(False)
                    self.process_controller = None
                    if cancelled:
                        self.srt_percent_var.set("—")
                        self._srt_tracker.reset(0.0)
                        self._srt_status.reset_last()
                        self.srt_status_var.set("Đã hủy")
                        self._log("Đã hủy tải model.", "warn")
                    elif err_msg:
                        self.srt_percent_var.set("—")
                        self._srt_tracker.reset(0.0)
                        self._srt_status.reset_last()
                        self.srt_status_var.set("Lỗi tải model")
                        self._log(err_msg, "error")
                        self._show_error("Lỗi tải model", err_msg)
                    else:
                        self._srt_tracker.report(100.0)
                        self._update_srt_status(done=True)
                        self._show_info("Xong", f"Đã tải model Whisper {model}.")
                    self._sync_srt_model_hint()

                self.after(0, done)

            self.srt_paused = False
            self.process_controller = ProcessController()
            threading.Thread(target=worker, daemon=True).start()

        def _install_whisper_model(self):
            self._install_fallback_model()

        def _update_srt_status(self, message: str = "", *, done: bool = False):
            self._srt_status.update(message, done=done)

        def _set_srt_progress(self, pct, message):
            def update():
                self._srt_tracker.report(float(pct), message or "")

            self.after(0, update)

        def _set_srt_running(self, active: bool):
            self.srt_running = active
            state = tk.DISABLED if active else tk.NORMAL
            self.srt_use_project_btn.configure(state=state)
            if self._srt_whisper_btn_frame and not self.whisper_installing:
                self.srt_install_btn.configure(state=state)
                self.srt_recheck_btn.configure(state=state)
            self._update_srt_controls_locked()

        def _start_create_srt(self, preview=False):
            if self.srt_running or self.rendering:
                return

            audio = self.srt_audio_var.get().strip()
            model = self.srt_model_var.get().strip() or DEFAULT_MODEL
            language = self.srt_language_var.get().strip() or DEFAULT_LANGUAGE
            if language == "auto":
                language = ""

            if not audio or not Path(audio).is_file():
                self._show_warning("Thiếu audio", "Chọn file audio hợp lệ.")
                return
            if not self.whisper_ok:
                if srt_packages_status()["needs_install"] and not self.whisper_installing:
                    self._install_srt_packages()
                    return
                self._show_warning(
                    "Chưa sẵn sàng",
                    "Nhập Groq API key. Nếu thiếu gói — bấm «Cài đặt» (app tự cài khi mở tab).",
                )
                return

            preview_seconds = None
            if preview:
                if not check_ffmpeg()["ok"]:
                    self._show_warning(
                        "Thiếu FFmpeg",
                        "Preview SRT cần FFmpeg để cắt audio.\nBấm «Cài FFmpeg» trên thanh cảnh báo.",
                    )
                    self._refresh_ffmpeg_status()
                    return
                try:
                    preview_seconds = float(
                        self.preview_var.get().strip() or str(DEFAULT_PREVIEW_SECONDS)
                    )
                    if preview_seconds <= 0:
                        raise ValueError
                except ValueError:
                    self._show_warning("Preview", "Thời lượng preview phải là số > 0.")
                    return

            self._apply_groq_api_key(silent=True)
            apply_env_api_keys()
            self._apply_srt_output_name()
            self._apply_srt_prompts_output_name()
            output = self.srt_output_var.get().strip()
            gen_prompts = bool(self.srt_gen_prompts_var.get()) and not preview
            prompts_out = self.srt_prompts_output_var.get().strip()

            out_path = Path(output)
            if out_path.suffix.lower() != ".srt":
                out_path = out_path.with_suffix(".srt")
            if preview:
                stem = out_path.stem
                if not stem.endswith("_preview"):
                    out_path = out_path.with_name(f"{stem}_preview.srt")

            if not is_writable_output_dir(out_path.parent):
                self._show_error("Lỗi", f"Không ghi được vào:\n{out_path.parent}")
                return
            if gen_prompts:
                prompts_path = Path(prompts_out) if prompts_out else default_prompts_path(Path(audio))
                if prompts_path.suffix.lower() != ".txt":
                    prompts_path = prompts_path.with_suffix(".txt")
                if not is_writable_output_dir(prompts_path.parent):
                    self._show_error("Lỗi", f"Không ghi được file tạo ảnh vào:\n{prompts_path.parent}")
                    return
                if not groq_api_key():
                    self._show_warning(
                        "Thiếu Groq API key",
                        "Thêm GROQ_API_KEY vào .env hoặc nhập ô Groq trên tab Tạo SRT.",
                    )
                    return
                if not groq_client_available():
                    if srt_packages_status()["needs_install"] and not self.whisper_installing:
                        self._install_srt_packages()
                        return
                    self._show_warning("Thiếu gói", "Chưa cài groq — bấm «Cài đặt».")
                    return

            prompts_only_srt: Path | None = None

            if out_path.is_file() and not preview:
                mode = self._get_srt_split_mode()
                mode_label = SRT_SPLIT_KEY_TO_LABEL[mode]
                if mode != "normal":
                    self._log(
                        f"Đã có {out_path.name} — cập nhật ngắt câu, không nhận dạng lại audio.",
                        "info",
                    )
                    if gen_prompts:
                        self._log(
                            "Sau ngắt câu → tạo file tạo ảnh từ SRT (Groq LLM).",
                            "info",
                        )
                    else:
                        self._log(
                            "Muốn nhận dạng lại từ audio → xóa file SRT hoặc đổi tên ở «File SRT xuất».",
                            "info",
                        )
                    try:
                        out, before, after = resplit_srt(
                            out_path,
                            split_mode=mode,
                            log_callback=self._log,
                        )
                    except (CreateSrtError, FileNotFoundError, OSError) as err:
                        self._show_error("Ngắt câu", str(err))
                        return
                    self._finish_resplit(out, before, after, mode, mode_label)
                    if gen_prompts:
                        prompts_only_srt = out
                    else:
                        return
                elif not self._ask_yes_no(
                    "Đã có SRT",
                    f"File đã tồn tại:\n{out_path}\n\n"
                    "«Bình thường» giữ nguyên nội dung — chỉ có thể nhận dạng lại từ audio (lâu).\n\n"
                    "Chạy Groq / Whisper tạo lại?",
                ):
                    if gen_prompts and self._ask_yes_no(
                        "Đã có SRT",
                        f"Không nhận dạng lại audio.\n\n"
                        f"Tạo lại file tạo ảnh từ SRT hiện có?\n{out_path}",
                    ):
                        prompts_only_srt = out_path
                    else:
                        return

            self.srt_paused = False
            self.process_controller = ProcessController()
            self._set_srt_running(True)
            self._srt_tracker.reset(0.0)
            self._update_srt_status("Chuẩn bị...")
            self._log("——— Preview SRT ———" if preview else "——— Tạo SRT ———", "info")
            if gen_prompts and prompts_only_srt is not None:
                self._log(
                    f"Tạo file tạo ảnh từ SRT → {prompts_path.name} (Groq LLM)",
                    "info",
                )
            elif gen_prompts:
                self._log("Pipeline: Groq STT → SRT → Groq LLM visual beat → file tạo ảnh", "info")
            self._save_settings()

            def worker():
                err_msg = None
                result_path = None
                prompts_result = None
                cancelled = False
                try:
                    if prompts_only_srt is not None:
                        prompts_result = run_prompts_from_srt(
                            prompts_only_srt,
                            prompts_path,
                            progress_callback=self._set_srt_progress,
                            log_callback=self._log,
                        )
                        result_path = prompts_only_srt
                    elif gen_prompts:
                        result_path, prompts_result = run_audio_pipeline(
                            audio,
                            srt_output=out_path,
                            prompts_output=prompts_path,
                            language=language,
                            split_mode=self._get_srt_split_mode(),
                            generate_prompts=True,
                            progress_callback=self._set_srt_progress,
                            log_callback=self._log,
                            process_controller=self.process_controller,
                            preview_seconds=preview_seconds,
                        )
                    else:
                        result_path = create_srt(
                            audio,
                            out_path,
                            model=model,
                            language=language,
                            split_mode=self._get_srt_split_mode(),
                            progress_callback=self._set_srt_progress,
                            log_callback=self._log,
                            process_controller=self.process_controller,
                            preview_seconds=preview_seconds,
                        )
                except CreateSrtCancelled:
                    cancelled = True
                except (CreateSrtError, AudioPipelineError) as err:
                    err_msg = str(err)
                except Exception as err:
                    err_msg = str(err)

                def done():
                    self._set_srt_running(False)
                    self.process_controller = None
                    self.srt_paused = False
                    if cancelled:
                        self.srt_percent_var.set("—")
                        self._srt_tracker.reset(0.0)
                        self._srt_status.reset_last()
                        self.srt_status_var.set("Đã hủy")
                        self._log("Đã hủy tạo SRT.", "warn")
                        return
                    if err_msg:
                        self.srt_percent_var.set("—")
                        self._srt_tracker.reset(0.0)
                        self._srt_status.reset_last()
                        self.srt_status_var.set("Lỗi")
                        self._log(err_msg, "error")
                        self._show_error(
                            "Lỗi tạo timeline" if "SRT đã xong" in err_msg else "Lỗi tạo SRT",
                            err_msg,
                        )
                    else:
                        self.last_srt_output = str(result_path)
                        self.srt_output_var.set(str(result_path))
                        self._sync_srt_output_display(from_output_var=True)
                        if prompts_result:
                            self.last_prompts_output = str(prompts_result)
                            self.srt_prompts_output_var.set(str(prompts_result))
                            self._sync_srt_prompts_output_display(from_output_var=True)
                            self._update_srt_open_buttons()
                        self._srt_tracker.report(100.0)
                        self._update_srt_status(done=True)
                        self._sync_srt_model_hint()
                        self._update_srt_open_buttons()
                        title = "Preview xong" if preview else "Xong"
                        lines = [f"Đã tạo SRT:\n{result_path}"]
                        if prompts_result:
                            lines.append(f"\nFile tạo ảnh:\n{prompts_result}")
                        lines.append("\n\nGán làm phụ đề dự án?")
                        if self._ask_yes_no(title, "".join(lines)):
                            self.subtitle_var.set(str(result_path))
                        if prompts_result and self._ask_yes_no(
                            "File tạo ảnh",
                            f"Gán file tạo ảnh cho tab Dự án?\n{prompts_result}",
                        ):
                            self.prompts_var.set(str(prompts_result))
                            self._sync_prompts_display(from_output_var=True)
                        self._save_settings()

                self.after(0, done)

            threading.Thread(target=worker, daemon=True).start()

