"""S4 — /api/upload 收 .webp（text_art.html 早已向用户声明支持，服务端却不认）。

2026-10-23 重测：白名单 VALID_EXT 只有 gif/png/jpg/jpeg，用户按页面提示传 webp
得到 400。本次只改 Python（HTML 的 accept/hint 文案不动），并确认 PIL 侧能解 webp；
解码能力缺失的部署给明确 400 文案而不是让 convert 抛异常落 500。
"""

from __future__ import annotations

import io
import os

import pytest
from PIL import Image, features

flask = pytest.importorskip("flask")

HAVE_WEBP = features.check("webp")


def _webp_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 4), (0, 0, 0)).save(buf, format="WEBP")
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


def test_template_claim_and_server_whitelist_agree():
    """页面 accept/hint 声明了 webp，服务端白名单必须同款（只改 Python 侧）。"""
    import app as app_mod
    from pathlib import Path

    html = (Path(app_mod.__file__).parent / "templates" / "text_art.html")
    text = html.read_text(encoding="utf-8")
    assert ".webp" in text
    assert ".webp" in app_mod.VALID_EXT


@pytest.mark.skipif(not HAVE_WEBP, reason="PIL 无 webp 编解码")
def test_valid_webp_upload_returns_200(client, tmp_path):
    resp = client.post("/api/upload",
                       data={"file": (io.BytesIO(_webp_bytes()), "plot.webp")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    body = resp.get_json()
    assert body["task_id"]
    assert body["original_size"] == {"width": 8, "height": 4}
    saved = [n for n in os.listdir(tmp_path / "uploads") if n.endswith(".webp")]
    assert len(saved) == 1
    assert saved[0].startswith(body["task_id"])


@pytest.mark.skipif(not HAVE_WEBP, reason="PIL 无 webp 编解码")
def test_webp_named_text_file_returns_400(client, tmp_path):
    """伪装成 .webp 的文本：400 而不是 500，且不留半成品文件。"""
    resp = client.post("/api/upload",
                       data={"file": (io.BytesIO(b"RIFF-not-an-image"), "evil.webp")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "error" in resp.get_json()
    leftovers = [n for n in os.listdir(tmp_path / "uploads") if n.endswith(".webp")]
    assert leftovers == []


def test_webp_without_pil_support_returns_400_with_clear_text(client, monkeypatch):
    """部署没编 libwebp 时：白名单照收，但给明确 400 文案而不是 500。"""
    import app as app_mod

    monkeypatch.setattr(app_mod, "_webp_supported", lambda: False)
    resp = client.post("/api/upload",
                       data={"file": (io.BytesIO(b"whatever"), "plot.webp")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "WebP" in resp.get_json()["error"]


def test_non_whitelisted_extension_still_rejected(client):
    """白名单扩展不得外溢（bmp 仍未收，见遗留问题清单）。"""
    resp = client.post("/api/upload",
                       data={"file": (io.BytesIO(b"x"), "a.bmp")},
                       content_type="multipart/form-data")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "Unsupported format"