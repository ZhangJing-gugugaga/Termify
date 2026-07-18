"""T11 Web 批量上传 — 多文件并行上传，各文件独立状态。

覆盖 GOAL-PROMPT §11.① 功能扩展：Web 批量上传。
"""

from __future__ import annotations

import importlib.util
import io
import json

import pytest
from PIL import Image


pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("flask"),
    reason="flask 未安装",
)


def _img_bytes(w=16, h=8, color=(100, 150, 200), fmt="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format=fmt)
    buf.seek(0)
    return buf


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create Flask test client with temp upload dir."""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_batch_upload_two_files(client):
    """POST /api/upload-batch 上传 2 个文件，返回 2 个 task_id。"""
    data = {
        "files": [
            (_img_bytes(16, 8, (100, 100, 100), "PNG"), "a.png"),
            (_img_bytes(16, 8, (200, 200, 200), "PNG"), "b.png"),
        ]
    }
    resp = client.post("/api/upload-batch", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert len(body["task_ids"]) == 2
    assert all("task_id" in t for t in body["task_ids"])
    assert len(body["errors"]) == 0


def test_batch_upload_three_mixed(client):
    """上传 3 个文件 (PNG + JPG + PNG)，全部成功。"""
    data = {
        "files": [
            (_img_bytes(12, 6, (50, 50, 50), "PNG"), "x.png"),
            (_img_bytes(12, 6, (150, 150, 150), "JPEG"), "y.jpg"),
            (_img_bytes(12, 6, (250, 250, 250), "PNG"), "z.png"),
        ]
    }
    resp = client.post("/api/upload-batch", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert len(body["task_ids"]) == 3
    filenames = [t["filename"] for t in body["task_ids"]]
    assert "x.png" in filenames
    assert "y.jpg" in filenames


def test_batch_upload_skips_invalid(client):
    """混合有效 + 无效文件：有效返回 task_id，无效进 errors。"""
    bad_buf = io.BytesIO(b"not an image")
    data = {
        "files": [
            (_img_bytes(8, 4, (100, 100, 100), "PNG"), "good.png"),
            (bad_buf, "bad.txt"),
        ]
    }
    resp = client.post("/api/upload-batch", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    # 有效文件应成功
    assert len(body["task_ids"]) >= 1
    assert body["task_ids"][0]["filename"] == "good.png"
    # 无效文件应进 errors
    assert len(body["errors"]) >= 1
    assert any(e["filename"] == "bad.txt" for e in body["errors"])


def test_batch_upload_empty_rejected(client):
    """空文件列表应返回 400。"""
    resp = client.post("/api/upload-batch", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_batch_upload_each_has_independent_task(client):
    """批量上传的每个文件都有独立的 task_id，互不重复。"""
    data = {
        "files": [
            (_img_bytes(10, 5, (10, 10, 10), "PNG"), "f1.png"),
            (_img_bytes(10, 5, (20, 20, 20), "PNG"), "f2.png"),
            (_img_bytes(10, 5, (30, 30, 30), "PNG"), "f3.png"),
        ]
    }
    resp = client.post("/api/upload-batch", data=data, content_type="multipart/form-data")
    body = json.loads(resp.data)
    task_ids = [t["task_id"] for t in body["task_ids"]]
    assert len(task_ids) == len(set(task_ids))  # 全部唯一