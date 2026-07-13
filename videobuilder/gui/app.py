#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tkinter as tk

from videobuilder.core.env_config import load_env
from videobuilder.core.ffmpeg_setup import ensure_ffmpeg_on_path
from videobuilder.core.pipeline import DEFAULT_PREVIEW_SECONDS, ENCODE_QUALITY_OPTIONS, ENCODER_OVERRIDE_OPTIONS, ZOOM_LEVEL_OPTIONS
from videobuilder.core.create_srt import DEFAULT_LANGUAGE, DEFAULT_MODEL, DEFAULT_SRT_SPLIT, SRT_SPLIT_KEY_TO_LABEL
from videobuilder.core.automation import (
    DEFAULT_AUTO_DURATION,
    AUTO_DURATION_KEY_TO_LABEL,
    DEFAULT_MACOS_SAY_VOICE,
    DEFAULT_TTS_ENGINE,
    DEFAULT_TTS_RATE,
    DEFAULT_TTS_VOICE,
    automation_prompt_path_hint,
    warmup_auto_defaults,
)

from videobuilder.gui.constants import (
    C,
    EFFECT_KEY_TO_LABEL,
    EFFECT_NONE,
    OUTPUT_STEM,
    RESOLUTION_UI,
)
from videobuilder.gui.api_tab import ApiTabMixin
from videobuilder.gui.auto_tab import AutoTabMixin
from videobuilder.gui.dialogs import DialogMixin
from videobuilder.gui.image_tab import ImageTabMixin
from videobuilder.gui.paths import default_output_path
from videobuilder.gui.project_tab import ProjectTabMixin
from videobuilder.gui.render_tab import RenderTabMixin
from videobuilder.gui.shell import ShellMixin
from videobuilder.gui.srt_tab import SrtTabMixin
from videobuilder.gui.tts_tab import TtsTabMixin
from videobuilder.gui.widgets import WidgetMixin
from videobuilder.gui.progress import (
    CanvasProgressBar,
    DirectProgressTracker,
    ProgressColors,
    SmoothProgressTracker,
    StatusPresenter,
)
from videobuilder.version import window_title


class VideoBuilderApp(
    tk.Tk,
    DialogMixin,
    WidgetMixin,
    ShellMixin,
    ProjectTabMixin,
    ApiTabMixin,
    AutoTabMixin,
    SrtTabMixin,
    TtsTabMixin,
    ImageTabMixin,
    RenderTabMixin,
):
        def __init__(self):
            super().__init__()
            self.title(window_title())
            # Mặc định lớn hơn 1040×720 — bớt scroll trong tab; vẫn fit màn hình.
            sw = max(self.winfo_screenwidth(), 1040)
            sh = max(self.winfo_screenheight(), 720)
            w = min(1280, max(1100, int(sw * 0.78)))
            h = min(980, max(820, int(sh * 0.82)))
            self.geometry(f"{w}x{h}")
            self.minsize(900, 640)
            self.configure(bg=C["bg"])

            load_env()
            from videobuilder.core.audio_pipeline import apply_env_api_keys

            from videobuilder.core.generate_images import apply_env_gemini_key

            apply_env_api_keys()
            apply_env_gemini_key()
            from videobuilder.core.groq_models import load_cached_groq_models

            load_cached_groq_models(force_reload=True)

            self.images_var = tk.StringVar()
            self.audio_var = tk.StringVar()
            self.prompts_var = tk.StringVar()
            self.prompts_dir_var = tk.StringVar()
            self.prompts_name_var = tk.StringVar(value="timeline")
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
            self.srt_input_var = tk.StringVar()
            self.srt_output_var = tk.StringVar()
            self.srt_output_dir_var = tk.StringVar()
            self.srt_output_name_var = tk.StringVar(value="subtitle")
            self.groq_api_key_var = tk.StringVar()
            self.gemini_api_key_var = tk.StringVar()
            self.elevenlabs_api_key_var = tk.StringVar()
            self.groq_api_status_var = tk.StringVar(value="Đang kiểm tra Groq...")
            self.gemini_api_status_var = tk.StringVar(value="Đang kiểm tra Gemini...")
            self.elevenlabs_api_status_var = tk.StringVar(value="Đang kiểm tra ElevenLabs...")
            self.tts_output_var = tk.StringVar()
            self.tts_engine_var = tk.StringVar(value=DEFAULT_TTS_ENGINE)
            self.tts_voice_var = tk.StringVar(value=DEFAULT_TTS_VOICE)
            self.tts_say_voice_var = tk.StringVar(value=DEFAULT_MACOS_SAY_VOICE)
            self.tts_enhance_var = tk.BooleanVar(value=False)
            self.tts_status_var = tk.StringVar(value="Đang kiểm tra TTS...")
            self.tts_running = False
            self.last_tts_output = None
            self.srt_prompts_output_var = tk.StringVar()
            self.srt_prompts_output_dir_var = tk.StringVar()
            self.srt_prompts_output_name_var = tk.StringVar(value="subtitle")
            self.srt_gen_prompts_var = tk.BooleanVar(value=True)
            self.srt_model_var = tk.StringVar(value=DEFAULT_MODEL)
            self.srt_language_var = tk.StringVar(value=DEFAULT_LANGUAGE)
            self.srt_split_var = tk.StringVar(value=SRT_SPLIT_KEY_TO_LABEL[DEFAULT_SRT_SPLIT])
            self.srt_input_var = tk.StringVar()
            self.auto_prompt_file_var = tk.StringVar(value=str(automation_prompt_path_hint()))
            self.auto_output_dir_var = tk.StringVar()
            self.auto_youtube_url_var = tk.StringVar()
            self.auto_seed_var = tk.StringVar(value="start")
            self.auto_script_var = tk.StringVar()
            self.auto_voice_var = tk.StringVar(value=DEFAULT_TTS_VOICE)
            self.auto_rate_var = tk.StringVar(value=DEFAULT_TTS_RATE)
            self.auto_duration_var = tk.StringVar(
                value=AUTO_DURATION_KEY_TO_LABEL[DEFAULT_AUTO_DURATION],
            )
            self.auto_topic_history = []
            self.img_prompts_var = tk.StringVar()
            self.img_output_dir_var = tk.StringVar()
            self.img_aspect_var = tk.StringVar(value="Tự động")
            self.img_skip_existing_var = tk.BooleanVar(value=True)
            self.img_engine_status_var = tk.StringVar(value="Đang kiểm tra Gemini...")
            self.srt_model_hint_var = tk.StringVar()
            self.srt_status_var = tk.StringVar(value="Sẵn sàng nhận dạng")
            self.srt_percent_var = tk.StringVar(value="—")
            self.status_var = tk.StringVar(value="Sẵn sàng render")
            self.percent_var = tk.StringVar(value="0%")
            self.encoder_info_var = tk.StringVar()
            self.ffmpeg_status_var = tk.StringVar()

            ensure_ffmpeg_on_path()

            self.rendering = False
            self.render_paused = False
            self.process_controller = None
            self.ffmpeg_installing = False
            self.ffmpeg_ok = False
            self.last_output = None
            self.last_srt_output = None
            self.last_prompts_output = None
            self.srt_running = False
            self.auto_running = False
            self.auto_installing = False
            self._auto_path_warning = ""
            self._auto_show_template_btn = False
            self._auto_tab_refresh_running = False
            self._auto_tab_refresh_job = None
            self.img_running = False
            self.img_installing = False
            self.img_engine_ok = False
            self.srt_paused = False
            self.whisper_ok = False
            self.whisper_installing = False
            self.groq_key_entry = None
            self.groq_key_toggle_btn = None
            self.gemini_key_entry = None
            self.gemini_key_toggle_btn = None
            self._groq_key_hidden = True
            self._gemini_key_hidden = True
            self._srt_packages_auto_started = False
            self._img_packages_auto_started = False
            self.img_create_btn = None
            self.whisper_status_var = tk.StringVar(value="Đang kiểm tra Groq / Whisper...")
            self._progress_colors: ProgressColors | None = None
            self._render_bar: CanvasProgressBar | None = None
            self._render_tracker: SmoothProgressTracker | None = None
            self._render_status: StatusPresenter | None = None
            self._srt_bar: CanvasProgressBar | None = None
            self._srt_tracker: DirectProgressTracker | None = None
            self._srt_status: StatusPresenter | None = None
            self._srt_whisper_btn_frame = None
            self._fonts = {}
            self._init_ui_thread_bridge()
            self._setup_theme()
            self._setup_widget_colors()
            self._build_ui()
            self._load_settings()
            self._hydrate_api_keys_from_env()
            self._sync_output_display(from_output_var=True)
            self._sync_prompts_display(from_output_var=True)
            self._refresh_ffmpeg_status()
            self._sync_duration_from_audio()
            self.after(300, self._sync_duration_from_audio)
            self.after(500, warmup_auto_defaults)
            self._center_on_screen()
            self._log("VideoBuilder sẵn sàng.", "info")
            self.protocol("WM_DELETE_WINDOW", self._on_close)




def main():
    app = VideoBuilderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
