"""Test đọc file prompt / timeline scene."""

from pathlib import Path

import pytest

from videobuilder.core.pipeline import (
    parse_prompt_scenes,
    parse_scene_bracket_time,
)


class TestParseSceneBracketTime:
    def test_colon_with_fraction(self):
        assert parse_scene_bracket_time("00", ":", "06", "33") == pytest.approx(6.33)

    def test_legacy_dot_format(self):
        assert parse_scene_bracket_time("00", ".", "06", None) == 6.0

    def test_minutes(self):
        assert parse_scene_bracket_time("01", ":", "03", "77") == pytest.approx(63.77)


class TestParsePromptScenes:
    def test_srt_locked_format(self, tmp_path: Path):
        text = (
            "001_[00:00.00-00:06.33] Scene one\n"
            "002_[00:06.33-00:12.40] Scene two\n"
        )
        path = tmp_path / "prompts.txt"
        path.write_text(text, encoding="utf-8")
        scenes = parse_prompt_scenes(path, 9999.0)
        assert len(scenes) == 2
        assert scenes[0] == (1, 0.0, pytest.approx(6.33))
        assert scenes[1] == (2, pytest.approx(6.33), pytest.approx(12.4))

    def test_legacy_dot_format(self, tmp_path: Path):
        text = "001_[00.00-00.06]_image.jpg\n002_[00.06-00.12]_next.jpg\n"
        path = tmp_path / "prompts.txt"
        path.write_text(text, encoding="utf-8")
        scenes = parse_prompt_scenes(path, 60.0)
        assert scenes[0][0] == 1
        assert scenes[0][1] == 0.0
        assert scenes[0][2] == 6.0

    def test_bracket_only_fallback(self, tmp_path: Path):
        text = "[00:00-00:04] intro\n[00:04-00:10] body\n"
        path = tmp_path / "prompts.txt"
        path.write_text(text, encoding="utf-8")
        scenes = parse_prompt_scenes(path, 60.0)
        assert scenes == [(1, 0.0, 4.0), (2, 4.0, 10.0)]

    def test_empty_raises(self, tmp_path: Path):
        path = tmp_path / "empty.txt"
        path.write_text("no timestamps here\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="Không tìm thấy scene"):
            parse_prompt_scenes(path, 60.0)

    def test_skips_character_reference(self, tmp_path: Path):
        text = (
            "001_[CHARACTER REFERENCE]\n"
            "002_[00:00.00-00:05.00] real scene\n"
        )
        path = tmp_path / "prompts.txt"
        path.write_text(text, encoding="utf-8")
        scenes = parse_prompt_scenes(path, 60.0)
        assert len(scenes) == 1
        assert scenes[0][0] == 2
