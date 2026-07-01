"""Test run_prompts_from_srt."""

from pathlib import Path
from unittest.mock import patch

import pytest

from videobuilder.core.audio_pipeline import AudioPipelineError, run_prompts_from_srt


def test_run_prompts_from_srt_empty(tmp_path: Path, monkeypatch):
    srt = tmp_path / "a.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "videobuilder.core.audio_pipeline.groq_api_key",
        lambda: "test-key",
    )
    monkeypatch.setattr(
        "videobuilder.core.audio_pipeline.groq_client_available",
        lambda: True,
    )
    with pytest.raises(AudioPipelineError, match="SRT rỗng"):
        run_prompts_from_srt(srt, tmp_path / "out.txt")


def test_run_prompts_from_srt_calls_llm(tmp_path: Path, monkeypatch):
    srt = tmp_path / "a.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nXin chào\n\n",
        encoding="utf-8",
    )
    out = tmp_path / "prompts.txt"

    monkeypatch.setattr(
        "videobuilder.core.audio_pipeline.groq_api_key",
        lambda: "test-key",
    )
    monkeypatch.setattr(
        "videobuilder.core.audio_pipeline.groq_client_available",
        lambda: True,
    )

    with patch(
        "videobuilder.core.audio_pipeline.generate_image_prompts_from_segments",
        return_value=out,
    ) as mock_gen:
        result = run_prompts_from_srt(srt, out)

    assert result == out
    mock_gen.assert_called_once()
    segments = mock_gen.call_args[0][0]
    assert len(segments) == 1
    assert segments[0].text == "Xin chào"
