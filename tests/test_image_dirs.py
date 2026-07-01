from pathlib import Path

import pytest

from videobuilder.core.pipeline import (
    count_valid_images,
    discover_images_dir,
    is_valid_image_file,
    list_images,
    resolve_images_dir,
)


def test_is_valid_image_file_jpeg(tmp_path: Path):
    good = tmp_path / "001_scene.jpg"
    good.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    bad = tmp_path / "fake.png"
    bad.write_text("<html>error</html>", encoding="utf-8")
    assert is_valid_image_file(good) is True
    assert is_valid_image_file(bad) is False


def test_list_images_rejects_fake_bulk_png(tmp_path: Path):
    folder = tmp_path / "gemini-folder-99"
    folder.mkdir()
    (folder / "Bulk_img_gen_00_52_1-07-2026_1.png").write_text("<html></html>", encoding="utf-8")
    with pytest.raises(RuntimeError, match="không phải ảnh hợp lệ"):
        list_images(folder)


def test_resolve_images_dir_picks_veo_subfolder(tmp_path: Path):
    root = tmp_path / "Downloads"
    root.mkdir()
    bad = root / "gemini-folder-1112"
    bad.mkdir()
    (bad / "Bulk_img_gen_00_52_1-07-2026_1.png").write_text("<html></html>", encoding="utf-8")
    good = root / "veo-folder-002"
    good.mkdir()
    (good / "001_hook.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    assert resolve_images_dir(root) == good.resolve()
    assert count_valid_images(good) == (1, 1)


def test_resolve_images_dir_picks_veo_sibling_when_gemini_mostly_fake(tmp_path: Path):
    root = tmp_path / "Downloads"
    root.mkdir()
    bad = root / "gemini-folder-1112"
    bad.mkdir()
    (bad / "Bulk_img_gen_00_52_1-07-2026_1.png").write_text("<html></html>", encoding="utf-8")
    (bad / "Bulk_img_gen_00_52_1-07-2026_2.png").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    good = root / "veo-folder-013"
    good.mkdir()
    for n in (1, 2, 3):
        (good / f"00{n}_scene.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    scenes = [(1, 0.0, 1.0), (2, 1.0, 2.0), (3, 2.0, 3.0)]
    assert resolve_images_dir(bad, scenes=scenes) == good.resolve()


def test_discover_images_dir_near_timeline(tmp_path: Path):
    project = tmp_path / "auto" / "topic"
    project.mkdir(parents=True)
    timeline = project / "timeline_topic.txt"
    timeline.write_text("001_[00:00.00-00:01.00] x\n", encoding="utf-8")
    images = project / "veo-folder-01"
    images.mkdir()
    (images / "001_a.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    found = discover_images_dir(timeline_path=timeline)
    assert found == images.resolve()
