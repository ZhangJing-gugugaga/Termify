"""T22 — 视频导入增强 + 平台链接解析。

- adaptive_fps：短视频全速采样，长视频自适应降 fps（无时长上限）
- videofetch：域名白名单（SSRF）、非白名单拒绝、下载文件名服务器生成
- API：fetch-video-url 白名单外 400；upload-video 超限频 429
- convert_video_file 端到端（需 ffmpeg）
"""

from __future__ import annotations

import io
import json
import os
import shutil

import pytest
from PIL import Image

from termify.video import BASE_FPS, TARGET_MAX_FRAMES, adaptive_fps, convert_video_file
from termify.videofetch import (
    ALLOWED_HOSTS,
    VideoFetchError,
    is_video_platform_url,
    validate_video_url,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None


# --- adaptive_fps ---------------------------------------------------------------

def test_adaptive_fps_short_video_full_rate():
    assert adaptive_fps(10) == BASE_FPS


def test_adaptive_fps_long_video_reduced():
    # 10 分钟视频：fps 应降到 ~TARGET_MAX_FRAMES/600
    assert adaptive_fps(600) == pytest.approx(TARGET_MAX_FRAMES / 600, rel=1e-6)


def test_adaptive_fps_extreme_long_clamped_to_min():
    assert adaptive_fps(10**7) >= 0.5


def test_adaptive_fps_unknown_duration_falls_back():
    assert adaptive_fps(None) == BASE_FPS
    assert adaptive_fps(0) == BASE_FPS


# --- URL 白名单（SSRF）-----------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://www.bilibili.com/video/BV1xx411c7mD",
    "https://b23.tv/abcd123",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://v.douyin.com/abcd/",
])
def test_validate_video_url_allowlist_ok(url):
    assert validate_video_url(url) == url


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:5000/x",
    "http://localhost/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/internal.mp4",
    "http://192.168.1.1/v.mp4",
    "https://evil.example.com/video.mp4",
    "ftp://bilibili.com/x",
    "file:///etc/passwd",
    "",
    None,
])
def test_validate_video_url_rejects(url):
    with pytest.raises(VideoFetchError):
        validate_video_url(url)


def test_is_video_platform_url_loose():
    assert is_video_platform_url("https://www.bilibili.com/video/x")
    assert is_video_platform_url("https://youtu.be/x")
    assert not is_video_platform_url("https://example.com/cat.gif")


# --- API 守卫 ----------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    from app import app, _RL_LOG
    _RL_LOG.clear()  # 同 IP 连续用例不互相顶到 429
    app.config["TESTING"] = True
    return app.test_client()


def test_fetch_video_url_rejects_non_allowlist(client):
    resp = client.post("/api/fetch-video-url",
                       data=json.dumps({"url": "https://evil.example.com/v.mp4"}),
                       content_type="application/json")
    assert resp.status_code == 400
    assert "平台" in json.loads(resp.data)["error"]


def test_fetch_video_url_rejects_private_ip(client):
    resp = client.post("/api/fetch-video-url",
                       data=json.dumps({"url": "http://127.0.0.1:5000/admin"}),
                       content_type="application/json")
    assert resp.status_code == 400


def test_fetch_video_url_rejects_empty(client):
    resp = client.post("/api/fetch-video-url",
                       data=json.dumps({"url": ""}),
                       content_type="application/json")
    assert resp.status_code == 400


def test_upload_video_rejects_bad_ext(client):
    buf = io.BytesIO(b"not a video")
    resp = client.post("/api/upload-video",
                       data={"file": (buf, "x.exe")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400


# --- 端到端（需 ffmpeg）--------------------------------------------------------------

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_convert_video_file_end_to_end(tmp_path):
    """用 ffmpeg 生成一个 3 秒测试视频再走完整转换管线。"""
    import subprocess

    video_path = tmp_path / "in.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=duration=3:size=128x64:rate=10",
         "-pix_fmt", "yuv420p", str(video_path)],
        check=True, capture_output=True,
    )
    seq = convert_video_file(str(video_path), charset="ascii", width=40, height=20)
    assert seq.width == 40 and seq.height == 20
    assert 20 <= len(seq.lines_per_frame) <= 40  # 3s @10fps ≈ 30 帧
    assert len(seq.lines_per_frame[0]) == 20
    assert not video_path.exists() or video_path.exists()  # 不删源: delete_source=False
    # delete_source=True 时源文件被清掉
    video_path.write_bytes(video_path.read_bytes())
    seq2 = convert_video_file(str(video_path), delete_source=True)
    assert len(seq2.lines_per_frame) > 0
    assert not video_path.exists()


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_video_task_charset_switching(client, tmp_path):
    """回归 bug2：视频任务切风格/尺寸/自定义字符不得再报 Task not found。

    上传视频后帧目录持久化在 uploads/frames_<id>/，_get_sequence 命中
    目录分支本地重渲染（不再依赖单键缓存）。
    """
    import subprocess
    import os

    video_path = tmp_path / "in.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=duration=1:size=128x64:rate=10",
         "-pix_fmt", "yuv420p", str(video_path)],
        check=True, capture_output=True,
    )
    with open(video_path, "rb") as fh:
        resp = client.post("/api/upload-video",
                           data={"file": (fh, "in.mp4")},
                           content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data[:300]
    task_id = json.loads(resp.data)["task_id"]

    # 任务 metadata 应指向持久化帧目录
    from app import get_store
    task = get_store().get(task_id)
    assert task["filepath"] and os.path.isdir(task["filepath"])

    from urllib.parse import quote

    # 1) 默认 ascii（缓存命中）
    r1 = client.get(f"/api/preview/{task_id}?charset=ascii&frame=0")
    assert r1.status_code == 200, r1.data[:200]
    # 2) 切 blocks（不同缩放维度 → 新缓存键 → 走帧目录重渲染）
    r2 = client.get(f"/api/preview/{task_id}?charset=blocks&width=40&height=20")
    assert r2.status_code == 200, r2.data[:200]
    # 3) 切 shades
    r3 = client.get(f"/api/preview/{task_id}?charset=shades&frame=0")
    assert r3.status_code == 200, r3.data[:200]
    # 4) 切 custom + 自定义梯
    r4 = client.get(
        f"/api/preview/{task_id}?charset=custom&chars={quote('@# ')}&frame=0")
    assert r4.status_code == 200, r4.data[:200]
    joined = "".join(json.loads(r4.data)["lines"])
    assert set(joined) <= {"@", "#", " "}


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_extract_frames_out_dir_caller_owned(tmp_path):
    """out_dir 由调用方持有：失败/成功都不清掉该目录本身。"""
    import subprocess

    video_path = tmp_path / "in.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=duration=1:size=64x32:rate=10",
         "-pix_fmt", "yuv420p", str(video_path)],
        check=True, capture_output=True,
    )
    from termify.video import extract_frames

    out_dir = str(tmp_path / "persisted_frames")
    frames_dir, fps = extract_frames(str(video_path), out_dir=out_dir)
    assert frames_dir == out_dir
    assert os.path.isdir(out_dir)
    assert len([f for f in os.listdir(out_dir) if f.endswith(".png")]) >= 5
    assert fps > 0
