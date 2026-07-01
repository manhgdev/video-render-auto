#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from videobuilder.gui.constants import C, FIELD_HELP
from videobuilder.gui.paths import default_output_folder


class WidgetMixin:
        def _font(self, size=10, weight="normal"):
            key = (size, weight)
            if key not in self._fonts:
                family = "Helvetica Neue" if sys.platform == "darwin" else "Segoe UI"
                self._fonts[key] = tkfont.Font(family=family, size=size, weight=weight)
            return self._fonts[key]

        def _setup_theme(self):
            style = ttk.Style(self)
            style.theme_use("clam")

            style.configure(".", background=C["bg"], foreground=C["text"], font=self._font(10))
            style.configure("TFrame", background=C["bg"])
            style.configure("Card.TFrame", background=C["card"])
            style.configure("Card.TLabel", background=C["card"], foreground=C["text"], font=self._font(10))
            style.configure("Muted.TLabel", background=C["card"], foreground=C["muted"], font=self._font(9))
            style.configure("Field.TLabel", background=C["card"], foreground=C["text"], font=self._font(10))
            style.configure("Footer.TLabel", background=C["footer"], foreground=C["muted"], font=self._font(9))
            style.configure("HeaderSub.TLabel", background=C["header"], foreground=C["header_sub"], font=self._font(9))

            style.configure("TEntry", fieldbackground="#ffffff", foreground=C["text"], bordercolor=C["border"], padding=7)
            style.map(
                "TEntry",
                fieldbackground=[("readonly", "#ffffff"), ("disabled", "#f9fafb"), ("focus", "#ffffff")],
                foreground=[("disabled", C["muted"])],
                bordercolor=[("focus", C["accent"])],
            )
            style.configure(
                "TSpinbox",
                fieldbackground="#ffffff",
                foreground=C["text"],
                bordercolor=C["border"],
                arrowcolor=C["muted"],
                padding=7,
            )
            style.map(
                "TSpinbox",
                fieldbackground=[("disabled", "#f9fafb"), ("focus", "#ffffff")],
                foreground=[("disabled", C["muted"])],
                bordercolor=[("focus", C["accent"])],
            )
            style.configure(
                "TCombobox",
                fieldbackground="#ffffff",
                background="#ffffff",
                foreground=C["text"],
                bordercolor=C["border"],
                arrowcolor=C["muted"],
                padding=6,
            )
            style.map(
                "TCombobox",
                fieldbackground=[("readonly", "#ffffff"), ("disabled", "#f9fafb"), ("focus", "#ffffff")],
                foreground=[("disabled", C["muted"])],
                selectbackground=[("readonly", "#eef2ff")],
                selectforeground=[("readonly", C["text"])],
                bordercolor=[("focus", C["accent"])],
            )
            style.configure("TButton", padding=(10, 6), font=self._font(10))
            style.configure("Small.TButton", padding=(8, 4), font=self._font(9))
            style.configure("Ghost.TButton", padding=(8, 4), font=self._font(9))
            style.configure("Horizontal.TProgressbar", troughcolor=C["progress_trough"], background=C["progress_bar"], thickness=8)

        def _pill_button(self, parent, text, command, *, kind="primary"):
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
                font=self._font(9, "bold" if kind == "primary" else "normal"),
                padx=12,
                pady=5,
                cursor="hand2",
                anchor="center",
            )
            btn._pill_enabled = True
            btn._pill_bg = bg
            btn._pill_fg = fg
            btn._pill_hover = hover
            btn._pill_command = command

            def invoke(_event=None):
                if getattr(btn, "_pill_enabled", True):
                    command()

            def enter(_event=None):
                if getattr(btn, "_pill_enabled", True):
                    btn.configure(bg=hover)

            def leave(_event=None):
                if getattr(btn, "_pill_enabled", True):
                    btn.configure(bg=bg)

            btn.bind("<Button-1>", invoke)
            btn.bind("<Enter>", enter)
            btn.bind("<Leave>", leave)
            return btn

        def _section_panel(self, parent, title: str) -> tuple[tk.Frame, ttk.Frame]:
            wrap = tk.Frame(
                parent, bg=C["card"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            tk.Label(
                wrap, text=title, bg=C["accent_soft"], fg=C["accent"],
                font=self._font(9, "bold"), anchor="w", padx=8, pady=3,
            ).pack(fill=tk.X)
            body = ttk.Frame(wrap, style="Card.TFrame", padding=10)
            body.pack(fill=tk.BOTH, expand=True)
            return wrap, body

        def _native_checkbutton(self, parent, variable):
            """Checkbox hệ thống — dấu ✓, không dùng indicator X của ttk/clam."""
            return tk.Checkbutton(
                parent,
                variable=variable,
                bg=C["card"],
                activebackground=C["card"],
                selectcolor="#ffffff",
                highlightthickness=0,
                bd=0,
                padx=0,
                pady=0,
            )

        def _setup_widget_colors(self):
            """Tránh ô input/combobox bị nền đen trên Windows."""
            self.option_add("*TCombobox*Listbox.background", "#ffffff")
            self.option_add("*TCombobox*Listbox.foreground", C["text"])
            self.option_add("*TCombobox*Listbox.selectBackground", "#eef2ff")
            self.option_add("*TCombobox*Listbox.selectForeground", C["text"])
            self.option_add("*Entry*background", "#ffffff")
            self.option_add("*Entry*foreground", C["text"])
            self.option_add("*Entry*selectBackground", "#eef2ff")
            self.option_add("*Entry*selectForeground", C["text"])
            self.option_add("*Spinbox*background", "#ffffff")
            self.option_add("*Spinbox*foreground", C["text"])
            self.option_add("*Spinbox*selectBackground", "#eef2ff")
            self.option_add("*Spinbox*selectForeground", C["text"])

        def _bind_mousewheel(self, canvas, inner=None):
            if not hasattr(self, "_scroll_canvases"):
                self._scroll_canvases = []
            if canvas not in self._scroll_canvases:
                self._scroll_canvases.append(canvas)
            tag = f"ScrollableCanvas{len(self._scroll_canvases)}"
            canvas.configure(takefocus=1)
            canvas.bind("<Enter>", lambda _e: canvas.focus_set(), add="+")

            def can_widget_scroll(widget, direction):
                if not hasattr(widget, "yview") or widget is canvas:
                    return False
                try:
                    first, last = widget.yview()
                except tk.TclError:
                    return False
                if direction < 0:
                    return first > 0.0
                return last < 1.0

            def widget_chain(widget):
                while widget is not None:
                    yield widget
                    if widget is canvas:
                        break
                    try:
                        widget = widget.master
                    except AttributeError:
                        break

            def on_wheel(event):
                if not self._event_inside_widget(event, canvas):
                    return None
                if event.delta == 0:
                    return None
                direction = -1 if event.delta > 0 else 1
                units = int(-1 * (event.delta / 120))
                if units == 0:
                    units = direction
                canvas.yview_scroll(units * 3, "units")
                return "break"

            def on_button(event):
                if not self._event_inside_widget(event, canvas):
                    return None
                direction = -1 if event.num == 4 else 1
                canvas.yview_scroll(direction * 3, "units")
                return "break"

            def bind_tree(widget):
                try:
                    tags = widget.bindtags()
                    if tag not in tags:
                        widget.bindtags((tag, *tags))
                    widget.bind("<Enter>", lambda _e: canvas.focus_set(), add="+")
                    widget.bind("<Button-1>", lambda _e: canvas.focus_set(), add="+")
                except tk.TclError:
                    return
                for child in widget.winfo_children():
                    bind_tree(child)

            self.bind_class(tag, "<MouseWheel>", on_wheel, add="+")
            self.bind_class(tag, "<Option-MouseWheel>", on_wheel, add="+")
            self.bind_class(tag, "<Shift-MouseWheel>", on_wheel, add="+")
            self.bind_class(tag, "<Button-4>", on_button, add="+")
            self.bind_class(tag, "<Button-5>", on_button, add="+")

            bind_root = inner or canvas
            bind_tree(canvas)
            if bind_root is not canvas:
                bind_tree(bind_root)
            if inner is not None:
                inner.bind("<Configure>", lambda _event: bind_tree(inner), add="+")

            self._bind_scroll_drag(canvas, inner or canvas)

        def _bind_scroll_drag(self, canvas, root):
            drag = {"y": 0, "top": 0.0, "active": False}

            def can_start_drag(widget):
                blocked = ("Entry", "TEntry", "Text", "Listbox", "TCombobox", "TSpinbox", "Spinbox", "Button", "TButton")
                try:
                    cls = widget.winfo_class()
                except tk.TclError:
                    return False
                return cls not in blocked

            def on_press(event):
                if not self._event_inside_widget(event, canvas) or not can_start_drag(event.widget):
                    drag["active"] = False
                    return None
                drag["active"] = True
                drag["y"] = event.y_root
                drag["top"] = canvas.yview()[0]
                try:
                    canvas.configure(cursor="sb_v_double_arrow")
                except tk.TclError:
                    pass
                return None

            def on_motion(event):
                if not drag["active"]:
                    return None
                bbox = canvas.bbox("all")
                if not bbox:
                    return "break"
                content_h = max(1, bbox[3] - bbox[1])
                view_h = max(1, canvas.winfo_height())
                scrollable = max(1, content_h - view_h)
                delta = event.y_root - drag["y"]
                canvas.yview_moveto(min(max(drag["top"] - (delta / scrollable), 0.0), 1.0))
                return "break"

            def on_release(_event):
                drag["active"] = False
                try:
                    canvas.configure(cursor="")
                except tk.TclError:
                    pass
                return None

            def bind_drag_tree(widget):
                try:
                    widget.bind("<ButtonPress-1>", on_press, add="+")
                    widget.bind("<B1-Motion>", on_motion, add="+")
                    widget.bind("<ButtonRelease-1>", on_release, add="+")
                except tk.TclError:
                    return
                for child in widget.winfo_children():
                    bind_drag_tree(child)

            bind_drag_tree(root)
            root.bind("<Configure>", lambda _event: bind_drag_tree(root), add="+")

        def _event_inside_widget(self, event, widget):
            try:
                x = event.x_root - widget.winfo_rootx()
                y = event.y_root - widget.winfo_rooty()
                return 0 <= x < widget.winfo_width() and 0 <= y < widget.winfo_height()
            except tk.TclError:
                return False

        def _setup_global_mousewheel(self):
            if getattr(self, "_global_mousewheel_bound", False):
                return
            self._global_mousewheel_bound = True

            def canvas_under_pointer(event):
                for canvas in reversed(getattr(self, "_scroll_canvases", [])):
                    if self._event_inside_widget(event, canvas):
                        return canvas
                return None

            def wheel_units(delta):
                if delta == 0:
                    return 0
                units = int(-delta / 120)
                if units == 0:
                    units = -1 if delta > 0 else 1
                return units

            def on_wheel(event):
                canvas = canvas_under_pointer(event)
                if canvas is None:
                    return None
                units = wheel_units(event.delta)
                if units:
                    canvas.yview_scroll(units * 3, "units")
                    return "break"
                return None

            def on_button(event):
                canvas = canvas_under_pointer(event)
                if canvas is None:
                    return None
                canvas.yview_scroll((-1 if event.num == 4 else 1) * 3, "units")
                return "break"

            self.bind_all("<MouseWheel>", on_wheel, add="+")
            self.bind_all("<Option-MouseWheel>", on_wheel, add="+")
            self.bind_all("<Shift-MouseWheel>", on_wheel, add="+")
            self.bind_all("<Button-4>", on_button, add="+")
            self.bind_all("<Button-5>", on_button, add="+")

        def _setup_keyboard_scroll(self, canvas):
            def scroll_units(units):
                canvas.yview_scroll(units, "units")
                return "break"

            def scroll_pages(pages):
                canvas.yview_scroll(pages, "pages")
                return "break"

            canvas.bind("<Up>", lambda _e: scroll_units(-3), add="+")
            canvas.bind("<Down>", lambda _e: scroll_units(3), add="+")
            canvas.bind("<Prior>", lambda _e: scroll_pages(-1), add="+")
            canvas.bind("<Next>", lambda _e: scroll_pages(1), add="+")

        def _build_scroll_area(self, parent):
            parent.rowconfigure(0, weight=1)
            parent.columnconfigure(0, weight=1)

            canvas = tk.Canvas(parent, bg=C["card"], highlightthickness=0, borderwidth=0)
            scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
            canvas.grid(row=0, column=0, sticky="nsew")
            scrollbar.grid(row=0, column=1, sticky="ns")

            inner = ttk.Frame(canvas, style="Card.TFrame", padding=4)
            window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

            def sync_scroll(_event=None):
                canvas.configure(scrollregion=canvas.bbox("all"))

            def sync_width(event):
                canvas.itemconfigure(window_id, width=event.width)

            inner.bind("<Configure>", sync_scroll)
            canvas.bind("<Configure>", sync_width)
            canvas.configure(yscrollcommand=scrollbar.set)
            self._bind_mousewheel(canvas, inner)
            self._setup_global_mousewheel()
            self._setup_keyboard_scroll(canvas)
            return inner

        def _center_on_screen(self):
            self.update_idletasks()
            w = max(self.winfo_width(), self.winfo_reqwidth())
            h = max(self.winfo_height(), self.winfo_reqheight())
            x = max(0, (self.winfo_screenwidth() - w) // 2)
            y = max(0, (self.winfo_screenheight() - h) // 2)
            self.geometry(f"{w}x{h}+{x}+{y}")

        def _center_on_parent(self, window, parent=None):
            parent = parent or self
            parent.update_idletasks()
            window.update_idletasks()
            w = max(window.winfo_width(), window.winfo_reqwidth())
            h = max(window.winfo_height(), window.winfo_reqheight())
            x = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
            y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 2)
            window.geometry(f"+{x}+{y}")

        def _make_info_icon(self, parent, on_click):
            size = 16
            icon = tk.Canvas(
                parent, width=size, height=size, bg=C["card"],
                highlightthickness=0, cursor="hand2",
            )
            pad = 1
            icon.create_oval(
                pad, pad, size - pad, size - pad,
                outline=C["accent"], width=1.2, fill=C["accent_soft"],
            )
            icon.create_text(
                size // 2, size // 2 + 0.5, text="i",
                font=self._font(8, "bold"), fill=C["accent"],
            )
            icon.bind("<Button-1>", on_click)
            return icon

        def _muted_label_with_help(self, parent, text, help_key=None):
            frame = tk.Frame(parent, bg=C["card"])
            ttk.Label(frame, text=text, style="Muted.TLabel").pack(side=tk.LEFT)
            if help_key and help_key in FIELD_HELP:
                title, message = FIELD_HELP[help_key]
                info = self._make_info_icon(
                    frame, lambda _e, t=title, m=message: self._show_info(t, m),
                )
                info.pack(side=tk.LEFT, padx=(4, 0))
            return frame

        def _grid_field_label(self, parent, row, label, help_key=None, label_width=12, col=0, pady=4, padx=(0, 8)):
            frame = tk.Frame(parent, bg=C["card"])
            frame.grid(row=row, column=col, sticky="w", padx=padx, pady=pady)
            ttk.Label(frame, text=label, style="Field.TLabel", width=label_width).pack(side=tk.LEFT)
            if help_key and help_key in FIELD_HELP:
                title, message = FIELD_HELP[help_key]
                info = self._make_info_icon(
                    frame, lambda _e, t=title, m=message: self._show_info(t, m),
                )
                info.pack(side=tk.LEFT, padx=(4, 0))
            return frame

        def _num_spinbox(self, parent, variable, from_, to, increment=1, width=10):
            return ttk.Spinbox(
                parent,
                textvariable=variable,
                from_=from_,
                to=to,
                increment=increment,
                width=width,
                wrap=False,
            )

        def _field(self, parent, row, label, widget_factory, help_key=None, label_width=12):
            self._grid_field_label(parent, row, label, help_key, label_width)
            widget = widget_factory(parent)
            widget.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
            return widget

        def _path_pick_clear_buttons(self, parent, pick_cmd, variable, on_clear=None):
            btn_frame = ttk.Frame(parent, style="Card.TFrame")
            btn_frame.grid(row=0, column=1, sticky="e")

            def clear():
                variable.set("")
                if on_clear:
                    on_clear()

            ttk.Button(btn_frame, text="Chọn", command=pick_cmd, style="Small.TButton", width=5).pack(
                side=tk.LEFT, padx=(0, 4),
            )
            ttk.Button(btn_frame, text="Xóa", command=clear, style="Small.TButton", width=4).pack(side=tk.LEFT)
            return btn_frame

        def _path_field(self, parent, row, label, variable, command, label_width=12, help_key=None, on_clear=None):
            self._grid_field_label(parent, row, label, help_key, label_width)
            row_frame = ttk.Frame(parent, style="Card.TFrame")
            row_frame.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
            row_frame.columnconfigure(0, weight=1)
            entry = ttk.Entry(row_frame, textvariable=variable)
            entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
            self._path_pick_clear_buttons(row_frame, command, variable, on_clear)
            return entry

        def _sanitize_export_stem(self, name: str, *, fallback: str = "subtitle") -> str:
            text = (name or "").strip() or fallback
            text = Path(text.replace("\\", "/")).name
            for ext in (".srt", ".txt", ".mp4"):
                if text.lower().endswith(ext):
                    text = Path(text).stem
            for ch in '<>:"/\\|?*':
                text = text.replace(ch, "")
            return text.strip() or fallback

        def _build_export_file_path(
            self,
            folder: str,
            name: str,
            suffix: str,
            *,
            audio_path: str = "",
        ) -> str:
            folder_text = (folder or "").strip().rstrip("/\\")
            if not folder_text:
                audio = (audio_path or "").strip()
                if audio and Path(audio).is_file():
                    folder_text = str(Path(audio).parent)
                else:
                    folder_text = str(default_output_folder())
            stem = self._sanitize_export_stem(name)
            return str(Path(folder_text) / f"{stem}{suffix}")

        def _sync_file_export_display(
            self,
            full_var,
            dir_var,
            name_var,
            suffix: str,
            *,
            from_output_var=False,
            fallback_stem: str = "subtitle",
            audio_path: str = "",
        ):
            if from_output_var:
                saved = full_var.get().strip()
                if not saved:
                    dir_var.set("")
                    name_var.set(fallback_stem)
                    return
                path = Path(saved)
                dir_var.set(self._format_output_dir(str(path.parent)))
                name_var.set(path.stem)
            full = self._build_export_file_path(
                dir_var.get(), name_var.get(), suffix,
                audio_path=audio_path,
            )
            path = Path(full)
            full_var.set(str(path))
            dir_var.set(self._format_output_dir(str(path.parent)))
            name_var.set(path.stem)

        def _export_path_field(
            self,
            parent,
            row,
            label: str,
            help_key: str,
            *,
            full_var,
            dir_var,
            name_var,
            suffix: str,
            pick_cmd,
            reset_cmd,
            apply_cmd,
            label_width=12,
            enable_var=None,
        ):
            """Thư mục + tên file + đuôi — dùng cho SRT, prompt, v.v."""
            self._grid_field_label(parent, row, label, help_key, label_width=label_width)
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
                inner, textvariable=dir_var, anchor="w",
                font=self._font(9), bg=C["entry_bg"], fg=C["muted"],
            ).grid(row=0, column=0, sticky="w", padx=(8, 0), pady=4)

            name_entry = tk.Entry(
                inner, textvariable=name_var,
                font=self._font(9), bg="#ffffff", fg=C["text"],
                relief=tk.FLAT, borderwidth=1, highlightthickness=1,
                highlightbackground=C["border"], highlightcolor=C["accent"],
            )
            name_entry.grid(row=0, column=1, sticky="ew", padx=2, pady=3)

            tk.Label(
                inner, text=suffix, font=self._font(9),
                bg=C["entry_bg"], fg=C["muted"],
            ).grid(row=0, column=2, sticky="e", padx=(2, 8), pady=4)

            btn_frame = ttk.Frame(row_frame, style="Card.TFrame")
            btn_frame.grid(row=0, column=1, sticky="e")
            if enable_var is not None:
                self._native_checkbutton(btn_frame, enable_var).pack(side=tk.LEFT, padx=(0, 6))
            ttk.Button(
                btn_frame, text="Chọn", command=pick_cmd, style="Small.TButton", width=5,
            ).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Button(
                btn_frame, text="Xóa", command=reset_cmd, style="Small.TButton", width=4,
            ).pack(side=tk.LEFT)
            name_entry.bind("<FocusOut>", lambda _e: apply_cmd())
            return name_entry

        def _output_path_field(self, parent, row):
            name_entry = self._export_path_field(
                parent, row, "File xuất", "output",
                full_var=self.output_var,
                dir_var=self.output_dir_var,
                name_var=self.output_name_var,
                suffix=".mp4",
                pick_cmd=self._pick_output,
                reset_cmd=self._reset_output_path,
                apply_cmd=self._apply_output_name,
            )
            return name_entry

        def _prompts_export_path_field(self, parent, row, label_width=12):
            return self._export_path_field(
                parent, row, "File timeline", "prompts",
                label_width=label_width,
                full_var=self.prompts_var,
                dir_var=self.prompts_dir_var,
                name_var=self.prompts_name_var,
                suffix=".txt",
                pick_cmd=self._pick_prompts,
                reset_cmd=self._reset_prompts_path,
                apply_cmd=self._apply_prompts_name,
            )

        def _opts_cell(self, parent, row, col, label, widget_factory, help_key=None):
            """col=0: cột trái, col=1: cột phải — mỗi cột gồm label + widget."""
            base = col * 2
            self._grid_field_label(
                parent, row, label, help_key, label_width=13, col=base,
                pady=5, padx=(0, 6),
            )
            widget = widget_factory(parent)
            widget.grid(row=row, column=base + 1, sticky="ew", padx=(0, 16 if col == 0 else 0), pady=5)
            return widget

        def _files_pair_entries(self, parent, row, left, right):
            row_frame = ttk.Frame(parent, style="Card.TFrame")
            row_frame.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
            row_frame.columnconfigure(1, weight=1)
            row_frame.columnconfigure(3, weight=1)
            for col, spec in enumerate((left, right)):
                label, variable, help_key = spec[:3]
                spin = spec[3] if len(spec) > 3 else None
                base = col * 2
                self._grid_field_label(
                    row_frame, 0, label, help_key, label_width=14, col=base,
                    pady=0, padx=(0, 6) if col == 0 else (12, 6),
                )
                if spin:
                    widget = self._num_spinbox(
                        row_frame, variable, spin["from"], spin["to"], spin.get("inc", 1), width=10,
                    )
                else:
                    widget = ttk.Entry(row_frame, textvariable=variable, width=10)
                widget.grid(row=0, column=base + 1, sticky="ew", padx=(0, 8 if col == 0 else 0))

        def _opts_path_span(self, parent, row, label, variable, command, help_key=None, on_clear=None):
            self._grid_field_label(parent, row, label, help_key, label_width=13, col=0, pady=5, padx=(0, 6))
            row_frame = ttk.Frame(parent, style="Card.TFrame")
            row_frame.grid(row=row, column=1, columnspan=3, sticky="ew", pady=5)
            row_frame.columnconfigure(0, weight=1)
            entry = ttk.Entry(row_frame, textvariable=variable)
            entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
            self._path_pick_clear_buttons(row_frame, command, variable, on_clear)
            return entry

        def _path_field_span(self, parent, row, label, variable, command):
            ttk.Label(parent, text=label, style="Field.TLabel", width=13).grid(
                row=row, column=0, sticky="w", padx=(0, 6), pady=5,
            )
            row_frame = ttk.Frame(parent, style="Card.TFrame")
            row_frame.grid(row=row, column=1, columnspan=3, sticky="ew", pady=5)
            row_frame.columnconfigure(0, weight=1)
            entry = ttk.Entry(row_frame, textvariable=variable)
            entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
            ttk.Button(row_frame, text="Chọn", command=command, style="Small.TButton", width=6).grid(
                row=0, column=1, sticky="e",
            )
            return entry

        def _pair_row(self, parent, row, left_factory, right_factory):
            left = ttk.Frame(parent, style="Card.TFrame")
            left.grid(row=row, column=0, sticky="ew", padx=(0, 4), pady=2)
            right = ttk.Frame(parent, style="Card.TFrame")
            right.grid(row=row, column=1, sticky="ew", padx=(4, 0), pady=2)
            left.columnconfigure(1, weight=1)
            right.columnconfigure(1, weight=1)
            left_factory(left)
            right_factory(right)
