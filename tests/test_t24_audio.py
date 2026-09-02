"""T24 — 音频链路（背景音乐 / 视频自带音频）。

- extract_audio / has_audio_stream：有音轨视频抽出 AAC，无音轨返回 None
- mux_audio_file：成品 MP4 合并音轨（-shortest）
- API：upload-music / remove-music / audio-info；generate MP4/HTML 带音频
- preview 大响应守卫（blocks 高分辨率多帧 → 413 too_large）
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess

import pytest
from PIL import Image

from termify.output.video import encode_mp4
from termify.video import (
    extract_audio,
    has_audio_stream,
    mux_audio_file,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _make_video(path, with_audio: bool):
    """ffmpeg testsrc 测试视频（可选 sine 音轨，固定 2 秒）。"""
    if with_audio:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=duration=2:size=128x64:rate=10",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
             str(path)],
            check=True, capture_output=True,
        )
    else:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=duration=2:size=128x64:rate=10",
             "-pix_fmt", "yuv420p",
             str(path)],
            check=True, capture_output=True,
        )


# --- 单元：探测 / 抽取 / 合成 --------------------------------------------------------

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_has_audio_stream_true_and_false(tmp_path):
    va = tmp_path / "a.mp4"
    _make_video(va, with_audio=True)
    vs = tmp_path / "s.mp4"
    _make_video(vs, with_audio=False)
    assert has_audio_stream(str(va)) is True
    assert has_audio_stream(str(vs)) is False


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_extract_audio_with_and_without_track(tmp_path):
    va = tmp_path / "a.mp4"
    _make_video(va, with_audio=True)
    out = tmp_path / "out.m4a"
    assert extract_audio(str(va), str(out)) == str(out)
    assert out.is_file() and out.stat().st_size > 0

    vs = tmp_path / "s.mp4"
    _make_video(vs, with_audio=False)
    out2 = tmp_path / "out2.m4a"
    assert extract_audio(str(vs), str(out2)) is None
    assert not out2.exists()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_mux_audio_file_adds_stream(tmp_path):
    vs = tmp_path / "s.mp4"
    _make_video(vs, with_audio=False)
    va = tmp_path / "a.mp4"
    _make_video(va, with_audio=True)
    out = tmp_path / "muxed.mp4"
    mux_audio_file(str(vs), str(va), str(out))
    assert out.is_file()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True)
    assert "audio" in probe.stdout


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_encode_mp4_with_audio_path(tmp_path):
    """encode_mp4(audio_path=...) 成品应含音轨（导出主链路）。"""
    from termify.charset import render_frame
    from termify.engine import FrameSequence, scale_frame

    from PIL import Image as PILImage

    img = PILImage.new("RGB", (40, 20), (200, 30, 30))
    scaled = scale_frame(img, 40, 20)
    frames = [render_frame(scaled, "ascii", 40, 20) for _ in range(5)]
    seq = FrameSequence(lines_per_frame=frames, interval=0.1,
                        width=40, height=20, charset="ascii")
    va = tmp_path / "a.mp4"
    _make_video(va, with_audio=True)
    audio_m4a = tmp_path / "audio.m4a"
    assert extract_audio(str(va), str(audio_m4a))
    out = tmp_path / "final.mp4"
    encode_mp4(seq, str(out), audio_path=str(audio_m4a))
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True)
    assert "audio" in probe.stdout


# --- 大响应守卫 ----------------------------------------------------------------------

def test_preview_payload_guard_thresholds():
    from app import _preview_payload_too_large as guard
    # blocks 200×60×514 帧 ≈ 259MB → 超限
    assert guard(514, 200, 60, "blocks") is True
    # blocks 80×24×514 ≈ 41MB → 超限
    assert guard(514, 80, 24, "blocks") is True
    # ascii 200×60×514（实测 ~6MB，估算 ~24MB）→ 放行
    assert guard(514, 200, 60, "ascii") is False
    # 单帧图片任意尺寸 → 放行
    assert guard(1, 400, 400, "blocks") is False


# --- API --------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    # 产物基准（uploads/tmp）已仓库根锚定，测试用 TERMIFY_BASE_DIR 指回 tmp_path 隔离。
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    from app import app, _RL_LOG
    _RL_LOG.clear()
    app.config["TESTING"] = True
    return app.test_client()


def _make_task(client) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 200, 10)).save(buf, format="PNG")
    buf.seek(0)
    resp = client.post("/api/upload-batch",
                       data={"files": [(buf, "t.png")]},
                       content_type="multipart/form-data")
    return json.loads(resp.data)["task_ids"][0]["task_id"]


def test_upload_music_rejects_bad_task(client):
    resp = client.post("/api/upload-music",
                       data={"task_id": "nothex", "file": (io.BytesIO(b"x"), "m.mp3")},
                       content_type="multipart/form-data")
    assert resp.status_code == 404


def test_upload_music_rejects_bad_ext(client):
    task_id = _make_task(client)
    resp = client.post("/api/upload-music",
                       data={"task_id": task_id, "file": (io.BytesIO(b"x"), "m.exe")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400


def test_music_upload_info_remove_roundtrip(client):
    task_id = _make_task(client)
    resp = client.post("/api/upload-music",
                       data={"task_id": task_id,
                             "file": (io.BytesIO(b"ID3fakemp3"), "bgm.mp3")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["ok"] is True and body["music"].startswith("music_")

    info = json.loads(client.get(f"/api/audio-info/{task_id}").data)
    assert info["has_audio"] is True and info["kind"] == "music"
    assert info["mime"] == "audio/mpeg"

    rm = json.loads(client.post("/api/remove-music",
                                data=json.dumps({"task_id": task_id}),
                                content_type="application/json").data)
    assert rm["ok"] is True
    info2 = json.loads(client.get(f"/api/audio-info/{task_id}").data)
    assert info2["has_audio"] is False


def test_music_reupload_replaces_previous(client):
    task_id = _make_task(client)
    for name in ("one.mp3", "two.wav"):
        resp = client.post("/api/upload-music",
                           data={"task_id": task_id,
                                 "file": (io.BytesIO(b"audio-bytes"), name)},
                           content_type="multipart/form-data")
        assert resp.status_code == 200
    info = json.loads(client.get(f"/api/audio-info/{task_id}").data)
    assert info["kind"] == "music" and info["mime"] == "audio/wav"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_upload_video_reports_has_audio_and_mp4_export_muxes(client, tmp_path):
    """端到端：带音轨视频上传 → has_audio=true → MP4 导出含音轨。"""
    vpath = tmp_path / "withaudio.mp4"
    _make_video(vpath, with_audio=True)
    resp = client.post("/api/upload-video",
                       data={"file": (io.BytesIO(vpath.read_bytes()), "withaudio.mp4")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    task_id = body["task_id"]
    assert body["has_audio"] is True
    # 抽出的音轨文件存在
    assert os.path.isfile(os.path.join("uploads", f"audio_{task_id}.m4a"))

    gen = json.loads(client.post(
        "/api/generate",
        data=json.dumps({"task_id": task_id, "charset": "ascii",
                         "format": "mp4", "width": 40, "height": 20}),
        content_type="application/json").data)
    assert "download_url" in gen
    mp4_name = gen["download_url"].rsplit("/", 1)[-1]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0",
         os.path.join("tmp", mp4_name)],
        capture_output=True, text=True)
    assert "audio" in probe.stdout


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_generate_html_embeds_video_audio(client, tmp_path):
    """HTML 导出：视频自带音轨以 data-URI 内嵌。"""
    vpath = tmp_path / "withaudio.mp4"
    _make_video(vpath, with_audio=True)
    resp = client.post("/api/upload-video",
                       data={"file": (io.BytesIO(vpath.read_bytes()), "withaudio.mp4")},
                       content_type="multipart/form-data")
    task_id = json.loads(resp.data)["task_id"]
    gen = json.loads(client.post(
        "/api/generate",
        data=json.dumps({"task_id": task_id, "charset": "ascii",
                         "format": "html", "width": 40, "height": 20}),
        content_type="application/json").data)
    html_name = gen["download_url"].rsplit("/", 1)[-1]
    html = open(os.path.join("tmp", html_name), encoding="utf-8").read()
    assert "data:audio/mp4;base64," in html
    assert "playBtn" in html


def test_music_uploaded_overrides_video_audio_in_html(client, tmp_path):
    """用户上传音乐优先于视频原声。"""
    vpath = tmp_path / "withaudio.mp4"
    _make_video(vpath, with_audio=True)
    resp = client.post("/api/upload-video",
                       data={"file": (io.BytesIO(vpath.read_bytes()), "withaudio.mp4")},
                       content_type="multipart/form-data")
    task_id = json.loads(resp.data)["task_id"]
    client.post("/api/upload-music",
                data={"task_id": task_id,
                      "file": (io.BytesIO(b"ID3custom"), "mine.mp3")},
                content_type="multipart/form-data")
    gen = json.loads(client.post(
        "/api/generate",
        data=json.dumps({"task_id": task_id, "charset": "ascii",
                         "format": "html", "width": 40, "height": 20}),
        content_type="application/json").data)
    html_name = gen["download_url"].rsplit("/", 1)[-1]
    html = open(os.path.join("tmp", html_name), encoding="utf-8").read()
    assert "data:audio/mpeg;base64," in html  # mp3 音乐，而非视频的 audio/mp4
