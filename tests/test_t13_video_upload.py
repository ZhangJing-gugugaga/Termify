"""T13 视频接入 — POST /api/upload-video (ffmpeg 抽帧+转换)。

覆盖 GOAL-PROMPT §11.③: 后端 ffmpeg 视频接入。
"""

from __future__ import annotations

import importlib.util
import io
import json
from unittest.mock import patch, MagicMock

import pytest
from PIL import Image


pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("flask"),
    reason="flask 未安装",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


def _make_fake_video(tmp_path, name="test.mp4", size=1024):
    """Create a small file that pretends to be a video."""
    p = tmp_path / name
    p.write_bytes(b"\x00" * size)
    return str(p)


def _mock_extract_frames(tmp_path):
    """Create fake frame PNGs and return mock values."""
    frames_dir = tmp_path / "fake_frames"
    frames_dir.mkdir(exist_ok=True)
    for i in range(3):
        img = Image.new("RGB", (32, 16), (i * 50, i * 50, i * 50))
        img.save(str(frames_dir / f"frame_{i:05d}.png"))
    return str(frames_dir), 10.0


def test_upload_video_rejects_no_file(client):
    """未上传文件应返回 400。"""
    resp = client.post("/api/upload-video", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_video_accepts_mp4(client, tmp_path):
    """上传 .mp4 文件应成功并返回 task_id。"""
    mock_result = _mock_extract_frames(tmp_path)

    with patch("termify.video.extract_frames", return_value=mock_result):
        with patch("termify.video.validate_video"):
            data = {"file": (io.BytesIO(b"video-bytes"), "test.mp4")}
            resp = client.post("/api/upload-video", data=data,
                               content_type="multipart/form-data")

    assert resp.status_code == 200, f"resp: {resp.data[:300]}"
    body = json.loads(resp.data)
    assert "task_id" in body
    assert body["frames_count"] == 3  # 3 fake frames
    assert body["original_size"]["type"] == "video"


def test_upload_video_rejects_oversize(client, tmp_path):
    """超大文件应在 validate 阶段被拒。"""
    def mock_validate(path):
        from termify.video import VideoError
        raise VideoError("Video file exceeds 20MB limit")

    with patch("termify.video.validate_video", side_effect=mock_validate):
        data = {"file": (io.BytesIO(b"x"), "huge.mp4")}
        resp = client.post("/api/upload-video", data=data,
                           content_type="multipart/form-data")

    assert resp.status_code in (400, 422)


def test_upload_video_ffmpeg_failure(client, tmp_path):
    """ffmpeg 抽帧失败应返回 422。"""
    def mock_extract(path):
        from termify.video import VideoError
        raise VideoError("ffmpeg is not installed or not on PATH")

    with patch("termify.video.extract_frames", side_effect=mock_extract):
        with patch("termify.video.validate_video"):
            data = {"file": (io.BytesIO(b"vid"), "test.mp4")}
            resp = client.post("/api/upload-video", data=data,
                               content_type="multipart/form-data")

    assert resp.status_code == 422


def test_video_validate_rejects_bad_ext(tmp_path):
    """validate_video 应拒绝非视频扩展名。"""
    from termify.video import validate_video, VideoError
    fake = tmp_path / "test.txt"
    fake.write_bytes(b"not a video")
    with pytest.raises(VideoError, match="Unsupported video format"):
        validate_video(str(fake))


def test_video_validate_rejects_oversize(tmp_path):
    """超大文件应在 validate 阶段被拒。"""
    from termify.video import validate_video, VideoError
    fake = tmp_path / "big.mp4"
    fake.write_bytes(b"x" * 1024)

    with patch("os.path.getsize", return_value=25 * 1024 * 1024):
        with pytest.raises(VideoError, match="exceeds"):
            validate_video(str(fake))
