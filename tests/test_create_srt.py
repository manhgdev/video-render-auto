from pathlib import Path
import threading

import pytest

from videobuilder.core.create_srt import (
    CreateSrtError,
    CreateSrtCancelled,
    default_srt_path,
    ensure_whisper,
    is_cublas_dll_error,
    is_gpu_runtime_error,
    is_groq_rate_limit,
    groq_api_key,
    normalize_output_path,
    normalize_srt_split,
    refine_srt_cues,
    resplit_srt,
    SRT_SPLIT_PARAMS,
    _group_words_to_cues_sentence,
    _groq_response_to_cues,
    _groq_model_for_language,
    _groq_skip_segment,
    _groq_collect_words,
    _groq_text_is_hallucination,
    _find_cue_timeline_gaps,
    _groq_filter_cues,
    _groq_prompt_from_cues,
    _groq_trim_prompt,
    GROQ_PROMPT_API_CHAR_MAX,
    GROQ_PROMPT_MAX_CHARS,
    GROQ_WHISPER_MODEL,
    GROQ_WHISPER_MODEL_VI,
    set_groq_api_key,
    whisper_model_cached,
    whisper_model_status_line,
    GROQ_API_KEY_ENV,
)
from videobuilder.core.pipeline import parse_srt_file, write_srt_from_cues
from videobuilder.core.pipeline import ProcessController


def test_default_srt_path():
    assert default_srt_path(Path("a/b/voice.mp3")) == Path("a/b/voice.srt")


def test_normalize_output_path():
    assert normalize_output_path(Path("x.mp3"), "out") == Path("out.srt")
    assert normalize_output_path(Path("x.mp3"), None) == Path("x.srt")


def test_create_srt_missing_audio():
    from videobuilder.core.create_srt import create_srt

    with pytest.raises(FileNotFoundError):
        create_srt("no_such_audio_file.mp3")


def test_whisper_numpy_message_detects_numpy2(monkeypatch):
    from videobuilder.core import create_srt as mod

    monkeypatch.setattr(mod, "numpy_major_version", lambda: 2)
    msg = mod.whisper_numpy_message()
    assert msg is not None
    assert "Cài đặt" in msg


def test_check_whisper_reports_status():
    from videobuilder.core.create_srt import check_whisper

    result = check_whisper()
    assert "ok" in result
    assert "message" in result
    assert isinstance(result["message"], str)


def test_is_groq_rate_limit():
    class RateLimitError(Exception):
        pass

    assert is_groq_rate_limit(RateLimitError("quota"))
    assert is_groq_rate_limit(RuntimeError("HTTP 429 Too Many Requests"))
    assert is_groq_rate_limit(RuntimeError("rate limit exceeded"))
    assert is_groq_rate_limit(RuntimeError("quota exceeded"))
    assert not is_groq_rate_limit(RuntimeError("connection reset"))


def test_groq_api_key_from_env(monkeypatch):
    set_groq_api_key(None)
    monkeypatch.delenv(GROQ_API_KEY_ENV, raising=False)
    assert groq_api_key() is None
    monkeypatch.setenv(GROQ_API_KEY_ENV, "  gsk_env  ")
    assert groq_api_key() == "gsk_env"
    set_groq_api_key("  gsk_ui  ")
    assert groq_api_key() == "gsk_ui"
    set_groq_api_key("")
    assert groq_api_key() == "gsk_env"


def test_groq_response_to_cues_segments():
    result = {
        "segments": [
            {"start": 0.0, "end": 1.5, "text": " Xin chào"},
            {"start": 1.5, "end": 3.0, "text": " thế giới"},
        ]
    }
    cues = _groq_response_to_cues(result, offset=10.0)
    assert len(cues) == 2
    assert cues[0] == (10.0, 11.5, "Xin chào")
    assert cues[1][0] == 11.5


def test_groq_model_for_language(monkeypatch, tmp_path):
    from videobuilder.core import groq_models as gm

    monkeypatch.setattr(gm, "_groq_model_cache_path", lambda: tmp_path / "cache.json")
    gm.clear_groq_model_cache()
    assert _groq_model_for_language("vi") == GROQ_WHISPER_MODEL_VI
    assert _groq_model_for_language("en") == GROQ_WHISPER_MODEL
    assert _groq_model_for_language("") == GROQ_WHISPER_MODEL


def test_groq_skip_segment_hallucination():
    assert _groq_skip_segment({"no_speech_prob": 0.9, "text": "garbage"})
    assert not _groq_skip_segment({"no_speech_prob": 0.2, "text": "Xin chào"})
    assert _groq_skip_segment(
        {"no_speech_prob": 0.5, "compression_ratio": 3.0, "text": "..."}
    )
    assert _groq_skip_segment(
        {
            "start": 0.0,
            "end": 30.0,
            "text": "Nội dung tự nhiên, câu hoàn chỉnh.",
            "no_speech_prob": 0.1,
        }
    )


def test_groq_text_is_hallucination():
    assert _groq_text_is_hallucination("Nội dung tự nhiên, câu hoàn chỉnh.", duration=30.0)
    assert _groq_text_is_hallucination("N dung t nhi c ho ch", duration=30.0)
    assert not _groq_text_is_hallucination(
        "Bạn mở mắt khi trời vẫn còn sám.",
        duration=2.0,
    )


def test_groq_filter_cues_drops_prompt_echo():
    cues = [
        (0.0, 2.0, "Xin chào"),
        (2.0, 32.0, "Nội dung tự nhiên, câu hoàn chỉnh."),
        (32.0, 35.0, "Tiếp theo"),
    ]
    filtered = _groq_filter_cues(cues)
    assert len(filtered) == 2
    assert filtered[0][2] == "Xin chào"
    assert filtered[1][2] == "Tiếp theo"


def test_groq_trim_prompt_respects_api_limit():
    long_text = "Câu tiếng Việt có dấu. " * 200
    trimmed = _groq_trim_prompt(long_text)
    assert len(trimmed) <= GROQ_PROMPT_API_CHAR_MAX
    assert len(trimmed) <= GROQ_PROMPT_MAX_CHARS
    assert trimmed.endswith(".")


def test_groq_prompt_from_cues_limits_context():
    cues = [(float(i), float(i + 1), f"Câu số {i} trong transcript.") for i in range(40)]
    prompt = _groq_prompt_from_cues(cues)
    assert len(prompt) <= GROQ_PROMPT_MAX_CHARS
    assert "Câu số 39" in prompt
    assert "Câu số 0" not in prompt


def test_groq_response_splits_long_segment():
    result = {
        "segments": [
            {
                "start": 0.0,
                "end": 20.0,
                "text": " Một câu dài về cuộc săn trong rừng cổ đại.",
                "no_speech_prob": 0.1,
                "words": [
                    {"word": " Một", "start": 0.0, "end": 1.0},
                    {"word": " câu", "start": 1.0, "end": 2.0},
                    {"word": " dài", "start": 2.0, "end": 3.0},
                    {"word": " về", "start": 3.0, "end": 4.0},
                    {"word": " cuộc", "start": 4.0, "end": 5.0},
                    {"word": " săn.", "start": 5.0, "end": 6.0},
                ],
            }
        ]
    }
    cues = _groq_response_to_cues(result, offset=100.0)
    assert len(cues) >= 1
    assert cues[0][0] == 100.0


def test_groq_collect_words():
    result = {
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "hello",
                "words": [
                    {"word": " hello", "start": 0.0, "end": 1.0},
                    {"word": " world", "start": 1.0, "end": 2.0},
                ],
            }
        ]
    }
    words = _groq_collect_words(result, offset=5.0)
    assert len(words) == 2
    assert words[0].start == 5.0
    assert words[1].start == 6.0


def test_check_whisper_with_groq_key(monkeypatch):
    from videobuilder.core import create_srt as mod
    from videobuilder.core.create_srt import check_whisper

    mod.set_groq_api_key("gsk_test")
    monkeypatch.setattr(mod, "groq_client_available", lambda: True)
    monkeypatch.setattr(mod, "srt_packages_status", lambda: {"needs_install": False, "groq_ok": True, "whisper_ok": True})
    monkeypatch.setattr(
        mod,
        "_check_local_whisper",
        lambda model=None: {"ok": True, "message": "local ok", "model_cached": True, "device": "CPU"},
    )
    result = check_whisper("small")
    assert result["ok"] is True
    assert result.get("groq") is True
    assert result.get("needs_install") is False
    assert "Groq" in result["message"]


def test_ensure_whisper_raises_without_package():
    try:
        from videobuilder.core.create_srt import ensure_whisper

        ensure_whisper()
    except CreateSrtError as err:
        assert "faster-whisper" in str(err)
    else:
        pytest.skip("faster-whisper installed")


def test_is_cublas_dll_error():
    assert is_cublas_dll_error(RuntimeError("Library cublas64_12.dll is not found or cannot be loaded"))
    assert is_cublas_dll_error(RuntimeError("cuda: Library cublas64_12.dll is not found"))
    assert not is_cublas_dll_error(RuntimeError("file not found"))


def test_is_gpu_runtime_error():
    assert is_gpu_runtime_error(RuntimeError("Library cublas64_12.dll is not found"))
    assert not is_gpu_runtime_error(RuntimeError("file not found"))


def test_whisper_model_status_line_unknown():
    line = whisper_model_status_line("unknown-model")
    assert "unknown-model" in line
    assert "chưa tải" in line


def test_whisper_model_cached_false_for_fake(tmp_path, monkeypatch):
    from videobuilder.core import create_srt as mod

    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    assert not whisper_model_cached("small")


def test_whisper_model_cached_true_when_snapshot_exists(tmp_path, monkeypatch):
    from videobuilder.core import create_srt as mod

    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    snap = (
        tmp_path / ".cache" / "huggingface" / "hub"
        / "models--Systran--faster-whisper-small" / "snapshots" / "abc"
    )
    snap.mkdir(parents=True)
    assert whisper_model_cached("small")
    line = whisper_model_status_line("small")
    assert "đã có trên máy" in line
    assert "Cân bằng" in line


def test_normalize_srt_split():
    assert normalize_srt_split("few") == "few"
    assert normalize_srt_split("Nhiều ngắt") == "many"
    assert normalize_srt_split("Khá ngắt") == "medium"
    assert normalize_srt_split("Rất ngắt") == "short"
    assert normalize_srt_split("Rất ít ngắt") == "very_few"
    assert normalize_srt_split("") == "normal"
    assert normalize_srt_split("unknown") == "normal"


def test_refine_srt_cues_very_few_merges_more():
    cues = [
        (0.0, 2.0, "Một."),
        (2.1, 4.0, "Hai."),
        (4.1, 6.0, "Ba."),
        (10.0, 12.0, "Bốn."),
    ]
    merged_few = refine_srt_cues(cues, "few")
    merged_very = refine_srt_cues(cues, "very_few")
    assert len(merged_very) <= len(merged_few)


def test_refine_srt_cues_few_merges():
    cues = [
        (0.0, 2.0, "Câu một."),
        (2.1, 4.0, "Câu hai."),
        (10.0, 12.0, "Câu ba."),
    ]
    merged = refine_srt_cues(cues, "few")
    assert len(merged) == 2
    assert merged[0][2] == "Câu một. Câu hai."


def test_refine_srt_cues_many_splits_long():
    cues = [
        (0.0, 3.0, "Câu thứ nhất."),
        (3.0, 8.0, "Câu thứ hai cũng dài nhưng vẫn là một câu."),
    ]
    split = refine_srt_cues(cues, "many")
    assert len(split) == 2
    assert split[0][2].startswith("Câu thứ nhất")
    assert split[1][2].startswith("Câu thứ hai")


def test_refine_srt_cues_many_keeps_unfinished_sentence():
    cues = [(0.0, 6.0, "Đây là một câu rất dài chưa có dấu chấm nên không được cắt")]
    split = refine_srt_cues(cues, "many")
    assert len(split) == 1


def test_merge_incomplete_sentence_cues():
    from videobuilder.core.create_srt import _merge_incomplete_sentence_cues

    cues = [
        (3.192, 5.852, "không có thuốc kháng sinh, không có siêu thị, không"),
        (5.852, 7.980, "có ai đến cứu bạn nếu cả bầu"),
    ]
    merged = _merge_incomplete_sentence_cues(cues)
    assert len(merged) == 1
    assert "siêu thị" in merged[0][2]
    assert "cứu bạn" in merged[0][2]


def test_split_many_at_capitals():
    from videobuilder.core.create_srt import _split_many_text

    text = "20 người 30 người Có trẻ nhỏ Có người già Một mùa khô"
    parts = _split_many_text(text, SRT_SPLIT_PARAMS["many"]["max_chars"])
    assert len(parts) >= 4
    assert parts[0] == "20 người 30 người"
    assert parts[1] == "Có trẻ nhỏ"
    assert parts[2] == "Có người già"
    assert parts[3].startswith("Một mùa")


def test_refine_many_splits_long_capital_phrases():
    cues = [
        (
            146.56,
            174.70,
            "20 người 30 người Có trẻ nhỏ Có người già Không có bác sĩ Không có thuốc.",
        ),
    ]
    split = refine_srt_cues(cues, "many")
    assert len(split) >= 5
    assert split[0][2] == "20 người 30 người"
    assert any("Có trẻ nhỏ" in c[2] for c in split)
    assert any("Không có thuốc" in c[2] for c in split)


def test_refine_many_merges_mid_sentence_cues():
    from videobuilder.core.create_srt import _refine_many_from_cues

    cues = [
        (8.910, 11.700, "Và điều đáng sợ nhất là, trong phần lớn lịch sử loài"),
        (11.700, 13.560, "người, chuyện biến mất gần như hoàn toàn"),
    ]
    refined = _refine_many_from_cues(cues)
    assert len(refined) == 1
    assert "loài người" in refined[0][2]


def test_group_words_breaks_on_sentence_end():
    class W:
        def __init__(self, word, start, end):
            self.word = word
            self.start = start
            self.end = end

    words = [
        W("Xin", 0.0, 0.2),
        W(" chào.", 0.2, 0.5),
        W(" Hôm", 0.55, 0.7),
        W(" nay.", 0.7, 0.9),
    ]
    params = SRT_SPLIT_PARAMS["many"]
    cues = _group_words_to_cues_sentence(
        words,
        max_chars=params["max_chars"],
        max_duration=params["max_duration"],
        min_gap=params["min_gap"],
        pause_split=params["pause_split"],
        min_chars=params["min_chars"],
    )
    assert len(cues) == 2
    assert "chào" in cues[0][2]
    assert "nay" in cues[1][2]


def test_group_words_does_not_split_on_comma_only():
    class W:
        def __init__(self, word, start, end):
            self.word = word
            self.start = start
            self.end = end

    words = [
        W("Ngày", 0.0, 0.2),
        W(" nay,", 0.2, 0.55),
        W(" hơn", 0.58, 0.75),
        W(" 8", 0.75, 0.85),
        W(" tỷ", 0.85, 1.0),
        W(" người", 1.0, 1.3),
    ]
    params = SRT_SPLIT_PARAMS["many"]
    cues = _group_words_to_cues_sentence(
        words,
        max_chars=params["max_chars"],
        max_duration=params["max_duration"],
        min_gap=params["min_gap"],
        pause_split=params["pause_split"],
        min_chars=params["min_chars"],
    )
    assert len(cues) == 1
    assert "Ngày nay" in cues[0][2]
    assert "người" in cues[0][2]


def test_merge_fragment_cues_joins_enumeration():
    from videobuilder.core.create_srt import _merge_fragment_cues

    cues = [
        (0.0, 1.0, "thành phố,"),
        (1.1, 2.0, "đường cao tốc,"),
        (2.1, 3.0, "mạng xã hội."),
    ]
    merged = _merge_fragment_cues(cues, max_chars=52, max_duration=5.0, merge_gap=0.55)
    assert len(merged) == 1
    assert "thành phố" in merged[0][2]
    assert "mạng xã hội" in merged[0][2]


def test_refine_srt_cues_normal_unchanged():
    cues = [(0.0, 1.0, "Hello"), (1.5, 2.5, "World")]
    assert refine_srt_cues(cues, "normal") == cues


def test_refine_short_splits_on_comma():
    cues = [
        (
            0.0,
            5.0,
            "thành phố, đường cao tốc, mạng xã hội, máy bay.",
        ),
    ]
    split = refine_srt_cues(cues, "short")
    assert len(split) >= 2
    joined = " ".join(c[2] for c in split)
    assert "thành phố" in joined
    assert "máy bay" in joined


def test_refine_many_from_cues_keeps_short_lines():
    from videobuilder.core.create_srt import _refine_many_from_cues

    cues = [
        (0.0, 2.0, "Ngày nay, hơn 8 tỷ người đang sống trên trái đất."),
        (2.5, 4.0, "Bạn nhìn quanh."),
    ]
    refined = _refine_many_from_cues(cues)
    assert len(refined) == 2
    assert refined[0][2].startswith("Ngày nay")


def test_refine_medium_splits_on_comma_not_many():
    cues = [(0.0, 5.0, "Ngày nay, hơn 8 tỷ người đang sống trên trái đất.")]
    many = refine_srt_cues(cues, "many")
    medium = refine_srt_cues(cues, "medium")
    assert len(many) == 1
    assert len(medium) >= 2
    assert medium[0][2].startswith("Ngày nay")


def test_split_medium_at_capitals_and_commas():
    from videobuilder.core.create_srt import _split_medium_text

    text = "20 người 30 người Có trẻ nhỏ, Có người già Một mùa khô"
    parts = _split_medium_text(text, SRT_SPLIT_PARAMS["medium"]["max_chars"])
    assert len(parts) >= 4
    assert parts[0] == "20 người 30 người"
    assert any("Có trẻ nhỏ" in p for p in parts)


def test_refine_medium_keeps_unfinished_sentence():
    cues = [(0.0, 6.0, "Đây là một câu rất dài chưa có dấu chấm nên không được cắt")]
    split = refine_srt_cues(cues, "medium")
    assert len(split) == 1


def test_resplit_srt_file_medium(tmp_path):
    srt = tmp_path / "clip.srt"
    write_srt_from_cues(
        srt,
        [
            (0.0, 5.0, "Ngày nay, hơn 8 tỷ người đang sống trên trái đất."),
        ],
    )
    out, before, after = resplit_srt(srt, split_mode="medium")
    assert out == srt
    assert before == 1
    assert after >= 2
    assert len(parse_srt_file(out)) == after


def test_resplit_srt_file_many(tmp_path):
    srt = tmp_path / "clip.srt"
    write_srt_from_cues(
        srt,
        [
            (0.0, 3.0, "Câu một."),
            (3.0, 8.0, "Câu hai. Câu ba."),
        ],
    )
    out, before, after = resplit_srt(srt, split_mode="many")
    assert out == srt
    assert before == 2
    assert after == 3
    assert len(parse_srt_file(out)) == after


def test_resplit_srt_file_few(tmp_path):
    srt = tmp_path / "clip.srt"
    write_srt_from_cues(
        srt,
        [
            (0.0, 2.0, "Một."),
            (2.1, 4.0, "Hai."),
            (10.0, 12.0, "Ba."),
        ],
    )
    _out, before, after = resplit_srt(srt, split_mode="few")
    assert before == 3
    assert after == 2


def test_resplit_srt_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        resplit_srt(tmp_path / "missing.srt", split_mode="many")


def test_collect_segments_respects_cancel():
    from videobuilder.core.create_srt import _collect_segments

    class Seg:
        def __init__(self, start, end, text):
            self.start = start
            self.end = end
            self.text = text

    controller = ProcessController()
    controller.cancel()

    with pytest.raises(CreateSrtCancelled):
        _collect_segments([Seg(0, 1, "a")], 10.0, process_controller=controller)


def test_run_cancellable_respects_cancel():
    from videobuilder.core.create_srt import _run_cancellable
    import time

    controller = ProcessController()

    def slow():
        time.sleep(5)
        return "done"

    def cancel_soon():
        time.sleep(0.1)
        controller.cancel()

    threading.Thread(target=cancel_soon, daemon=True).start()
    with pytest.raises(CreateSrtCancelled):
        _run_cancellable(slow, controller, poll=0.05)


def test_check_whisper_model_not_cached(tmp_path, monkeypatch):
    from videobuilder.core import create_srt as mod

    monkeypatch.setattr(mod, "groq_api_key", lambda: None)
    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mod, "cuda_available", lambda: True)
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError:
        pytest.skip("faster-whisper not installed")

    status = mod.check_whisper("medium")
    assert status["ok"] is True
    assert status["model_cached"] is False
    assert "chưa tải" in status["message"]
    assert "medium" in status["message"]


def test_check_whisper_model_cached(tmp_path, monkeypatch):
    from videobuilder.core import create_srt as mod

    monkeypatch.setattr(mod, "groq_api_key", lambda: None)
    monkeypatch.setattr(mod, "_huggingface_hub_dir", lambda: tmp_path / "hub")
    monkeypatch.setattr(mod, "cuda_available", lambda: False)
    snap = tmp_path / "hub" / "models--Systran--faster-whisper-small" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError:
        pytest.skip("faster-whisper not installed")

    status = mod.check_whisper("small")
    assert status["ok"] is True
    assert status["model_cached"] is True
    assert "đã có trên máy" in status["message"]


def test_find_cue_timeline_gaps_detects_short_srt_hole():
    cues = [
        (50.883, 53.440, "không có thuốc sát trùng"),
        (70.649, 84.217, "Mùi trong gió"),
    ]
    gaps = _find_cue_timeline_gaps(cues, audio_duration=90.0)
    hole = next((g for g in gaps if g[0] == pytest.approx(53.440)), None)
    assert hole is not None
    assert hole[1] == pytest.approx(70.649)


def test_groq_relaxed_filter_keeps_sparse_real_speech():
    text = "Một hai ba bốn năm sáu."
    assert _groq_text_is_hallucination(text, duration=17.0, strict=True)
    assert not _groq_text_is_hallucination(text, duration=17.0, strict=False)
    seg = {
        "start": 53.0,
        "end": 70.0,
        "text": text,
        "no_speech_prob": 0.1,
    }
    assert _groq_skip_segment(seg, strict=True)
    assert not _groq_skip_segment(seg, strict=False)

