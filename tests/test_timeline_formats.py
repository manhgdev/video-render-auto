"""Test 7 định dạng timestamp timeline."""

from pathlib import Path

import pytest

from videobuilder.core.timeline_formats import (
    format_prompt_line_prefix,
    format_readable_timecode,
    format_readable_time_range,
    parse_bracket_time_range,
    parse_prompt_scene_line,
    parse_prompt_timecode_token,
    parse_srt_arrow_line,
    parse_time_token,
    scenes_from_timeline_text,
)
from videobuilder.core.pipeline import parse_prompt_scenes


class TestParseTimeToken:
    """Đọc từng token thời gian."""

    def test_srt_comma(self):
        assert parse_time_token("00:00:00,000") == 0.0
        assert parse_time_token("00:00:09,000") == pytest.approx(9.0)

    def test_srt_dot(self):
        assert parse_time_token("00:00:00.000") == 0.0
        assert parse_time_token("00:00:09.000") == pytest.approx(9.0)

    def test_technical_colon_ms(self):
        assert parse_time_token("00:00:00:000") == 0.0
        assert parse_time_token("00:00:09:000") == pytest.approx(9.0)

    def test_readable_mm_ss_mmm(self):
        assert parse_time_token("00:00.000") == 0.0
        assert parse_time_token("00:09.000") == pytest.approx(9.0)

    def test_legacy_dot(self):
        assert parse_prompt_timecode_token("00.00.01.92") == pytest.approx(1.92)
        assert parse_prompt_timecode_token("00:06.33") == pytest.approx(6.33)


class TestSrtArrow:
    def test_comma(self):
        pair = parse_srt_arrow_line("00:00:00,000 --> 00:00:09,000")
        assert pair == (0.0, 9.0)

    def test_dot(self):
        pair = parse_srt_arrow_line("00:00:00.000 --> 00:00:09.000")
        assert pair == (0.0, 9.0)


class TestBracketRanges:
    def test_technical_colon(self):
        pair = parse_bracket_time_range("[00:00:00:000-00:00:09:000]")
        assert pair == (0.0, 9.0)

    def test_technical_dot(self):
        pair = parse_bracket_time_range("[00:00:00.000-00:00:09.000]")
        assert pair == (0.0, 9.0)

    def test_readable(self):
        pair = parse_bracket_time_range("[00:00.000-00:09.000]")
        assert pair == (0.0, 9.0)


class TestPromptSceneLine:
    def test_single_visual(self):
        entry = parse_prompt_scene_line("001_[00:00.000-00:09.000] Scene prompt")
        assert entry is not None
        assert entry.scene_num == 1
        assert entry.start == 0.0
        assert entry.end == pytest.approx(9.0)
        assert entry.visual_index == 1
        assert entry.visual_total == 1

    def test_visual_split(self):
        entry = parse_prompt_scene_line(
            "001_[00:00.000-00:09.000]_VISUAL_01_03 beat one"
        )
        assert entry is not None
        assert entry.visual_index == 1
        assert entry.visual_total == 3
        assert entry.start == 0.0
        assert entry.end == pytest.approx(3.0)

        entry3 = parse_prompt_scene_line(
            "001_[00:00.000-00:09.000]_VISUAL_03_03 beat three"
        )
        assert entry3.start == pytest.approx(6.0)
        assert entry3.end == pytest.approx(9.0)


class TestFormatReadable:
    def test_zero(self):
        assert format_readable_timecode(0.0) == "00:00.000"

    def test_nine_seconds(self):
        assert format_readable_timecode(9.0) == "00:09.000"

    def test_range(self):
        assert format_readable_time_range(0.0, 9.0) == "00:00.000-00:09.000"

    def test_prompt_prefix_single(self):
        assert format_prompt_line_prefix(1, 0.0, 9.0) == "001_[00:00.000-00:09.000]"

    def test_prompt_prefix_visual(self):
        assert (
            format_prompt_line_prefix(1, 0.0, 9.0, visual_index=1, visual_total=3)
            == "001_[00:00.000-00:09.000]_VISUAL_01_03"
        )


class TestScenesFromTimelineText:
    def test_all_seven_formats_via_parse_prompt_scenes(self, tmp_path: Path):
        cases = [
            (
                "srt_comma.srt",
                "1\n00:00:00,000 --> 00:00:09,000\nText\n",
                (1, 0.0, 9.0),
            ),
            (
                "srt_dot.srt",
                "1\n00:00:00.000 --> 00:00:09.000\nText\n",
                (1, 0.0, 9.0),
            ),
            (
                "tech_colon.txt",
                "[00:00:00:000-00:00:09:000] intro\n",
                (1, 0.0, 9.0),
            ),
            (
                "tech_dot.txt",
                "[00:00:00.000-00:00:09.000] intro\n",
                (1, 0.0, 9.0),
            ),
            (
                "readable.txt",
                "[00:00.000-00:09.000] intro\n",
                (1, 0.0, 9.0),
            ),
            (
                "prompt.txt",
                "001_[00:00.000-00:09.000] scene\n",
                (1, 0.0, 9.0),
            ),
            (
                "visual.txt",
                "001_[00:00.000-00:09.000]_VISUAL_02_03 scene\n",
                (1, pytest.approx(3.0), pytest.approx(6.0)),
            ),
        ]
        for name, content, expected in cases:
            path = tmp_path / name
            path.write_text(content, encoding="utf-8")
            scenes = parse_prompt_scenes(path, 60.0)
            assert len(scenes) >= 1, name
            assert scenes[0][0] == expected[0], name
            assert scenes[0][1] == expected[1], name
            assert scenes[0][2] == expected[2], name

    def test_visual_file_three_beats(self, tmp_path: Path):
        text = (
            "001_[00:00.000-00:09.000]_VISUAL_01_03 a\n"
            "001_[00:00.000-00:09.000]_VISUAL_02_03 b\n"
            "001_[00:00.000-00:09.000]_VISUAL_03_03 c\n"
        )
        scenes = scenes_from_timeline_text(text, 60.0)
        assert len(scenes) == 3
        assert scenes[0][1] == 0.0
        assert scenes[0][2] == pytest.approx(3.0)
        assert scenes[2][2] == pytest.approx(9.0)
