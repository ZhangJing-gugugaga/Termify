"""per-work admin_token 不得出现在发布响应体；cookie 路径必须继续可用。

背景：`/api/gallery/upload` 与 `/api/gallery/upload-text` 曾把
`admin_token` 直接写进 JSON 响应体，同时另发一份 httponly cookie。响应体
里的那份对前端零消费者（static/js、templates 全仓库无引用），却给任何能读
响应（XSS / DevTools / 中间层日志）的人留下了 30 天有效的管理凭据。

覆盖：
1. 两条发布路径的响应体都不含 admin_token（顶层与 work 子树，文本级 + 键级）；
2. cookie 仍照原样下发（名字 termify_admin_<id> / HttpOnly / SameSite=Lax /
   Max-Age=30 天）——这是唯一的管理凭据通道；
3. 用该 cookie 走 per-work 管理操作仍 200（删除），无 cookie / 错 token 仍 403。
"""

from __future__ import annotations

import io
import json

import pytest
from tests.gallery_marks import requires_gallery
from PIL import Image

try:
    import flask  # noqa: F401

    _HAVE_FLASK = True
except ImportError:  # pragma: no cover — 无 flask 的裸环境
    _HAVE_FLASK = False

pytestmark = pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装")

_COOKIE_ATTRS = ("HttpOnly", "SameSite=Lax", "Max-Age=2592000")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_admin_tok.db"))
    # 未设管理口令：确保 cookie 之外的鉴权路径在此测试里一律不成立。
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


def _png_bytes(w=4, h=2):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 200, 30)).save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def _publish_text(client):
    return client.post("/api/gallery/upload-text", json={
        "art": "HELLO\nWORLD", "font": "ghost", "title": "令牌回归",
        "author": "tester", "tags": ["ASCII art"], "fg": [51, 255, 51]})


def _publish_upload(client):
    return client.post("/api/gallery/upload",
                       data={"source": (io.BytesIO(_png_bytes()), "x.png"),
                             "title": "令牌回归",
                             "params": json.dumps({"charset": "ascii",
                                                   "width": 4, "height": 2})},
                       content_type="multipart/form-data")


def _admin_cookie(resp, work_id):
    found = [h for h in resp.headers.getlist("Set-Cookie")
             if h.startswith(f"termify_admin_{work_id}=")]
    assert len(found) == 1, resp.headers.getlist("Set-Cookie")
    return found[0]


def _assert_no_admin_token(resp):
    """文本级 + 键级双重断言：键重命名/嵌套都逃不掉。"""
    body = resp.get_data(as_text=True)
    assert "admin_token" not in body
    data = json.loads(resp.data)
    assert "admin_token" not in data
    assert "admin_token" not in json.dumps(data.get("work") or {}, ensure_ascii=False)
    assert "termify_admin_" not in body


# ═══ 1. 两条发布路径的响应体 ═══════════════════════════════════════════════


@requires_gallery
def test_upload_text_response_has_no_admin_token(client):
    resp = _publish_text(client)
    assert resp.status_code == 200, resp.data
    _assert_no_admin_token(resp)
    data = json.loads(resp.data)
    assert data["ok"] is True and data["id"]
    assert data["url"] == f"/v/{data['id']}"


@requires_gallery
def test_upload_response_has_no_admin_token(client):
    resp = _publish_upload(client)
    assert resp.status_code == 200, resp.data
    _assert_no_admin_token(resp)
    data = json.loads(resp.data)
    assert data["ok"] is True and data["id"]


# ═══ 2. cookie 照原样下发 ══════════════════════════════════════════════════


@requires_gallery
def test_publish_still_sets_httponly_cookie(client):
    resp = _publish_text(client)
    assert resp.status_code == 200, resp.data
    work_id = json.loads(resp.data)["id"]
    cookie = _admin_cookie(resp, work_id)
    for attr in _COOKIE_ATTRS:
        assert attr in cookie, cookie
    token = cookie.split("=", 1)[1].split(";", 1)[0]
    assert token and len(token) >= 16, cookie


# ═══ 3. cookie 仍是有效管理凭据 ════════════════════════════════════════════


@requires_gallery
def test_cookie_still_authorizes_delete(client):
    resp = _publish_text(client)
    assert resp.status_code == 200, resp.data
    work_id = json.loads(resp.data)["id"]
    client.set_cookie(f"termify_admin_{work_id}",
                      _admin_cookie(resp, work_id).split("=", 1)[1]
                      .split(";", 1)[0], domain="localhost")

    stranger = client.application.test_client()
    assert stranger.delete(f"/api/gallery/work/{work_id}").status_code == 403
    assert stranger.delete(
        f"/api/gallery/work/{work_id}",
        headers={"X-Termify-Admin": "wrong-token"}).status_code == 403

    ok = client.delete(f"/api/gallery/work/{work_id}")
    assert ok.status_code == 200, ok.data
    assert json.loads(ok.data)["ok"] is True
    assert client.get(f"/api/gallery/work/{work_id}").status_code == 404
