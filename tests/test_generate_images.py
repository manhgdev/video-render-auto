#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path

import pytest

from videobuilder.core.generate_images import (
    GenerateImagesError,
    image_output_path,
    parse_prompt_entries,
    resolve_aspect_ratio,
    _api_error_kind,
    _format_model_errors,
    _scene_has_image,
)


SAMPLE_LINE = (
    '001_[00.00.00-00.00.01.92] CHARACTER BIBLE: hero '
    'Câu audio bám sát: "hello" Ý cảnh: intro '
    'Hình ảnh cần thể hiện: wave Góc máy: wide '
    'Bối cảnh: studio Nhãn trong ảnh: none Phong cách: flat'
)


def test_parse_prompt_entries(tmp_path: Path):
    prompt_file = tmp_path / "prompts.txt"
    prompt_file.write_text(SAMPLE_LINE + "\n\n002_[00.00.02-00.00.04] test\n", encoding="utf-8")
    entries = parse_prompt_entries(prompt_file)
    assert len(entries) == 2
    assert entries[0].scene_num == 1
    assert entries[0].start == pytest.approx(0.0)
    assert entries[0].end == pytest.approx(1.92, rel=0.01)
    assert entries[1].scene_num == 2


def test_parse_prompt_entries_empty(tmp_path: Path):
    prompt_file = tmp_path / "empty.txt"
    prompt_file.write_text("no valid lines\n", encoding="utf-8")
    with pytest.raises(GenerateImagesError):
        parse_prompt_entries(prompt_file)


def test_image_output_path(tmp_path: Path):
    from videobuilder.core.generate_images import PromptImageEntry

    entry = PromptImageEntry(scene_num=1, start=0.0, end=1.92, line=SAMPLE_LINE)
    path = image_output_path(entry, tmp_path)
    assert path.name == "001_[00.00.00-00.00.01.92].jpg"


def test_scene_has_image_empty_dir(tmp_path: Path):
    assert _scene_has_image(1, tmp_path) is False


def test_format_quota_error():
    assert _api_error_kind(Exception("429 RESOURCE_EXHAUSTED quota")) == "quota"
    msg = _format_model_errors([
        ("gemini-2.5-flash-image", "quota", "429"),
        ("gemini-3.1-flash-image", "quota", "429"),
    ])
    assert "hết quota" in msg.lower()


def test_resolve_aspect_ratio():
    assert resolve_aspect_ratio("9:16") == "9:16"
    assert resolve_aspect_ratio("auto", resolution_label="Shorts (9:16)") == "9:16"
    assert resolve_aspect_ratio("auto", resolution_label="1080p (16:9)") == "16:9"
