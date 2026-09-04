"""T33 — 文字艺术字（FIGlet 直转 + LLM 双模式 + 文字作品入库）。

覆盖：
- textart 单元：精选字体/FIGlet 渲染/非 ASCII 过滤（lddgo 语义）/直接创作归一化
- API：/api/text/fonts、/api/text/convert、/api/llm/config（key 不回传/管理员门禁）
- /api/text/ai：未配置 400 need_config；mock OpenAI 兼容上游跑通 params/direct 双模式
- /api/gallery/upload-text：文字作品入库 → /v/ 页回放（frames 白名单）→ 私有直链鉴权
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import threading

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
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t33.db"))
    monkeypatch.setenv("TERMIFY_ADMIN_PWD", "t33-admin")

    from termify.taskstore import cache_clear_all, reset_store_for_tests

    cache_clear_all()
    reset_store_for_tests()
    import app as app_mod
    from termify import gallery as gallery_mod

    # 画廊 DB / 数据目录全部指到 tmp（GALLERY_DB 在导入期绑定，需一并替换）
    gdata = tmp_path / "gallery_data"
    gdata.mkdir()
    monkeypatch.setattr(app_mod, "GALLERY_DATA_DIR", str(gdata))
    db = gallery_mod.GalleryDB(str(gdata / "termify.db"))
    db.init_db()
    monkeypatch.setattr(app_mod, "GALLERY_DB", db)

    # LLM 配置同样隔离（save_config 写 data_dir/llm_config.json）
    from termify import llm as llm_mod

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


# ── textart 单元 ─────────────────────────────────────────────

def test_curated_fonts_available():
    from termify import textart

    fonts = textart.curated_fonts()
    assert len(fonts) == len(textart.CURATED_FONTS)
    slugs = {f["slug"] for f in fonts}
    assert "standard" in slugs and "ansi_shadow" in slugs
    for f in fonts:
        assert f["name"] and f["slug"]


def test_render_figlet_and_cjk_filter():
    from termify import textart

    art = textart.render_figlet("hello", "ansi_shadow")
    assert "╗" in art  # ansi_shadow 的标志性块字符
    cols, rows = textart.art_dims(art)
    assert cols > 10 and rows >= 5
    # lddgo 语义：非 ASCII 被忽略而不是报错
    art2 = textart.render_figlet("你好hello", "standard")
    assert art2 == textart.render_figlet("hello", "standard")
    with pytest.raises(textart.TextArtError):
        textart.render_figlet("你好")


def test_render_figlet_invalid_font_falls_back():
    from termify import textart

    art = textart.render_figlet("hi", "no_such_font")
    assert art.strip()
    assert textart.known_font("no_such_font") is False


def test_render_figlet_too_long():
    from termify import textart

    with pytest.raises(textart.TextArtError):
        textart.render_figlet("a" * 100, "standard")


def test_normalize_direct_art_fence_and_dedent():
    from termify import textart

    raw = "Here you go:\n```\n    @..@\n  (----)\n ( >__< )\n   ^^~~^^\n```"
    art = textart.normalize_direct_art(raw)
    assert "```" not in art and "Here" not in art
    # 公共缩进（1 格）被等量去除，字符间相对对齐保持不变
    lines = art.splitlines()
    assert lines[0].lstrip().startswith("@..@")
    assert lines[1][0] == " "  # (----) 原本缩进 2 格，去掉公共 1 格后仍比最浅行深 1 格


def test_normalize_direct_art_strips_control_and_caps():
    from termify import textart

    art = textart.normalize_direct_art("ab\x07cd\tef")
    assert art == "abcd    ef"
    # P2 起 direct 归一化不再因超尺寸拒绝 —— auto_fit_art 负责降级
    wide = textart.normalize_direct_art("x" * 250)
    assert len(wide) == 250


def test_validate_stored_art():
    from termify import textart

    # 前导空格保留（字符画对齐依赖它），行尾空格剥离
    assert textart.validate_stored_art("  hi \n there ") == "  hi\n there"
    with pytest.raises(textart.TextArtError):
        textart.validate_stored_art("")
    with pytest.raises(textart.TextArtError):
        textart.validate_stored_art(None)


def test_render_art_png(tmp_path):
    from termify import textart

    dst = tmp_path / "art.png"
    w = textart.render_art_png("HELLO\nWORLD", str(dst))
    assert dst.is_file() and dst.stat().st_size > 100
    from PIL import Image

    with Image.open(dst) as im:
        assert im.size[0] >= w and im.size[1] > 0


# ── API：fonts / convert ─────────────────────────────────────

def test_fonts_endpoint(client):
    resp = client.get("/api/text/fonts")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["ok"] and len(data["fonts"]) >= 20
    assert {"name", "slug"} <= set(data["fonts"][0].keys())


def test_convert_endpoint(client):
    resp = client.post("/api/text/convert", json={"text": "hello", "font": "ansi_shadow"})
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["ok"] and data["font"] == "ansi_shadow"
    assert data["cols"] > 10 and data["rows"] >= 5
    assert "\n" in data["art"]


def test_convert_endpoint_errors(client):
    resp = client.post("/api/text/convert", json={"text": "你好世界"})
    assert resp.status_code == 400
    resp = client.post("/api/text/convert", json={"text": "a" * 100})
    assert resp.status_code == 400
    resp = client.post("/api/text/convert", json={"text": "hi", "font": "ghost"})
    assert resp.status_code == 200
    assert json.loads(resp.data)["font"] == "ghost"


# ── API：llm config ──────────────────────────────────────────

def test_llm_config_key_never_returned(client):
    # 设置 TERMIFY_ADMIN_PWD 的环境（autouse fixture）→ 保存需管理员
    resp = client.post("/api/llm/config", json={
        "base_url": "http://127.0.0.1:1/v1", "model": "mock",
        "api_key": "sk-super-secret-1", "admin_pwd": "t33-admin"})
    assert resp.status_code == 200, resp.data
    summary = json.loads(resp.data)
    assert summary["has_key"] is True and summary["configured"] is True
    assert "sk-super-secret-1" not in resp.get_data(as_text=True)

    got = client.get("/api/llm/config")
    body = got.get_data(as_text=True)
    assert got.status_code == 200
    assert "sk-super-secret-1" not in body
    assert json.loads(body)["requires_admin"] is True


def test_llm_config_requires_admin_when_pwd_set(client):
    resp = client.post("/api/llm/config", json={
        "base_url": "http://127.0.0.1:1/v1", "model": "m"})
    assert resp.status_code == 403
    ok = client.post("/api/llm/config", json={
        "base_url": "http://127.0.0.1:1/v1", "model": "m",
        "admin_pwd": "t33-admin"})
    assert ok.status_code == 200


def test_llm_config_validation_and_key_keep(client):
    resp = client.post("/api/llm/config", json={
        "base_url": "http://127.0.0.1:1/v1", "model": "m1",
        "api_key": "sk-keep-1", "admin_pwd": "t33-admin"})
    assert resp.status_code == 200
    # 不带 api_key 字段 → 保留旧 key（用 headers 走管理员门禁）
    resp2 = client.post("/api/llm/config",
                        headers={"X-Termify-Admin": "t33-admin"},
                        json={"base_url": "http://127.0.0.1:2/v1", "model": "m2"})
    assert resp2.status_code == 200
    assert json.loads(resp2.data)["has_key"] is True
    # 显式空串 → 清除
    resp3 = client.post("/api/llm/config",
                        headers={"X-Termify-Admin": "t33-admin"},
                        json={"base_url": "http://127.0.0.1:2/v1",
                              "model": "m2", "api_key": ""})
    assert resp3.status_code == 200
    assert json.loads(resp3.data)["has_key"] is False
    # 非法 URL
    bad = client.post("/api/llm/config", headers={"X-Termify-Admin": "t33-admin"},
                      json={"base_url": "ftp://x", "model": "m"})
    assert bad.status_code == 400


# ── mock OpenAI 兼容上游 ─────────────────────────────────────

class _MockState:
    reply = ""
    auth = "Bearer sk-t33-1"


class _MockOpenAIHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.headers.get("Authorization") != _MockState.auth:
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps({
            "choices": [{"message": {"content": _MockState.reply}}]
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D102
        pass


@pytest.fixture
def mock_llm(monkeypatch, client):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0),
                                             _MockOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    resp = client.post("/api/llm/config", json={
        "base_url": base, "model": "mock-1",
        "api_key": "sk-t33-1", "admin_pwd": "t33-admin"})
    assert resp.status_code == 200, resp.data
    yield server
    server.shutdown()
    server.server_close()


def test_text_ai_need_config(client):
    resp = client.post("/api/text/ai", json={"prompt": "火焰 HELLO", "mode": "params"})
    assert resp.status_code == 400
    data = json.loads(resp.data)
    assert data.get("need_config") is True


def test_text_ai_params_mode(client, mock_llm):
    _MockState.reply = json.dumps({"text": "HELLO", "font": "fire_font-s"})
    resp = client.post("/api/text/ai", json={"prompt": "火焰感的 HELLO", "mode": "params"})
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data)
    assert data["mode"] == "params" and data["font"] == "fire_font-s"
    assert data["text"] == "HELLO"
    assert data["rows"] >= 4 and len(data["art"]) > 20


def test_text_ai_params_mode_bad_json(client, mock_llm):
    _MockState.reply = "抱歉，我不会。"
    resp = client.post("/api/text/ai", json={"prompt": "x", "mode": "params"})
    assert resp.status_code == 400
    assert "error" in json.loads(resp.data)


def test_text_ai_direct_mode(client, mock_llm):
    _MockState.reply = "```\n   /\\_/\\\n  ( o.o )\n   > ^ <\n```"
    resp = client.post("/api/text/ai", json={"prompt": "画一只猫", "mode": "direct"})
    assert resp.status_code == 200, resp.data
    data = json.loads(resp.data)
    assert data["mode"] == "direct"
    assert "/\\_/\\" in data["art"]
    assert "```" not in data["art"]


def test_text_ai_upstream_401(client, mock_llm):
    _MockState.auth = "Bearer wrong"
    try:
        resp = client.post("/api/text/ai", json={"prompt": "x", "mode": "direct"})
        assert resp.status_code == 400
        assert "API key" in json.loads(resp.data)["error"]
    finally:
        _MockState.auth = "Bearer sk-t33-1"


# ── 文字作品入库 + /v/ 回放 ──────────────────────────────────

def _publish_text(client, *, art="HELLO\nWORLD", font="ghost", private="0"):
    return client.post("/api/gallery/upload-text", json={
        "art": art, "font": font, "title": "T33 文字作品",
        "author": "tester", "tags": ["ASCII art"], "is_private": private,
        "fg": [51, 255, 51]})


def test_upload_text_and_view_page(client):
    resp = _publish_text(client)
    assert resp.status_code == 200, resp.data
    body = json.loads(resp.data)
    work_id = body["id"]
    assert body["work"]["params"]["kind"] == "text"
    assert body["work"]["params"]["frames"] == ["HELLO\nWORLD"]

    # /v/ 页：frames/font 进白名单，frames_dir 绝不出现
    page = client.get(f"/v/{work_id}")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "frames" in text and "ghost" in text
    assert "frames_dir" not in text

    # 列表/详情公开 dict
    detail = client.get(f"/api/gallery/work/{work_id}")
    assert detail.status_code == 200
    params = json.loads(detail.data)["params"]
    assert params["kind"] == "text" and params["font"] == "ghost"

    # source PNG 直链可访问（公开作品）
    src = client.get(f"/gallery/file/{work_id}/source")
    assert src.status_code == 200
    assert src.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_upload_text_private_auth(client):
    resp = _publish_text(client, private="1")
    assert resp.status_code == 200
    work_id = json.loads(resp.data)["id"]
    stranger = client.__class__(client.application)
    assert stranger.get(f"/gallery/file/{work_id}/source").status_code == 403
    assert client.get(f"/gallery/file/{work_id}/source").status_code == 200


def test_upload_text_validation_and_rate(client):
    resp = client.post("/api/gallery/upload-text", json={"art": ""})
    assert resp.status_code == 400
    resp = client.post("/api/gallery/upload-text", json={"art": None})
    assert resp.status_code == 400
    # 超尺寸艺术字拒绝
    big = "\n".join("x" * 150 for _ in range(130))
    resp = client.post("/api/gallery/upload-text", json={"art": big})
    assert resp.status_code == 400
