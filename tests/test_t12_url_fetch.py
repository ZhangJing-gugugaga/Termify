"""T12 URL 直输 — POST /api/fetch-url 安全约束 + 正常流程。

覆盖 GOAL-PROMPT §11.②: URL 直输.
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


def test_fetch_url_rejects_empty(client):
    """空 URL 应返回 400。"""
    resp = client.post("/api/fetch-url",
                       data=json.dumps({"url": ""}),
                       content_type="application/json")
    assert resp.status_code == 400


def test_fetch_url_rejects_malformed_scheme(client):
    """非 HTTP/HTTPS scheme 应拒绝。"""
    resp = client.post("/api/fetch-url",
                       data=json.dumps({"url": "ftp://evil.com/x.png"}),
                       content_type="application/json")
    assert resp.status_code == 400


def test_fetch_url_blocks_private_ip(client):
    """内网 IP (127.0.0.1) 应被 SSRF 防护拦截。"""
    resp = client.post("/api/fetch-url",
                       data=json.dumps({"url": "http://127.0.0.1/secret.png"}),
                       content_type="application/json")
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert "internal" in body.get("error", "").lower() or "private" in body.get("error", "").lower()


def test_fetch_url_blocks_private_ip_192(client):
    """192.168.x 内网应拦截。"""
    resp = client.post("/api/fetch-url",
                       data=json.dumps({"url": "http://192.168.1.1/img.png"}),
                       content_type="application/json")
    assert resp.status_code == 400


def test_fetch_url_success(client, tmp_path):
    """成功下载 + 转换流程（mock urllib）。"""
    img_buf = io.BytesIO()
    Image.new("RGB", (16, 16), (100, 100, 100)).save(img_buf, format="PNG")
    img_bytes = img_buf.getvalue()

    mock_resp = MagicMock()
    mock_resp.headers = {"Content-Type": "image/png", "Content-Length": str(len(img_bytes))}
    mock_resp.read = MagicMock(side_effect=[img_bytes, b""])
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        resp = client.post("/api/fetch-url",
                           data=json.dumps({"url": "https://example.com/cat.png"}),
                           content_type="application/json")

    assert resp.status_code == 200, f"resp: {resp.data[:300]}"
    body = json.loads(resp.data)
    assert "task_id" in body
    assert body["frames_count"] >= 1


def test_fetch_url_wrong_content_type(client):
    """非 image/* Content-Type 应拒绝。"""
    mock_resp = MagicMock()
    mock_resp.headers = {"Content-Type": "text/html", "Content-Length": "100"}
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        resp = client.post("/api/fetch-url",
                           data=json.dumps({"url": "https://example.com/page"}),
                           content_type="application/json")

    assert resp.status_code == 400
