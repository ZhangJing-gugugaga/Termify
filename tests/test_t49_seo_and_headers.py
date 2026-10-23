"""T49 — 后端/路由四条缺陷的回归锁（C-3 / D-2 / D-3 / D-4）。

只锁"响应头与路由行为"这类可被 curl 直接观测的事实，不动任何页面与样式。
"""

from __future__ import annotations

import pytest

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # pragma: no cover
    _HAVE_FLASK = False

pytestmark = pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装")

# 验收用的版本串：只要求"带 ?v= 参数"，具体值无关；用较新的一次点击穿
# 模板里已升到 ?v=20261031c 的那批 URL。
ASSET_QUERY = "?v=20261031c"


def _client():
    from app import app as flask_app
    return flask_app.test_client()


# --- 1. C-3 静态资源 Cache-Control 只允许一条 --------------------------------

def test_versioned_static_has_exactly_one_immutable_cache_control():
    r = _client().get("/static/css/app.css" + ASSET_QUERY)
    assert r.status_code == 200
    # 关键断言用 getlist：headers[...] 会把重复头用逗号拼起来，掩盖"两条"。
    assert r.headers.getlist("Cache-Control") == [
        "public, max-age=31536000, immutable"]


def test_plain_static_has_exactly_one_short_cache_control():
    r = _client().get("/static/css/app.css")
    assert r.status_code == 200
    cc = r.headers.getlist("Cache-Control")
    assert cc == ["public, max-age=86400"]
    assert "immutable" not in cc[0]


def test_static_304_keeps_single_versioned_cache_control():
    """304 复用同一套头：否则浏览器拿 304 后会用回自己缓存的旧策略。"""
    c = _client()
    r = c.get("/static/js/app.js" + ASSET_QUERY)
    etag = r.headers.get("ETag")
    assert etag
    r304 = c.get("/static/js/app.js" + ASSET_QUERY,
                 headers={"If-None-Match": etag})
    assert r304.status_code == 304
    assert r304.headers.getlist("Cache-Control") == [
        "public, max-age=31536000, immutable"]


def test_preexisting_cache_control_headers_are_collapsed_to_one():
    """即使上游已下发多条 Cache-Control（werkzeug 的 SEND_FILE_MAX_AGE_DEFAULT
    那条就是这种来源），本 hook 也必须收敛成唯一一条权威值。"""
    import app as app_mod
    from flask import Response

    with app_mod.app.test_request_context("/static/css/app.css" + ASSET_QUERY):
        resp = Response("x", mimetype="text/css")
        resp.headers.add("Cache-Control", "public, max-age=86400")
        resp.headers.add("Cache-Control", "no-cache")
        resp.headers.add("Expires", "Sat, 01 Jan 2000 00:00:00 GMT")
        out = app_mod._static_cache_policy(resp)
    assert out.headers.getlist("Cache-Control") == [
        "public, max-age=31536000, immutable"]
    # 老的 Expires 与策略矛盾，必须摘掉，只留 Cache-Control 说了算
    assert "Expires" not in out.headers


def test_non_static_routes_keep_their_own_cache_semantics():
    """static 的收敛不得外溢：HTML 仍是 no-cache，且只一条。"""
    r = _client().get("/")
    assert r.status_code == 200
    assert r.headers.getlist("Cache-Control") == ["no-cache"]

def test_large_response_compression_gate_still_works():
    """>1MiB 压缩闸门不因本次改动失效：超阈值响应仍以 identity 直发。"""
    import app as app_mod
    from flask import Response

    with app_mod.app.test_request_context("/api/preview/abc"):
        resp = Response(b"x" * (app_mod.COMPRESS_MAX_SIZE + 1),
                        mimetype="application/json")
        app_mod._skip_compress_large(resp)
        assert resp.headers.get("Content-Encoding") == "identity"
        # 非 static 端点：缓存策略 hook 不碰它
        assert app_mod._static_cache_policy(resp) is resp
        assert "Cache-Control" not in resp.headers

# --- 2. D-2 robots.txt / sitemap.xml ----------------------------------------

@pytest.fixture
def isolated_gallery(monkeypatch, tmp_path):
    """把画廊库指到 tmp_path，避免测试读写仓库里的 data/（已 gitignore 的本地数据）。"""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t49.db"))

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
    yield db
    cache_clear_all()
    reset_store_for_tests()


def _work(work_id, is_private=0, created_at="2026-01-02T03:04:05"):
    import json as _json
    return {
        "id": work_id,
        "title": "t49 " + work_id,
        "description": "",
        "tags": _json.dumps([]),
        "author": "tester",
        "source_path": "/nonexistent/source.png",
        "thumbnail_path": "/nonexistent/thumb.png",
        "og_path": "/nonexistent/og.png",
        "params_json": _json.dumps({"kind": "image"}),
        "is_private": is_private,
        "admin_token": "tok-" + work_id,
        "created_at": created_at,
        "ip": "203.0.113.10",
    }


def _sitemap_locs(client=None):
    """把 sitemap 走一遍真正的 XML 解析，返回 <loc> 列表。"""
    import xml.etree.ElementTree as ET

    r = (_client() if client is None else client).get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("application/xml")
    root = ET.fromstring(r.get_data(as_text=True))
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    return [el.text for el in root.findall("s:url/s:loc", ns)]


def test_robots_txt_allows_all_and_points_at_sitemap():
    r = _client().get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/plain")
    body = r.get_data(as_text=True)
    assert "User-agent: *" in body
    assert "Allow: /" in body
    sitemap_lines = [ln for ln in body.splitlines() if ln.startswith("Sitemap: ")]
    assert len(sitemap_lines) == 1
    assert sitemap_lines[0].endswith("/sitemap.xml")
    assert sitemap_lines[0].startswith("Sitemap: http")


def test_sitemap_xml_is_valid_and_lists_the_three_pages(isolated_gallery):
    locs = _sitemap_locs()
    assert len(locs) >= 3
    base = locs[0].rsplit("/", 1)[0]
    for path in ("/", "/gallery", "/text-art"):
        assert base + path in locs, path
    # 绝对 URL：不能是 /xxx 这种站内相对路径
    assert all(loc.startswith("http") for loc in locs)


def test_sitemap_lists_published_works_with_lastmod(isolated_gallery):
    isolated_gallery.insert_work(_work("t49public001"))
    isolated_gallery.insert_work(_work("t49private01", is_private=1))
    r = _client().get("/sitemap.xml")
    body = r.get_data(as_text=True)
    tree_locs = _sitemap_locs()
    assert any(loc.endswith("/v/t49public001") for loc in tree_locs)
    # 私密作品不进 sitemap
    assert "t49private01" not in body
    # lastmod 用作品的 created_at
    assert "<lastmod>2026-01-02T03:04:05</lastmod>" in body


def test_sitemap_only_reads_the_gallery_db(isolated_gallery):
    isolated_gallery.insert_work(_work("t49ro0000001"))
    _before, total_before = isolated_gallery.list_works(page=1, limit=1000)
    _sitemap_locs()
    _after, total_after = isolated_gallery.list_works(page=1, limit=1000)
    assert (total_before, total_after) == (1, 1)

# --- 3. D-3 尾斜杠归一 ------------------------------------------------------

@pytest.mark.parametrize("path,target", [
    ("/gallery/", "/gallery"),
    ("/text-art/", "/text-art"),
])
def test_trailing_slash_redirects_301(path, target):
    r = _client().get(path)
    assert r.status_code == 301
    assert r.headers["Location"] == target
    h = _client().head(path)
    assert h.status_code == 301
    assert h.headers["Location"] == target


def test_trailing_slash_targets_serve_200_without_redirect():
    """目标路径本身 200 且不再 301 —— 尾斜杠规则不会反噬正常路由。"""
    c = _client()
    for path in ("/gallery", "/text-art"):
        r = c.get(path)
        assert r.status_code == 200
        assert "Location" not in r.headers


def test_trailing_slash_redirect_is_not_a_loop():
    """跟随一次跳转后必须落到 200，且终态路径已去掉尾斜杠。"""
    r = _client().get("/gallery/", follow_redirects=True)
    assert r.status_code == 200
    assert r.request.path == "/gallery"

# --- 4. D-4 /index.html 归位 ------------------------------------------------

def test_index_html_redirects_301_to_root():
    r = _client().get("/index.html")
    assert r.status_code == 301
    assert r.headers["Location"] == "/"


def test_index_html_head_also_redirects():
    h = _client().head("/index.html")
    assert h.status_code == 301
    assert h.headers["Location"] == "/"


def test_index_html_follows_to_the_real_homepage():
    r = _client().get("/index.html", follow_redirects=True)
    assert r.status_code == 200
    assert r.request.path == "/"
