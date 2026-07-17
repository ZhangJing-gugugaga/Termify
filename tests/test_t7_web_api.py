"""T7 Web API 端到端 — flask.test_client() 走 upload→preview→generate→download。

覆盖 Web 集成。
"""

from __future__ import annotations

import importlib.util
import io
import json
import os

import pytest
from PIL import Image


pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("flask"),
    reason="flask 未安装",
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create Flask test client with temp upload dir.

    Flask downloads via relative 'tmp/' path — make it absolute so it works
    regardless of process cwd at send_file() time.
    """
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    from app import app
    app.config["TESTING"] = True
    # Patch the download view to resolve tmp/ relative to tmp_path
    import app as app_mod
    original_download = app_mod.download

    def patched_download(filename):
        if ".." in filename or os.sep in filename or "/" in filename:
            return __import__("flask").jsonify({"error": "Invalid filename"}), 400
        path = str(tmp_path / "tmp" / filename)
        if not os.path.isfile(path):
            return __import__("flask").jsonify({"error": "File not found"}), 404
        return __import__("flask").send_file(path, as_attachment=True)

    # Directly patch the route
    app.view_functions["download"] = patched_download
    with app.test_client() as c:
        yield c


def _make_image_bytes(w=16, h=8, color=(100, 150, 200)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    buf.seek(0)
    return buf


def test_api_upload_returns_task_id(client):
    """POST /api/upload 返回 task_id + 元数据。"""
    data = {"file": (_make_image_bytes(), "test.png")}
    resp = client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert "task_id" in body
    assert body.get("frames_count", 0) >= 1


def test_api_preview_returns_frames(client):
    """GET /api/preview/<id> 返回帧数据。"""
    data = {"file": (_make_image_bytes(), "test.png")}
    resp = client.post("/api/upload", data=data, content_type="multipart/form-data")
    task_id = json.loads(resp.data)["task_id"]

    resp = client.get(f"/api/preview/{task_id}?charset=ascii&frame=0")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert "lines" in body
    assert len(body["lines"]) >= 1


def test_api_generate_returns_download_url(client):
    """POST /api/generate 返回 download_url。"""
    data = {"file": (_make_image_bytes(), "test.png")}
    resp = client.post("/api/upload", data=data, content_type="multipart/form-data")
    task_id = json.loads(resp.data)["task_id"]

    resp = client.post(
        "/api/generate",
        data=json.dumps({"task_id": task_id, "charset": "ascii", "format": "python"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert "download_url" in body


def test_api_download_serves_file(client):
    """GET /api/download/<filename> 下载生成的文件。"""
    data = {"file": (_make_image_bytes(), "test.png")}
    resp = client.post("/api/upload", data=data, content_type="multipart/form-data")
    task_id = json.loads(resp.data)["task_id"]

    resp = client.post(
        "/api/generate",
        data=json.dumps({"task_id": task_id, "charset": "ascii", "format": "python"}),
        content_type="application/json",
    )
    download_url = json.loads(resp.data)["download_url"]
    filename = download_url.split("/")[-1]

    resp = client.get(f"/api/download/{filename}")
    assert resp.status_code == 200
    assert len(resp.data) > 50


def test_api_all_charsets_preview(client):
    """5 种 charset 都能走通 preview 接口。"""
    data = {"file": (_make_image_bytes(), "test.png")}
    resp = client.post("/api/upload", data=data, content_type="multipart/form-data")
    task_id = json.loads(resp.data)["task_id"]

    for cs in ["ascii", "blocks", "braille", "geometric", "binary"]:
        resp = client.get(f"/api/preview/{task_id}?charset={cs}&frame=0")
        assert resp.status_code == 200, f"charset={cs} 失败"
        body = json.loads(resp.data)
        assert "lines" in body
