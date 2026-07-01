from pathlib import Path

from videobuilder.core.path_checks import path_exists_or_assume, path_is_file_safe


def test_path_is_file_safe_missing():
    assert path_is_file_safe("/nonexistent/vb_test_12345.txt", timeout=0.2) is False


def test_path_exists_or_assume_timeout_assumes_true(monkeypatch):
    from videobuilder.core import path_checks as pc

    monkeypatch.setattr(pc, "path_exists_safe", lambda *_a, **_k: None)
    assert path_exists_or_assume("Z:/maybe/slow.txt", is_dir=False) is True


def test_path_exists_or_assume_real_file(tmp_path: Path):
    f = tmp_path / "ok.txt"
    f.write_text("x", encoding="utf-8")
    assert path_exists_or_assume(f, is_dir=False) is True
