#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import threading

import tkinter as tk
from tkinter import ttk

from videobuilder.core.create_srt import (
    check_whisper,
    groq_api_key,
    groq_client_available,
    install_srt_packages,
    set_groq_api_key,
    srt_packages_status,
)
from videobuilder.core.env_config import GEMINI_API_KEY_ENV, GROQ_API_KEY_ENV
from videobuilder.core.generate_images import (
    GenerateImagesError,
    check_gemini_image,
    install_genai_package,
    set_gemini_api_key,
    verify_gemini_api_key,
)
from videobuilder.core.generate_prompts import check_prompt_llm
from videobuilder.core.groq_models import (
    groq_llm_chain_label,
    groq_whisper_chain_label,
    load_cached_groq_models,
)
from videobuilder.gui.constants import C

API_FIELD_LABEL_WIDTH = 11


class ApiTabMixin:
        def _api_key_block(
            self,
            parent,
            row: int,
            label: str,
            textvar,
            *,
            apply_cmd,
            clear_cmd,
            check_cmd,
            toggle_cmd,
            help_key=None,
        ):
            """Một hàng API key: nhãn + ô nhập + Hiện / Xóa / Kiểm tra."""
            if help_key:
                self._grid_field_label(
                    parent, row, label, help_key,
                    label_width=API_FIELD_LABEL_WIDTH, col=0, pady=4,
                )
            else:
                ttk.Label(
                    parent, text=label, style="Field.TLabel", width=API_FIELD_LABEL_WIDTH,
                ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)

            row_frame = ttk.Frame(parent, style="Card.TFrame")
            row_frame.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
            row_frame.columnconfigure(0, weight=1)

            inner = tk.Frame(
                row_frame, bg=C["entry_bg"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            inner.grid(row=0, column=0, sticky="ew", padx=(0, 6))
            inner.columnconfigure(0, weight=1)

            entry = tk.Entry(
                inner, textvariable=textvar,
                font=self._font(9), bg="#ffffff", fg=C["text"],
                relief=tk.FLAT, borderwidth=1, highlightthickness=1,
                highlightbackground=C["border"], highlightcolor=C["accent"],
                show="*",
            )
            entry.grid(row=0, column=0, sticky="ew", padx=6, pady=4)
            entry.bind("<FocusOut>", lambda _e: apply_cmd(silent=True))
            entry.bind("<Return>", lambda _e: apply_cmd())

            btns = ttk.Frame(row_frame, style="Card.TFrame")
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

        def _status_chip(self, parent, textvar, *, row: int) -> tk.Frame:
            inner = tk.Frame(
                parent, bg=C["entry_bg"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            inner.grid(row=row, column=1, columnspan=2, sticky="ew", pady=(0, 4))
            inner.columnconfigure(0, weight=1)
            msg = tk.Label(
                inner, textvariable=textvar,
                font=self._font(8), bg=C["entry_bg"], fg=C["muted"],
                anchor="w", justify=tk.LEFT,
            )
            msg.grid(row=0, column=0, sticky="ew", padx=6, pady=4)
            return inner

        def _apply_groq_api_key(self, *, silent: bool = False):
            set_groq_api_key(self.groq_api_key_var.get())
            self._refresh_groq_api_status()
            if self._srt_whisper_btn_frame:
                self._refresh_srt_engine_status()
            if not silent:
                self._save_settings()

        def _clear_groq_api_key(self):
            self.groq_api_key_var.set("")
            self._apply_groq_api_key()

        def _check_groq_api_key(self):
            if self.whisper_installing or self.srt_running:
                return
            self._apply_groq_api_key(silent=True)
            self._refresh_srt_engine_status()
            from videobuilder.core.create_srt import DEFAULT_MODEL

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
            if self.groq_key_entry:
                self.groq_key_entry.configure(
                    show="*" if self._groq_key_hidden else "",
                )
            if self.groq_key_toggle_btn:
                self.groq_key_toggle_btn.configure(
                    text="Hiện" if self._groq_key_hidden else "Ẩn",
                )

        def _apply_gemini_api_key(self, *, silent: bool = False):
            set_gemini_api_key(self.gemini_api_key_var.get())
            self._refresh_gemini_api_status()
            self._refresh_img_engine_status()
            if not silent:
                self._save_settings()

        def _clear_gemini_api_key(self):
            self.gemini_api_key_var.set("")
            self._apply_gemini_api_key()

        def _check_gemini_api_key(self):
            if self.img_running or self.img_installing:
                return
            self._apply_gemini_api_key(silent=True)
            ok, msg = verify_gemini_api_key()
            if ok:
                self._log(f"Gemini OK — {msg}", "success")
                self._show_info("Gemini", msg)
            else:
                self._log(msg, "warn")
                self._show_warning("Gemini", msg)

        def _toggle_gemini_key_visibility(self):
            self._gemini_key_hidden = not self._gemini_key_hidden
            if self.gemini_key_entry:
                self.gemini_key_entry.configure(
                    show="*" if self._gemini_key_hidden else "",
                )
            if self.gemini_key_toggle_btn:
                self.gemini_key_toggle_btn.configure(
                    text="Hiện" if self._gemini_key_hidden else "Ẩn",
                )

        def _refresh_groq_api_status(self):
            if not getattr(self, "_groq_api_inner", None):
                return
            load_cached_groq_models()
            llm = check_prompt_llm()
            key = self.groq_api_key_var.get().strip() or groq_api_key()
            if llm.get("ok"):
                msg = f"Groq LLM {groq_llm_chain_label()} ✓"
                bg, fg = C["ok_bg"], C["ok_fg"]
            elif key and not groq_client_available():
                msg = "Có key — cần cài gói Groq (bấm «Cài Groq»)"
                bg, fg = C["warn_bg"], C["warn_fg"]
            elif key:
                msg = "Có Groq key — kiểm tra tab Tạo SRT để xem STT"
                bg, fg = C["ok_bg"], C["ok_fg"]
            else:
                msg = f"Chưa có {GROQ_API_KEY_ENV}"
                bg, fg = C["warn_bg"], C["warn_fg"]
            self.groq_api_status_var.set(msg)
            self._groq_api_inner.configure(bg=bg)
            for child in self._groq_api_inner.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=bg, fg=fg)

        def _refresh_gemini_api_status(self):
            if not getattr(self, "_gemini_api_inner", None):
                return
            status = check_gemini_image(api_key=self.gemini_api_key_var.get())
            self.img_engine_ok = bool(status.get("ok"))
            self.img_engine_status_var.set(status.get("message", ""))
            needs_install = bool(status.get("needs_install"))
            if status.get("ok"):
                bg, fg = C["ok_bg"], C["ok_fg"]
                self.gemini_install_btn.pack_forget()
            elif needs_install and not self.img_installing:
                bg, fg = C["warn_bg"], C["warn_fg"]
                self.gemini_install_btn.pack(side=tk.LEFT, padx=(0, 2))
            else:
                bg, fg = C["warn_bg"], C["warn_fg"]
                self.gemini_install_btn.pack_forget()
            self.gemini_api_status_var.set(status.get("message", ""))
            self._gemini_api_inner.configure(bg=bg)
            for child in self._gemini_api_inner.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(bg=bg, fg=fg)
            self._update_img_controls_locked()

        def _refresh_api_tab_status(self):
            self._refresh_groq_api_status()
            self._refresh_gemini_api_status()

        def _install_groq_packages_from_api(self):
            if self.srt_running or self.whisper_installing:
                return
            self._install_srt_packages()

        def _install_gemini_package_from_api(self):
            if self.img_running or self.img_installing:
                return
            self._install_img_packages()

        def _build_api_tab(self, parent):
            parent.columnconfigure(1, weight=1)

            groq_panel, groq_body = self._section_panel(parent, "Groq — STT & LLM prompt")
            groq_panel.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
            groq_body.columnconfigure(1, weight=1)

            self._groq_key_hidden = True
            (
                self.groq_key_entry,
                self.groq_key_toggle_btn,
                _groq_check,
            ) = self._api_key_block(
                groq_body, 0, "Groq", self.groq_api_key_var,
                apply_cmd=self._apply_groq_api_key,
                clear_cmd=self._clear_groq_api_key,
                check_cmd=self._check_groq_api_key,
                toggle_cmd=self._toggle_groq_key_visibility,
                help_key="api_groq",
            )

            self._grid_field_label(
                groq_body, 1, "Trạng thái", "api_groq_status",
                label_width=API_FIELD_LABEL_WIDTH, col=0, pady=2,
            )
            self._groq_api_inner = self._status_chip(groq_body, self.groq_api_status_var, row=1)

            groq_btns = ttk.Frame(groq_body, style="Card.TFrame")
            groq_btns.grid(row=2, column=1, sticky="w", pady=(0, 2))
            ttk.Button(
                groq_btns, text="Cài Groq + Whisper",
                command=self._install_groq_packages_from_api, style="Small.TButton",
            ).pack(side=tk.LEFT)

            gemini_panel, gemini_body = self._section_panel(parent, "Gemini — tạo ảnh")
            gemini_panel.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))
            gemini_body.columnconfigure(1, weight=1)

            self._gemini_key_hidden = True
            (
                self.gemini_key_entry,
                self.gemini_key_toggle_btn,
                _gemini_check,
            ) = self._api_key_block(
                gemini_body, 0, "Gemini", self.gemini_api_key_var,
                apply_cmd=self._apply_gemini_api_key,
                clear_cmd=self._clear_gemini_api_key,
                check_cmd=self._check_gemini_api_key,
                toggle_cmd=self._toggle_gemini_key_visibility,
                help_key="api_gemini",
            )

            self._grid_field_label(
                gemini_body, 1, "Trạng thái", "api_gemini_status",
                label_width=API_FIELD_LABEL_WIDTH, col=0, pady=2,
            )
            self._gemini_api_inner = self._status_chip(gemini_body, self.gemini_api_status_var, row=1)

            gemini_btns = ttk.Frame(gemini_body, style="Card.TFrame")
            gemini_btns.grid(row=2, column=1, sticky="w", pady=(0, 2))
            self.gemini_install_btn = ttk.Button(
                gemini_btns, text="Cài google-genai",
                command=self._install_gemini_package_from_api, style="Small.TButton",
            )
            self.gemini_install_btn.pack(side=tk.LEFT)

            hint = tk.Label(
                parent,
                text="Key lưu trong cài đặt app hoặc file .env (GROQ_API_KEY, GEMINI_API_KEY).",
                font=self._font(8), bg=C["card"], fg=C["muted"], anchor="w", justify=tk.LEFT,
            )
            hint.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 0))

            self.groq_api_key_var.trace_add("write", lambda *_: self._refresh_groq_api_status())
            self.gemini_api_key_var.trace_add("write", lambda *_: self._refresh_gemini_api_status())
            self._refresh_api_tab_status()
