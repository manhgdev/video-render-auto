"""Timeline path resolution after image_prompts → timeline rename."""

from __future__ import annotations

from pathlib import Path

from videobuilder.core.timeline_paths import (
    default_timeline_path,
    legacy_prompt_filename,
    resolve_timeline_path,
    timeline_filename,
)


def test_timeline_filename():
    assert timeline_filename("my_topic") == "timeline_my_topic.txt"
    assert legacy_prompt_filename("my_topic") == "image_prompts_my_topic.txt"


def test_resolve_timeline_path_after_rename(tmp_path: Path):
    legacy = tmp_path / legacy_prompt_filename("clip")
    timeline = tmp_path / timeline_filename("clip")
    timeline.write_text("scene 1\n", encoding="utf-8")
    resolved = resolve_timeline_path(str(legacy))
    assert resolved == timeline.resolve()


def test_resolve_timeline_path_scans_folder(tmp_path: Path):
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"\x00")
    found = tmp_path / "timeline_voice.mp3.txt"
    found.write_text("prompt line\n", encoding="utf-8")
    resolved = resolve_timeline_path(None, audio_path=audio)
    assert resolved == found.resolve()


def test_default_timeline_path_prefers_existing(tmp_path: Path):
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"\x00")
    legacy = tmp_path / legacy_prompt_filename("track")
    legacy.write_text("old name still works\n", encoding="utf-8")
    assert default_timeline_path(audio) == legacy.resolve()


def test_resolve_timeline_path_from_dir_and_stem(tmp_path: Path):
    target = tmp_path / "timeline_demo.txt"
    target.write_text("x\n", encoding="utf-8")
    resolved = resolve_timeline_path(
        str(tmp_path / "image_prompts_demo.txt"),
        folder=tmp_path,
        stem="demo",
    )
    assert resolved == target.resolve()


def test_resolve_timeline_path_stem_alias_after_rename(tmp_path: Path):
    target = tmp_path / "timeline_demo.txt"
    target.write_text("x\n", encoding="utf-8")
    resolved = resolve_timeline_path(
        folder=tmp_path,
        stem="image_prompts_demo",
    )
    assert resolved == target.resolve()


def test_resolve_timeline_path_scans_when_stem_is_timeline(tmp_path: Path):
    target = tmp_path / "timeline_my_video.txt"
    target.write_text("scene\n", encoding="utf-8")
    resolved = resolve_timeline_path(folder=tmp_path, stem="timeline")
    assert resolved == target.resolve()


def test_resolve_timeline_path_prefers_image_prompts_over_audio_script(tmp_path: Path):
    """Không nhầm *_AUDIO_SCRIPT.txt với file image_prompts có scene."""
    audio = tmp_path / "WW2_ALTERNATE_HISTORY_ENGLISH_USA_AUDIO_SCRIPT.mp3"
    audio.write_bytes(b"\x00")
    script = tmp_path / "WW2_ALTERNATE_HISTORY_ENGLISH_USA_AUDIO_SCRIPT.txt"
    script.write_text("Plain narration without scene markers.\n", encoding="utf-8")
    prompts = tmp_path / "image_prompts_ww2_VEO3_KEYWORDS.txt"
    prompts.write_text(
        "001_[00.00.000-00.13.720] Scene one\n\n"
        "002_[00.13.720-00.26.272] Scene two\n",
        encoding="utf-8",
    )
    resolved = resolve_timeline_path(None, audio_path=audio)
    assert resolved == prompts.resolve()
    assert default_timeline_path(audio) == prompts.resolve()
