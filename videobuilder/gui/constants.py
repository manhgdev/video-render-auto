#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path

from videobuilder.core.pipeline import ENCODE_QUALITY_OPTIONS, TRANSITION_EFFECTS, ZOOM_LEVEL_OPTIONS, ENCODER_OVERRIDE_OPTIONS

OUTPUT_BASENAME = "output.mp4"
OUTPUT_STEM = Path(OUTPUT_BASENAME).stem

TELEGRAM_URL = "https://t.me/zm_dev"
TELEGRAM_HANDLE = "t.me/zm_dev"

EFFECT_NONE = "none"
_FIXED_EFFECTS = [(k, v) for k, v in TRANSITION_EFFECTS.items() if k != "random"]
EFFECT_UI_OPTIONS = [(EFFECT_NONE, "Không (cắt nhanh)"), ("random", "Ngẫu nhiên")] + _FIXED_EFFECTS
EFFECT_LABEL_TO_KEY = {label: key for key, label in EFFECT_UI_OPTIONS}
EFFECT_KEY_TO_LABEL = {key: label for key, label in EFFECT_UI_OPTIONS}

RESOLUTION_UI_ORDER = [
    ("auto", "Auto (theo ảnh)"),
    ("720p", "720p (16:9)"),
    ("1080p", "1080p (16:9)"),
    ("2k", "2K (16:9)"),
    ("4k", "4K (16:9)"),
    ("shorts", "Shorts (9:16)"),
]
RESOLUTION_UI = {key: label for key, label in RESOLUTION_UI_ORDER}
RESOLUTION_LABEL_TO_KEY = {label: key for key, label in RESOLUTION_UI_ORDER}

QUALITY_LABEL_TO_KEY = {v: k for k, v in ENCODE_QUALITY_OPTIONS.items()}
ZOOM_LABEL_TO_KEY = {v: k for k, v in ZOOM_LEVEL_OPTIONS.items()}
ENCODER_LABEL_TO_KEY = {v: k for k, v in ENCODER_OVERRIDE_OPTIONS.items()}
STRIP_METADATA_UI = ("Tắt", "Bật")

C = {
    "bg": "#edf0f5",
    "header": "#111827",
    "header_sub": "#9ca3af",
    "card": "#ffffff",
    "card_title": "#6b7280",
    "border": "#e5e7eb",
    "text": "#1f2937",
    "muted": "#6b7280",
    "accent": "#4f46e5",
    "accent_hover": "#4338ca",
    "accent_soft": "#eef2ff",
    "footer": "#ffffff",
    "entry_bg": "#f9fafb",
    "progress_trough": "#e5e7eb",
    "progress_bar": "#4f46e5",
    "warn_bg": "#fff7ed",
    "warn_fg": "#9a3412",
    "warn_btn": "#ea580c",
    "warn_btn_hover": "#c2410c",
    "ok_bg": "#ecfdf5",
    "ok_fg": "#047857",
    "log_bg": "#f1f5f9",
    "log_fg": "#334155",
    "log_muted": "#64748b",
    "log_error": "#dc2626",
    "log_warn": "#d97706",
    "log_success": "#16a34a",
    "tab_track": "#e8ecf1",
    "tab_active": "#ffffff",
}

SRT_LANGUAGE_OPTIONS = ("auto", "vi", "en", "ja", "ko", "zh")
SRT_FIELD_LABEL_WIDTH = 13

TAB_ITEMS = (
    ("files", "Dự án"),
    ("opts", "Cài đặt"),
    ("auto", "Tự động"),
    ("srt", "Tạo SRT"),
    ("contact", "Liên hệ"),
)

FIELD_HELP = {
    "images_dir": (
        "Thư mục ảnh",
        "Chứa ảnh từng scene, khớp số scene trong file prompt.\n\n"
        "Đặt tên:\n"
        "• NNN_tên.jpg — scene thường (001, 002, ...)\n"
        "• 001_...CHARACTER REFERENCE... — ảnh nhân vật cho scene 1\n\n"
        "Ví dụ:\n"
        "  001_manh1.jpg\n"
        "  002_[00.00–00.02]_manh1.jpg\n"
        "  003_manh1.jpg",
    ),
    "audio": (
        "File audio",
        "File nhạc / lồng tiếng — quyết định độ dài video.\n\n"
        "Bắt buộc đọc được qua FFmpeg (cần cài FFmpeg trước).\n"
        "Đuôi: .mp3, .wav, .m4a, .aac",
    ),
    "prompts": (
        "File timeline",
        "File text (.txt) mô tả scene và mốc thời gian.\n\n"
        "• Nút Chọn — mở file .txt có sẵn (file tạo ảnh từ tab Tạo SRT)\n"
        "• Tên file — sửa ở ô bên cạnh (đuôi .txt cố định)\n"
        "• Mặc định: cùng thư mục, cùng tên với audio (.txt)\n\n"
        "Format file tạo ảnh (mỗi prompt một dòng, cách nhau dòng trống):\n"
        "  001_[00.00.00-00.00.01.92] CHARACTER BIBLE: ... Câu audio bám sát: ...",
    ),
    "output": (
        "File xuất",
        "Video MP4 sau khi render.\n\n"
        "• Mặc định: cùng thư mục với VideoBuilder.exe\n"
        "  (nếu không ghi được → Downloads)\n"
        "• Thư mục — chọn bằng nút Chọn\n"
        "• Tên file — sửa ở ô bên cạnh (đuôi .mp4 cố định)\n\n"
        "Ví dụ: D:\\Tools\\VideoBuilder\\output.mp4",
    ),
    "effect": (
        "Hiệu ứng",
        "Hiệu ứng chuyển cảnh giữa các ảnh (FFmpeg xfade).\n\n"
        "Thứ tự: Không (cắt nhanh) → Ngẫu nhiên → Fade, Wipe...\n\n"
        "• Không (cắt nhanh) — cắt thẳng, encode nhanh nhất, khớp audio\n"
        "• Ngẫu nhiên — mỗi lần chuyển khác nhau\n"
        "• Fade, Dissolve, Wipe... — hiệu ứng cố định\n\n"
        "Kèm Thời lượng (s) bên cạnh.",
    ),
    "transition": (
        "Thời lượng chuyển cảnh",
        "Số giây mỗi lần chuyển ảnh (crossfade).\n\n"
        "• Không hiệu ứng → luôn 0\n"
        "• Có hiệu ứng, để 0 → mặc định 0.28 giây\n"
        "• Gợi ý: 0.2 – 0.5 s\n\n"
        "Ví dụ: 0.28",
    ),
    "resolution": (
        "Độ phân giải",
        "Kích thước khung hình output.\n\n"
        "• Auto (theo ảnh) — lấy từ ảnh scene đầu\n"
        "• 720p / 1080p / 2K / 4K — 16:9\n"
        "• Shorts (9:16) — fit toàn ảnh, letterbox\n\n"
        "Ví dụ: Auto hoặc 1080p (16:9)",
    ),
    "fps": (
        "FPS",
        "Số khung hình mỗi giây.\n\n"
        "• 24 — điện ảnh\n"
        "• 30 — mặc định, YouTube\n"
        "• 60 — mượt hơn, file nặng hơn",
    ),
    "quality": (
        "Chất lượng encode",
        "Tốc độ vs chất lượng nén video.\n\n"
        "• Nhanh — render nhanh, file lớn hơn\n"
        "• Cân bằng / Chất lượng cao — chậm hơn, file nhỏ hơn",
    ),
    "zoom": (
        "Zoom",
        "Zoom vào nhẹ từng scene, chuyển động mượt (smoothstep).\n\n"
        "• Tắt — ảnh tĩnh\n"
        "• Nhẹ / Vừa / Mạnh — ~3% / 5% / 7%\n\n"
        "Render từng scene (có % tiến trình), ghép nhanh — không treo.",
    ),
    "encoder": (
        "Encoder",
        "Bộ mã hóa video.\n\n"
        "• Tự động — GPU nếu có (NVENC / AMF / QSV), không thì CPU\n"
        "• libx264 — CPU, tương thích cao\n"
        "• h264_nvenc / amf / qsv — GPU",
    ),
    "speed": (
        "Speed (%)",
        "Tốc độ phát video sau khi render xong.\n\n"
        "• 100 — bình thường (không đổi)\n"
        "• 150 — nhanh hơn 1.5x (video ngắn hơn)\n"
        "• 75 — chậm hơn (video dài hơn)\n\n"
        "Video + audio + phụ đề (đã burn) đổi cùng nhau, vẫn đồng bộ.",
    ),
    "preview": (
        "Preview (s)",
        "Độ dài khi bấm Preview.\n\n"
        "Tab Dự án: render vài giây đầu → output_preview.mp4\n"
        "Tab Tạo SRT: nhận dạng vài giây đầu → tên_preview.srt\n\n"
        "Ví dụ: 30",
    ),
    "volume": (
        "Âm lượng (%)",
        "Mức âm lượng audio trong video.\n\n"
        "• 100 — giữ nguyên\n"
        "• 50 — giảm một nửa\n"
        "• 150 — to hơn (tối đa hợp lý)\n\n"
        "Ví dụ: 100",
    ),
    "strip_metadata": (
        "Xóa metadata",
        "Xóa thông tin ẩn trong file MP4 sau khi render xong.\n\n"
        "• Tắt — giữ metadata FFmpeg\n"
        "• Bật — xóa title, encoder, handler_name...\n\n"
        "Chạy cuối cùng (sau Speed), copy stream — rất nhanh.",
    ),
    "watermark_opacity": (
        "Độ mờ logo (%)",
        "Độ trong suốt watermark (chỉ khi có file logo).\n\n"
        "• 70 — mặc định, vừa nhìn\n"
        "• 100 — logo đậm, rõ hơn\n"
        "• 30–50 — mờ nhẹ, kín đáo",
    ),
    "watermark": (
        "Watermark",
        "Logo PNG góc phải dưới (~18% rộng video).\n\n"
        "Độ mờ chỉnh ở «Độ mờ logo (%)».\n"
        "Để trống nếu không dùng.\n\n"
        "Ví dụ: logo.png",
    ),
    "subtitle": (
        "File phụ đề",
        "Lời thoại từ file SRT/ASS — chèn chữ trắng ở mép dưới video.\n\n"
        "Khác với chữ có sẵn trong ảnh AI (vd. XUYÊN KHÔNG…).\n"
        "Đuôi: .srt hoặc .ass · để trống nếu không dùng.\n\n"
        "Preview chỉ render vài giây đầu → chỉ thấy một phần cue.",
    ),
    "subtitle_font": (
        "Cỡ chữ phụ đề",
        "Kích thước chữ (px).\n\n"
        "• 0 — tự động (~32px ở Shorts 9:16)\n"
        "• 22–30 — vừa, dễ đọc\n"
        "• < 18 — quá nhỏ, khó đọc trên điện thoại",
    ),
    "subtitle_offset": (
        "Lệch thời gian phụ đề",
        "Dịch toàn bộ phụ đề sớm/muộn (giây).\n\n"
        "• Âm — hiện sớm hơn (vd -2.5)\n"
        "• Dương — hiện muộn hơn (vd 1.0)\n"
        "• 0 — giữ nguyên file gốc\n\n"
        "Dùng khi phụ đề lệch so với audio.",
    ),
    "subtitle_margin": (
        "Lề dưới phụ đề",
        "Khoảng cách từ mép dưới video (px).\n\n"
        "• 12–20 — sát mép dưới\n"
        "• 24–40 — cao hơn, tránh UI YouTube\n\n"
        "Ví dụ: 18",
    ),
    "subtitle_outline": (
        "Nền chữ",
        "Độ tương phản nền phía sau chữ trắng.\n\n"
        "• 0 — nền tối mờ, không viền (khuyên dùng)\n"
        "• 1 — nền + viền mảnh\n"
        "• 2 — nền đậm hơn",
    ),
    "srt_whisper": (
        "API key",
        "Groq free tier: STT (Whisper) + LLM (prompt ảnh visual beat).\n\n"
        "Lấy key tại console.groq.com\n"
        "Hiện/Ẩn · Xóa · Kiểm tra · Cài đặt gói groq + Whisper.\n"
        "Có thể dùng GROQ_API_KEY trong .env.",
    ),
    "srt_groq_api_key": (
        "Groq API key",
        "API key lấy tại console.groq.com (free tier).\n\n"
        "• STT: Groq Whisper (ưu tiên)\n"
        "• STT: Groq Whisper (turbo/large-v3, tự đổi khi limit)\n"
        "• Timeline: Groq LLM (compound, scout, 8b, qwen… tự fallback)\n"
        "• Rate limit / hết quota → tự chuyển faster-whisper local\n"
        "• Lưu trong cài đặt app (file settings local)\n\n"
        "Có thể dùng biến môi trường GROQ_API_KEY thay cho ô nhập.",
    ),
    "srt_audio": (
        "Audio → SRT",
        "File nhạc / lồng tiếng cần chuyển thành phụ đề.\n\n"
        "Groq API trước; rate limit / hết quota → faster-whisper local → .srt\n"
        "Đuôi: .mp3, .wav, .m4a, .aac",
    ),
    "srt_output": (
        "File SRT xuất",
        "File phụ đề .srt sau khi nhận dạng.\n\n"
        "Mặc định: cùng thư mục, cùng tên với audio.\n"
        "Có thể chọn đường dẫn khác.",
    ),
    "srt_prompts_output": (
        "File tạo ảnh",
        "File mô tả scene và mốc thời gian (.txt) — Groq LLM visual beat.\n\n"
        "Ô tick bên trái «Chọn» — bật/tắt tạo file khi bấm Tạo SRT.\n"
        "Format: mỗi prompt một dòng liền (CHARACTER BIBLE, Câu audio bám sát, Ý cảnh...), "
        "cách nhau một dòng trống.\n"
        "Giống «File SRT xuất»: thư mục + tên file + .txt\n"
        "Mặc định: cùng thư mục, cùng tên với audio.\n"
        "Đã có SRT → có thể tạo lại file tạo ảnh mà không nhận dạng lại audio.",
    ),
    "srt_model": (
        "Whisper",
        "Model faster-whisper local — dùng khi Groq bị giới hạn.\n\n"
        "• tiny / base — nhanh, kém hơn\n"
        "• small — cân bằng (khuyên dùng)\n"
        "• medium / large-v3 — chính xác hơn, chậm\n\n"
        "Model tải một lần, lưu cache Hugging Face.",
    ),
    "srt_language": (
        "Ngôn ngữ",
        "Mã ISO ngôn ngữ trong audio.\n\n"
        "• auto — tự phát hiện (mặc định)\n"
        "• vi — Tiếng Việt (Groq dùng whisper-large-v3)\n"
        "• en — English",
    ),
    "srt_split": (
        "Ngắt câu",
        "Độ dài mỗi dòng phụ đề.\n\n"
        "• Rất ít ngắt — gộp rất nhiều, dòng dài nhất\n"
        "• Ít ngắt — gộp nhiều câu\n"
        "• Bình thường — giữ nguyên segment nhận dạng\n"
        "• Nhiều ngắt — tách theo hết câu (. ! ?) và cụm viết hoa đầu dòng\n"
        "• Khá ngắt — như Nhiều ngắt, thêm tách dấu phẩy\n"
        "• Rất ngắt — dòng ngắn, tách dấu phẩy (kiểu Shorts)\n\n"
        "Đã có .srt → «Áp dụng» (vài giây). Nhận dạng mới chỉ khi tạo SRT.",
    ),
}
