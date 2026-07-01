"""Phiên bản — tự tăng patch khi chạy build.py (`python -m videobuilder.version`)."""

import re
import sys
from pathlib import Path

APP_NAME = "VideoBuilder"
APP_VERSION = "1.2.7"
APP_PRODUCT = "Video Builder"
APP_DESCRIPTION = "Video Builder — ghép ảnh + audio thành video Shorts"
APP_COMPANY = "manhgdev"
APP_COPYRIGHT = "Copyright (c) 2026 manhgdev"

_VERSION_RE = re.compile(
    r'^(APP_VERSION\s*=\s*")(\d+)\.(\d+)\.(\d+)(")',
    re.MULTILINE,
)


def version_tuple() -> tuple[int, int, int, int]:
    parts = [int(x) for x in APP_VERSION.split(".")]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def window_title() -> str:
    return f"{APP_PRODUCT} v{APP_VERSION} — {APP_COMPANY}"


def exe_stem() -> str:
    return f"{APP_NAME}_v{APP_VERSION}"


def exe_filename() -> str:
    return f"{exe_stem()}.exe"


def bump_patch() -> str:
    """Tăng patch (1.2.2 -> 1.2.3), ghi lại file này. Trả về version mới."""
    path = Path(__file__)
    text = path.read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if not match:
        raise SystemExit("Không tìm thấy APP_VERSION trong version.py")
    prefix, major, minor, patch, suffix = match.groups()
    new_ver = f"{major}.{minor}.{int(patch) + 1}"
    path.write_text(
        text[: match.start()] + f"{prefix}{new_ver}{suffix}" + text[match.end() :],
        encoding="utf-8",
    )
    return new_ver


if __name__ == "__main__":
    old = APP_VERSION
    new = bump_patch()
    print(new)
    print(f"{old} -> {new}", file=sys.stderr)
