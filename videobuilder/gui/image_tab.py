#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

from videobuilder.core.create_srt import CreateSrtCancelled
from videobuilder.core.generate_images import (
    GenerateImagesError,
    apply_env_gemini_key,
    check_gemini_image,
    generate_images_from_prompts,
    resolve_aspect_ratio,
)
from videobuilder.core.pipeline import ProcessController, RenderCancelled
from videobuilder.core.progress import reset_progress_floor
from videobuilder.gui.constants import C
from videobuilder.gui.paths import default_output_folder, is_writable_output_dir


IMG_FIELD_LABEL_WIDTH = 13
IMG_ASPECT_LABELS = ("Tự động", "16:9", "9:16", "1:1")
IMG_ASPECT_LABEL_TO_KEY = {
    "Tự động": "auto",
    "16:9": "16:9",
    "9:16": "9:16",
    "1:1": "1:1",
}


class ImageTabMixin:
        def _img_busy_reason(self) -> str:
            if self.img_running:
                return "Đang tạo ảnh — chờ xong hoặc bấm Hủy."
            if self.auto_running:
                return "Pipeline tự động đang chạy."
            if self.srt_running:
                return "Đang chạy tạo SRT."
            if self.rendering:
                return "Đang render video."
            return ""

        def _pick_img_prompts(self):
            initial = self.img_prompts_var.get().strip()
            initialdir = (
                Path(initial).parent
                if initial and Path(initial).parent.exists()
                else default_output_folder()
            )
            path = filedialog.askopenfilename(
                parent=self,
                title="Chọn file prompt tạo ảnh",
                initialdir=str(initialdir),
                filetypes=[("Text", "*.txt"), ("Tất cả", "*.*")],
            )
            if path:
                self.img_prompts_var.set(path)
                self._save_settings()

        def _pick_img_output_dir(self):
            current = self.img_output_dir_var.get().strip()
            initialdir = Path(current) if current and Path(current).exists() else default_output_folder()
            path = filedialog.askdirectory(
                parent=self,
                title="Chọn thư mục lưu ảnh scene",
                initialdir=str(initialdir),
            )
            if path:
                self.img_output_dir_var.set(self._format_output_dir(path))
                self._save_settings()

        def _use_project_paths_for_image(self):
            prompts = self.prompts_var.get().strip()
            images = self.images_var.get().strip()
            if prompts and Path(prompts).is_file():
                self.img_prompts_var.set(prompts)
            if images and Path(images).is_dir():
                self.img_output_dir_var.set(self._format_output_dir(images))
            elif images:
                self.img_output_dir_var.set(self._format_output_dir(images))
            self._save_settings()
            if not prompts and not images:
                self._show_warning("Chưa có dữ liệu", "Chọn file prompt và thư mục ảnh ở tab Dự án trước.")

        def _get_img_aspect_key(self) -> str:
            label = self.img_aspect_var.get().strip()
            return IMG_ASPECT_LABEL_TO_KEY.get(label, "auto")

        def _refresh_img_engine_status(self):
            status = check_gemini_image(api_key=self.gemini_api_key_var.get())
            self.img_engine_ok = bool(status.get("ok"))
            self.img_engine_status_var.set(status.get("message", ""))
            if getattr(self, "_img_status_inner", None):
                bg = C["ok_bg"] if self.img_engine_ok else C["warn_bg"]
                fg = C["ok_fg"] if self.img_engine_ok else C["warn_fg"]
                self._img_status_inner.configure(bg=bg)
                self._img_status_msg.configure(bg=bg, fg=fg)
            self._update_img_controls_locked()

        def _install_img_packages(self):
            if self.img_running or self.img_installing:
                return
            self.img_installing = True
            self.gemini_api_status_var.set("Đang cài google-genai...")
            self._set_img_running(True)
            self._log("Đang cài google-genai...", "info")

            def worker():
                err_msg = None
                try:
                    from videobuilder.core.generate_images import install_genai_package
                    install_genai_package(log_callback=self._log)
                except GenerateImagesError as err:
                    err_msg = str(err)
                except subprocess.CalledProcessError as err:
                    err_msg = str(err) or "Không cài được google-genai."

                def done():
                    self.img_installing = False
                    self._set_img_running(False)
                    self._refresh_gemini_api_status()
                    self._refresh_img_engine_status()
                    if err_msg:
                        self._show_error("Cài đặt", err_msg)
                        self._log(err_msg, "error")
                    else:
                        self._show_info("Xong", "Đã cài google-genai.")
                        self._log("Đã cài google-genai.", "success")

                self.after(0, done)

            threading.Thread(target=worker, daemon=True).start()

        def _ensure_img_packages_auto(self):
            if self.img_installing or getattr(self, "_img_packages_auto_started", False):
                return
            if not check_gemini_image(api_key=self.gemini_api_key_var.get()).get("needs_install"):
                return
            if check_gemini_image(api_key=self.gemini_api_key_var.get()).get("ok"):
                return
            if not self.gemini_api_key_var.get().strip():
                return
            self._img_packages_auto_started = True
            self._install_img_packages()

        def _set_img_running(self, active: bool):
            self.img_running = active
            self._update_img_controls_locked()

        def _update_img_controls_locked(self):
            if not getattr(self, "img_create_btn", None):
                return
            if self.img_running or self.img_installing:
                self.img_create_btn.configure(state=tk.DISABLED)
                self._style_primary_button(self.img_create_btn, False)
            elif self.img_engine_ok:
                self.img_create_btn.configure(state=tk.NORMAL)
                self._style_primary_button(self.img_create_btn, True)
            else:
                self.img_create_btn.configure(state=tk.DISABLED)
                self._style_primary_button(self.img_create_btn, False)
            self._update_render_control_buttons()

        def _open_images_folder(self):
            target = self.img_output_dir_var.get().strip() or self.images_var.get().strip()
            if not target or not Path(target).exists():
                self._show_info("Chưa có thư mục", "Chọn thư mục ảnh trước.")
                return
            self._open_folder_path(target)

        def _update_img_open_buttons(self):
            mode = getattr(self, "_footer_mode", "render")
            if mode != "image":
                return
            prompts_target = self.img_prompts_var.get().strip() or self.prompts_var.get().strip()
            img_dir = self.img_output_dir_var.get().strip() or self.images_var.get().strip()
            prompts_ok = bool(prompts_target and Path(prompts_target).is_file())
            folder_ok = bool(img_dir and Path(img_dir).exists())
            self.open_video_btn.configure(state=tk.NORMAL if folder_ok else tk.DISABLED)
            self.open_folder_btn.configure(state=tk.NORMAL if folder_ok else tk.DISABLED)
            if hasattr(self, "open_prompts_btn"):
                self.open_prompts_btn.configure(state=tk.NORMAL if prompts_ok else tk.DISABLED)

        def _set_img_progress(self, pct, message):
            self._set_srt_progress(pct, message)

        def _start_generate_images(self):
            busy = self._img_busy_reason()
            if busy:
                self._show_warning("Đang bận", busy)
                return

            prompts = self.img_prompts_var.get().strip()
            images_dir = self.img_output_dir_var.get().strip()
            if not prompts or not Path(prompts).is_file():
                self._show_warning("Thiếu file prompt", "Chọn file prompt tạo ảnh (.txt).")
                return
            if not images_dir:
                self._show_warning("Thiếu thư mục", "Chọn thư mục lưu ảnh scene.")
                return
            out_path = Path(images_dir)
            if not is_writable_output_dir(out_path if out_path.is_dir() else out_path.parent):
                self._show_error("Lỗi", f"Không ghi được vào:\n{out_path}")
                return

            self._apply_gemini_api_key(silent=True)
            apply_env_gemini_key()
            status = check_gemini_image(api_key=self.gemini_api_key_var.get())
            if not status.get("ok"):
                if status.get("needs_install"):
                    self._show_warning(
                        "Chưa sẵn sàng",
                        "Cần Gemini API key và google-genai — cấu hình ở tab API key.",
                    )
                    return
                self._show_warning(
                    "Chưa sẵn sàng",
                    status.get("message", "Nhập Gemini key ở tab API key."),
                )
                return

            aspect = self._get_img_aspect_key()
            skip_existing = bool(self.img_skip_existing_var.get())
            resolution = self.resolution_var.get().strip()
            aspect_resolved = resolve_aspect_ratio(aspect, resolution_label=resolution)

            reset_progress_floor()
            self._apply_footer_mode("image")
            self._set_img_running(True)
            self.srt_running = True
            self.srt_paused = False
            self.process_controller = ProcessController()
            self._update_render_control_buttons()
            if self._srt_tracker is not None:
                self._srt_tracker.reset(0.0)
            self.srt_status_var.set("Đang tạo ảnh...")
            self._set_img_progress(1, "Bắt đầu tạo ảnh...")
            self._log("——— Tạo ảnh từ prompt ———", "info")

            def worker():
                err_msg = None
                saved = None
                cancelled = False
                try:
                    saved = generate_images_from_prompts(
                        prompts,
                        out_path,
                        aspect_ratio=aspect_resolved,
                        skip_existing=skip_existing,
                        api_key=self.gemini_api_key_var.get().strip(),
                        progress_callback=self._set_img_progress,
                        log_callback=self._log,
                        process_controller=self.process_controller,
                    )
                except (CreateSrtCancelled, RenderCancelled):
                    cancelled = True
                except (GenerateImagesError, OSError, ValueError) as err:
                    err_msg = str(err)
                except Exception as err:
                    err_msg = str(err)

                def done():
                    self._set_img_running(False)
                    self.srt_running = False
                    self.srt_paused = False
                    self.process_controller = None
                    if cancelled:
                        if self._srt_tracker is not None:
                            self._srt_tracker.reset(0.0)
                        self.srt_status_var.set("Đã hủy")
                        self._log("Đã hủy tạo ảnh.", "warn")
                    elif err_msg:
                        if self._srt_tracker is not None:
                            self._srt_tracker.reset(0.0)
                        self.srt_status_var.set("Lỗi")
                        self._log(err_msg, "error")
                        self._show_error("Tạo ảnh", err_msg)
                    else:
                        count = len(saved or [])
                        if self._srt_tracker is not None:
                            self._srt_tracker.report(100.0)
                        self.srt_status_var.set(f"Xong — {count} ảnh mới")
                        self._log(f"Hoàn thành tạo ảnh ({count} ảnh mới).", "success")
                    self._update_img_open_buttons()
                    self._update_render_control_buttons()

                self.after(0, done)

            threading.Thread(target=worker, daemon=True).start()

        def _build_image_tab(self, parent):
            lw = IMG_FIELD_LABEL_WIDTH
            parent.columnconfigure(1, weight=1)

            paths_panel, paths_body = self._section_panel(parent, "Nguồn & xuất")
            paths_panel.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
            paths_body.columnconfigure(1, weight=1)

            self._path_field(
                paths_body, 0, "File prompt", self.img_prompts_var, self._pick_img_prompts,
                label_width=lw, help_key="img_prompts",
            )
            self._path_field(
                paths_body, 1, "Thư mục ảnh", self.img_output_dir_var, self._pick_img_output_dir,
                label_width=lw, help_key="img_output_dir",
            )

            use_row = ttk.Frame(paths_body, style="Card.TFrame")
            use_row.grid(row=2, column=1, sticky="w", pady=(0, 2))
            ttk.Button(
                use_row, text="Lấy từ tab Dự án",
                command=self._use_project_paths_for_image, style="Small.TButton",
            ).pack(side=tk.LEFT)

            opts_panel, opts_body = self._section_panel(parent, "Tùy chọn")
            opts_panel.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 8))
            opts_body.columnconfigure(1, weight=1)
            opts_body.columnconfigure(3, weight=1)

            self._grid_field_label(
                opts_body, 0, "Tỷ lệ ảnh", "img_aspect", label_width=lw, col=0, pady=2,
            )
            aspect_frame = ttk.Frame(opts_body, style="Card.TFrame")
            aspect_frame.grid(row=0, column=1, sticky="ew", pady=2)
            ttk.Combobox(
                aspect_frame,
                textvariable=self.img_aspect_var,
                values=IMG_ASPECT_LABELS,
                state="readonly",
                width=12,
            ).pack(side=tk.LEFT)

            self._grid_field_label(
                opts_body, 0, "Bỏ qua có sẵn", "img_skip_existing", label_width=lw, col=2, pady=2,
            )
            skip_frame = ttk.Frame(opts_body, style="Card.TFrame")
            skip_frame.grid(row=0, column=3, sticky="w", pady=2)
            ttk.Checkbutton(
                skip_frame,
                text="Không ghi đè ảnh scene đã có",
                variable=self.img_skip_existing_var,
            ).pack(side=tk.LEFT)

            self._grid_field_label(
                opts_body, 1, "Gemini", "img_status", label_width=lw, col=0, pady=2,
            )
            self._img_status_inner = tk.Frame(
                opts_body, bg=C["entry_bg"],
                highlightbackground=C["border"], highlightthickness=1,
            )
            self._img_status_inner.grid(row=1, column=1, columnspan=3, sticky="ew", pady=2)
            self._img_status_msg = tk.Label(
                self._img_status_inner, textvariable=self.img_engine_status_var,
                font=self._font(8), bg=C["entry_bg"], fg=C["muted"], anchor="w",
            )
            self._img_status_msg.pack(fill=tk.X, padx=6, pady=4)

            hint = tk.Label(
                parent,
                text="Gemini API key cấu hình ở tab API key.",
                font=self._font(8), bg=C["card"], fg=C["muted"], anchor="w",
            )
            hint.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 0))

            self._refresh_img_engine_status()
