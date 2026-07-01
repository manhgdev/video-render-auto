"""Test render khi bỏ qua scene thiếu ảnh."""

from pathlib import Path

import pytest

from videobuilder.core.pipeline import (
    MissingSceneImagesError,
    build_scene_pairs,
    scenes_to_timeline_pairs,
)

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20


def _write_jpg(folder: Path, name: str) -> Path:
    path = folder / name
    path.write_bytes(_JPEG)
    return path


class TestSkipMissingTimeline:
    def test_raises_when_not_skipping(self, tmp_path: Path):
        prompts = tmp_path / "prompts.txt"
        prompts.write_text(
            "001_[00:00.00-00:05.00] one\n\n"
            "002_[00:05.00-00:10.00] two\n",
            encoding="utf-8",
        )
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        _write_jpg(images_dir, "001_one.jpg")

        from videobuilder.core.pipeline import parse_prompt_scenes

        scenes = parse_prompt_scenes(prompts, 60.0)
        with pytest.raises(MissingSceneImagesError):
            scenes_to_timeline_pairs(scenes, images_dir, 60.0, skip_missing=False)

    def test_extends_previous_image_over_missing_scene(self, tmp_path: Path):
        prompts = tmp_path / "prompts.txt"
        prompts.write_text(
            "001_[00:00.00-00:05.00] one\n\n"
            "002_[00:05.00-00:10.00] two\n\n"
            "003_[00:10.00-00:15.00] three\n",
            encoding="utf-8",
        )
        images_dir = tmp_path / "images"
        images_dir.mkdir()
        img1 = _write_jpg(images_dir, "001_one.jpg")
        img3 = _write_jpg(images_dir, "003_three.jpg")

        from videobuilder.core.pipeline import parse_prompt_scenes

        scenes = parse_prompt_scenes(prompts, 60.0)
        pairs = scenes_to_timeline_pairs(scenes, images_dir, 60.0, skip_missing=True)

        assert len(pairs) == 2
        assert pairs[0][0] == img1
        assert pairs[0][1] == pytest.approx(0.0)
        assert pairs[0][2] == pytest.approx(10.0)
        assert pairs[1][0] == img3
        assert pairs[1][1] == pytest.approx(10.0)
        assert pairs[1][2] == pytest.approx(60.0)

    def test_raises_when_no_images_at_all(self, tmp_path: Path):
        prompts = tmp_path / "prompts.txt"
        prompts.write_text(
            "001_[00:00.00-00:05.00] one\n\n"
            "002_[00:05.00-00:10.00] two\n",
            encoding="utf-8",
        )
        images_dir = tmp_path / "images"
        images_dir.mkdir()

        from videobuilder.core.pipeline import parse_prompt_scenes

        scenes = parse_prompt_scenes(prompts, 60.0)
        with pytest.raises(RuntimeError, match=r"Không (có scene nào có ảnh|tìm thấy ảnh)"):
            build_scene_pairs(images_dir, scenes, 60.0, skip_missing=True)
