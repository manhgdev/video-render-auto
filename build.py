#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build VideoBuilder — py build.py [--platform windows|macos|linux|all]"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

from release.bootstrap import maybe_reexec_into_project_venv  # noqa: E402

maybe_reexec_into_project_venv(__file__)

sys.path.insert(0, str(ROOT))

from release.bootstrap import detect_platform, ensure_python  # noqa: E402

ALL_PLATFORMS = ("windows", "macos", "linux")


def host_platform() -> str:
    return detect_platform()


def can_build_here(target: str) -> bool:
    """PyInstaller chỉ build đúng nền tảng khi chạy trên OS đó."""
    return target == host_platform()


def bump_version() -> str:
    return subprocess.check_output(
        [sys.executable, "-m", "videobuilder.version"],
        text=True,
        cwd=ROOT,
    ).strip()


def _prepare_workdir(workpath: Path) -> None:
    """Xóa workdir cũ; retry khi Windows giữ file .pkg từ build trước."""
    if not workpath.exists():
        return
    for attempt in range(5):
        try:
            shutil.rmtree(workpath)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(1.5)


def build_one(platform: str) -> Path:
    spec = ROOT / "release" / platform / "VideoBuilder.spec"
    if not spec.is_file():
        raise SystemExit(f"Không tìm thấy spec: {spec}")
    if not can_build_here(platform):
        raise SystemExit(
            f"Không build được '{platform}' trên máy {host_platform()}.\n"
            f"  → Build trên máy {platform}, hoặc: py build.py --all (chỉ build bản native)\n"
            f"  → Hoặc push GitHub và chạy workflow «Build all platforms»"
        )

    workpath = ROOT / "build" / platform
    distpath = ROOT / "dist"
    print(f"=== VideoBuilder {platform} ===")
    _prepare_workdir(workpath)
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-q", "--upgrade", "pip", "pyinstaller"],
        cwd=ROOT,
    )
    subprocess.check_call(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm",
            "--distpath", str(distpath),
            "--workpath", str(workpath),
            str(spec),
        ],
        cwd=ROOT,
    )
    return distpath


def build_all_native(*, do_bump: bool) -> None:
    host = host_platform()
    if do_bump:
        ver = bump_version()
        print(f"Phiên bản: v{ver}")
    build_one(host)
    print(f"Xong: {ROOT / 'dist'}")

    skipped = [p for p in ALL_PLATFORMS if p != host]
    if skipped:
        print()
        print("Không build chéo được (PyInstaller):")
        for p in skipped:
            print(f"  • {p} — cần máy {p} hoặc GitHub Actions (.github/workflows/build.yml)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Package VideoBuilder (PyInstaller)",
    )
    parser.add_argument(
        "--platform", "-p",
        choices=[*ALL_PLATFORMS, "all"],
        default=None,
        help="Target OS (default: current). all = native build + note for other OSes",
    )
    parser.add_argument(
        "--no-bump",
        action="store_true",
        help="Do not bump patch version (CI)",
    )
    args = parser.parse_args()
    ensure_python()

    platform = args.platform or host_platform()

    if platform == "all":
        build_all_native(do_bump=not args.no_bump)
        return

    if not args.no_bump:
        ver = bump_version()
        print(f"Phiên bản: v{ver}")

    build_one(platform)
    print(f"Xong: {ROOT / 'dist'}")


if __name__ == "__main__":
    main()
