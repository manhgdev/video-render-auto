# VideoBuilder

Ghép ảnh + audio + timeline prompt → video Shorts.

## Chạy

```bash
py run.py          # Windows
python3 run.py     # macOS / Linux

py build.py        # build OS hiện tại → dist/
py build.py --all  # build bản native + nhắc 2 OS còn lại
python3 build.py
```

**Cả 3 nền tảng:** PyInstaller **không build chéo** (Win không ra `.app`/Linux). Cách làm:

| Cách | Lệnh |
|------|------|
| Từng máy | `py build.py` trên Windows / Mac / Linux |
| GitHub Actions | **Push `main`** (hoặc tag `v*`) → Actions → tải artifact |

Push lên `main`/`master` sẽ tự chạy test + build cả 3 OS. Artifact tên `VideoBuilder-<os>-<commit>`.

```bash
py build.py -p windows   # chỉ Windows (phải đang ở Windows)
py build.py -p macos
py build.py -p linux
py build.py --no-bump      # CI — không tăng version
```

## Test

```bash
py -m pytest tests/ -q
```

Thêm `tests/test_*.py` khi cần kiểm tra tính năng mới.

## Cấu trúc

```
manh1/
├── run.py / build.py
├── tests/                 # pytest
├── videobuilder/
├── release/
│   ├── bootstrap.py
│   └── windows|macos|linux/VideoBuilder.spec
└── dist/
```

## Người dùng (`dist/`)

Mở `.exe` / `.app` → **Cài FFmpeg** → **RENDER**. Video xuất cạnh file exe.

---

Copyright (c) 2026 manhgdev
