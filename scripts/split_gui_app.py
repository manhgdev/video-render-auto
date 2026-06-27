#!/usr/bin/env python3
"""One-shot: tách videobuilder/gui/app.py thành các module nhỏ."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "videobuilder" / "gui"
SRC = GUI / "app.py"

lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)


def slice_methods(start: int, end: int) -> str:
    """1-based inclusive line numbers."""
    return "".join(lines[start - 1 : end])


def write_mixin(path: Path, class_name: str, imports: str, body: str):
    content = f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
{imports}

class {class_name}:
{body}
'''
    path.write_text(content, encoding="utf-8")
    print(f"wrote {path.name} ({len(content.splitlines())} lines)")


# --- constants.py (lines 149-429) ---
const_block = slice_methods(149, 429)
(GUI / "constants.py").write_text(
    f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path

from videobuilder.core.pipeline import ENCODE_QUALITY_OPTIONS, TRANSITION_EFFECTS, ZOOM_LEVEL_OPTIONS, ENCODER_OVERRIDE_OPTIONS

{const_block}''',
    encoding="utf-8",
)
print("wrote constants.py")

# --- paths.py (lines 68-147) ---
paths_block = slice_methods(68, 147)
(GUI / "paths.py").write_text(
    f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
from pathlib import Path

from videobuilder.core.ffmpeg_setup import get_app_dir

from videobuilder.gui.constants import OUTPUT_BASENAME

{paths_block}''',
    encoding="utf-8",
)
print("wrote paths.py")

INDENT = "    "

def body_from_ranges(ranges: list[tuple[int, int]]) -> str:
    parts = []
    for start, end in ranges:
        chunk = slice_methods(start, end)
        for line in chunk.splitlines(keepends=True):
            if line.strip():
                parts.append(INDENT + line)
            else:
                parts.append("\n")
    return "".join(parts)

DIALOGS_IMPORTS = '''
import subprocess
import sys
from pathlib import Path

import tkinter as tk
from tkinter import ttk

from videobuilder.core.pipeline import MissingSceneImagesError, RenderCancelled
from videobuilder.gui.constants import C
'''

write_mixin(GUI / "dialogs.py", "DialogMixin", DIALOGS_IMPORTS, body_from_ranges([(648, 868)]))

WIDGETS_IMPORTS = '''
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from videobuilder.gui.constants import C, FIELD_HELP
'''

write_mixin(
    GUI / "widgets.py",
    "WidgetMixin",
    WIDGETS_IMPORTS,
    body_from_ranges([(516, 647), (870, 983), (1139, 1204)]),
)

SRT_IMPORTS = '''
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

from videobuilder.core.create_srt import (
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    WHISPER_MODELS,
    WHISPER_NUMPY_SPEC,
    CreateSrtError,
    CreateSrtCancelled,
    check_whisper,
    create_srt,
    default_srt_path,
    whisper_model_status_line,
)
from videobuilder.core.pipeline import ProcessController
from videobuilder.core.ffmpeg_setup import check_ffmpeg
from videobuilder.core.pipeline import DEFAULT_PREVIEW_SECONDS
from videobuilder.gui.constants import C, SRT_FIELD_LABEL_WIDTH, SRT_LANGUAGE_OPTIONS
from videobuilder.gui.paths import is_writable_output_dir
'''

write_mixin(
    GUI / "srt_tab.py",
    "SrtTabMixin",
    SRT_IMPORTS,
    body_from_ranges([(984, 1138), (1256, 1261), (1660, 2005)]),
)

SHELL_IMPORTS = '''
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
    SmoothProgressTracker,
    StatusPresenter,
    short_render_status,
    short_srt_status,
)
from videobuilder.version import APP_VERSION
'''

write_mixin(
    GUI / "shell.py",
    "ShellMixin",
    SHELL_IMPORTS,
    body_from_ranges([(1205, 1255), (1262, 1351), (1352, 1656)]),
)

PROJECT_IMPORTS = '''
import json
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

from videobuilder.gui.constants import C, TELEGRAM_HANDLE, TELEGRAM_URL
from videobuilder.gui.paths import (
    default_output_folder,
    default_output_path,
    get_settings_file,
    is_writable_output_dir,
    normalize_output_path,
)
'''

write_mixin(
    GUI / "project_tab.py",
    "ProjectTabMixin",
    PROJECT_IMPORTS,
    body_from_ranges([(1657, 1659), (2006, 2122), (2218, 2407), (2539, 2629)]),
)

RENDER_IMPORTS = '''
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
from videobuilder.core.ffmpeg_setup import check_ffmpeg, ensure_ffmpeg_on_path, install_ffmpeg
'''

write_mixin(
    GUI / "render_tab.py",
    "RenderTabMixin",
    RENDER_IMPORTS,
    body_from_ranges([(2123, 2217), (2408, 2538), (2631, 2936)]),
)

init_src = slice_methods(433, 515)
init_body = "".join(("    " + line if line.strip() else line) for line in init_src.splitlines(keepends=True))

# --- thin app.py ---
APP = f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tkinter as tk

from videobuilder.core.ffmpeg_setup import ensure_ffmpeg_on_path
from videobuilder.core.pipeline import DEFAULT_PREVIEW_SECONDS, ENCODE_QUALITY_OPTIONS, ENCODER_OVERRIDE_OPTIONS, ZOOM_LEVEL_OPTIONS, detect_video_encoder
from videobuilder.core.create_srt import DEFAULT_LANGUAGE, DEFAULT_MODEL
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
{init_body}


def main():
    app = VideoBuilderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
'''

(GUI / "app.py").write_text(APP, encoding="utf-8")
print("wrote app.py")
