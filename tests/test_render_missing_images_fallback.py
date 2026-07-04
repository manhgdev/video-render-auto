from pathlib import Path
from types import SimpleNamespace

from videobuilder.core.pipeline import MissingSceneImagesError


class _DummyRenderTab:
    def __init__(self):
        self.rendering = False
        self.srt_running = False
        self.ffmpeg_ok = True
        self.gemini_api_key_var = SimpleNamespace(get=lambda: "")
        self._warnings: list[tuple[str, str]] = []
        self._logs: list[tuple[str, str]] = []
        self._build_options = None

    def _validate(self, *, preview=False, skip_missing_images=False):
        if not skip_missing_images:
            raise MissingSceneImagesError([2], [(1, 0.0, 5.0), (2, 5.0, 10.0)], Path("/tmp/images"))
        return {"preview": preview, "skip_missing_images": skip_missing_images}

    def _show_warning(self, title, message):
        self._warnings.append((title, message))

    def _show_validation_error(self, err):
        raise AssertionError(f"unexpected validation error: {err}")

    def _log(self, message, level="info"):
        self._logs.append((level, message))

    def _can_generate_missing_images(self):
        return False

    def _begin_render_skipping_missing_images(self, err, *, preview):
        from videobuilder.gui import render_tab as rt

        return rt.RenderTabMixin._begin_render_skipping_missing_images(self, err, preview=preview)

    def _run_build(self, options, ask_open=True):
        self._build_options = options


def test_start_render_falls_back_without_gemini(monkeypatch):
    from videobuilder.core import generate_images as gi
    from videobuilder.gui import render_tab as rt

    monkeypatch.setattr(rt, "check_ffmpeg", lambda: {"ok": True})
    monkeypatch.setattr(gi, "check_gemini_image", lambda *, api_key=None: {"ok": False, "needs_install": True})

    dummy = _DummyRenderTab()

    rt.RenderTabMixin._start_render(dummy)

    assert dummy._build_options == {"preview": False, "skip_missing_images": True}
    assert dummy._warnings
    assert "bỏ qua scene thiếu ảnh" in dummy._warnings[0][1].lower()
    assert any(level == "warn" for level, _ in dummy._logs)