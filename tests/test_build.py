"""Test build.py — chọn nền tảng."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build as build_mod


def test_can_build_only_native():
    host = build_mod.host_platform()
    assert build_mod.can_build_here(host)
    for other in ("windows", "macos", "linux"):
        if other != host:
            assert not build_mod.can_build_here(other)


def test_all_platforms_have_spec():
    for platform in build_mod.ALL_PLATFORMS:
        spec = ROOT / "release" / platform / "VideoBuilder.spec"
        assert spec.is_file(), platform
