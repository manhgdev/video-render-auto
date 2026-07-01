#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import tkinter as tk

from videobuilder.core.pipeline import (
    DEFAULT_PREVIEW_SECONDS,
    ENCODER_OVERRIDE_OPTIONS,
    ENCODE_QUALITY_OPTIONS,
    FPS_OPTIONS,
    MissingSceneImagesError,
    ProcessController,
    RenderCancelled,
    RESOLUTION_PRESETS,
    SubtitleStyle,
    ZOOM_LEVEL_OPTIONS,
    apply_playback_speed,
    build_scene_pairs,
    build_video,
    detect_resolution_from_images,
    get_media_duration,
    parse_prompt_scenes,
    strip_video_metadata,
    validate_scene_images,
)
from videobuilder.core.progress import reset_progress_floor
from videobuilder.gui.constants import (
    C,
    EFFECT_LABEL_TO_KEY,
    EFFECT_NONE,
    ENCODER_LABEL_TO_KEY,
    QUALITY_LABEL_TO_KEY,
    RESOLUTION_LABEL_TO_KEY,
    ZOOM_LABEL_TO_KEY,
)
from videobuilder.gui.paths import is_writable_output_dir, normalize_output_path
from videobuilder.gui.progress import should_log_render_progress
from videobuilder.core.ffmpeg_setup import (
    check_ffmpeg,
    ensure_ffmpeg_on_path,
    ffmpeg_can_auto_install,
    ffmpeg_install_hint,
    install_ffmpeg,
)


class RenderTabMixin:
        def _show_ffmpeg_banner(self, *, compact: bool = True) -> None:
            if not self.ffmpeg_banner.winfo_ismapped():
                self.ffmpeg_banner.pack(fill=tk.X, after=self.header, pady=(0, 1))
            else:
                self.ffmpeg_banner.pack_configure(pady=(0, 1))

        def _hide_ffmpeg_banner(self) -> None:
            if self.ffmpeg_banner.winfo_ismapped():
                self.ffmpeg_banner.pack_forget()

        def _sync_encoder_header(self) -> None:
            from videobuilder.core.pipeline import detect_video_encoder

            encoder, label = detect_video_encoder()
            status = check_ffmpeg()
            parts = [label, encoder]
            if status.get("ok") and status.get("short"):
                parts.append(f"ffmpeg {status['short']}")
            self.encoder_info_var.set(" · ".join(parts))

        def _refresh_ffmpeg_status(self):
            ensure_ffmpeg_on_path()
            status = check_ffmpeg()
            self.ffmpeg_ok = status["ok"]

            if status["ok"] and not self.ffmpeg_installing:
                self.ffmpeg_status_var.set(status["message"])
                self._hide_ffmpeg_banner()
                self.ffmpeg_install_btn.pack_forget()
                self.ffmpeg_recheck_btn.pack_forget()
                self._sync_encoder_header()
                self._sync_duration_from_audio()
                self._set_rendering_locked()
                return

            self._show_ffmpeg_banner(compact=self.ffmpeg_installing)

            if status["ok"]:
                self.ffmpeg_banner.configure(bg=C["ok_bg"])
                self.ffmpeg_inner.configure(bg=C["ok_bg"])
                self.ffmpeg_icon_label.configure(
                    text="✓", bg=C["ok_bg"], fg=C["ok_fg"], font=self._font(8, "bold"),
                )
                self.ffmpeg_msg_label.configure(
                    bg=C["ok_bg"], fg=C["ok_fg"], font=self._font(8),
                )
                self.ffmpeg_status_var.set(status["message"])
                self.ffmpeg_install_btn.pack_forget()
                if not self.ffmpeg_installing:
                    self.ffmpeg_recheck_btn.pack(side=tk.RIGHT, padx=(0, 2))
                self._sync_duration_from_audio()
            else:
                self.ffmpeg_banner.configure(bg=C["warn_bg"])
                self.ffmpeg_inner.configure(bg=C["warn_bg"])
                self.ffmpeg_icon_label.configure(
                    text="!", bg=C["warn_bg"], fg=C["warn_fg"], font=self._font(8, "bold"),
                )
                self.ffmpeg_msg_label.configure(
                    bg=C["warn_bg"], fg=C["warn_fg"], font=self._font(8),
                )
                self.ffmpeg_status_var.set(status["message"])
                self.ffmpeg_install_btn.pack_forget()
                self.ffmpeg_recheck_btn.pack_forget()
                self.ffmpeg_recheck_btn.pack(side=tk.RIGHT, padx=(0, 2))
                if ffmpeg_can_auto_install() and not self.ffmpeg_installing:
                    self.ffmpeg_install_btn.pack(side=tk.RIGHT, padx=(4, 0))
            self._set_rendering_locked()

        def _set_rendering_locked(self):
            if self.ffmpeg_ok and not self.rendering:
                self.render_btn.configure(state=tk.NORMAL)
                self.preview_btn.configure(state=tk.NORMAL)
                self._style_primary_button(self.render_btn, True)
            elif not self.ffmpeg_installing:
                self.render_btn.configure(state=tk.DISABLED)
                self.preview_btn.configure(state=tk.DISABLED)
                self._style_primary_button(self.render_btn, False)
            self._update_render_control_buttons()

        def _update_render_control_buttons(self):
            active = self.rendering or self.srt_running or getattr(self, "img_running", False)
            ctrl_state = tk.NORMAL if active else tk.DISABLED
            if active and self.srt_running and self.srt_paused:
                pause_bg, pause_fg, pause_text = "#dcfce7", "#15803d", "Tiếp tục"
            elif active and self.rendering and self.render_paused:
                pause_bg, pause_fg, pause_text = "#dcfce7", "#15803d", "Tiếp tục"
            elif active:
                pause_bg, pause_fg, pause_text = "#fef3c7", "#b45309", "Tạm dừng"
            else:
                pause_bg, pause_fg, pause_text = "#f3f4f6", "#9ca3af", "Tạm dừng"
            self.pause_btn.configure(
                state=ctrl_state,
                text=pause_text,
                bg=pause_bg,
                fg=pause_fg,
            )
            cancel_bg = "#fee2e2" if active else "#f3f4f6"
            cancel_fg = "#b91c1c" if active else "#9ca3af"
            self.cancel_btn.configure(state=ctrl_state, bg=cancel_bg, fg=cancel_fg)

        def _start_ffmpeg_install(self):
            if self.ffmpeg_installing or self.rendering:
                return
            if not ffmpeg_can_auto_install():
                self._refresh_ffmpeg_status()
                self._show_info("FFmpeg", ffmpeg_install_hint().capitalize() + ".")
                return

            self.ffmpeg_installing = True
            self._show_ffmpeg_banner(compact=True)
            self.ffmpeg_install_btn.configure(state=tk.DISABLED, text="Đang cài...")
            self.ffmpeg_status_var.set("Đang cài FFmpeg — vui lòng chờ...")
            self.status_var.set("Đang cài FFmpeg...")
            self._log("Bắt đầu cài FFmpeg...", "info")

            def log(msg):
                self._log(msg, "info")
                self.after(0, lambda: self.ffmpeg_status_var.set(msg))

            def worker():
                try:
                    result = install_ffmpeg(progress_callback=log)
                except Exception as err:
                    result = {"ok": False, "message": str(err)}

                def done():
                    self.ffmpeg_installing = False
                    self.ffmpeg_install_btn.configure(state=tk.NORMAL, text="Cài FFmpeg")
                    if result.get("ok"):
                        self._refresh_ffmpeg_status()
                        self._sync_duration_from_audio()
                        self.status_var.set("Sẵn sàng render")
                        self._show_info("Xong", result.get("message", "Đã cài FFmpeg."))
                    else:
                        self._refresh_ffmpeg_status()
                        self.status_var.set("Cần cài FFmpeg")
                        self._log(result.get("message", "Không cài được FFmpeg."), "error")
                        self._show_error("Lỗi", result.get("message", "Không cài được FFmpeg."))

                self.after(0, done)

            threading.Thread(target=worker, daemon=True).start()

        def _set_rendering(self, active):
            state = tk.DISABLED if active else tk.NORMAL
            if not self.ffmpeg_ok:
                state = tk.DISABLED
            self.render_btn.configure(state=state)
            self.preview_btn.configure(state=state)
            self._style_primary_button(self.render_btn, state == tk.NORMAL)
            self._update_render_control_buttons()

        def _toggle_pause_render(self):
            if not self.process_controller:
                return
            if self.srt_running:
                if getattr(self, "img_running", False):
                    action = "tạo ảnh"
                elif getattr(self, "auto_running", False):
                    action = "pipeline tự động"
                else:
                    action = "tạo SRT"
                if self.srt_paused:
                    self.process_controller.resume()
                    self.srt_paused = False
                    self._update_srt_status(f"Đang tiếp tục {action}...")
                    self._log(f"Tiếp tục {action}.", "info")
                else:
                    self.process_controller.pause()
                    self.srt_paused = True
                    self._update_srt_status("Tạm dừng")
                    self._log(f"Tạm dừng {action}.", "warn")
                self._update_render_control_buttons()
                return
            if not self.rendering:
                return
            if self.render_paused:
                self.process_controller.resume()
                self.render_paused = False
                self.status_var.set("Đang tiếp tục render...")
                self._log("Tiếp tục render.", "info")
            else:
                self.process_controller.pause()
                self.render_paused = True
                self.status_var.set("Tạm dừng")
                self._log("Tạm dừng render.", "warn")
            self._update_render_control_buttons()

        def _cancel_render(self):
            if (self.srt_running or getattr(self, "auto_running", False) or getattr(self, "img_running", False)) and self.process_controller:
                if getattr(self, "img_running", False):
                    label = "tạo ảnh"
                elif getattr(self, "auto_running", False):
                    label = "tự động"
                else:
                    label = "SRT"
                self._log(f"Đang hủy {label}...", "warn")
                self.srt_status_var.set("Đang hủy...")
                self.process_controller.cancel()
                return
            if not self.rendering or not self.process_controller:
                return
            if not self._ask_yes_no("Hủy render", "Bạn có chắc muốn hủy render đang chạy?", kind="warning"):
                return
            self._log("Đang hủy render...", "warn")
            self.process_controller.cancel()

        def _set_open_buttons(self, enabled):
            state = tk.NORMAL if enabled else tk.DISABLED
            self.open_video_btn.configure(state=state)
            self.open_folder_btn.configure(state=state)

        def _quick_estimate_seconds(self, options: dict) -> float:
            preview = options.get("preview_seconds")
            zoom = (options.get("zoom_level") or "off") not in ("off", "")
            transition = float(options.get("transition") or 0) > 0
            if preview:
                base = float(preview)
            else:
                base = 120.0
            if zoom:
                return max(30.0, base * 2.8 + 20.0)
            if transition:
                return max(25.0, base * 1.8 + 15.0)
            return max(20.0, base * 1.2 + 8.0)

        def _estimate_render_seconds(self, options: dict) -> float:
            try:
                ensure_ffmpeg_on_path()
                audio_dur = get_media_duration(Path(options["audio"]))
            except Exception:
                audio_dur = 90.0

            if options.get("preview_seconds"):
                audio_dur = min(audio_dur, float(options["preview_seconds"]))

            zoom = (options.get("zoom_level") or "off") not in ("off", "")
            transition = float(options.get("transition") or 0) > 0
            n_scenes = 40
            try:
                scenes = parse_prompt_scenes(Path(options["prompts"]), audio_dur)
                pairs = build_scene_pairs(Path(options["images"]), scenes, audio_dur)
                n_scenes = max(1, len(pairs))
            except Exception:
                pass

            if zoom:
                est = 12.0 + n_scenes * 0.75 + audio_dur * 0.45
            elif transition:
                est = 15.0 + n_scenes * 0.35 + audio_dur * 1.1
            else:
                est = 10.0 + audio_dur * 0.9

            if options.get("subtitle"):
                est += 6.0
            if options.get("watermark"):
                est += 2.0

            return max(18.0, est)

        def _start_progress_heartbeat(self):
            reset_progress_floor()
            self._render_tracker.start(1.0)

        def _stop_progress_heartbeat(self):
            self._render_tracker.stop()

        def _set_progress(self, pct, message):
            pct = max(0.0, min(100.0, float(pct)))

            def apply():
                if message and should_log_render_progress(message):
                    self._log(message)
                if message:
                    if pct >= 100:
                        self._render_status.update(done=True)
                    elif self.rendering:
                        self._render_status.update(message)
                self._render_tracker.ingest(pct, touch_time=bool(message))

            self.after(0, apply)

        def _validate(self, preview=False):
            images = self.images_var.get().strip()
            audio = self.audio_var.get().strip()
            prompts = self.prompts_var.get().strip()
            self._apply_output_name()
            output = self.output_var.get().strip()

            if not images or not Path(images).is_dir():
                raise ValueError("Chọn thư mục ảnh hợp lệ.")
            if not audio or not Path(audio).is_file():
                raise ValueError("Chọn file audio hợp lệ.")
            if not prompts or not Path(prompts).is_file():
                raise ValueError("Chọn file prompt hợp lệ.")
            if not check_ffmpeg()["ok"]:
                raise ValueError("Chưa có FFmpeg — bấm «Cài FFmpeg» trước khi render.")
            try:
                audio_duration = get_media_duration(Path(audio))
            except Exception as exc:
                raise ValueError(
                    f"Không đọc được độ dài audio — kiểm tra file hoặc cài FFmpeg.\n({exc})"
                ) from exc

            try:
                scenes = parse_prompt_scenes(Path(prompts), audio_duration)
            except Exception as exc:
                raise ValueError(f"Không đọc được file prompt:\n{exc}") from exc

            try:
                validate_scene_images(scenes, Path(images))
            except MissingSceneImagesError:
                raise
            except Exception as exc:
                # Thư mục ảnh rỗng / sai đường dẫn thường bị hiểu nhầm là lỗi prompt
                msg = str(exc).strip()
                if msg.startswith("Không tìm thấy ảnh trong thư mục"):
                    raise ValueError(
                        f"{msg}\n\n"
                        "Gợi ý: tab Tạo ảnh → tạo ảnh scene vào đúng thư mục, "
                        "rồi tab Dự án → «Thư mục ảnh» trỏ vào thư mục đó."
                    ) from exc
                raise

            if not output:
                raise ValueError("Chọn file xuất.")
            out_path = normalize_output_path(output)
            if not is_writable_output_dir(out_path.parent):
                raise ValueError(
                    f"Không ghi được vào thư mục:\n{out_path.parent}\n\n"
                    "Chọn thư mục khác bằng nút Chọn (vd. Desktop, Videos, cạnh file .exe)."
                )
            output = str(out_path)

            try:
                transition = float(self.transition_var.get().strip() or "0")
                if transition < 0:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("Thời lượng chuyển cảnh phải là số >= 0.") from exc

            effect = self._get_effect_key()
            if effect == EFFECT_NONE:
                transition = 0.0
            elif transition <= 0:
                transition = DEFAULT_2D_TRANSITION_DURATION

            res_key = RESOLUTION_LABEL_TO_KEY.get(self.resolution_var.get().strip(), "auto")
            if res_key == "auto":
                try:
                    width, height = detect_resolution_from_images(Path(images))
                except Exception as exc:
                    raise ValueError(f"Auto resolution: {exc}") from exc
            else:
                width, height = RESOLUTION_PRESETS.get(res_key, RESOLUTION_PRESETS["1080p"])

            try:
                fps = int(self.fps_var.get().strip() or "30")
                if fps not in FPS_OPTIONS:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("FPS phải là 24, 30 hoặc 60.") from exc

            quality = QUALITY_LABEL_TO_KEY.get(self.quality_var.get().strip(), "fast")
            zoom_level = ZOOM_LABEL_TO_KEY.get(self.zoom_var.get().strip(), "off")
            encoder = ENCODER_LABEL_TO_KEY.get(self.encoder_var.get().strip(), "auto")

            try:
                speed_pct = float(self.speed_var.get().strip() or "100")
                if speed_pct < 25 or speed_pct > 300:
                    raise ValueError
            except ValueError as exc:
                raise ValueError("Speed phải từ 25–300% (100 = bình thường).") from exc

            try:
                volume_pct = float(self.volume_var.get().strip() or "100")
                if volume_pct <= 0 or volume_pct > 300:
                    raise ValueError
                audio_volume = volume_pct / 100.0
            except ValueError as exc:
                raise ValueError("Âm lượng phải từ 1–300%.") from exc

            watermark = self.watermark_var.get().strip() or None
            try:
                wm_opacity_pct = float(self.watermark_opacity_var.get().strip() or "70")
                if wm_opacity_pct < 10 or wm_opacity_pct > 100:
                    raise ValueError
                watermark_opacity = wm_opacity_pct / 100.0
            except ValueError as exc:
                raise ValueError("Độ mờ logo phải từ 10–100%.") from exc

            subtitle = self.subtitle_var.get().strip() or None

            subtitle_style = None
            if subtitle:
                try:
                    font_text = self.subtitle_font_var.get().strip() or "8"
                    font_size = None if font_text in ("0", "") else int(font_text)
                    if font_size is not None and not (8 <= font_size <= 72):
                        raise ValueError
                    offset_sec = float(self.subtitle_offset_var.get().strip() or "0")
                    margin_v = int(self.subtitle_margin_var.get().strip() or "18")
                    outline = int(self.subtitle_outline_var.get().strip() or "1")
                    if margin_v < 0 or margin_v > 200:
                        raise ValueError
                    if outline < 0 or outline > 2:
                        raise ValueError
                    subtitle_style = SubtitleStyle(
                        font_size=font_size,
                        margin_v=margin_v,
                        outline=outline,
                        offset_sec=offset_sec,
                    )
                except ValueError as exc:
                    raise ValueError(
                        "Phụ đề: cỡ chữ 8–72 (0=auto), lệch số thực, lề 0–200, nền chữ 0–2."
                    ) from exc

            preview_seconds = None
            if preview:
                try:
                    preview_seconds = float(self.preview_var.get().strip() or str(DEFAULT_PREVIEW_SECONDS))
                    if preview_seconds <= 0:
                        raise ValueError
                except ValueError as exc:
                    raise ValueError("Thời lượng preview phải là số > 0.") from exc
                stem = Path(output).stem
                output = str(Path(output).with_name(f"{stem}_preview.mp4"))

            return {
                "images": images,
                "audio": audio,
                "prompts": prompts,
                "output": output,
                "transition": transition,
                "effect": effect,
                "timeline": None,
                "width": width,
                "height": height,
                "fps": fps,
                "quality": quality,
                "zoom_level": zoom_level,
                "encoder": encoder,
                "speed_pct": speed_pct,
                "audio_volume": audio_volume,
                "watermark": watermark,
                "watermark_opacity": watermark_opacity,
                "subtitle": subtitle,
                "subtitle_style": subtitle_style,
                "preview_seconds": preview_seconds,
                "strip_metadata": self.strip_metadata_var.get().strip() == "Bật",
            }

        def _run_build(self, options, ask_open=True):
            self.rendering = True
            self.render_paused = False
            self.process_controller = ProcessController()
            self._set_rendering(True)
            self._set_open_buttons(False)
            self._render_tracker.start(1.0)
            self._render_status.reset_last()
            self._render_status.update("Chuẩn bị render...")
            self._log("——— Bắt đầu render ———", "info")
            self._start_progress_heartbeat()
            self._set_progress(1.0, "Chuẩn bị render...")

            def worker():
                cancelled = False
                try:
                    build_video(
                        audio=options["audio"],
                        prompts=options["prompts"],
                        images_dir=options["images"],
                        output=options["output"],
                        fps=options["fps"],
                        width=options["width"],
                        height=options["height"],
                        zoom_level=options["zoom_level"],
                        transition=options["transition"],
                        transition_type=options["effect"],
                        timeline_duration=options["timeline"],
                        progress_callback=self._set_progress,
                        encode_quality=options["quality"],
                        encoder_override=options["encoder"],
                        audio_volume=options["audio_volume"],
                        watermark=options["watermark"],
                        watermark_opacity=options["watermark_opacity"],
                        subtitle=options["subtitle"],
                        subtitle_style=options["subtitle_style"],
                        preview_seconds=options["preview_seconds"],
                        log_callback=self._log,
                        process_controller=self.process_controller,
                    )
                    speed_pct = options.get("speed_pct", 100)
                    strip_meta = options.get("strip_metadata", False)
                    finale_steps = []
                    if abs(speed_pct - 100) > 0.5:
                        finale_steps.append("speed")
                    if strip_meta:
                        finale_steps.append("meta")
                    step_span = 0.5 / max(len(finale_steps), 1)

                    for i, step in enumerate(finale_steps):
                        pb = 99.0 + i * step_span
                        if step == "speed":
                            self._log("——— Chỉnh tốc độ (speed) ———", "info")
                            apply_playback_speed(
                                options["output"],
                                speed_pct / 100.0,
                                encoder_override=options["encoder"],
                                encode_quality=options["quality"],
                                log_callback=self._log,
                                process_controller=self.process_controller,
                                progress_callback=self._set_progress,
                                progress_base=pb,
                                progress_span=step_span,
                            )
                        elif step == "meta":
                            self._log("——— Xóa metadata ———", "info")
                            strip_video_metadata(
                                options["output"],
                                log_callback=self._log,
                                process_controller=self.process_controller,
                                progress_callback=self._set_progress,
                                progress_base=pb,
                                progress_span=step_span,
                            )
                    if not finale_steps:
                        self._set_progress(99.0, "Hoàn tất...")
                    self._set_progress(100.0, "Hoàn thành!")
                    self.last_output = options["output"]
                    self._save_settings()

                    def on_done():
                        self._set_open_buttons(True)
                        if ask_open and self._ask_yes_no(
                            "Xong",
                            f"Đã tạo video:\n{options['output']}\n\nMở video ngay?",
                        ):
                            self._open_path(options["output"])

                    self.after(0, on_done)
                except RenderCancelled:
                    cancelled = True
                    self._log("Đã hủy render.", "warn")
                    try:
                        Path(options["output"]).unlink(missing_ok=True)
                    except OSError:
                        pass
                    self.after(0, lambda: self.status_var.set("Đã hủy render"))
                except Exception as err:
                    self._log(f"Lỗi render: {err}", "error")
                    self._log(traceback.format_exc(), "error")
                    err_copy = err
                    self.after(0, lambda e=err_copy: self._show_render_error(e))
                finally:
                    def done():
                        if cancelled:
                            self._render_tracker.reset(0.0)
                            self.percent_var.set("0%")
                        else:
                            self._render_tracker.finish(100.0)
                        self.rendering = False
                        self.render_paused = False
                        self.process_controller = None
                        self._set_rendering(False)

                    self.after(0, done)

            threading.Thread(target=worker, daemon=True).start()

        def _start_render(self):
            if self.rendering:
                return
            if not check_ffmpeg()["ok"]:
                self._show_warning("Thiếu FFmpeg", "Chưa có FFmpeg. Bấm «Cài FFmpeg» trên thanh cảnh báo.")
                self._refresh_ffmpeg_status()
                return
            try:
                options = self._validate(preview=False)
            except (ValueError, MissingSceneImagesError) as err:
                self._show_validation_error(err)
                return
            self._run_build(options)

        def _start_preview(self):
            if self.rendering or self.srt_running:
                return
            if self._footer_mode == "srt":
                self._start_create_srt(preview=True)
                return
            if not check_ffmpeg()["ok"]:
                self._show_warning("Thiếu FFmpeg", "Chưa có FFmpeg. Bấm «Cài FFmpeg» trên thanh cảnh báo.")
                self._refresh_ffmpeg_status()
                return
            try:
                options = self._validate(preview=True)
            except (ValueError, MissingSceneImagesError) as err:
                self._show_validation_error(err)
                return
            self._run_build(options)
