from videobuilder.core.progress import report_progress, reset_progress_floor
from videobuilder.gui.progress import (
    short_render_status,
    short_srt_status,
    should_log_render_progress,
)


def test_report_progress_monotonic():
    reset_progress_floor()
    seen = []

    def cb(pct, msg):
        seen.append(pct)

    report_progress(cb, 10, "a")
    report_progress(cb, 5, "b")
    report_progress(cb, 20, "c")
    assert seen == [10, 10, 20]


def test_short_render_status_collapses_prep():
    assert short_render_status("Đọc audio & timeline...") == "Chuẩn bị..."
    assert short_render_status("Zoom scene 3/20...") == "Zoom 3/20"


def test_short_srt_status():
    assert short_srt_status("Đang nhận dạng giọng nói...") == "Nhận dạng giọng nói..."
    assert short_srt_status("Tải model Whisper (cuda)...") == "Tải model Whisper..."


def test_should_log_render_progress():
    assert should_log_render_progress("Ghép 20 scene...") is True
    assert should_log_render_progress("Đang encode... 12%") is False
    assert should_log_render_progress("Đọc file prompt...") is False
