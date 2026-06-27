#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tkinter as tk

from videobuilder.core.ffmpeg_setup import ensure_ffmpeg_on_path
from videobuilder.core.pipeline import DEFAULT_PREVIEW_SECONDS, ENCODE_QUALITY_OPTIONS, ENCODER_OVERRIDE_OPTIONS, ZOOM_LEVEL_OPTIONS, detect_video_encoder
from videobuilder.core.create_srt import DEFAULT_LANGUAGE, DEFAULT_MODEL, DEFAULT_SRT_SPLIT, SRT_SPLIT_KEY_TO_LABEL
from videobuilder.gui.constants import (
    C,
    EFFECT_KEY_TO_LABEL,
    EFFECT_NONE,
    OUTPUT_STEM,
    RESOLUTION_UI,
)
from videobuilder.gui.dialogs import DialogMixin
from videobuilder.gui.paths import default_output_path
from videobuilder.gui.project_tab import ProjectTabMixin
from videobuilder.gui.render_tab import RenderTabMixin
from videobuilder.gui.shell import ShellMixin
from videobuilder.gui.srt_tab import SrtTabMixin
from videobuilder.gui.widgets import WidgetMixin
from videobuilder.gui.progress import ProgressColors
from videobuilder.version import window_title


class VideoBuilderApp(
    tk.Tk,
    DialogMixin,
    WidgetMixin,
    ShellMixin,
    ProjectTabMixin,
    SrtTabMixin,
    RenderTabMixin,
):
        def __init__(self):
            super().__init__()
            self.title(window_title())
            self.geometry("740x580")
            self.minsize(680, 500)
            self.configure(bg=C["bg"])

            self.images_var = tk.StringVar()
            self.audio_var = tk.StringVar()
            self.prompts_var = tk.StringVar()
            self.output_var = tk.StringVar(value=str(default_output_path()))
            self.output_dir_var = tk.StringVar()
            self.output_name_var = tk.StringVar(value=OUTPUT_STEM)
            self.duration_var = tk.StringVar(value="—")
            self.transition_var = tk.StringVar(value="0")
            self.effect_var = tk.StringVar(value=EFFECT_KEY_TO_LABEL[EFFECT_NONE])
            self.resolution_var = tk.StringVar(value=RESOLUTION_UI["auto"])
            self.fps_var = tk.StringVar(value="30")
            self.quality_var = tk.StringVar(value=ENCODE_QUALITY_OPTIONS["fast"])
            self.zoom_var = tk.StringVar(value=ZOOM_LEVEL_OPTIONS["off"])
            self.encoder_var = tk.StringVar(value=ENCODER_OVERRIDE_OPTIONS["auto"])
            self.speed_var = tk.StringVar(value="100")
            self.volume_var = tk.StringVar(value="100")
            self.strip_metadata_var = tk.StringVar(value="Tắt")
            self.watermark_opacity_var = tk.StringVar(value="70")
            self.watermark_var = tk.StringVar()
            self.subtitle_var = tk.StringVar()
            self.subtitle_font_var = tk.StringVar(value="8")
            self.subtitle_offset_var = tk.StringVar(value="0")
            self.subtitle_margin_var = tk.StringVar(value="18")
            self.subtitle_outline_var = tk.StringVar(value="1")
            self.preview_var = tk.StringVar(value=str(int(DEFAULT_PREVIEW_SECONDS)))
            self.srt_audio_var = tk.StringVar()
            self.srt_output_var = tk.StringVar()
            self.srt_output_dir_var = tk.StringVar()
            self.srt_output_name_var = tk.StringVar(value="subtitle")
            self.srt_model_var = tk.StringVar(value=DEFAULT_MODEL)
            self.srt_language_var = tk.StringVar(value=DEFAULT_LANGUAGE)
            self.srt_split_var = tk.StringVar(value=SRT_SPLIT_KEY_TO_LABEL[DEFAULT_SRT_SPLIT])
            self.srt_model_hint_var = tk.StringVar()
            self.srt_status_var = tk.StringVar(value="Sẵn sàng nhận dạng")
            self.srt_percent_var = tk.StringVar(value="—")
            self.status_var = tk.StringVar(value="Sẵn sàng render")
            self.percent_var = tk.StringVar(value="0%")
            self.encoder_info_var = tk.StringVar()
            self.ffmpeg_status_var = tk.StringVar()

            ensure_ffmpeg_on_path()
            encoder, label = detect_video_encoder()
            self.encoder_info_var.set(f"{label} · {encoder}")

            self.rendering = False
            self.render_paused = False
            self.process_controller = None
            self.ffmpeg_installing = False
            self.ffmpeg_ok = False
            self.last_output = None
            self.last_srt_output = None
            self.srt_running = False
            self.srt_paused = False
            self.whisper_ok = False
            self.whisper_installing = False
            self.whisper_status_var = tk.StringVar(value="Đang kiểm tra Whisper...")
            self._progress_colors: ProgressColors | None = None
            self._render_bar: CanvasProgressBar | None = None
            self._render_tracker: SmoothProgressTracker | None = None
            self._render_status: StatusPresenter | None = None
            self._srt_bar: CanvasProgressBar | None = None
            self._srt_tracker: DirectProgressTracker | None = None
            self._srt_status: StatusPresenter | None = None
            self._srt_whisper_btn_frame = None
            self._fonts = {}
            self._setup_theme()
            self._setup_widget_colors()
            self._build_ui()
            self._load_settings()
            self._sync_output_display(from_output_var=True)
            self._refresh_ffmpeg_status()
            self._sync_duration_from_audio()
            self.after(300, self._sync_duration_from_audio)
            self._center_on_screen()
            self._log("VideoBuilder sẵn sàng.", "info")
            self.protocol("WM_DELETE_WINDOW", self._on_close)




def main():
    app = VideoBuilderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
