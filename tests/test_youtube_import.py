from pathlib import Path

from videobuilder.core.youtube_import import (
    _clean_ytdlp_error,
    _pick_subtitle_file,
    _subtitle_rank,
    normalize_youtube_url,
)


def test_clean_ytdlp_error_strips_ansi():
    err = Exception("\x1b[0;31mERROR:\x1b[0m HTTP Error 429: Too Many Requests")
    assert "429" in _clean_ytdlp_error(err)
    assert "\x1b" not in _clean_ytdlp_error(err)


def test_normalize_youtube_url():
    assert "watch?v=" in normalize_youtube_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert "youtu.be/" in normalize_youtube_url("youtu.be/dQw4w9WgXcQ")


def test_pick_subtitle_prefers_vietnamese(tmp_path: Path):
    en = tmp_path / "abc.en.srt"
    vi = tmp_path / "abc.vi.srt"
    en.write_text("1\n", encoding="utf-8")
    vi.write_text("2\n", encoding="utf-8")
    assert _pick_subtitle_file(tmp_path) == vi
    assert _subtitle_rank(vi)[0] < _subtitle_rank(en)[0]
