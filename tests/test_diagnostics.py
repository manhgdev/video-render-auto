from pathlib import Path


def test_write_diagnostic_creates_persistent_log(monkeypatch, tmp_path):
    from videobuilder.core import diagnostics

    log_file = tmp_path / "logs" / "videobuilder.log"
    monkeypatch.setattr(diagnostics, "diagnostics_file", lambda: log_file)

    assert diagnostics.write_diagnostic("render click failed", "error") == log_file
    content = log_file.read_text(encoding="utf-8")
    assert "[ERROR] render click failed" in content


def test_app_version_is_requested_release():
    from videobuilder.version import APP_VERSION, exe_filename

    assert APP_VERSION == "1.2.3"
    assert exe_filename() == "VideoBuilder_v1.2.3.exe"


def test_frozen_windows_ffmpeg_uses_user_app_data(monkeypatch, tmp_path):
    import sys

    from videobuilder.core import ffmpeg_setup

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(ffmpeg_setup.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert ffmpeg_setup.get_tools_dir() == tmp_path / "VideoBuilder" / "tools" / "ffmpeg"
