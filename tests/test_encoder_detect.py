from pathlib import Path
from unittest.mock import patch

import videobuilder.core.pipeline as pipeline


def test_append_vaapi_upload():
    assert pipeline.append_vaapi_upload("scale=1280:720") == "scale=1280:720,format=nv12,hwupload"


def test_append_vaapi_filter_complex():
    src = "[0:v]setsar=1[vbase];[vbase][1:v]overlay=0:0[vout]"
    out, label = pipeline.append_vaapi_filter_complex(src)
    assert label == "[vvaapi]"
    assert out.endswith("[vout]format=nv12,hwupload[vvaapi]")


def test_detect_video_encoder_prefers_vaapi_on_linux(monkeypatch, tmp_path):
    dri = tmp_path / "dri"
    dri.mkdir()
    (dri / "renderD128").touch()

    fake_encoders = " V....D h264_vaapi H.264/AVC (VAAPI) (codec h264)\n"

    pipeline._ENCODER_CACHE = None
    pipeline._VAAPI_DEVICE_CACHE = None
    pipeline._VAAPI_DEVICE_CHECKED = False

    with patch.object(Path, "glob", return_value=[dri / "renderD128"]):
        with patch.object(Path, "is_dir", return_value=True):
            with patch("subprocess.check_output", return_value=fake_encoders):
                encoder, label = pipeline.detect_video_encoder()

    assert encoder == "h264_vaapi"
    assert label == "Linux GPU"


def test_encoder_args_vaapi():
    assert pipeline.encoder_args("h264_vaapi", "fast") == ["-c:v", "h264_vaapi", "-qp", "23"]
