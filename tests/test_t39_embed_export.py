"""T39 — 一行嵌入（embed）导出的三端点契约回归。

覆盖本轮后端接线（前端模板/JS 不在本文件范围）：
- POST /api/generate format=embed → {download_url, file_size, format, snippet}，
  snippet 为自包含形态（<termify-play src="data:application/json…），
  产物名带 _embed 段，不与整页 HTML 互相覆盖
- GET /api/gallery/embed-frames/<id> → frames_payload 纯文本 + 长缓存头
- GET /api/gallery/download/<id>?format=embed → 引用形态片段（指向 B 端点，
  不含 data URI）；&as=json 回 {snippet, download_url}
"""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image
from tests.gallery_marks import requires_gallery

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # pragma: no cover
    _HAVE_FLASK = False

pytestmark = pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t39.db"))
    monkeypatch.delenv("TERMIFY_ADMIN_PWD", raising=False)

    from termify.taskstore import cache_clear_all, reset_store_for_tests

    cache_clear_all()
    reset_store_for_tests()
    import app as app_mod
    from termify import gallery as gallery_mod

    gdata = tmp_path / "gallery_data"
    gdata.mkdir()
    monkeypatch.setattr(app_mod, "GALLERY_DATA_DIR", str(gdata))
    db = gallery_mod.GalleryDB(str(gdata / "termify.db"))
    db.init_db()
    monkeypatch.setattr(app_mod, "GALLERY_DB", db)
    app_mod._RL_LOG.clear()
    yield
    cache_clear_all()
    reset_store_for_tests()
    app_mod._RL_LOG.clear()


@pytest.fixture
def client():
    from app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _png(color=(20, 200, 80), size=(48, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _task_id(client) -> str:
    resp = client.post("/api/upload",
                       data={"file": (io.BytesIO(_png()), "t39.png")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)["task_id"]


def _work_id(client) -> str:
    resp = client.post(
        "/api/gallery/upload",
        data={"source": (io.BytesIO(_png()), "t39.png"), "title": "T39 作品",
              "author": "tester", "tags": ["ASCII art"], "charset": "ascii",
              "width": "40", "height": "20", "is_private": "0"},
        content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)["id"]


# ── A: /api/generate 自包含片段 ─────────────────────────────────

def test_generate_embed_returns_self_contained_snippet(client):
    task_id = _task_id(client)
    resp = client.post("/api/generate", json={
        "task_id": task_id, "charset": "ascii", "width": 40, "height": 20,
        "format": "embed",
    })
    assert resp.status_code == 200, resp.data
    body = json.loads(resp.data)

    assert body["format"] == "embed"
    assert body["download_url"] == f"/api/download/{task_id}_ascii_embed.html"
    assert body["download_url"].endswith("_embed.html")
    # 自包含：帧以 data URI 直接烤进片段，不依赖服务器
    assert '<termify-play src="data:application/json;charset=utf-8,' in body["snippet"]
    assert "termify-play" in body["snippet"]
    assert body["file_size"].endswith(("B", "KB"))


def test_generate_embed_artifact_is_downloadable(client):
    task_id = _task_id(client)
    body = json.loads(client.post("/api/generate", json={
        "task_id": task_id, "charset": "ascii", "width": 40, "height": 20,
        "format": "embed",
    }).data)
    resp = client.get(body["download_url"])
    assert resp.status_code == 200, resp.data
    assert b'<termify-play src="data:application/json' in resp.data


def test_generate_embed_color_tag_matches_existing_rule(client):
    """产物名配色段沿用 _src/_src256（与 python/html 产物同一套规则）。"""
    task_id = _task_id(client)
    body = json.loads(client.post("/api/generate", json={
        "task_id": task_id, "charset": "ascii", "width": 40, "height": 20,
        "format": "embed", "color": "source",
    }).data)
    assert body["download_url"].endswith(f"{task_id}_ascii_src_embed.html")


# ── B: 只读帧端点 ───────────────────────────────────────────────

@requires_gallery
def test_embed_frames_payload_and_cache_header(client):
    wid = _work_id(client)
    resp = client.get(f"/api/gallery/embed-frames/{wid}"
                      "?charset=ascii&width=40&height=20")
    assert resp.status_code == 200, resp.data
    assert resp.mimetype == "application/json"
    assert resp.headers["Cache-Control"] == "public, max-age=86400"

    d = json.loads(resp.data)
    assert d["w"] == 40 and d["h"] == 20
    assert d["charset"] == "ascii"
    assert d["interval"] > 0
    assert d["frames"]
    # 帧必须是纯文本：ANSI 转义在 README / CMS 里会被吞掉或变形
    assert "\x1b" not in "".join(d["frames"])


@requires_gallery
def test_embed_frames_unknown_work_is_404(client):
    resp = client.get("/api/gallery/embed-frames/does-not-exist")
    assert resp.status_code == 404


@requires_gallery
def test_embed_frames_rejects_bad_charset(client):
    wid = _work_id(client)
    resp = client.get(f"/api/gallery/embed-frames/{wid}?charset=nope")
    assert resp.status_code == 400


# ── C: 引用形态下载 ─────────────────────────────────────────────

@requires_gallery
def test_download_embed_is_reference_not_data_uri(client):
    wid = _work_id(client)
    resp = client.get(f"/api/gallery/download/{wid}?format=embed"
                      "&charset=ascii&width=40&height=20")
    assert resp.status_code == 200, resp.data
    body = resp.data.decode("utf-8")

    assert "/api/gallery/embed-frames/" in body
    assert "data:application/json" not in body
    assert "termify-play" in body
    # src_url 必须带上当前这组查询串，否则宿主页取到的不是用户看到的那版
    assert "charset=ascii" in body and "width=40" in body
    assert f"gallery_{wid}_ascii_embed.html" in resp.headers["Content-Disposition"]


@requires_gallery
def test_download_embed_as_json(client):
    wid = _work_id(client)
    resp = client.get(f"/api/gallery/download/{wid}?format=embed"
                      "&charset=ascii&width=40&height=20&as=json")
    assert resp.status_code == 200, resp.data
    d = json.loads(resp.data)
    assert set(d) == {"snippet", "download_url"}
    assert "/api/gallery/embed-frames/" in d["snippet"]
    assert "data:application/json" not in d["snippet"]
    assert "as=json" not in d["download_url"]
    assert f"/api/gallery/download/{wid}?format=embed" in d["download_url"]


@requires_gallery
def test_download_embed_appears_in_format_error_text(client):
    """格式校验文案要列出全部可用形态（补 mp4/embed 后不能还说只有 html）。"""
    wid = _work_id(client)
    resp = client.get(f"/api/gallery/download/{wid}?format=nope")
    assert resp.status_code == 400
    msg = json.loads(resp.data)["error"]
    assert "embed" in msg and "mp4" in msg
