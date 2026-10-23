"""S2 — /api/* 的 5xx 必须是 JSON（此前回 Flask 默认 HTML，前端 r.json() 直接抛错）。

回归方式：把一个已注册视图换成必抛异常的桩。不走 @app.route 新增路由——
Flask 在第一个请求之后禁止注册新路由（_got_first_request 断言）。

PROPAGATE_EXCEPTIONS 显式置 False：生产是 DEBUG=False + TESTING 未设，等价于
False；测试客户端默认继承 TESTING=True 会把异常直接抛给调用方，反而不经过
handle_exception -> errorhandler(500) 这条真实链路。
"""

from __future__ import annotations

import logging

import pytest

flask = pytest.importorskip("flask")


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    from app import app
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    monkeypatch.setitem(app.config, "TESTING", True)
    return app.test_client()


def _boom(*_args, **_kwargs):
    raise RuntimeError("kaboom: internal detail C:\\srv\\termify\\app.py")


def test_api_500_returns_json_not_html(client, monkeypatch, caplog):
    import app as app_mod

    monkeypatch.setitem(app_mod.app.view_functions, "text_fonts", _boom)
    with caplog.at_level(logging.ERROR):
        resp = client.get("/api/text/fonts")

    assert resp.status_code == 500
    assert resp.headers["Content-Type"].startswith("application/json")
    body = resp.get_json()
    assert body["status"] == 500
    assert body["error"]
    # 脱敏：不回显 traceback、异常原文、服务器路径
    raw = resp.get_data(as_text=True)
    for leak in ("Traceback", "kaboom", "srv", "RuntimeError"):
        assert leak not in raw
    # 日志照旧：异常必须留在日志里（不吞异常）
    assert any("kaboom" in rec.getMessage() or rec.exc_info
               for rec in caplog.records)


def test_non_api_500_keeps_the_default_html_page(client, monkeypatch):
    import app as app_mod

    monkeypatch.setitem(app_mod.app.view_functions, "index", _boom)
    resp = client.get("/")
    assert resp.status_code == 500
    assert resp.headers["Content-Type"].startswith("text/html")
    assert b"Internal Server Error" in resp.data


def test_api_404_still_json_and_untouched(client):
    """500 收口不得影响既有 404 JSON 分支。"""
    resp = client.get("/api/nope-not-here")
    assert resp.status_code == 404
    assert resp.headers["Content-Type"].startswith("application/json")
