#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path

import tkinter as tk
from tkinter import ttk

from videobuilder.core.pipeline import (
    DEFAULT_2D_TRANSITION_DURATION,
    ENCODE_QUALITY_OPTIONS,
    ENCODER_OVERRIDE_OPTIONS,
    FPS_OPTIONS,
    ZOOM_LEVEL_OPTIONS,
)
from videobuilder.gui.constants import (
    C,
    EFFECT_UI_OPTIONS,
    RESOLUTION_UI_ORDER,
    STRIP_METADATA_UI,
    TAB_ITEMS,
)
from videobuilder.gui.progress import (
    CanvasProgressBar,
    DirectProgressTracker,
    ProgressColors,
    SmoothProgressTracker,
    StatusPresenter,
    short_render_status,
    short_srt_status,
)
from videobuilder.version import APP_VERSION


class ShellMixin:
        def _style_primary_button(self, button, enabled: bool):
            if enabled:
                button.configure(
                    bg="#dbeafe", fg="#1e3a8a", activebackground="#bfdbfe",
                    activeforeground="#1e3a8a", relief=tk.FLAT, cursor="hand2",
                )
            else:
                button.configure(
                    bg="#e2e8f0", fg="#64748b", activebackground="#e2e8f0",
                    activeforeground="#6b7280", relief=tk.FLAT, cursor="arrow",
                )

        def _make_action_button(self, parent, text, command, *, kind="primary", width=None):
            palette = {
                "primary": ("#dbeafe", "#1e3a8a", "#bfdbfe"),
                "soft": (C["accent_soft"], C["accent"], "#dbeafe"),
                "warn": ("#fef3c7", "#b45309", "#fde68a"),
                "danger": ("#fee2e2", "#b91c1c", "#fecaca"),
                "ghost": ("#f1f5f9", C["text"], "#e2e8f0"),
            }
            bg, fg, active = palette.get(kind, palette["ghost"])
            return tk.Button(
                parent, text=text, command=command, width=width,
                font=self._font(10 if kind == "primary" else 9, "bold" if kind in ("primary", "danger") else "normal"),
                bg=bg, fg=fg, activebackground=active, activeforeground=fg,
                disabledforeground="#94a3b8", relief=tk.FLAT, cursor="hand2",
                padx=14, pady=7, borderwidth=0,
            )

        def _apply_footer_mode(self, mode: str):
            """Chuyển footer giữa render (Dự án/Cài đặt) và SRT / Tạo ảnh."""
            prev = getattr(self, "_footer_mode", "render")
            is_srt = mode in ("srt", "auto", "image")
            was_srt = prev in ("srt", "auto", "image")
            self._footer_mode = mode
            light_srt_switch = is_srt and was_srt and prev != mode

            if not self._log_block.winfo_ismapped():
                self._log_block.pack(fill=tk.X)
            if getattr(self, "log_text", None) is not None:
                self.log_text.configure(height=5 if is_srt else 4)

            if not light_srt_switch:
                if is_srt:
                    self._render_bar.wrap.pack_forget()
                    self._srt_bar.wrap.pack(fill=tk.BOTH, expand=True)
                    self._footer_duration_label.pack_forget()
                    self._footer_percent_label.configure(textvariable=self.srt_percent_var)
                    self._footer_status_label.configure(textvariable=self.srt_status_var)
                else:
                    self._srt_bar.wrap.pack_forget()
                    self._render_bar.wrap.pack(fill=tk.BOTH, expand=True)
                    if not self._footer_duration_label.winfo_ismapped():
                        self._footer_duration_label.pack(side=tk.RIGHT, padx=(0, 4))
                    self._footer_percent_label.configure(textvariable=self.percent_var)
                    self._footer_status_label.configure(textvariable=self.status_var)

            self.render_btn.pack_forget()
            self.srt_create_btn.pack_forget()
            self.img_create_btn.pack_forget()
            self.preview_btn.pack_forget()
            self.pause_btn.pack_forget()
            self.cancel_btn.pack_forget()
            self.open_video_btn.pack_forget()
            self.open_folder_btn.pack_forget()
            self.open_prompts_btn.pack_forget()

            if mode == "srt":
                self.srt_create_btn.pack(side=tk.LEFT)
            elif mode == "image":
                self.img_create_btn.pack(side=tk.LEFT)
            elif mode == "auto":
                pass
            else:
                self.render_btn.pack(side=tk.LEFT)

            if mode != "auto" and mode != "image":
                self.preview_btn.pack(side=tk.LEFT, padx=(8, 0))
            self.pause_btn.pack(side=tk.LEFT, padx=(8, 0))
            self.cancel_btn.pack(side=tk.LEFT, padx=(4, 0))
            self.open_video_btn.pack(side=tk.LEFT, padx=(8, 0))

            if is_srt:
                self.open_prompts_btn.pack(side=tk.LEFT, padx=(4, 0))
                if mode == "image":
                    self.open_video_btn.configure(text="Thư mục ảnh", command=self._open_images_folder)
                    self._update_img_open_buttons()
                    self._update_img_controls_locked()
                else:
                    self.open_video_btn.configure(text="Mở srt", command=self._open_srt)
                    if mode == "srt":
                        self._update_srt_open_buttons()
                        self._update_srt_controls_locked()
                    elif mode == "auto":
                        self.after(0, self._update_srt_open_buttons)
            else:
                self.open_video_btn.configure(text="Mở video", command=self._open_video)
                enabled = bool(self.last_output and Path(self.last_output).is_file())
                self._set_open_buttons(enabled)
                if self.ffmpeg_ok and not self.rendering:
                    self.preview_btn.configure(state=tk.NORMAL)
                else:
                    self.preview_btn.configure(state=tk.DISABLED)

            self.open_folder_btn.pack(side=tk.LEFT, padx=(4, 0))

        def _show_tab(self, key: str):
            if key == getattr(self, "_active_tab", None):
                return
            self._active_tab = key
            for k, panel in self._tab_panels.items():
                panel.pack_forget()
            self._tab_panels[key].pack(fill=tk.BOTH, expand=True)
            self._render_footer.pack(side=tk.BOTTOM, fill=tk.X)
            if key in ("srt", "auto", "image"):
                self._apply_footer_mode(key)
                if key == "srt":
                    self.after(1, self._schedule_srt_tab_refresh)
                elif key == "auto":
                    self.after(0, self._on_auto_tab_shown)
                elif key == "image":
                    self._refresh_img_engine_status()
            elif key == "api":
                self._apply_footer_mode("render")
                self._refresh_api_tab_status()
            else:
                self._apply_footer_mode("render")
            track = C["tab_track"]
            active = C["tab_active"]
            for k, btn in self._tab_buttons.items():
                if k == key:
                    btn.configure(
                        bg=active, fg=C["accent"], font=self._font(9, "bold"),
                        highlightbackground=C["border"], relief=tk.FLAT,
                    )
                else:
                    btn.configure(
                        bg=track, fg=C["muted"], font=self._font(9),
                        highlightbackground=track, relief=tk.FLAT,
                    )

        def _add_segmented_tab(self, track_inner, content, key: str, title: str):
            shell = ttk.Frame(content, style="Card.TFrame")
            self._tab_panels[key] = shell

            btn = tk.Label(
                track_inner,
                text=title,
                font=self._font(9),
                bg=C["tab_track"],
                fg=C["muted"],
                padx=18,
                pady=8,
                cursor="hand2",
                highlightthickness=1,
                highlightbackground=C["tab_track"],
                borderwidth=0,
            )
            btn.pack(side=tk.LEFT, padx=2)
            btn.bind("<Button-1>", lambda _e, k=key: self._show_tab(k))

            def on_enter(_event, k=key, b=btn):
                if self._active_tab != k:
                    b.configure(fg=C["text"])

            def on_leave(_event, k=key, b=btn):
                if self._active_tab != k:
                    b.configure(fg=C["muted"])

            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)
            self._tab_buttons[key] = btn
            return shell

        def _build_tab_shell(self, parent):
            self._tab_buttons = {}
            self._tab_panels = {}
            self._active_tab = None

            wrap = tk.Frame(parent, bg=C["card"])
            wrap.pack(fill=tk.BOTH, expand=True)
            wrap.columnconfigure(0, weight=1)
            wrap.rowconfigure(2, weight=1)

            bar_row = tk.Frame(wrap, bg=C["card"])
            bar_row.grid(row=0, column=0, sticky="ew", pady=(0, 8))

            track = tk.Frame(bar_row, bg=C["tab_track"], highlightthickness=1, highlightbackground=C["border"])
            track.pack(anchor="w")

            track_inner = tk.Frame(track, bg=C["tab_track"])
            track_inner.pack(padx=4, pady=4)

            tk.Frame(wrap, bg=C["border"], height=1).grid(row=1, column=0, sticky="ew")

            content = tk.Frame(wrap, bg=C["card"])
            content.grid(row=2, column=0, sticky="nsew")

            shells = {}
            for key, title in TAB_ITEMS:
                shells[key] = self._add_segmented_tab(track_inner, content, key, title)

            self._show_tab("files")
            return shells

        def _build_ui(self):
            self._progress_colors = ProgressColors(
                trough=C["progress_trough"],
                bar=C["progress_bar"],
                border=C["border"],
            )
            self.header = tk.Frame(self, bg=C["header"], padx=18, pady=10)
            self.header.pack(fill=tk.X)

            tk.Label(
                self.header, text="VideoBuilder", font=self._font(16, "bold"),
                bg=C["header"], fg="#ffffff",
            ).pack(side=tk.LEFT)
            tk.Label(
                self.header, text="manhgdev", font=self._font(9, "bold"),
                bg=C["accent"], fg="#ffffff", padx=8, pady=2,
            ).pack(side=tk.LEFT, padx=(8, 0))
            tk.Label(
                self.header, text=f"v{APP_VERSION}", font=self._font(10),
                bg=C["header"], fg=C["header_sub"],
            ).pack(side=tk.LEFT, padx=(6, 0))
            ttk.Label(
                self.header, textvariable=self.encoder_info_var, style="HeaderSub.TLabel",
            ).pack(side=tk.RIGHT)

            self.ffmpeg_banner = tk.Frame(self, bg=C["warn_bg"], padx=8, pady=2)
            # Chỉ hiện khi thiếu FFmpeg hoặc đang cài — _refresh_ffmpeg_status() quyết định

            ffmpeg_inner = tk.Frame(self.ffmpeg_banner, bg=C["warn_bg"])
            ffmpeg_inner.pack(fill=tk.X)
            self.ffmpeg_inner = ffmpeg_inner

            self.ffmpeg_icon_label = tk.Label(
                ffmpeg_inner,
                text="!",
                font=self._font(8, "bold"),
                bg=C["warn_bg"],
                fg=C["warn_fg"],
                width=1,
            )
            self.ffmpeg_icon_label.pack(side=tk.LEFT)

            self.ffmpeg_msg_label = tk.Label(
                ffmpeg_inner,
                textvariable=self.ffmpeg_status_var,
                font=self._font(8),
                bg=C["warn_bg"],
                fg=C["warn_fg"],
                anchor="w",
            )
            self.ffmpeg_msg_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 6))

            self.ffmpeg_install_btn = tk.Button(
                ffmpeg_inner,
                text="Cài FFmpeg",
                font=self._font(7, "bold"),
                bg="#dbeafe",
                fg="#1e3a8a",
                activebackground="#bfdbfe",
                activeforeground="#1e3a8a",
                relief=tk.FLAT,
                cursor="hand2",
                padx=6,
                pady=0,
                command=self._start_ffmpeg_install,
            )
            self.ffmpeg_install_btn.pack_forget()

            self.ffmpeg_recheck_btn = ttk.Button(
                ffmpeg_inner,
                text="Kiểm tra",
                command=self._refresh_ffmpeg_status,
                style="Small.TButton",
                width=8,
            )

            content = tk.Frame(self, bg=C["card"])
            content.pack(fill=tk.BOTH, expand=True)

            self._render_footer = tk.Frame(
                content, bg=C["footer"], highlightbackground=C["border"], highlightthickness=1,
            )
            self._render_footer.pack(side=tk.BOTTOM, fill=tk.X)
            footer = self._render_footer

            body_outer = tk.Frame(content, bg=C["card"], padx=18, pady=12)
            body_outer.pack(fill=tk.BOTH, expand=True)

            prog_block = tk.Frame(footer, bg=C["footer"], padx=16, pady=8)
            prog_block.pack(fill=tk.X)

            log_block = tk.Frame(footer, bg=C["footer"])
            log_block.pack(fill=tk.X)
            self._log_block = log_block

            btn_block = tk.Frame(footer, bg=C["footer"], padx=16, pady=10)
            btn_block.pack(fill=tk.X)

            row_prog = tk.Frame(prog_block, bg=C["footer"])
            row_prog.pack(fill=tk.X)
            ttk.Label(row_prog, text="Tiến độ", style="Footer.TLabel", width=8).pack(side=tk.LEFT)
            self._prog_bar_slot = tk.Frame(row_prog, bg=C["footer"])
            self._prog_bar_slot.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
            self._render_bar = CanvasProgressBar(self._prog_bar_slot, self._progress_colors)
            self._render_bar.wrap.pack(fill=tk.BOTH, expand=True)
            self._render_status = StatusPresenter(self.status_var, short_render_status)
            self._render_tracker = SmoothProgressTracker(
                self, self._render_bar, self.percent_var, is_active=lambda: self.rendering,
            )
            self._srt_status = StatusPresenter(self.srt_status_var, short_srt_status)
            self._srt_bar = CanvasProgressBar(self._prog_bar_slot, self._progress_colors)
            self._srt_tracker = DirectProgressTracker(
                self._srt_bar, self.srt_percent_var, self._srt_status,
            )
            self._footer_duration_label = tk.Label(
                row_prog, textvariable=self.duration_var, font=self._font(10),
                bg=C["footer"], fg=C["muted"], width=9,
            )
            self._footer_duration_label.pack(side=tk.RIGHT, padx=(0, 4))
            self._footer_percent_label = tk.Label(
                row_prog, textvariable=self.percent_var, font=self._font(10, "bold"),
                bg=C["footer"], fg=C["accent"], width=6,
            )
            self._footer_percent_label.pack(side=tk.RIGHT)

            self._build_log_panel(log_block)

            row_btns = tk.Frame(btn_block, bg=C["footer"])
            row_btns.pack(fill=tk.X)
            self.render_btn = self._make_action_button(row_btns, "RENDER", self._start_render, kind="primary")
            self.render_btn.pack(side=tk.LEFT)
            self.preview_btn = self._make_action_button(row_btns, "Preview", self._start_preview, kind="soft")
            self.preview_btn.pack(side=tk.LEFT, padx=(8, 0))
            self.pause_btn = self._make_action_button(row_btns, "Tạm dừng", self._toggle_pause_render, kind="warn")
            self.pause_btn.configure(state=tk.DISABLED)
            self.pause_btn.pack(side=tk.LEFT, padx=(8, 0))
            self.cancel_btn = self._make_action_button(row_btns, "Hủy", self._cancel_render, kind="danger")
            self.cancel_btn.configure(state=tk.DISABLED)
            self.cancel_btn.pack(side=tk.LEFT, padx=(4, 0))
            self.open_video_btn = ttk.Button(row_btns, text="Mở video", command=self._open_video, state=tk.DISABLED)
            self.open_video_btn.pack(side=tk.LEFT, padx=(8, 0))
            self.open_folder_btn = ttk.Button(row_btns, text="Thư mục", command=self._open_folder, state=tk.DISABLED)
            self.open_folder_btn.pack(side=tk.LEFT, padx=(4, 0))
            self.open_prompts_btn = ttk.Button(
                row_btns, text="Mở tạo ảnh", command=self._open_prompts, state=tk.DISABLED,
            )
            self.srt_create_btn = self._make_action_button(row_btns, "Tạo SRT", self._start_create_srt, kind="primary")
            self.img_create_btn = self._make_action_button(
                row_btns, "Tạo ảnh", self._start_generate_images, kind="primary",
            )
            self._footer_status_label = tk.Label(
                row_btns, textvariable=self.status_var, font=self._font(10),
                bg=C["footer"], fg=C["muted"], width=24, anchor=tk.E,
            )
            self._footer_status_label.pack(side=tk.RIGHT, padx=(12, 0))
            self._footer_mode = "render"

            shells = self._build_tab_shell(body_outer)
            tab_files_shell = shells["files"]
            tab_opts_shell = shells["opts"]
            tab_auto_shell = shells["auto"]
            tab_srt_shell = shells["srt"]
            tab_image_shell = shells["image"]
            tab_api_shell = shells["api"]
            tab_contact_shell = shells["contact"]

            tab_files = self._build_scroll_area(tab_files_shell)
            tab_opts = self._build_scroll_area(tab_opts_shell)
            tab_auto_scroll = self._build_scroll_area(tab_auto_shell)
            tab_auto = ttk.Frame(tab_auto_scroll, style="Card.TFrame", padding=(14, 10))
            tab_auto.pack(fill=tk.BOTH, expand=True, anchor="nw")
            tab_srt = self._build_scroll_area(tab_srt_shell)
            tab_image_scroll = self._build_scroll_area(tab_image_shell)
            tab_image = ttk.Frame(tab_image_scroll, style="Card.TFrame", padding=(14, 10))
            tab_image.pack(fill=tk.BOTH, expand=True, anchor="nw")
            tab_api_scroll = self._build_scroll_area(tab_api_shell)
            tab_api = ttk.Frame(tab_api_scroll, style="Card.TFrame", padding=(14, 10))
            tab_api.pack(fill=tk.BOTH, expand=True, anchor="nw")
            tab_contact_scroll = self._build_scroll_area(tab_contact_shell)
            tab_contact = ttk.Frame(tab_contact_scroll, style="Card.TFrame", padding=18)
            tab_contact.pack(fill=tk.BOTH, expand=True, anchor="nw")
            self._build_contact_tab(tab_contact)
            self._build_auto_tab(tab_auto)
            self._build_srt_tab(tab_srt)
            self._build_image_tab(tab_image)
            self._build_api_tab(tab_api)
            self._sync_srt_model_hint()
            self.after(1, self._schedule_srt_tab_refresh)

            tab_files.columnconfigure(1, weight=1)
            self._path_field(tab_files, 0, "Thư mục ảnh", self.images_var, self._pick_images, help_key="images_dir")
            audio_entry = self._path_field(
                tab_files, 1, "File audio", self.audio_var, self._pick_audio,
                help_key="audio", on_clear=self._sync_duration_from_audio,
            )
            audio_entry.bind("<FocusOut>", lambda _e: self._sync_duration_from_audio())
            self._prompts_export_path_field(tab_files, 2)
            self._output_path_field(tab_files, 3)
            self._path_field(tab_files, 4, "File phụ đề", self.subtitle_var, self._pick_subtitle, help_key="subtitle")
            self._files_pair_entries(
                tab_files, 5,
                ("Cỡ chữ", self.subtitle_font_var, "subtitle_font", {"from": 0, "to": 72, "inc": 1}),
                ("Lệch (s)", self.subtitle_offset_var, "subtitle_offset", {"from": -30, "to": 30, "inc": 0.5}),
            )
            self._files_pair_entries(
                tab_files, 6,
                ("Lề dưới", self.subtitle_margin_var, "subtitle_margin", {"from": 0, "to": 120, "inc": 2}),
                ("Nền chữ", self.subtitle_outline_var, "subtitle_outline", {"from": 0, "to": 2, "inc": 1}),
            )

            tab_opts.columnconfigure(1, weight=1)
            tab_opts.columnconfigure(3, weight=1)

            self.effect_combo = self._opts_cell(
                tab_opts, 0, 0, "Hiệu ứng",
                lambda p: ttk.Combobox(
                    p, textvariable=self.effect_var,
                    values=[lbl for _, lbl in EFFECT_UI_OPTIONS], state="readonly",
                ),
                help_key="effect",
            )
            self.effect_combo.bind("<<ComboboxSelected>>", self._on_effect_changed)
            self._opts_cell(
                tab_opts, 0, 1, "Thời lượng (s)",
                lambda p: self._num_spinbox(p, self.transition_var, 0, 5, 0.05, width=8),
                help_key="transition",
            )

            self._opts_cell(
                tab_opts, 1, 0, "Độ phân giải",
                lambda p: ttk.Combobox(
                    p, textvariable=self.resolution_var,
                    values=[label for _, label in RESOLUTION_UI_ORDER], state="readonly",
                ),
                help_key="resolution",
            )
            self._opts_cell(
                tab_opts, 1, 1, "FPS",
                lambda p: ttk.Combobox(
                    p, textvariable=self.fps_var,
                    values=[str(f) for f in FPS_OPTIONS], state="readonly", width=8,
                ),
                help_key="fps",
            )

            self._opts_cell(
                tab_opts, 2, 0, "Chất lượng",
                lambda p: ttk.Combobox(
                    p, textvariable=self.quality_var,
                    values=list(ENCODE_QUALITY_OPTIONS.values()), state="readonly",
                ),
                help_key="quality",
            )
            self._opts_cell(
                tab_opts, 2, 1, "Zoom",
                lambda p: ttk.Combobox(
                    p, textvariable=self.zoom_var,
                    values=list(ZOOM_LEVEL_OPTIONS.values()), state="readonly",
                ),
                help_key="zoom",
            )

            self._opts_cell(
                tab_opts, 3, 0, "Encoder",
                lambda p: ttk.Combobox(
                    p, textvariable=self.encoder_var,
                    values=list(ENCODER_OVERRIDE_OPTIONS.values()), state="readonly",
                ),
                help_key="encoder",
            )
            self._opts_cell(
                tab_opts, 3, 1, "Speed (%)",
                lambda p: self._num_spinbox(p, self.speed_var, 25, 300, 5, width=8),
                help_key="speed",
            )

            self._opts_cell(
                tab_opts, 4, 0, "Preview (s)",
                lambda p: self._num_spinbox(p, self.preview_var, 5, 600, 5, width=8),
                help_key="preview",
            )
            self._opts_cell(
                tab_opts, 4, 1, "Âm lượng (%)",
                lambda p: self._num_spinbox(p, self.volume_var, 1, 300, 5, width=8),
                help_key="volume",
            )
            self._opts_cell(
                tab_opts, 5, 0, "Xóa metadata",
                lambda p: ttk.Combobox(
                    p, textvariable=self.strip_metadata_var,
                    values=list(STRIP_METADATA_UI), state="readonly", width=8,
                ),
                help_key="strip_metadata",
            )
            self._opts_cell(
                tab_opts, 5, 1, "Độ mờ logo (%)",
                lambda p: self._num_spinbox(p, self.watermark_opacity_var, 10, 100, 5, width=8),
                help_key="watermark_opacity",
            )

            ttk.Separator(tab_opts, orient=tk.HORIZONTAL).grid(
                row=6, column=0, columnspan=4, sticky="ew", pady=(6, 4),
            )
            self._opts_path_span(
                tab_opts, 7, "Watermark", self.watermark_var, self._pick_watermark, help_key="watermark",
            )
