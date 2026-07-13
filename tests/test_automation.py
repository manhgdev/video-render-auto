from pathlib import Path

import pytest

from videobuilder.core.automation import (
    AutomationError,
    TOOL_PRODUCTION_PROMPT,
    _base_system_prompt,
    _parse_json_object,
    discover_existing_topic_hints,
    filter_unique_topics,
    read_optional_text_auto,
    project_folder_for_topic,
    slugify_topic,
)


def test_slugify_topic_vietnamese():
    assert slugify_topic("Bạn Sẽ Sống Sót Thế Nào?") == "ban_se_song_sot_the_nao"
    assert slugify_topic("  ") == "video"


def test_project_folder_uses_topic_slug(tmp_path: Path):
    folder = project_folder_for_topic("Lửa Trại Cổ Đại!", tmp_path)
    assert folder == tmp_path / "lua_trai_co_dai"


def test_parse_json_object_accepts_markdown_fence():
    data = _parse_json_object('```json\n{"topics":["a","b"]}\n```')
    assert data == {"topics": ["a", "b"]}


def test_parse_json_object_rejects_missing_object():
    with pytest.raises(AutomationError):
        _parse_json_object("khong co json")


def test_optional_prompt_missing_file_is_empty(tmp_path: Path):
    assert read_optional_text_auto(tmp_path / "missing.txt") == ""


def test_base_prompt_is_tool_oriented():
    prompt = _base_system_prompt("")
    assert TOOL_PRODUCTION_PROMPT.strip() in prompt
    assert '{"script":"..."}' in prompt


def test_filter_unique_topics_removes_duplicates_and_excluded():
    topics = [
        "Top 5 siêu năng lực con người có thể đạt được",
        "Top 5 siêu năng lực con người có thể đạt được!",
        "Bí mật lửa trại cổ đại",
    ]
    assert filter_unique_topics(topics, ["top_5_sieu_nang_luc_con_nguoi_co_the_dat_duoc"]) == [
        "Bí mật lửa trại cổ đại",
    ]


def test_auto_packages_status_fast(monkeypatch):
    from videobuilder.core.automation import auto_packages_status, invalidate_auto_packages_cache

    monkeypatch.setattr(
        "videobuilder.core.automation._auto_package_installed",
        lambda name: name == "groq",
    )
    monkeypatch.setattr(
        "videobuilder.core.create_srt.groq_api_key",
        lambda: "key",
    )
    monkeypatch.setattr(
        "videobuilder.core.automation.elevenlabs_api_keys",
        lambda: [],
    )
    invalidate_auto_packages_cache()
    status = auto_packages_status(force=True)
    assert status["groq_ok"] is True
    assert status["elevenlabs_key"] is False
    assert status["needs_install"] is True
    assert "yt-dlp" in status["missing"]
    assert status["ready_for_topics"] is True
    assert status["ready_for_pipeline"] is False


def test_split_text_for_tts_chunks():
    from videobuilder.core.automation import _split_text_for_tts

    short = "Xin chào."
    assert _split_text_for_tts(short, max_chars=100) == [short]
    long = ("Câu một. " * 50) + ("Câu hai. " * 50)
    parts = _split_text_for_tts(long, max_chars=80)
    assert len(parts) >= 2
    assert all(len(p) <= 80 for p in parts)
    assert "Câu một" in parts[0]
    assert sum(len(p) for p in parts) >= len(long) - len(parts)  # strip mất khoảng trắng biên

def test_normalize_auto_duration():
    from videobuilder.core.automation import normalize_auto_duration

    assert normalize_auto_duration("6") == "6"
    assert normalize_auto_duration("Short 10 giây") == "10"
    assert normalize_auto_duration("Dài (7–12 phút)") == "full"
    assert normalize_auto_duration("") == "full"


def test_macos_say_voice_list_or_unavailable():
    from videobuilder.core.automation import list_macos_say_voice_names, macos_say_available

    if macos_say_available():
        names = list_macos_say_voice_names()
        assert names
        assert "Linh" in names or any("Linh" in n for n in names)
    else:
        assert list_macos_say_voice_names() == []


def test_synthesize_text_macos_say_smoke(tmp_path: Path):
    import sys

    from videobuilder.core.automation import (
        AutomationError,
        macos_say_available,
        synthesize_text_macos_say,
    )

    out = tmp_path / "say_smoke.mp3"
    if not macos_say_available():
        with pytest.raises(AutomationError, match="macOS"):
            synthesize_text_macos_say("hello", out)
        return
    if sys.platform != "darwin":
        return
    path = synthesize_text_macos_say("Xin chào.", out, voice="Linh")
    assert path.is_file()
    assert path.stat().st_size > 500


def test_discover_existing_topic_hints_from_project_dirs(tmp_path: Path):
    project = tmp_path / "top_5_sieu_nang_luc_con_nguoi_co_the_dat_duoc"
    project.mkdir()
    (project / "audio_script_top_5_sieu_nang_luc_con_nguoi_co_the_dat_duoc.txt").write_text(
        "x",
        encoding="utf-8",
    )
    hints = discover_existing_topic_hints(tmp_path)
    assert "top_5_sieu_nang_luc_con_nguoi_co_the_dat_duoc" in hints
