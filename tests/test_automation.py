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


def test_discover_existing_topic_hints_from_project_dirs(tmp_path: Path):
    project = tmp_path / "top_5_sieu_nang_luc_con_nguoi_co_the_dat_duoc"
    project.mkdir()
    (project / "audio_script_top_5_sieu_nang_luc_con_nguoi_co_the_dat_duoc.txt").write_text(
        "x",
        encoding="utf-8",
    )
    hints = discover_existing_topic_hints(tmp_path)
    assert "top_5_sieu_nang_luc_con_nguoi_co_the_dat_duoc" in hints
