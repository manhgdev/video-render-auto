"""GUI thread bridge + lightweight whisper check."""

from __future__ import annotations

import threading
import time

import pytest

from tests._tk_skip import requires_tk


def test_check_whisper_light_does_not_import_faster_whisper(monkeypatch):
    from videobuilder.core import create_srt as cs

    def boom():
        raise AssertionError("faster_whisper must not be imported")

    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", type("M", (), {"WhisperModel": boom})())
    monkeypatch.setattr(cs, "groq_api_key", lambda: "test-key")
    monkeypatch.setattr(cs, "groq_client_available", lambda: True)
    monkeypatch.setattr(cs, "srt_packages_status", lambda: {"groq_ok": True, "needs_install": False})
    monkeypatch.setattr(cs, "load_cached_groq_models", lambda: None)
    monkeypatch.setattr(cs, "groq_whisper_chain_label", lambda _lang="": "whisper-large-v3-turbo")

    status = cs.check_whisper_light()
    assert status["ok"] is True
    assert status["groq"] is True


@requires_tk
def test_run_on_ui_thread_from_worker():
    import tkinter as tk

    from videobuilder.gui.app import VideoBuilderApp

    root = tk.Tk()
    root.withdraw()
    app = VideoBuilderApp()
    app.update()

    result: list[str] = []
    event = threading.Event()

    def worker():
        try:
            app._run_on_ui_thread(lambda: result.append("ok"))
        except Exception as exc:
            result.append(f"err:{exc}")
        finally:
            event.set()

    threading.Thread(target=worker, daemon=True).start()
    assert event.wait(timeout=5.0), "worker did not finish"
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        app.update()
        if result:
            break
        time.sleep(0.02)

    app.destroy()
    assert result == ["ok"]


@requires_tk
def test_auto_tab_switch_is_fast():
    import time
    import tkinter as tk

    from videobuilder.gui.app import VideoBuilderApp

    root = tk.Tk()
    root.withdraw()
    app = VideoBuilderApp()
    app.update()
    t0 = time.perf_counter()
    app._show_tab("auto")
    app.update()
    elapsed = time.perf_counter() - t0
    assert getattr(app, "auto_status_var", None) is not None
    app.destroy()
    root.destroy()
    assert elapsed < 0.25, f"tab auto took {elapsed:.2f}s"
