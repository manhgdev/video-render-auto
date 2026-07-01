#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Kiểm tra và cài đặt FFmpeg trên Windows."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

FFMPEG_PORTABLE_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def get_app_dir() -> Path:
    """Thư mục app: cạnh .exe khi đóng gói; thư mục gốc repo khi dev."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def get_bundle_dir() -> Path:
    """File đóng gói trong .exe (PyInstaller _MEIPASS); dev = root repo."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parents[2]


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_tools_dir() -> Path:
    return get_app_dir() / "tools" / "ffmpeg"


def get_local_bin() -> Path:
    return get_tools_dir() / "bin"


def _run_version(cmd: str) -> str | None:
    try:
        out = subprocess.check_output(
            [cmd, "-version"],
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        line = out.splitlines()[0] if out else ""
        return line.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _discover_bin_dirs() -> list[Path]:
    found: list[Path] = []

    local_bin = get_local_bin()
    if local_bin.is_dir() and (local_bin / "ffmpeg.exe").is_file():
        found.append(local_bin)

    if sys.platform == "win32":
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            winget_root = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
            if winget_root.is_dir():
                for exe in winget_root.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"):
                    found.append(exe.parent)

        for pattern in (
            Path(r"C:\ffmpeg\bin"),
            Path(r"C:\Program Files\ffmpeg\bin"),
            Path(os.environ.get("ProgramFiles", "")) / "ffmpeg" / "bin",
        ):
            if pattern.is_dir() and (pattern / "ffmpeg.exe").is_file():
                found.append(pattern)

    which = shutil.which("ffmpeg")
    if which:
        found.append(Path(which).resolve().parent)

    unique: list[Path] = []
    seen = set()
    for path in found:
        key = str(path.resolve()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path.resolve())
    return unique


def ffmpeg_install_hint() -> str:
    if sys.platform == "darwin":
        return "cài thủ công: brew install ffmpeg"
    if sys.platform == "win32":
        return "bấm «Cài FFmpeg» hoặc: winget install Gyan.FFmpeg"
    return "cài thủ công ffmpeg + ffprobe (apt/dnf/pacman...)"


def ffmpeg_can_auto_install() -> bool:
    return sys.platform == "win32"


def ensure_ffmpeg_on_path() -> bool:
    for bin_dir in _discover_bin_dirs():
        path_str = str(bin_dir)
        current = os.environ.get("PATH", "")
        if path_str.lower() not in current.lower():
            os.environ["PATH"] = path_str + os.pathsep + current

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg and not shutil.which("ffprobe"):
        sibling = Path(ffmpeg).resolve().parent / "ffprobe"
        if sibling.is_file():
            path_str = str(sibling.parent)
            current = os.environ.get("PATH", "")
            if path_str.lower() not in current.lower():
                os.environ["PATH"] = path_str + os.pathsep + current

    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def resolve_ffprobe() -> str | None:
    ensure_ffmpeg_on_path()
    for bin_dir in _discover_bin_dirs():
        for name in ("ffprobe.exe", "ffprobe"):
            probe = bin_dir / name
            if probe.is_file():
                return str(probe)
    return shutil.which("ffprobe")


def resolve_ffmpeg() -> str | None:
    ensure_ffmpeg_on_path()
    for bin_dir in _discover_bin_dirs():
        for name in ("ffmpeg.exe", "ffmpeg"):
            exe = bin_dir / name
            if exe.is_file():
                return str(exe)
    return shutil.which("ffmpeg")


def check_ffmpeg() -> dict:
    ensure_ffmpeg_on_path()
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        version = _run_version(ffmpeg) or ""
        short = _short_version(version)
        return {
            "ok": True,
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
            "version": version,
            "short": short,
            "message": f"FFmpeg sẵn sàng ({short})" if short else "FFmpeg sẵn sàng",
        }
    return {
        "ok": False,
        "ffmpeg": None,
        "ffprobe": None,
        "version": None,
        "short": None,
        "message": f"Chưa có FFmpeg — {ffmpeg_install_hint()}",
    }


def _short_version(version_line: str) -> str:
    match = re.search(r"ffmpeg version (\S+)", version_line)
    return match.group(1) if match else version_line[:40]


def _has_winget() -> bool:
    return bool(shutil.which("winget"))


def _install_via_winget(log) -> bool:
    if not _has_winget():
        log("Winget không có trên máy.")
        return False

    log("Đang cài FFmpeg qua winget (có thể mất vài phút)...")
    cmd = [
        "winget", "install", "-e", "--id", "Gyan.FFmpeg",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except subprocess.TimeoutExpired:
        log("Winget quá thời gian chờ.")
        return False

    combined = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0 or "already installed" in combined.lower() or "đã được cài" in combined.lower():
        if ensure_ffmpeg_on_path():
            log("Cài winget xong.")
            return True

    if ensure_ffmpeg_on_path():
        log("FFmpeg đã có sau winget.")
        return True

    log("Winget không cài được FFmpeg.")
    if proc.stderr:
        log(proc.stderr.strip()[:200])
    return False


def _download(url: str, dest: Path, log) -> None:
    log("Đang tải FFmpeg portable...")
    last_pct = -1
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk = 1024 * 256
        while True:
            data = resp.read(chunk)
            if not data:
                break
            out.write(data)
            downloaded += len(data)
            if total > 0:
                pct = int(downloaded * 100 / total)
                if pct >= last_pct + 5:
                    last_pct = pct
                    log(f"Đang tải... {min(pct, 100)}%")


def _install_portable(log) -> bool:
    if sys.platform != "win32":
        log("Tự cài portable chỉ hỗ trợ Windows.")
        return False

    tools_dir = get_tools_dir()
    local_bin = get_local_bin()
    tools_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "ffmpeg.zip"
        try:
            _download(FFMPEG_PORTABLE_URL, zip_path, log)
        except Exception as err:
            log(f"Tải FFmpeg thất bại: {err}")
            return False

        log("Đang giải nén...")
        extract_dir = Path(tmp) / "extract"
        extract_dir.mkdir()
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as err:
            log(f"File tải về lỗi: {err}")
            return False

        bin_dir = None
        for candidate in extract_dir.rglob("ffmpeg.exe"):
            if candidate.parent.name.lower() == "bin":
                bin_dir = candidate.parent
                break
        if bin_dir is None:
            log("Không tìm thấy ffmpeg.exe sau giải nén.")
            return False

        if local_bin.exists():
            shutil.rmtree(local_bin.parent, ignore_errors=True)
        local_bin.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(bin_dir, local_bin)
        log(f"Đã cài portable vào {local_bin}")

    return ensure_ffmpeg_on_path()


def install_ffmpeg(progress_callback=None) -> dict:
    def log(message: str):
        if progress_callback:
            progress_callback(message)

    if sys.platform != "win32":
        status = check_ffmpeg()
        if status["ok"]:
            log(status["message"])
            return status
        return {
            **status,
            "message": f"App không tự cài FFmpeg trên hệ điều hành này. {ffmpeg_install_hint().capitalize()}.",
        }

    status = check_ffmpeg()
    if status["ok"]:
        log(status["message"])
        return status

    if _install_via_winget(log):
        return check_ffmpeg()

    if _install_portable(log):
        return check_ffmpeg()

    return {
        "ok": False,
        "ffmpeg": None,
        "ffprobe": None,
        "version": None,
        "short": None,
        "message": "Không cài được FFmpeg. Thử mở PowerShell Admin: winget install Gyan.FFmpeg",
    }
