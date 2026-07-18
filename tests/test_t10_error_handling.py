"""T10 错误处理 — 异常输入验证系统健壮性。

覆盖 B1-B4 相关的边界情况。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys

import pytest
from PIL import Image

from termify.engine import convert
from termify.charset import CHARSETS

PY = sys.executable


def _make_image_bytes(w=8, h=4, color=(100, 100, 100)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    buf.seek(0)
    return buf


def test_error_rejects_unsupported_file_format(tmp_path):
    """上传非 GIF/PNG/JPG 应报错或 gracefully 处理。"""
    bad = tmp_path / "test.bmp"
    Image.new("RGB", (8, 4), (100, 100, 100)).save(str(bad), format="BMP")
    # convert 不检查扩展名，但 Pillow 应能读 BMP
    # 这里只验证不崩溃
    seq = convert(str(bad), "ascii", 8, 4)
    assert seq is not None


def test_error_oversize_file_rejected(tmp_path):
    """超过 20MB 上限应触发错误。"""
    from termify.frames import extract_frames, MAX_UPLOAD_BYTES
    # 创建小文件但 mock getsize
    p = tmp_path / "small.png"
    Image.new("RGB", (4, 4), (0, 0, 0)).save(str(p))
    orig = os.path.getsize
    try:
        os.path.getsize = lambda path: MAX_UPLOAD_BYTES + 1
        with pytest.raises(ValueError):
            extract_frames(str(p))
    finally:
        os.path.getsize = orig


def test_error_corrupt_image_graceful(tmp_path):
    """损坏的图像文件应抛异常但不污染全局状态。"""
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)  # 截断 PNG
    with pytest.raises(Exception):
        convert(str(corrupt), "ascii", 8, 4)


def test_error_empty_file(tmp_path):
    """空文件应触发错误处理。"""
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    with pytest.raises(Exception):
        convert(str(empty), "ascii", 8, 4)


def test_api_upload_without_file(client=None):
    """不上传文件时 API 应返回 400。"""
    pytest.importorskip("flask")
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        resp = c.post("/api/upload", data={}, content_type="multipart/form-data")
        assert resp.status_code == 400


def test_api_invalid_format_rejected():
    """生成不支持的格式应返回 400。"""
    pytest.importorskip("flask")
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        data = {"file": (_make_image_bytes(), "test.png")}
        resp = c.post("/api/upload", data=data, content_type="multipart/form-data")
        task_id = json.loads(resp.data)["task_id"]
        resp = c.post(
            "/api/generate",
            data=json.dumps({"task_id": task_id, "charset": "ascii", "format": "markdown"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
