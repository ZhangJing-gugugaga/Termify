"""T36 — 汉字活字引擎 API：/api/cjk/styles 与 /api/cjk/render。

render 正常路径 mock 掉 render_cjk_text（LLM/缓存层在模块单测覆盖）；
限流 / 未配置 LLM 等纯路由逻辑用 Flask test client 真跑。
"""

from __future__ import annotations

import importlib.util
import os

import pytest

pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("flask"),
    reason="flask 未安装",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_cjk.db"))

    from termify.taskstore import cache_clear_all, reset_store_for_tests

    cache_clear_all()
    reset_store_for_tests()
    import app as app_mod
    from termify import gallery as gallery_mod
    from termify import llm as llm_mod

    gdata = tmp_path / "gallery_data"
    gdata.mkdir()
    monkeypatch.setattr(app_mod, "GALLERY_DATA_DIR", str(gdata))
    db = gallery_mod.GalleryDB(str(gdata / "termify.db"))
    db.init_db()
    monkeypatch.setattr(app_mod, "GALLERY_DB", db)
    # LLM 配置隔离：默认清空（未配置状态）
    if os.path.exists(llm_mod.config_path(str(gdata))):
        os.remove(llm_mod.config_path(str(gdata)))

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


@pytest.fixture
def configured_llm(monkeypatch):
    import app as app_mod
    from termify import llm as llm_mod

    llm_mod.save_config(app_mod.GALLERY_DATA_DIR,
                        base_url="https://fake.example/v4",
                        model="fake-model", api_key="k")


def test_cjk_styles_lists_three_styles(client):
    resp = client.get("/api/cjk/styles")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    styles = data["styles"]
    assert len(styles) == 3
    assert {s["slug"] for s in styles} == {"pixel", "brush", "outline"}
    for s in styles:
        assert s["name"] and s["height"] > 0 and s["width"] > 0
        assert s["width"] % 2 == 0


def test_render_requires_configured_llm(client):
    resp = client.post("/api/cjk/render",
                       json={"text": "你好", "style": "pixel"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["need_config"] is True
    assert "自部署" in data["error"]


def test_render_rejects_unknown_style(client, configured_llm):
    resp = client.post("/api/cjk/render",
                       json={"text": "你好", "style": "nope"})
    assert resp.status_code == 400


def test_render_rejects_empty_text(client, configured_llm):
    resp = client.post("/api/cjk/render",
                       json={"text": "!!!", "style": "pixel"})
    assert resp.status_code == 400


def test_render_success_path(client, configured_llm, monkeypatch):
    import app as app_mod

    art = "  ----  \n  |  |  \n ------ "
    monkeypatch.setattr(
        app_mod._cjk_render_mod, "render_cjk_text",
        lambda text, style, cfg, llm_mod, **kw: {
            "art": art, "missing": ["好"], "style": style,
            "cols": 8, "rows": 3})
    resp = client.post("/api/cjk/render",
                       json={"text": "你好", "style": "brush"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["art"] == art
    assert data["missing"] == ["好"]
    assert data["style"] == "brush"
    assert data["cols"] == 8 and data["rows"] == 3


def test_render_rate_limited(client, configured_llm, monkeypatch):
    import app as app_mod

    monkeypatch.setattr(
        app_mod._cjk_render_mod, "render_cjk_text",
        lambda text, style, cfg, llm_mod, **kw: {
            "art": "x", "missing": [], "style": style,
            "cols": 1, "rows": 1})
    for _ in range(6):  # per_minute=6
        resp = client.post("/api/cjk/render",
                           json={"text": "你", "style": "pixel"})
        assert resp.status_code == 200
    resp = client.post("/api/cjk/render",
                       json={"text": "你", "style": "pixel"})
    assert resp.status_code == 429
