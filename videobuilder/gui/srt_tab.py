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
    WHISPER_NUMPY_SPEC,
    CreateSrtError,
    CreateSrtCancelled,
    check_whisper,
    create_srt,
    default_srt_path,
    download_whisper_model,
    normalize_srt_split,
    resplit_srt,
    whisper_model_cached,
    whisper_model_status_line,
)
from videobuilder.core.pipeline import ProcessController
from videobuilder.core.ffmpeg_setup import check_ffmpeg
from videobuilder.core.pipeline import DEFAULT_PREVIEW_SECONDS
from videobuilder.gui.constants import C, SRT_FIELD_LABEL_WIDTH, SRT_LANGUAGE_OPTIONS
from videobuilder.gui.paths import is_writable_output_dir


class SrtTabMixin:
        def _srt_output_path_field(self, parent, row, label_width=SRT_FIELD_LABEL_WIDTH):
            self._grid_field_label(parent, row, "File SRT xuất", "srt_output", label_width=label_width)
            row_frame = ttk.Frame(parent, style="Card.TFrame")
            row_frame.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
            row_frame.columnconfigure(0, weight=1)

            inner = tk.Frame(
                row_frame, bg=C["entry_bg"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            inner.grid(row=0, column=0, sticky="ew", padx=(0, 6))
            inner.columnconfigure(1, weight=1)

            tk.Label(
                inner, textvariable=self.srt_output_dir_var, anchor="w",
                font=self._font(9), bg=C["entry_bg"], fg=C["muted"],
            ).grid(row=0, column=0, sticky="w", padx=(8, 0), pady=4)

            name_entry = tk.Entry(
                inner, textvariable=self.srt_output_name_var,
                font=self._font(9), bg="#ffffff", fg=C["text"],
                relief=tk.FLAT, borderwidth=1, highlightthickness=1,
                highlightbackground=C["border"], highlightcolor=C["accent"],
            )
            name_entry.grid(row=0, column=1, sticky="ew", padx=2, pady=3)

            tk.Label(
                inner, text=".srt", font=self._font(9),
                bg=C["entry_bg"], fg=C["muted"],
            ).grid(row=0, column=2, sticky="e", padx=(2, 8), pady=4)

            btn_frame = ttk.Frame(row_frame, style="Card.TFrame")
            btn_frame.grid(row=0, column=1, sticky="e")
            ttk.Button(
                btn_frame, text="Chọn", command=self._pick_srt_output, style="Small.TButton", width=5,
            ).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Button(
                btn_frame, text="Xóa", command=self._reset_srt_output_path, style="Small.TButton", width=4,
            ).pack(side=tk.LEFT)
            name_entry.bind("<FocusOut>", lambda _e: self._apply_srt_output_name())
            return name_entry

        def _srt_whisper_model_box(self, parent, row, label_width=SRT_FIELD_LABEL_WIDTH):
            box = tk.Frame(
                parent, bg=C["card"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            box.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(0, 8))
            box.columnconfigure(1, weight=1)

            self._grid_field_label(
                box, 0, "Whisper", "srt_whisper", label_width=label_width, col=0, pady=4,
            )
            whisper_row = ttk.Frame(box, style="Card.TFrame")
            whisper_row.grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)
            whisper_row.columnconfigure(0, weight=1)

            self._srt_whisper_inner = tk.Frame(
                whisper_row, bg=C["entry_bg"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            self._srt_whisper_inner.grid(row=0, column=0, sticky="ew", padx=(0, 6))
            self._srt_whisper_inner.columnconfigure(0, weight=1)
            self._srt_whisper_msg = tk.Label(
                self._srt_whisper_inner, textvariable=self.whisper_status_var,
                font=self._font(9), bg=C["entry_bg"], fg=C["muted"], anchor="w",
            )
            self._srt_whisper_msg.grid(row=0, column=0, sticky="ew", padx=8, pady=5)

            self._srt_whisper_btn_frame = ttk.Frame(whisper_row, style="Card.TFrame")
            self._srt_whisper_btn_frame.grid(row=0, column=1, sticky="e")
            self.srt_install_btn = ttk.Button(
                self._srt_whisper_btn_frame, text="Cài Whisper",
                command=self._install_whisper, style="Small.TButton", width=9,
            )
            self.srt_recheck_btn = ttk.Button(
                self._srt_whisper_btn_frame, text="Kiểm tra lại",
                command=self._refresh_whisper_status, style="Small.TButton", width=11,
            )

            self._grid_field_label(
                box, 1, "Model", "srt_model", label_width=label_width, col=0, pady=4,
            )
            ml_row = ttk.Frame(box, style="Card.TFrame")
            ml_row.grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
            ml_row.columnconfigure(0, weight=2)
            ml_row.columnconfigure(2, weight=1)
            ml_row.columnconfigure(4, weight=1)

            self.srt_model_combo = ttk.Combobox(
                ml_row, textvariable=self.srt_model_var,
                values=list(WHISPER_MODELS), state="readonly",
            )
            self.srt_model_combo.grid(row=0, column=0, sticky="ew", padx=(0, 10))
            self.srt_model_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_srt_model_hint())

            lang_label = self._grid_field_label(
                ml_row, 0, "Ngôn ngữ", "srt_language", label_width=11, col=1, pady=0, padx=(0, 6),
            )
            lang_label.grid_configure(sticky="e")

            ttk.Combobox(
                ml_row, textvariable=self.srt_language_var,
                values=SRT_LANGUAGE_OPTIONS, state="readonly",
            ).grid(row=0, column=2, sticky="ew", padx=(0, 10))

            split_label = self._grid_field_label(
                ml_row, 0, "Ngắt câu", "srt_split", label_width=9, col=3, pady=0, padx=(0, 6),
            )
            split_label.grid_configure(sticky="e")

            ttk.Combobox(
                ml_row,
                textvariable=self.srt_split_var,
                values=[label for _key, label in SRT_SPLIT_OPTIONS],
                state="readonly",
            ).grid(row=0, column=4, sticky="ew", padx=(0, 6))

            ttk.Button(
                ml_row,
                text="Áp dụng",
                command=self._apply_srt_split,
                style="Small.TButton",
                width=8,
            ).grid(row=0, column=5, sticky="e")

            self._grid_field_label(
                box, 2, "Chi tiết", "srt_model", label_width=label_width, col=0, pady=4,
            )
            detail = tk.Frame(
                box, bg=C["entry_bg"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            detail.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(2, 4))
            detail.columnconfigure(0, weight=1)

            tk.Label(
                detail, textvariable=self.srt_model_hint_var, font=self._font(9),
                bg=C["entry_bg"], fg=C["text"], anchor="w", justify=tk.LEFT, wraplength=520,
            ).grid(row=0, column=0, sticky="ew", padx=8, pady=(6, 2))

            tk.Label(
                detail,
                text="Model mới → bấm «Cài đặt» tải về một lần · lưu cache, lần sau không tải lại.",
                font=self._font(8), bg=C["entry_bg"], fg=C["muted"], anchor="w", justify=tk.LEFT,
                wraplength=520,
            ).grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 6))

            self.srt_model_var.trace_add("write", lambda *_: self._sync_srt_model_hint())

        def _sync_srt_model_hint(self):
            model = self.srt_model_var.get().strip() or DEFAULT_MODEL
            self.srt_model_hint_var.set(whisper_model_status_line(model))
            self._refresh_whisper_status()

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
                    "Bình thường giữ nguyên file — không khôi phục segment gốc Whisper.",
                    "info",
                )
            elif before == after:
                self._log(
                    "Số cue không đổi — file có thể đã ở dạng này, hoặc chọn chế độ khác.",
                    "warn",
                )

        def _build_srt_tab(self, parent):
            lw = SRT_FIELD_LABEL_WIDTH
            parent.columnconfigure(1, weight=1)

            self._srt_whisper_model_box(parent, 0, lw)

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

        def _update_srt_open_buttons(self):
            if self._footer_mode != "srt":
                return
            target = self.last_srt_output or self.srt_output_var.get().strip()
            self._set_open_buttons(bool(target and Path(target).is_file()))

        def _on_srt_audio_cleared(self):
            self.srt_output_var.set("")
            self.srt_output_dir_var.set("")
            self.srt_output_name_var.set("subtitle")

        def _sanitize_srt_output_name(self, name: str) -> str:
            text = (name or "").strip() or "subtitle"
            text = Path(text.replace("\\", "/")).name
            if text.lower().endswith(".srt"):
                text = Path(text).stem
            for ch in '<>:"/\\|?*':
                text = text.replace(ch, "")
            return text.strip() or "subtitle"

        def _build_srt_output_path(self, folder: str, name: str) -> str:
            folder_text = (folder or "").strip().rstrip("/\\")
            if not folder_text:
                audio = self.srt_audio_var.get().strip()
                if audio and Path(audio).is_file():
                    folder_text = str(Path(audio).parent)
                else:
                    folder_text = str(default_output_folder())
            stem = self._sanitize_srt_output_name(name)
            return str(Path(folder_text) / f"{stem}.srt")

        def _sync_srt_output_display(self, from_output_var=False):
            if from_output_var:
                saved = self.srt_output_var.get().strip()
                if not saved:
                    self.srt_output_dir_var.set("")
                    self.srt_output_name_var.set("subtitle")
                    return
                path = Path(saved)
                self.srt_output_dir_var.set(self._format_output_dir(str(path.parent)))
                self.srt_output_name_var.set(path.stem)
            full = self._build_srt_output_path(
                self.srt_output_dir_var.get(), self.srt_output_name_var.get(),
            )
            path = Path(full)
            self.srt_output_var.set(str(path))
            self.srt_output_dir_var.set(self._format_output_dir(str(path.parent)))
            self.srt_output_name_var.set(path.stem)

        def _apply_srt_output_name(self):
            self._sync_srt_output_display()

        def _reset_srt_output_path(self):
            audio = self.srt_audio_var.get().strip()
            if audio and Path(audio).is_file():
                self._sync_srt_output_from_audio(audio)
            else:
                self.srt_output_var.set("")
                self.srt_output_dir_var.set("")
                self.srt_output_name_var.set("subtitle")

        def _sync_srt_output_from_audio(self, audio_path: str):
            path = Path(audio_path)
            if path.is_file():
                self.srt_output_var.set(str(default_srt_path(path)))
                self._sync_srt_output_display(from_output_var=True)

        def _pick_srt_audio(self):
            path = filedialog.askopenfilename(
                parent=self,
                title="Chọn file audio",
                filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac"), ("Tất cả", "*.*")],
            )
            if path:
                self.srt_audio_var.set(path)
                if not self.srt_output_var.get().strip():
                    self._sync_srt_output_from_audio(path)

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
                self._sync_srt_output_display()

        def _use_project_audio_for_srt(self):
            audio = self.audio_var.get().strip()
            if not audio or not Path(audio).is_file():
                self._show_warning("Chưa có audio", "Chọn file audio ở tab Dự án trước.")
                return
            self.srt_audio_var.set(audio)
            self._sync_srt_output_from_audio(audio)

        def _refresh_whisper_status(self):
            if not self._srt_whisper_btn_frame:
                return
            model = self.srt_model_var.get().strip() or DEFAULT_MODEL
            status = check_whisper(model)
            self.whisper_ok = status["ok"]
            self.whisper_status_var.set(status["message"])

            if not status["ok"]:
                bg, fg = C["warn_bg"], C["warn_fg"]
                self.srt_recheck_btn.pack_forget()
                if not self.whisper_installing:
                    self.srt_install_btn.pack(side=tk.LEFT)
            elif status.get("model_cached") is False:
                bg, fg = C["warn_bg"], C["warn_fg"]
                self.srt_install_btn.pack_forget()
                if not self.whisper_installing:
                    self.srt_recheck_btn.pack(side=tk.LEFT)
                    self.srt_recheck_btn.configure(
                        text="Cài đặt",
                        command=self._install_whisper_model,
                        width=9,
                    )
            else:
                bg, fg = C["ok_bg"], C["ok_fg"]
                self.srt_install_btn.pack_forget()
                if not self.whisper_installing:
                    self.srt_recheck_btn.pack(side=tk.LEFT)
                    self.srt_recheck_btn.configure(
                        text="Kiểm tra lại",
                        command=self._refresh_whisper_status,
                        width=11,
                    )

            self._srt_whisper_inner.configure(bg=bg)
            self._srt_whisper_msg.configure(bg=bg, fg=fg)
            self._update_srt_controls_locked()

        def _update_srt_controls_locked(self):
            if not self.srt_create_btn:
                return
            if self.srt_running or self.whisper_installing:
                self.srt_create_btn.configure(state=tk.DISABLED)
                self.preview_btn.configure(state=tk.DISABLED)
            elif self.whisper_ok:
                self.srt_create_btn.configure(state=tk.NORMAL)
                self.preview_btn.configure(state=tk.NORMAL)
            else:
                self.srt_create_btn.configure(state=tk.DISABLED)
                self.preview_btn.configure(state=tk.DISABLED)
            self._update_srt_control_buttons()

        def _update_srt_control_buttons(self):
            self._update_render_control_buttons()

        def _install_whisper(self):
            if self.srt_running or self.whisper_installing:
                return
            self.whisper_installing = True
            if self._srt_whisper_btn_frame:
                self.srt_install_btn.configure(state=tk.DISABLED, text="Đang cài...")
                self.srt_recheck_btn.pack_forget()
            self.whisper_status_var.set("Đang cài faster-whisper — vui lòng chờ...")
            self._update_srt_status("Đang cài Whisper...")
            self._log(f'pip install "{WHISPER_NUMPY_SPEC}" faster-whisper', "info")
            self._set_srt_running(True)

            def worker():
                try:
                    subprocess.check_call(
                        [
                            sys.executable, "-m", "pip", "install",
                            WHISPER_NUMPY_SPEC, "faster-whisper",
                        ],
                    )
                    msg = "Đã cài faster-whisper."
                    level = "success"
                except subprocess.CalledProcessError as err:
                    msg = f"Không cài được faster-whisper (mã {err.returncode})."
                    level = "error"

                def done():
                    self.whisper_installing = False
                    self._set_srt_running(False)
                    if self._srt_whisper_btn_frame:
                        self.srt_install_btn.configure(state=tk.NORMAL, text="Cài Whisper")
                    self._refresh_whisper_status()
                    if level == "success":
                        self._update_srt_status("Đã cài Whisper")
                        self._show_info("Xong", msg)
                    else:
                        self._srt_status.reset_last()
                        self.srt_status_var.set("Lỗi cài Whisper")
                    self._log(msg, level)

                self.after(0, done)

            threading.Thread(target=worker, daemon=True).start()

        def _install_whisper_model(self):
            if self.srt_running or self.whisper_installing or self.rendering:
                return
            if not self.whisper_ok:
                self._show_warning("Chưa có Whisper", "Bấm «Cài Whisper» trước.")
                return

            model = self.srt_model_var.get().strip() or DEFAULT_MODEL
            if whisper_model_cached(model):
                self._refresh_whisper_status()
                return

            if self._srt_whisper_btn_frame:
                self.srt_recheck_btn.configure(state=tk.DISABLED, text="Đang tải...")
                self.srt_install_btn.pack_forget()
            self.whisper_status_var.set(f"Đang tải model {model}...")
            self._update_srt_status(f"Đang tải model {model}...")
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
                        self._show_info("Xong", f"Đã tải model {model}.")
                    self._sync_srt_model_hint()

                self.after(0, done)

            self.srt_paused = False
            self.process_controller = ProcessController()
            threading.Thread(target=worker, daemon=True).start()

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
                self._show_warning("Chưa có Whisper", "Bấm «Cài Whisper» trước khi tạo SRT.")
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

            self._apply_srt_output_name()
            output = self.srt_output_var.get().strip()

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

            if out_path.is_file() and not preview:
                mode = self._get_srt_split_mode()
                mode_label = SRT_SPLIT_KEY_TO_LABEL[mode]
                if mode != "normal":
                    self._log(
                        f"Đã có {out_path.name} — chỉ cập nhật ngắt câu, không nhận dạng lại audio.",
                        "info",
                    )
                    self._log(
                        "Muốn Whisper lại từ đầu → xóa file SRT hoặc đổi tên ở «File SRT xuất».",
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
                    return
                if not self._ask_yes_no(
                    "Đã có SRT",
                    f"File đã tồn tại:\n{out_path}\n\n"
                    "«Bình thường» giữ nguyên nội dung — chỉ có thể nhận dạng lại từ audio (lâu).\n\n"
                    "Chạy Whisper tạo lại?",
                ):
                    return

            self.srt_paused = False
            self.process_controller = ProcessController()
            self._set_srt_running(True)
            self._srt_tracker.reset(0.0)
            self._update_srt_status("Chuẩn bị...")
            self._log("——— Preview SRT ———" if preview else "——— Tạo SRT ———", "info")
            self._save_settings()

            def worker():
                err_msg = None
                result_path = None
                cancelled = False
                try:
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
                except CreateSrtError as err:
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
                        self._show_error("Lỗi tạo SRT", err_msg)
                    else:
                        self.last_srt_output = str(result_path)
                        self._srt_tracker.report(100.0)
                        self._update_srt_status(done=True)
                        self._sync_srt_model_hint()
                        self._update_srt_open_buttons()
                        title = "Preview xong" if preview else "Xong"
                        prompt = (
                            f"Đã tạo preview SRT:\n{result_path}\n\nGán làm phụ đề dự án?"
                            if preview
                            else f"Đã tạo SRT:\n{result_path}\n\nGán làm phụ đề dự án?"
                        )
                        if self._ask_yes_no(title, prompt):
                            self.subtitle_var.set(str(result_path))
                        self._save_settings()

                self.after(0, done)

            threading.Thread(target=worker, daemon=True).start()


