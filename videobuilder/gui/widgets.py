#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from videobuilder.gui.constants import C, FIELD_HELP


class WidgetMixin:
        def _font(self, size=10, weight="normal"):
            key = (size, weight)
            if key not in self._fonts:
                self._fonts[key] = tkfont.Font(family="Segoe UI", size=size, weight=weight)
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

            style.configure("TEntry", fieldbackground="#ffffff", foreground=C["text"], bordercolor=C["border"], padding=4)
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
                padding=4,
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
                padding=3,
            )
            style.map(
                "TCombobox",
                fieldbackground=[("readonly", "#ffffff"), ("disabled", "#f9fafb"), ("focus", "#ffffff")],
                foreground=[("disabled", C["muted"])],
                selectbackground=[("readonly", "#eef2ff")],
                selectforeground=[("readonly", C["text"])],
                bordercolor=[("focus", C["accent"])],
            )
            style.configure("TButton", padding=(10, 5), font=self._font(10))
            style.configure("Small.TButton", padding=(8, 3), font=self._font(9))
            style.configure("Ghost.TButton", padding=(8, 3), font=self._font(9))
            style.configure("Horizontal.TProgressbar", troughcolor=C["progress_trough"], background=C["progress_bar"], thickness=8)

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

        def _bind_mousewheel(self, canvas):
            def on_wheel(event):
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

            def on_enter(_event):
                canvas.bind_all("<MouseWheel>", on_wheel)

            def on_leave(_event):
                canvas.unbind_all("<MouseWheel>")

            canvas.bind("<Enter>", on_enter)
            canvas.bind("<Leave>", on_leave)

        def _build_scroll_area(self, parent):
            parent.rowconfigure(0, weight=1)
            parent.columnconfigure(0, weight=1)

            canvas = tk.Canvas(parent, bg=C["card"], highlightthickness=0, borderwidth=0)
            scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
            canvas.grid(row=0, column=0, sticky="nsew")
            scrollbar.grid(row=0, column=1, sticky="ns")

            inner = ttk.Frame(canvas, style="Card.TFrame", padding=6)
            window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

            def sync_scroll(_event=None):
                canvas.configure(scrollregion=canvas.bbox("all"))

            def sync_width(event):
                canvas.itemconfigure(window_id, width=event.width)

            inner.bind("<Configure>", sync_scroll)
            canvas.bind("<Configure>", sync_width)
            canvas.configure(yscrollcommand=scrollbar.set)
            self._bind_mousewheel(canvas)
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

        def _output_path_field(self, parent, row):
            self._grid_field_label(parent, row, "File xuất", "output")
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
                inner, textvariable=self.output_dir_var, anchor="w",
                font=self._font(9), bg=C["entry_bg"], fg=C["muted"],
            ).grid(row=0, column=0, sticky="w", padx=(8, 0), pady=4)

            name_entry = tk.Entry(
                inner, textvariable=self.output_name_var,
                font=self._font(9), bg="#ffffff", fg=C["text"],
                relief=tk.FLAT, borderwidth=1, highlightthickness=1,
                highlightbackground=C["border"], highlightcolor=C["accent"],
            )
            name_entry.grid(row=0, column=1, sticky="ew", padx=2, pady=3)

            tk.Label(
                inner, text=".mp4", font=self._font(9),
                bg=C["entry_bg"], fg=C["muted"],
            ).grid(row=0, column=2, sticky="e", padx=(2, 8), pady=4)

            btn_frame = ttk.Frame(row_frame, style="Card.TFrame")
            btn_frame.grid(row=0, column=1, sticky="e")
            ttk.Button(
                btn_frame, text="Chọn", command=self._pick_output, style="Small.TButton", width=5,
            ).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Button(
                btn_frame, text="Xóa", command=self._reset_output_path, style="Small.TButton", width=4,
            ).pack(side=tk.LEFT)
            name_entry.bind("<FocusOut>", lambda _e: self._apply_output_name())
            return name_entry

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


