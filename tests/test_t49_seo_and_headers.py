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
# 模板里已升到 ?v=20261030a 的那批 URL。
ASSET_QUERY = "?v=20261030a"


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
