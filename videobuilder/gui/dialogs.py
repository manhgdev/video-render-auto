#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import sys
from pathlib import Path

import tkinter as tk
from tkinter import ttk

from videobuilder.core.pipeline import MissingSceneImagesError, RenderCancelled
from videobuilder.gui.constants import C


class DialogMixin:
        def _dialog(self, title, message, kind="info", ask=False):
            win = tk.Toplevel(self)
            win.title(title)
            win.transient(self)
            win.grab_set()
            win.configure(bg=C["card"])

            accent = {
                "info": C["accent"],
                "warning": C["log_warn"],
                "error": C["log_error"],
            }.get(kind, C["accent"])

            outer = tk.Frame(win, bg=C["card"], padx=20, pady=16)
            outer.pack(fill=tk.BOTH, expand=True)

            tk.Label(
                outer, text=title, font=self._font(11, "bold"),
                bg=C["card"], fg=accent, anchor="w",
            ).pack(fill=tk.X)

            line_count = message.count("\n") + 1
            use_scroll = line_count >= 8 or len(message) > 480

            if use_scroll:
                win.resizable(True, True)
                win.minsize(460, 300)
                msg_frame = tk.Frame(outer, bg=C["card"])
                msg_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
                text_h = min(16, max(8, line_count))
                text = tk.Text(
                    msg_frame,
                    font=self._font(10),
                    bg="#f9fafb",
                    fg=C["text"],
                    wrap=tk.WORD,
                    width=54,
                    height=text_h,
                    relief=tk.FLAT,
                    highlightthickness=1,
                    highlightbackground=C["border"],
                    padx=10,
                    pady=8,
                )
                scroll = ttk.Scrollbar(msg_frame, orient=tk.VERTICAL, command=text.yview)
                text.configure(yscrollcommand=scroll.set)
                text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                scroll.pack(side=tk.RIGHT, fill=tk.Y)
                text.insert("1.0", message)
                text.configure(state=tk.DISABLED)
            else:
                win.resizable(False, False)
                tk.Label(
                    outer, text=message, font=self._font(10),
                    bg=C["card"], fg=C["text"], justify=tk.LEFT, wraplength=420, anchor="w",
                ).pack(fill=tk.X, pady=(8, 0))

            result = {"value": False}
            btn_row = tk.Frame(outer, bg=C["card"])
            btn_row.pack(fill=tk.X, pady=(14, 0))

            def close(value=False):
                result["value"] = value
                win.grab_release()
                win.destroy()

            if ask:
                tk.Button(
                    btn_row, text="Có", font=self._font(10, "bold"), bg=C["accent"], fg="#ffffff",
                    activebackground=C["accent_hover"], relief=tk.FLAT, padx=14, pady=5,
                    command=lambda: close(True),
                ).pack(side=tk.RIGHT)
                tk.Button(
                    btn_row, text="Không", font=self._font(10), bg="#f3f4f6", fg=C["text"],
                    activebackground="#e5e7eb", relief=tk.FLAT, padx=14, pady=5,
                    command=lambda: close(False),
                ).pack(side=tk.RIGHT, padx=(0, 8))
            else:
                tk.Button(
                    btn_row, text="OK", font=self._font(10, "bold"), bg=C["accent"], fg="#ffffff",
                    activebackground=C["accent_hover"], relief=tk.FLAT, padx=16, pady=5,
                    command=lambda: close(False),
                ).pack(side=tk.RIGHT)

            win.bind("<Escape>", lambda _e: close(False))
            win.protocol("WM_DELETE_WINDOW", lambda: close(False))
            self._center_on_parent(win)
            win.wait_window()
            return result["value"]

        def _read_text_file(self, path: Path) -> str:
            for encoding in ("utf-8-sig", "utf-8", "cp1258", "latin-1"):
                try:
                    return path.read_text(encoding=encoding)
                except UnicodeDecodeError:
                    continue
            raise ValueError(f"Không đọc được encoding: {path}")

        def _open_with_text_editor(self, path: Path):
            path = Path(path)
            if not path.is_file():
                self._show_warning("Không tìm thấy", f"Chưa có file:\n{path}")
                return
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["notepad.exe", str(path)], close_fds=True)
                else:
                    subprocess.Popen(["xdg-open", str(path)])
            except OSError as err:
                self._show_error("Lỗi", f"Không mở được trình soạn thảo:\n{err}")

        def _show_srt_viewer(self, path: str | Path):
            path = Path(path)
            if not path.is_file():
                self._show_warning("Không tìm thấy", f"Chưa có file:\n{path}")
                return
            try:
                content = self._read_text_file(path)
            except OSError as err:
                self._show_error("Lỗi đọc", str(err))
                return
            except ValueError:
                self._show_error("Lỗi đọc", "Không đọc được encoding của file SRT.")
                return

            win = tk.Toplevel(self)
            win.title(f"Xem SRT — {path.name}")
            win.transient(self)
            win.configure(bg=C["card"])
            win.resizable(True, True)
            win.minsize(520, 360)
            win.geometry("580x460")

            outer = tk.Frame(win, bg=C["card"], padx=16, pady=12)
            outer.pack(fill=tk.BOTH, expand=True)
            outer.rowconfigure(1, weight=1)
            outer.columnconfigure(0, weight=1)

            header = tk.Frame(outer, bg=C["card"])
            header.grid(row=0, column=0, sticky="ew")
            tk.Label(
                header, text=path.name, font=self._font(11, "bold"),
                bg=C["card"], fg=C["accent"], anchor="w",
            ).pack(side=tk.LEFT)
            tk.Label(
                header, text=str(path.parent), font=self._font(8),
                bg=C["card"], fg=C["muted"], anchor="w",
            ).pack(side=tk.LEFT, padx=(10, 0))

            msg_frame = tk.Frame(outer, bg=C["card"])
            msg_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
            msg_frame.rowconfigure(0, weight=1)
            msg_frame.columnconfigure(0, weight=1)

            text = tk.Text(
                msg_frame,
                font=self._font(10),
                bg="#f9fafb",
                fg=C["text"],
                wrap=tk.WORD,
                relief=tk.FLAT,
                highlightthickness=1,
                highlightbackground=C["border"],
                padx=10,
                pady=8,
            )
            scroll = ttk.Scrollbar(msg_frame, orient=tk.VERTICAL, command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            text.grid(row=0, column=0, sticky="nsew")
            scroll.grid(row=0, column=1, sticky="ns")
            text.insert("1.0", content)
            text.configure(state=tk.DISABLED)

            btn_row = tk.Frame(outer, bg=C["card"])
            btn_row.grid(row=2, column=0, sticky="e", pady=(12, 0))

            def close():
                win.destroy()

            editor_label = "Mở Notepad" if sys.platform == "win32" else "Mở app ngoài"
            tk.Button(
                btn_row, text=editor_label, font=self._font(10),
                bg="#f3f4f6", fg=C["text"], activebackground="#e5e7eb",
                relief=tk.FLAT, padx=12, pady=5,
                command=lambda: self._open_with_text_editor(path),
            ).pack(side=tk.RIGHT, padx=(8, 0))
            tk.Button(
                btn_row, text="Đóng", font=self._font(10, "bold"),
                bg=C["accent"], fg="#ffffff", activebackground=C["accent_hover"],
                relief=tk.FLAT, padx=16, pady=5, command=close,
            ).pack(side=tk.RIGHT)

            win.bind("<Escape>", lambda _e: close())
            win.protocol("WM_DELETE_WINDOW", close)
            self._center_on_parent(win)

        def _show_info(self, title, message):
            self._dialog(title, message, kind="info", ask=False)

        def _show_warning(self, title, message):
            self._dialog(title, message, kind="warning", ask=False)

        def _show_error(self, title, message):
            self._dialog(title, message, kind="error", ask=False)

        def _show_validation_error(self, err):
            if isinstance(err, MissingSceneImagesError):
                self._show_error("Thiếu ảnh", str(err))
            else:
                self._show_error("Lỗi", str(err))

        def _show_render_error(self, err):
            if isinstance(err, MissingSceneImagesError):
                self._show_error("Thiếu ảnh", str(err))
            elif isinstance(err, RenderCancelled):
                return
            else:
                self._show_error("Lỗi render", str(err))

        def _ask_yes_no(self, title, message, kind="info"):
            return self._dialog(title, message, kind=kind, ask=True)

