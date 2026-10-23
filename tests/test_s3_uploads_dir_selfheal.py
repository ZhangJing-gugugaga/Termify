"""S3 — uploads/ 目录缺失时 /api/upload 曾直接 500（未捕获 FileNotFoundError）。

2026-10-23 重测：清掉 uploads/ 后上传即落 500，server.log 里 9 条 traceback。
/api/generate 早有 os.makedirs(..., exist_ok=True) 自愈，upload 写盘路径没有。
修复点选在 _safe_uploads_path（所有 uploads 写入的唯一闸口），与 _tmp_out_path
对 tmp/ 的做法一致；校验之后才建目录，非法文件名不留副作用。
"""

from __future__ import annotations

import io
import shutil

import pytest
from PIL import Image

flask = pytest.importorskip("flask")


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 4), (0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    from app import app, _RL_LOG
    _RL_LOG.clear()
    app.config["TESTING"] = True
    return app.test_client()


def test_upload_recreates_missing_uploads_dir(client, tmp_path):
    uploads = tmp_path / "uploads"
    shutil.rmtree(uploads)
    assert not uploads.exists()

    resp = client.post("/api/upload",
                       data={"file": (io.BytesIO(_png_bytes()), "a.png")},
                       content_type="multipart/form-data")

    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    assert uploads.is_dir()
    body = resp.get_json()
    assert body["task_id"]
    assert body["frames_count"] >= 1


def test_batch_upload_recreates_missing_uploads_dir(client, tmp_path):
    uploads = tmp_path / "uploads"
    shutil.rmtree(uploads)
    assert not uploads.exists()

    resp = client.post("/api/upload-batch",
                       data={"files": [(io.BytesIO(_png_bytes()), "a.png"),
                                       (io.BytesIO(_png_bytes()), "b.png")]},
                       content_type="multipart/form-data")

    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    body = resp.get_json()
    assert body["errors"] == []
    assert len(body["task_ids"]) == 2
    assert uploads.is_dir()


def test_invalid_filename_does_not_create_the_uploads_dir(client, tmp_path):
    """自愈只发生在校验通过之后：不支持的扩展名不该顺手把目录建出来。"""
    uploads = tmp_path / "uploads"
    shutil.rmtree(uploads)
    resp = client.post("/api/upload",
                       data={"file": (io.BytesIO(b"not an image"), "a.txt")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400
    assert not uploads.exists()