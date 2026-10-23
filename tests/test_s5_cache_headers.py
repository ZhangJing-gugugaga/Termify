"""S5 — 缓存头一致性：/api/* 兜底 no-store，robots/sitemap 给一小时公共缓存。

2026-10-23 重测：/api/* 的 JSON 响应完全没有 Cache-Control，浏览器按启发式缓存
把旧列表/旧状态留住；robots.txt 与 sitemap.xml 同样裸奔。
"""

from __future__ import annotations

import pytest

flask = pytest.importorskip("flask")


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    import app as app_mod
    app_mod._RL_LOG.clear()
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


# --- 1. /api/* 兜底 no-store -------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("get", "/api/text/fonts"),
    ("get", "/api/cjk/ttf/fonts"),
    ("post", "/api/upload"),          # 400 JSON 也要 no-store
    ("get", "/api/fetch-url"),        # 缺 url 的 400 JSON
    ("get", "/api/definitely-not-a-route"),  # 404 JSON
])
def test_api_responses_are_no_store(client, method, path):
    resp = getattr(client, method)(path)
    assert resp.headers.getlist("Cache-Control") == ["no-store"]


def test_api_no_store_hook_keeps_explicit_cache_control():
    """显式缓存头（source-frames 的 60s / 单帧 immutable）不许被兜底覆盖。"""
    from flask import jsonify, make_response
    from app import _api_cache_no_store, app as flask_app

    with flask_app.test_request_context("/api/gallery/source-frames/x/manifest"):
        resp = make_response(jsonify({}))
        resp.headers["Cache-Control"] = "public, max-age=60"
        out = _api_cache_no_store(resp)
    assert out.headers.getlist("Cache-Control") == ["public, max-age=60"]


def test_api_hook_ignores_non_api_paths():
    """非 /api/ 路径不归这条策略管（HTML 的 no-cache、静态的 max-age 各有其主）。"""
    from flask import make_response
    from app import _api_cache_no_store, app as flask_app

    with flask_app.test_request_context("/gallery"):
        out = _api_cache_no_store(make_response("ok"))
    assert "Cache-Control" not in out.headers


# --- 2. 静态资源策略不变 -----------------------------------------------------

def test_static_cache_headers_unchanged(client):
    versioned = client.get("/static/css/app.css?v=1")
    assert versioned.headers.getlist("Cache-Control") == [
        "public, max-age=31536000, immutable"]
    plain = client.get("/static/css/app.css")
    assert plain.headers.getlist("Cache-Control") == ["public, max-age=86400"]


def test_html_page_is_not_no_store(client):
    resp = client.get("/")
    assert "no-store" not in (resp.headers.get("Cache-Control") or "")


# --- 3. robots.txt / sitemap.xml --------------------------------------------

@pytest.mark.parametrize("path", ["/robots.txt", "/sitemap.xml"])
def test_crawler_files_are_publicly_cacheable(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert resp.headers.getlist("Cache-Control") == ["public, max-age=3600"]