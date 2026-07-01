import json
from pathlib import Path

import pytest


def test_hydrate_api_keys_from_env(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GROQ_API_KEY=gsk_from_env\nGEMINI_API_KEY=gem_from_env\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("videobuilder.core.env_config.project_root", lambda: tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import videobuilder.core.env_config as env_config

    def _load_test_env(*, force: bool = False) -> None:
        if env_config._loaded and not force:
            return
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file, override=True)
        except ImportError:
            env_config._load_env_manual(env_file)
        env_config._loaded = True

    monkeypatch.setattr("videobuilder.core.env_config.load_env", _load_test_env)
    env_config._loaded = False

    import tkinter as tk

    from videobuilder.gui.app import VideoBuilderApp

    settings = tmp_path / ".video_builder_settings.json"
    settings.write_text(json.dumps({"groq_api_key": "", "gemini_api_key": ""}), encoding="utf-8")
    monkeypatch.setattr("videobuilder.gui.paths.get_settings_file", lambda: settings)
    monkeypatch.setattr("videobuilder.gui.project_tab.get_settings_file", lambda: settings)

    root = tk.Tk()
    root.withdraw()
    app = VideoBuilderApp()
    app.update()
    assert app.groq_api_key_var.get() == "gsk_from_env"
    assert app.gemini_api_key_var.get() == "gem_from_env"
    app.destroy()
    root.destroy()
