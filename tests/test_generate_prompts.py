"""Test Groq LLM visual beat → image_prompts.txt."""

import json
from pathlib import Path

import pytest

from videobuilder.core.generate_prompts import (
    TranscriptSegment,
    VisualBeat,
    estimate_beat_density,
    estimate_hook_window,
    GROQ_LLM_MAX_INPUT_TOKENS,
    split_segments_for_groq_llm,
    _build_visual_beat_user_payload,
    _request_token_estimate,
    transcript_duration,
    format_beat_line,
    format_prompt_timecode,
    format_time_range,
    merge_segment_texts,
    parse_gemini_beats,
    parse_gemini_response,
    write_image_prompts_txt,
    DEFAULT_ART_STYLE,
    DEFAULT_CHARACTER_STYLE,
    DEFAULT_LABELS,
    HOOK_TYPES,
    HOOK_RENDER_MAX_SEC,
    HOOK_CHAIN_MIN,
)
from videobuilder.core.groq_models import GROQ_LLM_DEFAULT_MODEL


class TestPromptTimecode:
    def test_zero(self):
        assert format_prompt_timecode(0.0) == "00.00.00"

    def test_subminute_fraction(self):
        assert format_prompt_timecode(1.92) == "00.00.01.92"

    def test_ten_point_eight_seconds(self):
        assert format_prompt_timecode(10.8) == "00.00.10.80"

    def test_two_minutes(self):
        assert format_prompt_timecode(120.0) == "00.02.00"

    def test_range_subminute(self):
        assert format_time_range(0.0, 1.92) == "00.00.00-00.00.01.92"

    def test_parse_token_values(self):
        from videobuilder.core.generate_prompts import parse_prompt_timecode_token

        assert parse_prompt_timecode_token("00.00.00") == 0.0
        assert parse_prompt_timecode_token("00.00.01.92") == pytest.approx(1.92)
        assert parse_prompt_timecode_token("00.02.00") == pytest.approx(120.0)
        assert parse_prompt_timecode_token("00.02.04.64") == pytest.approx(124.64)
        assert parse_prompt_timecode_token("00.10.80") == pytest.approx(10.8)
        assert parse_prompt_timecode_token("00.01.30") == pytest.approx(90.0)
        assert parse_prompt_timecode_token("00.08.28") == pytest.approx(508.0)
        assert parse_prompt_timecode_token("00.00.52") == pytest.approx(52.0)
        assert parse_prompt_timecode_token("00:06.33") == pytest.approx(6.33)


class TestFormatBeatLine:
    def test_inline_single_line_format(self):
        beat = VisualBeat(
            start=0.0,
            end=1.92,
            audio_quote="Bạn mở mắt khi trời vẫn còn xám.",
            character_desc=DEFAULT_CHARACTER_STYLE,
            scene_intent="Tạo hook giữ chân",
            camera="Cận cảnh đôi mắt mở ra.",
            background="bình minh xám",
            visual="nhân vật chính vừa mở mắt",
            labels=DEFAULT_LABELS,
            style=DEFAULT_ART_STYLE,
        )
        block = format_beat_line(1, beat)
        assert block.startswith("001_[00.00.00-00.00.01.92]")
        assert "\n" not in block
        assert "CHARACTER BIBLE:" in block
        assert 'Câu audio bám sát: "Bạn mở mắt khi trời vẫn còn xám."' in block
        assert "Phong cách:" in block
        assert "Mô tả nhân vật:" not in block


class TestParseGeminiBeats:
    def test_valid_json(self):
        data = {
            "beats": [
                {
                    "start_sec": 0.0,
                    "end_sec": 5.5,
                    "audio_quote": "Một câu thật",
                    "scene_intent": "Ý cảnh",
                    "visual": "Hình ảnh",
                }
            ]
        }
        beats = parse_gemini_beats(data)
        assert len(beats) == 1
        assert beats[0].audio_quote == "Một câu thật"

    def test_missing_beats_raises(self):
        with pytest.raises(Exception, match="beats"):
            parse_gemini_beats({})


class TestWritePrompts:
    def test_writes_txt(self, tmp_path: Path):
        beat = VisualBeat(
            start=0.0,
            end=6.33,
            audio_quote="Test",
            character_desc="Nhân vật",
            scene_intent="Ý",
            camera="Góc",
            background="Nền",
            visual="Ảnh",
            labels=DEFAULT_LABELS,
            style=DEFAULT_ART_STYLE,
        )
        out = write_image_prompts_txt([beat], tmp_path / "out")
        text = out.read_text(encoding="utf-8")
        assert "001_[" in text
        assert out.suffix == ".txt"

    def test_writes_blank_line_between_beats(self, tmp_path: Path):
        beat_a = VisualBeat(
            start=0.0,
            end=5.0,
            audio_quote="A",
            character_desc="Nhân vật",
            scene_intent="Ý",
            camera="Góc",
            background="Nền",
            visual="Ảnh A",
            labels=DEFAULT_LABELS,
            style=DEFAULT_ART_STYLE,
        )
        beat_b = VisualBeat(
            start=5.0,
            end=10.0,
            audio_quote="B",
            character_desc="Nhân vật",
            scene_intent="Ý",
            camera="Góc",
            background="Nền",
            visual="Ảnh B",
            labels=DEFAULT_LABELS,
            style=DEFAULT_ART_STYLE,
        )
        out = write_image_prompts_txt([beat_a, beat_b], tmp_path / "out")
        text = out.read_text(encoding="utf-8")
        assert "001_[" in text
        assert "002_[" in text
        assert "\n\n" in text
        assert text.count("\n\n") == 1


def test_segments_from_cues():
    from videobuilder.core.generate_prompts import segments_from_cues

    segs = segments_from_cues([(0.0, 1.0, " A "), (2.0, 3.0, "")])
    assert len(segs) == 1
    assert segs[0].text == "A"


def test_check_prompt_llm_no_key(monkeypatch):
    from videobuilder.core import generate_prompts as gp

    monkeypatch.setattr(gp, "groq_api_key", lambda: None)
    status = gp.check_prompt_llm()
    assert status["ok"] is False
    assert status["llm"] is False


def test_check_prompt_llm_with_key(monkeypatch):
    from videobuilder.core.generate_prompts import check_prompt_llm
    from videobuilder.core.create_srt import set_groq_api_key

    set_groq_api_key("test-key")
    status = check_prompt_llm()
    assert status["llm"] is bool(status["ok"])
    set_groq_api_key(None)


def test_split_segments_for_groq_llm_many_cues():
    segments = [
        TranscriptSegment(
            i * 3.0,
            i * 3.0 + 2.5,
            f"Câu transcript số {i}. " + "Người xưa đi săn trong rừng. " * 3,
        )
        for i in range(204)
    ]
    chunks = split_segments_for_groq_llm(segments)
    assert len(chunks) >= 2
    assert chunks[0][1] is True
    assert all(not include_hook for _, include_hook in chunks[1:])
    merged_count = sum(len(c) for c, _ in chunks)
    assert merged_count == 204
    for chunk, include_hook in chunks:
        tokens = _request_token_estimate(
            chunk,
            all_segments=segments,
            include_hook=include_hook,
            chunk_index=0,
            chunk_total=len(chunks),
        )
        assert tokens <= GROQ_LLM_MAX_INPUT_TOKENS


def test_estimate_hook_window_merges_opening_cues():
    segments = [
        TranscriptSegment(0.0, 2.0, "Bạn có biết"),
        TranscriptSegment(2.0, 4.5, "điều này không?"),
        TranscriptSegment(4.5, 12.0, "Ngày xưa có một vị vua"),
    ]
    start, end = estimate_hook_window(segments)
    assert start == 0.0
    assert 3.0 <= end - start <= HOOK_RENDER_MAX_SEC
    assert end >= 4.5


def test_transcript_duration_from_last_segment_end():
    segments = [
        TranscriptSegment(0.0, 5.0, "A"),
        TranscriptSegment(5.0, 520.0, "B"),
    ]
    assert transcript_duration(segments) == 520.0


def test_estimate_beat_density_long_video():
    segments = [TranscriptSegment(0.0, 500.0, "x")]
    density = estimate_beat_density(segments)
    assert density["total_beats_min"] == 80
    assert density["total_beats_max"] == 110
    assert 8 <= density["opening_0_30s_beats_min"] <= 18
    assert density["opening_0_30s_beats_max"] >= density["opening_0_30s_beats_min"]


def test_estimate_beat_density_scales_shorter_video():
    segments = [TranscriptSegment(0.0, 300.0, "x")]
    density = estimate_beat_density(segments)
    assert 40 <= density["total_beats_min"] <= 55
    assert density["total_beats_max"] >= density["total_beats_min"]


def test_merge_segment_texts_for_hook():
    segments = [
        TranscriptSegment(0.0, 1.5, "Câu một."),
        TranscriptSegment(1.5, 3.0, "Câu hai."),
        TranscriptSegment(10.0, 12.0, "Sau hook."),
    ]
    quote = merge_segment_texts(segments, 0.0, 3.0)
    assert quote == "Câu một. Câu hai."


def test_parse_gemini_response_enforces_hook_chain_first():
    segments = [
        TranscriptSegment(0.0, 2.0, "Nếu bạn ở đây"),
        TranscriptSegment(2.0, 5.0, "thì điều gì xảy ra?"),
        TranscriptSegment(5.0, 12.0, "Câu chuyện bắt đầu từ"),
    ]
    data = {
        "hook": {
            "start_sec": 0.0,
            "end_sec": 5.0,
            "hook_type": "câu hỏi gây tò mò",
            "audio_quote": "Nếu bạn ở đây thì điều gì xảy ra?",
            "visual": "Nhân vật que nhìn thẳng, dấu hỏi lớn",
            "scene_intent": "Tò mò",
        },
        "beats": [
            {
                "start_sec": 5.0,
                "end_sec": 12.0,
                "audio_quote": "Câu chuyện bắt đầu từ",
                "visual": "Cảnh kể chuyện",
            },
            {
                "start_sec": 0.0,
                "end_sec": 2.0,
                "audio_quote": "trùng hook — phải bỏ",
                "visual": "x",
            },
        ],
    }
    beats = parse_gemini_response(data, segments)
    hook_beats = [b for b in beats if b.is_hook]
    assert len(hook_beats) >= HOOK_CHAIN_MIN
    assert beats[0].is_hook is True
    assert beats[0].hook_type == HOOK_TYPES[1]
    assert beats[0].start == 0.0
    assert all(b.scene_bridge.strip() for b in hook_beats)
    body = [b for b in beats if not b.is_hook]
    assert len(body) == 1
    assert body[0].start >= 5.0


def test_parse_gemini_response_hook_chain_json():
    segments = [
        TranscriptSegment(0.0, 3.0, "Bạn mở mắt."),
        TranscriptSegment(3.0, 7.0, "Không có đồng hồ."),
        TranscriptSegment(7.0, 15.0, "Chỉ có tiếng thú."),
    ]
    data = {
        "hook_chain": [
            {
                "start_sec": 0.0,
                "end_sec": 3.0,
                "audio_quote": "Bạn mở mắt.",
                "scene_intent": "Cú mở cảnh",
                "visual": "Nhân vật vừa mở mắt",
                "scene_bridge": "Cùng nhân vật, lia sang chi tiết phủ định",
            },
            {
                "start_sec": 3.0,
                "end_sec": 7.0,
                "audio_quote": "Không có đồng hồ.",
                "scene_intent": "Điều bất thường",
                "visual": "Đồng hồ bị gạch",
                "scene_bridge": "Cùng bối cảnh, phát hiện nguy hiểm",
            },
            {
                "start_sec": 7.0,
                "end_sec": 10.0,
                "audio_quote": "Chỉ có tiếng thú.",
                "scene_intent": "Nguy hiểm",
                "visual": "Dấu chân thú",
                "scene_bridge": "Chuyển sang nội dung chính",
            },
        ],
        "beats": [
            {
                "start_sec": 10.0,
                "end_sec": 15.0,
                "audio_quote": "Chỉ có tiếng thú.",
                "visual": "Tiếp nội dung",
            },
        ],
    }
    beats = parse_gemini_response(data, segments)
    assert len([b for b in beats if b.is_hook]) == 3
    assert "Điểm nối chuyển cảnh" in format_beat_line(1, beats[0])


def test_is_payload_too_large_not_tpm_rate_limit():
    from videobuilder.core.generate_prompts import _is_payload_too_large

    tpm_err = (
        "Rate limit reached for model on tokens per minute (TPM): "
        "Limit 30000, Used 28802, Requested 1948. Please try again in 1.5s."
    )
    assert not _is_payload_too_large(RuntimeError(tpm_err))
    assert _is_payload_too_large(RuntimeError("Error 413 request too large"))


def test_parse_groq_retry_after_seconds():
    from videobuilder.core.generate_prompts import _parse_groq_retry_after_seconds

    assert _parse_groq_retry_after_seconds(
        Exception("Please try again in 1.5s")
    ) == pytest.approx(1.5)
    assert _parse_groq_retry_after_seconds(
        Exception("Please try again in 1h39m44.064s")
    ) == pytest.approx(5984.064, rel=1e-3)


def test_groq_llm_model_chain_default(monkeypatch, tmp_path):
    from videobuilder.core import groq_models as gm

    monkeypatch.setattr(gm, "_groq_model_cache_path", lambda: tmp_path / "cache.json")
    gm.clear_groq_model_cache()
    chain = gm.groq_llm_model_chain()
    assert chain[0] == gm.GROQ_LLM_DEFAULT_MODEL
    assert "meta-llama/llama-4-scout-17b-16e-instruct" in chain
    assert "llama-3.1-8b-instant" in chain
    assert "whisper-large-v3-turbo" not in chain
    assert len(chain) == len(set(chain))


def test_groq_llm_model_chain_env_override(monkeypatch, tmp_path):
    from videobuilder.core import groq_models as gm

    monkeypatch.setattr(gm, "_groq_model_cache_path", lambda: tmp_path / "cache.json")
    gm.clear_groq_model_cache()
    monkeypatch.setenv(gm.GROQ_LLM_MODEL_ENV, "qwen/qwen3-32b")
    chain = gm.groq_llm_model_chain()
    assert chain[0] == "qwen/qwen3-32b"
    assert "llama-3.1-8b-instant" in chain


def test_groq_whisper_model_chain_vi():
    from videobuilder.core import groq_models as gm

    chain = gm.groq_whisper_model_chain("vi")
    assert chain[0] == gm.GROQ_WHISPER_LARGE
    assert gm.GROQ_WHISPER_TURBO in chain


def test_groq_whisper_model_chain_auto():
    from videobuilder.core import groq_models as gm

    chain = gm.groq_whisper_model_chain("")
    assert chain[0] == gm.GROQ_WHISPER_TURBO
    assert gm.GROQ_WHISPER_LARGE in chain


def test_groq_model_cache_persists_llm(monkeypatch, tmp_path):
    from videobuilder.core import groq_models as gm

    cache_path = tmp_path / ".groq_active_models.json"
    monkeypatch.setattr(gm, "_groq_model_cache_path", lambda: cache_path)
    gm.clear_groq_model_cache()

    gm.set_active_llm_model("llama-3.1-8b-instant")
    assert cache_path.is_file()
    assert json.loads(cache_path.read_text(encoding="utf-8"))["llm"] == "llama-3.1-8b-instant"

    gm.reset_active_llm_model()
    gm.load_cached_groq_models(force_reload=True)
    assert gm.groq_llm_active_model() == "llama-3.1-8b-instant"
    assert gm.groq_llm_model_chain()[0] == "llama-3.1-8b-instant"
    assert gm.groq_llm_using_cached_model()


def test_groq_model_cache_persists_whisper(monkeypatch, tmp_path):
    from videobuilder.core import groq_models as gm

    cache_path = tmp_path / ".groq_active_models.json"
    monkeypatch.setattr(gm, "_groq_model_cache_path", lambda: cache_path)
    gm.clear_groq_model_cache()

    gm.set_active_whisper_model(gm.GROQ_WHISPER_TURBO, language="")
    gm.set_active_whisper_model(gm.GROQ_WHISPER_LARGE, language="vi")
    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert data["whisper"]["_auto"] == gm.GROQ_WHISPER_TURBO
    assert data["whisper"]["vi"] == gm.GROQ_WHISPER_LARGE

    gm.reset_active_whisper_model()
    gm.load_cached_groq_models(force_reload=True)
    assert gm.groq_whisper_active_model("") == gm.GROQ_WHISPER_TURBO
    assert gm.groq_whisper_active_model("vi") == gm.GROQ_WHISPER_LARGE
    assert gm.groq_whisper_model_chain("vi")[0] == gm.GROQ_WHISPER_LARGE
    assert gm.groq_whisper_active_model("en") == gm.GROQ_WHISPER_TURBO


def test_groq_chat_json_with_fallback_switches_on_rate_limit(monkeypatch, tmp_path):
    from videobuilder.core import generate_prompts as gp
    from videobuilder.core import groq_models as gm

    monkeypatch.setattr(gm, "_groq_model_cache_path", lambda: tmp_path / "cache.json")
    gm.clear_groq_model_cache()
    calls: list[str] = []

    def fake_chat(client, *, model, system, user, max_tokens=4096):
        calls.append(model)
        if model == gm.GROQ_LLM_DEFAULT_MODEL:
            raise gp.GroqLlmRateLimitError("429 rate limit")
        return {"beats": []}

    monkeypatch.setattr(gp, "_groq_chat_json", fake_chat)
    chain_before = gm.groq_llm_model_chain()
    data = gp._groq_chat_json_with_fallback(None, system="s", user="u")
    assert data == {"beats": []}
    assert calls[0] == gm.GROQ_LLM_DEFAULT_MODEL
    assert calls[1] == chain_before[1]
    assert gm.groq_llm_active_model() == calls[1]


def test_fill_timeline_gaps_covers_sparse_llm_output():
    """LLM chỉ trả vài beat — fill phải bổ sung cho segment còn lại."""
    from videobuilder.core.generate_prompts import fill_timeline_gaps

    segments = [
        TranscriptSegment(0.0, 2.0, "Câu một."),
        TranscriptSegment(2.0, 5.0, "Câu hai."),
        TranscriptSegment(5.0, 8.0, "Câu ba."),
        TranscriptSegment(8.0, 12.0, "Câu bốn."),
        TranscriptSegment(12.0, 16.0, "Câu năm."),
        TranscriptSegment(16.0, 20.0, "Câu sáu."),
        TranscriptSegment(20.0, 25.0, "Câu bảy."),
        TranscriptSegment(25.0, 30.0, "Câu tám."),
        TranscriptSegment(30.0, 40.0, "Câu chín."),
        TranscriptSegment(40.0, 50.0, "Câu mười."),
    ]
    sparse = [
        VisualBeat(
            start=0.0, end=3.0, audio_quote="Câu một. Câu hai.",
            character_desc="Người chính", scene_intent="Hook",
            camera="Góc rộng", background="Phòng",
            visual="Mở cảnh", labels="['a']", style="tối giản",
            is_hook=True, hook_type=HOOK_TYPES[0],
        ),
        VisualBeat(
            start=3.0, end=6.0, audio_quote="Câu ba.",
            character_desc="Người chính", scene_intent="",
            camera="Góc rộng", background="Phòng",
            visual="Cảnh", labels="[]", style="minimal",
            is_hook=True,
        ),
        VisualBeat(
            start=45.0, end=50.0, audio_quote="Câu mười.",
            character_desc="X", scene_intent="",
            camera="Góc rộng", background="Ngoài trời",
            visual="Cuối", labels=DEFAULT_LABELS, style=DEFAULT_ART_STYLE,
        ),
    ]
    filled = fill_timeline_gaps(sparse, segments)
    assert len(filled) > len(sparse)
    for seg in segments:
        covered = any(b.start < seg.end - 0.05 and b.end > seg.start + 0.05 for b in filled)
        assert covered, f"segment {seg.start}-{seg.end} không được phủ"
    assert all(DEFAULT_CHARACTER_STYLE in b.character_desc for b in filled)
    assert all(b.labels == DEFAULT_LABELS for b in filled)
    assert all(DEFAULT_ART_STYLE in b.style for b in filled)


def test_reanchor_hook_chain_closes_llm_gaps():
    """Hook LLM nhảy 6s→10s — reanchor phải nối liền theo SRT."""
    from videobuilder.core.generate_prompts import (
        TranscriptSegment,
        VisualBeat,
        _reanchor_hook_chain_to_segments,
        DEFAULT_CHARACTER_STYLE,
        DEFAULT_LABELS,
        DEFAULT_ART_STYLE,
        HOOK_TYPES,
    )

    segments = [
        TranscriptSegment(0.0, 1.86, "Bạn mở mắt khi trời vẫn còn sám."),
        TranscriptSegment(2.8, 6.1, "Không có đồng hồ, không có lịch."),
        TranscriptSegment(6.1, 10.32, "Nhưng cả cơ thể bạn đã biết hôm nay là một ngày nguy hiểm."),
        TranscriptSegment(10.32, 13.73, "Bên ngoài nơi trú ẩn, gió lạnh lùa qua."),
        TranscriptSegment(13.73, 17.32, "Không khí buổi sáng giống như trước một cơn bão."),
    ]
    bad_hooks = [
        VisualBeat(
            0.0, 1.86, "Bạn mở mắt khi trời vẫn còn sám.", DEFAULT_CHARACTER_STYLE,
            "hook", "Cận", "Tối", "visual", DEFAULT_LABELS, DEFAULT_ART_STYLE,
            is_hook=True, hook_type=HOOK_TYPES[0],
        ),
        VisualBeat(
            2.8, 6.1, "Không có đồng hồ.", DEFAULT_CHARACTER_STYLE,
            "hook", "Trung", "Sám", "visual", DEFAULT_LABELS, DEFAULT_ART_STYLE,
            is_hook=True, hook_type=HOOK_TYPES[0],
        ),
        VisualBeat(
            10.32, 13.73, "Bên ngoài nơi trú ẩn.", DEFAULT_CHARACTER_STYLE,
            "hook", "Rộng", "Ngoài", "visual", DEFAULT_LABELS, DEFAULT_ART_STYLE,
            is_hook=True, hook_type=HOOK_TYPES[0],
        ),
    ]
    fixed = _reanchor_hook_chain_to_segments(bad_hooks, segments)
    assert len(fixed) == 3
    assert fixed[0].start == 0.0
    assert abs(fixed[0].end - fixed[1].start) < 0.02
    assert abs(fixed[1].end - fixed[2].start) < 0.02
    assert fixed[0].end >= 2.8 - 0.02
    assert fixed[2].end <= 10.0 + 0.1


def test_finalize_respects_srt_gap_between_cues():
    """Cue SRT cách nhau >1.2s — beat sau bắt đầu đúng cue, không kéo ảnh qua."""
    from videobuilder.core.generate_prompts import (
        TranscriptSegment,
        VisualBeat,
        finalize_timeline_coverage,
        DEFAULT_CHARACTER_STYLE,
        DEFAULT_LABELS,
        DEFAULT_ART_STYLE,
    )

    segments = [
        TranscriptSegment(0.0, 1.86, "Một."),
        TranscriptSegment(2.8, 6.1, "Hai."),
        TranscriptSegment(6.1, 10.0, "Ba."),
        TranscriptSegment(10.0, 15.0, "Bốn."),
        TranscriptSegment(20.0, 25.0, "Năm."),
    ]
    beats = [
        VisualBeat(
            0.0, 1.86, "Một.", DEFAULT_CHARACTER_STYLE,
            "hook", "Cận", "Tối", "v1", DEFAULT_LABELS, DEFAULT_ART_STYLE,
            is_hook=True,
        ),
        VisualBeat(
            2.8, 6.1, "Hai.", DEFAULT_CHARACTER_STYLE,
            "hook", "Trung", "Sám", "v2", DEFAULT_LABELS, DEFAULT_ART_STYLE,
            is_hook=True,
        ),
        VisualBeat(
            10.0, 15.0, "Bốn.", DEFAULT_CHARACTER_STYLE,
            "body", "Rộng", "Ngoài", "v3", DEFAULT_LABELS, DEFAULT_ART_STYLE,
        ),
        VisualBeat(
            20.0, 25.0, "Năm.", DEFAULT_CHARACTER_STYLE,
            "body", "Rộng", "Ngoài", "v4", DEFAULT_LABELS, DEFAULT_ART_STYLE,
        ),
    ]
    out = finalize_timeline_coverage(beats, segments)
    body = [b for b in out if not b.is_hook]
    four = next(b for b in body if "Bốn" in b.audio_quote)
    five = next(b for b in body if "Năm" in b.audio_quote)
    assert four.end <= 15.01
    assert five.start >= 20.0 - 0.02
    assert five.start - four.end > 4.0


def test_finalize_preserves_hook_chain_not_merged_with_body():
    """0–10s luôn là hook_chain riêng — không bị gap-fill/stitch nhầm thành body."""
    from videobuilder.core.generate_prompts import (
        TranscriptSegment,
        VisualBeat,
        finalize_timeline_coverage,
        HOOK_CHAIN_MIN,
        HOOK_CHAIN_MAX,
        DEFAULT_CHARACTER_STYLE,
        DEFAULT_LABELS,
        DEFAULT_ART_STYLE,
        HOOK_TYPES,
    )

    segments = [
        TranscriptSegment(0.0, 1.86, "Một."),
        TranscriptSegment(2.8, 6.1, "Hai."),
        TranscriptSegment(6.1, 10.32, "Ba."),
        TranscriptSegment(10.32, 15.0, "Bốn."),
    ]
    beats = [
        VisualBeat(
            0.0, 1.86, "Một.", DEFAULT_CHARACTER_STYLE,
            "Cú mở cảnh", "Cận", "Tối", "hook visual 1", DEFAULT_LABELS, DEFAULT_ART_STYLE,
            is_hook=True, hook_type=HOOK_TYPES[0],
        ),
        VisualBeat(
            2.8, 6.1, "Hai.", DEFAULT_CHARACTER_STYLE,
            "Bất thường", "Trung", "Sám", "hook visual 2", DEFAULT_LABELS, DEFAULT_ART_STYLE,
            is_hook=True, hook_type=HOOK_TYPES[0],
        ),
        VisualBeat(
            6.1, 10.32, "Ba.", DEFAULT_CHARACTER_STYLE,
            "Nguy hiểm", "Rộng", "Ngoài", "hook visual 3", DEFAULT_LABELS, DEFAULT_ART_STYLE,
            is_hook=True, hook_type=HOOK_TYPES[0],
        ),
        VisualBeat(
            10.32, 15.0, "Bốn.", DEFAULT_CHARACTER_STYLE,
            "body", "Rộng", "Ngoài", "body visual", DEFAULT_LABELS, DEFAULT_ART_STYLE,
        ),
    ]
    out = finalize_timeline_coverage(beats, segments)
    hooks = [b for b in out if b.is_hook]
    body = [b for b in out if not b.is_hook]
    assert HOOK_CHAIN_MIN <= len(hooks) <= HOOK_CHAIN_MAX
    assert hooks[0].start == 0.0
    assert all(h.hook_type for h in hooks)
    assert all("hook visual" in h.visual for h in hooks)
    assert body[0].start >= hooks[-1].end - 0.02
    assert not body[0].is_hook


def test_realign_body_matches_segments_not_stretched_llm_window():
    """Body beat không bị kéo dài — quote bám đúng cue SRT trong cửa sổ."""
    from videobuilder.core.generate_prompts import (
        TranscriptSegment,
        VisualBeat,
        _realign_body_beats_to_segments,
        DEFAULT_CHARACTER_STYLE,
        DEFAULT_LABELS,
        DEFAULT_ART_STYLE,
    )

    segments = [
        TranscriptSegment(10.0, 14.0, "Câu mười."),
        TranscriptSegment(20.0, 24.0, "Câu hai mươi."),
        TranscriptSegment(30.0, 35.0, "Câu ba mươi."),
    ]
    # LLM đặt beat lệch / nhảy cóc — realign phải gán đúng cue
    body = [
        VisualBeat(
            10.0, 14.0, "Câu mười.", DEFAULT_CHARACTER_STYLE,
            "cảnh 1", "Cận", "Nền", "visual một", DEFAULT_LABELS, DEFAULT_ART_STYLE,
        ),
        VisualBeat(
            70.0, 80.0, "sai hoàn toàn", DEFAULT_CHARACTER_STYLE,
            "cảnh 2", "Rộng", "Nền", "visual hai", DEFAULT_LABELS, DEFAULT_ART_STYLE,
        ),
    ]
    fixed = _realign_body_beats_to_segments(body, segments, hook_end=0.0)
    assert len(fixed) >= 3
    assert fixed[0].audio_quote == "Câu mười."
    assert fixed[1].audio_quote == "Câu hai mươi."
    assert fixed[2].audio_quote == "Câu ba mươi."
    assert "sai hoàn toàn" not in " ".join(b.audio_quote for b in fixed)
    assert fixed[1].start >= 20.0 - 0.02
    assert fixed[2].start >= 30.0 - 0.02
    assert fixed[0].end <= 14.01


def test_large_srt_gap_splits_beats_not_one_long_image():
    """Mọi project: hole SRT lớn → beat mới neo đúng cue, không kéo ảnh 20s."""
    from videobuilder.core.generate_prompts import (
        TranscriptSegment,
        VisualBeat,
        _realign_body_beats_to_segments,
        DEFAULT_CHARACTER_STYLE,
        DEFAULT_LABELS,
        DEFAULT_ART_STYLE,
    )

    segments = [
        TranscriptSegment(50.883, 53.440, "Cue A."),
        TranscriptSegment(70.649, 73.568, "Cue B sau hole."),
        TranscriptSegment(73.568, 75.514, "Cue C."),
    ]
    body = [
        VisualBeat(
            50.883, 53.440, "Cue A.", DEFAULT_CHARACTER_STYLE,
            "cảnh", "Rộng", "Nền", "VIS_A",
            DEFAULT_LABELS, DEFAULT_ART_STYLE,
        ),
        VisualBeat(
            70.649, 73.568, "Cue B sau hole.",
            DEFAULT_CHARACTER_STYLE, "cảnh", "Rộng", "Nền", "VIS_B",
            DEFAULT_LABELS, DEFAULT_ART_STYLE,
        ),
    ]
    fixed = _realign_body_beats_to_segments(body, segments, hook_end=10.32)
    a = next(b for b in fixed if "Cue A" in b.audio_quote)
    b = next(b for b in fixed if "Cue B" in b.audio_quote)
    assert a.end <= 53.45
    assert b.start >= 70.6
    assert b.end - b.start < 8.0
    assert b.start - a.end > 15.0
    assert "VIS_B" in b.visual


def test_max_srt_cue_merge_gap_derived_from_pacing():
    from videobuilder.core.generate_prompts import (
        OPENING_DENSE_SEC,
        _max_srt_cue_merge_gap,
    )

    assert _max_srt_cue_merge_gap(0.0) < OPENING_DENSE_SEC
    assert _max_srt_cue_merge_gap(50.0) < 17.0
    assert _max_srt_cue_merge_gap(50.0) >= _max_srt_cue_merge_gap(5.0)


def test_realign_after_30s_uses_segment_llm_not_early_beat():
    """Cue sau 30s lấy visual LLM đúng cửa sổ — không dính beat 10s."""
    from videobuilder.core.generate_prompts import (
        TranscriptSegment,
        VisualBeat,
        _realign_body_beats_to_segments,
        DEFAULT_CHARACTER_STYLE,
        DEFAULT_LABELS,
        DEFAULT_ART_STYLE,
    )

    segments = [
        TranscriptSegment(10.0, 14.0, "Mười giây."),
        TranscriptSegment(32.0, 36.0, "Ba mươi hai giây."),
        TranscriptSegment(45.0, 49.0, "Bốn mươi lăm giây."),
    ]
    body = [
        VisualBeat(
            10.0, 14.0, "Mười giây.", DEFAULT_CHARACTER_STYLE,
            "cảnh mười", "Cận", "Nền", "VISUAL_10S", DEFAULT_LABELS, DEFAULT_ART_STYLE,
        ),
        VisualBeat(
            32.0, 36.0, "Ba mươi hai giây.", DEFAULT_CHARACTER_STYLE,
            "cảnh 32", "Rộng", "Nền", "VISUAL_32S", DEFAULT_LABELS, DEFAULT_ART_STYLE,
        ),
        VisualBeat(
            45.0, 49.0, "Bốn mươi lăm giây.", DEFAULT_CHARACTER_STYLE,
            "cảnh 45", "Trung", "Nền", "VISUAL_45S", DEFAULT_LABELS, DEFAULT_ART_STYLE,
        ),
    ]
    fixed = _realign_body_beats_to_segments(body, segments, hook_end=0.0)
    quotes = [b.audio_quote for b in fixed]
    visuals = [b.visual for b in fixed]
    idx_32 = quotes.index("Ba mươi hai giây.")
    assert "VISUAL_32S" in visuals[idx_32]
    assert "VISUAL_10S" not in visuals[idx_32]


def test_snap_beat_times_to_segments():
    from videobuilder.core.generate_prompts import (
        TranscriptSegment,
        _snap_beat_times_to_segments,
    )

    segments = [
        TranscriptSegment(2.8, 6.1, "Hai."),
        TranscriptSegment(6.1, 10.0, "Ba."),
    ]
    start, end = _snap_beat_times_to_segments(3.5, 5.0, segments)
    assert start == 2.8
    assert end == 6.1


def test_beat_from_dict_infers_audio_quote_from_segments():
    from videobuilder.core.generate_prompts import (
        TranscriptSegment,
        _beat_from_dict,
    )

    segments = [
        TranscriptSegment(0.0, 2.5, "Bạn mở mắt."),
        TranscriptSegment(2.5, 5.0, "Trời vẫn còn xám."),
    ]
    beat = _beat_from_dict(
        {
            "start_sec": 0.0,
            "end_sec": 5.0,
            "visual": "Cảnh mở.",
        },
        segments=segments,
    )
    assert beat.audio_quote == "Bạn mở mắt. Trời vẫn còn xám."


def test_parse_llm_beats_fills_missing_audio_quote():
    from videobuilder.core.generate_prompts import (
        TranscriptSegment,
        parse_llm_beats,
    )

    segments = [
        TranscriptSegment(10.0, 14.0, "Đoạn giữa video."),
    ]
    beats = parse_llm_beats(
        {
            "beats": [
                {
                    "start_sec": 10.0,
                    "end_sec": 14.0,
                    "scene_intent": "Minh họa",
                }
            ]
        },
        segments,
    )
    assert len(beats) == 1
    assert beats[0].audio_quote == "Đoạn giữa video."


def test_normalize_beat_rejects_python_list_labels():
    from videobuilder.core.generate_prompts import _beat_from_dict

    beat = _beat_from_dict({
        "start_sec": 10.0,
        "end_sec": 14.0,
        "audio_quote": "Test quote",
        "character_desc": "Người chính",
        "labels": "['gió lạnh', 'cỏ khô']",
        "style": "tối giản",
    })
    assert beat.labels == DEFAULT_LABELS
    assert beat.character_desc == DEFAULT_CHARACTER_STYLE
    assert beat.style == DEFAULT_ART_STYLE
    beat = VisualBeat(
        start=0.0,
        end=5.0,
        audio_quote="Hook audio",
        character_desc=DEFAULT_CHARACTER_STYLE,
        scene_intent="Tò mò",
        camera="Cận",
        background="Tối giản",
        visual="Nhân vật đối mặt nguy hiểm",
        labels=DEFAULT_LABELS,
        style=DEFAULT_ART_STYLE,
        is_hook=True,
        hook_type=HOOK_TYPES[0],
    )
    line = format_beat_line(1, beat)
    assert line.startswith("001_[")
    assert "hook" in line.lower() or HOOK_TYPES[0] in line
    assert "Điểm nối chuyển cảnh:" in line


class TestTranscriptAudit:
    def test_detects_large_gap_like_short_srt(self):
        from videobuilder.core.generate_prompts import audit_transcript_segments

        segments = [
            TranscriptSegment(50.883, 53.440, "không có thuốc sát trùng"),
            TranscriptSegment(70.649, 84.217, "Mùi trong gió"),
        ]
        issues = audit_transcript_segments(segments)
        gaps = [i for i in issues if i.kind == "gap"]
        assert len(gaps) == 1
        assert gaps[0].gap_sec == pytest.approx(17.209, abs=0.01)

    def test_validate_blocks_prompt_generation_on_gap(self):
        from videobuilder.core.generate_prompts import (
            GeneratePromptsError,
            validate_transcript_for_prompts,
        )

        segments = [
            TranscriptSegment(0.0, 2.0, "Mở đầu"),
            TranscriptSegment(12.0, 14.0, "Sau im lặng dài"),
        ]
        with pytest.raises(GeneratePromptsError, match="Thiếu transcript"):
            validate_transcript_for_prompts(segments)

    def test_clean_transcript_passes_audit(self):
        from videobuilder.core.generate_prompts import validate_transcript_for_prompts

        segments = [
            TranscriptSegment(0.0, 2.0, "Mở đầu"),
            TranscriptSegment(2.5, 5.0, "Tiếp theo"),
        ]
        assert validate_transcript_for_prompts(segments) == []

    def test_generate_image_prompts_runs_audit(self, tmp_path: Path, monkeypatch):
        from videobuilder.core.generate_prompts import (
            GeneratePromptsError,
            generate_image_prompts_from_segments,
        )

        segments = [
            TranscriptSegment(0.0, 2.0, "OK"),
            TranscriptSegment(15.0, 18.0, "Gap quá lớn"),
        ]
        with pytest.raises(GeneratePromptsError):
            generate_image_prompts_from_segments(
                segments,
                tmp_path / "out.txt",
                skip_transcript_audit=False,
            )

        called = {"n": 0}

        def fake_llm(*_a, **_k):
            called["n"] += 1
            return []

        monkeypatch.setattr(
            "videobuilder.core.generate_prompts.call_groq_visual_beats",
            fake_llm,
        )
        generate_image_prompts_from_segments(
            segments[:1],
            tmp_path / "ok.txt",
            skip_transcript_audit=False,
        )
        assert called["n"] == 1

