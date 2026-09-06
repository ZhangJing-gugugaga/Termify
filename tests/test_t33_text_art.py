"""T33 — 字符艺术（FIGlet 直转 + 中文点阵 + 字符作品入库）。

覆盖：
- textart 单元：精选字体/FIGlet 渲染/非 ASCII 过滤（lddgo 语义）/入库校验
- API：/api/text/fonts、/api/text/convert（含 CJK 自动分流）
- /api/gallery/upload-text：文字作品入库 → /v/ 页回放（frames 白名单）→ 私有直链鉴权
"""

from __future__ import annotations

import importlib.util
import json
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
    # T37 起中文输入不再报错——自动分流到 TTF 点阵路径（纯本地）
    resp = client.post("/api/text/convert", json={"text": "你好世界"})
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["ok"] and data["mode"] == "cjk" and data["font"] in (
        "songti", "heiti", "kaiti")
    resp = client.post("/api/text/convert", json={"text": "a" * 100})
    assert resp.status_code == 400
    resp = client.post("/api/text/convert", json={"text": "hi", "font": "ghost"})
    assert resp.status_code == 200
    assert json.loads(resp.data)["font"] == "ghost"


# ── 下载响应头：中文文件名（RFC 5987）────────────────────────
# 回归：中文 name 曾直接放进 filename=，werkzeug 发响应头时
# UnicodeEncodeError 崩掉整个响应——浏览器表现为下载失效。

def test_export_png_chinese_name_header(client):
    import re

    resp = client.post("/api/text/export-png",
                       json={"art": "HELLO\nWORLD", "name": "文字艺术"})
    assert resp.status_code == 200
    assert resp.data[:4] == b"\x89PNG"
    cd = resp.headers["Content-Disposition"]
    cd.encode("latin-1")  # 头必须 latin-1 可编码
    assert "filename*=UTF-8''" in cd
    ascii_part = re.search(r'filename="([^"]+)"', cd).group(1)
    assert all(ord(c) < 128 for c in ascii_part), ascii_part


def test_export_html_chinese_name_header(client):
    resp = client.post("/api/text/export-html",
                       json={"art": "HELLO\nWORLD", "name": "字符艺术"})
    assert resp.status_code == 200
    assert b"<pre>" in resp.data
    cd = resp.headers["Content-Disposition"]
    cd.encode("latin-1")
    assert "filename*=UTF-8''" in cd


def test_export_ascii_name_kept(client):
    resp = client.post("/api/text/export-png",
                       json={"art": "HELLO", "name": "my_art-01"})
    assert resp.status_code == 200
    assert 'filename="my_art-01.png"' in resp.headers["Content-Disposition"]


def test_imgascii_endpoint_png(client):
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (32, 16), (200, 60, 40)).save(buf, format="PNG")
    buf.seek(0)
    resp = client.post("/api/text/imgascii", data={
        "file": (buf, "测试图.png"),
        "palette": "green", "width": "40", "height": "20"},
        content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data
    data = resp.get_json()
    assert data["ok"] is True and data["mode"] == "mono"
    assert data["art"].strip() and data["rows"] > 0 and data["cols"] > 0


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


# ── 终端命令导出：主题着色 + 原色直通 ─────────────────────────
# 回归：终端命令曾直接 base64 原始 art（无 ANSI）——粘贴到终端全白。

def _cmd_payload(cmd: str) -> str:
    import base64
    inner = cmd.split("b64decode('")[1].split("')")[0]
    return base64.b64decode(inner).decode("utf-8")


def test_render_terminal_command_theme_colors():
    from termify import textart

    cmd = textart.render_terminal_command("HI\nYO", "amber")
    payload = _cmd_payload(cmd)
    assert "38;2;255;176;0" in payload  # amber 主题 RGB


def test_render_terminal_command_source_art_passthrough():
    from termify import textart

    src = "\x1b[38;2;9;8;7mX\x1b[0m"
    payload = _cmd_payload(textart.render_terminal_command(src, "green"))
    assert payload == src  # 原色 art 原样嵌入，不被主题覆写


def test_render_terminal_command_no_theme_plain():
    from termify import textart

    assert _cmd_payload(textart.render_terminal_command("HI\nYO")) == "HI\nYO"


def test_terminal_command_endpoint(client):
    resp = client.post("/api/text/terminal-command",
                       json={"art": "HI\nWORLD", "theme": "cyan"})
    assert resp.status_code == 200, resp.data
    cmd = json.loads(resp.data)["cmd"]
    assert cmd.startswith("python -c")
    assert "38;2;0;212;255" in _cmd_payload(cmd)  # cyan 主题 RGB


def test_terminal_command_endpoint_source_art(client):
    art = "\x1b[38;2;9;8;7mX\x1b[0m"
    resp = client.post("/api/text/terminal-command",
                       json={"art": art, "theme": "green"})
    assert resp.status_code == 200
    assert _cmd_payload(json.loads(resp.data)["cmd"]) == art


# ── 字符高度：上限 64 + 超限自动收缩提示依据 ────────────────────

def test_convert_cjk_height_upper_limit(client):
    resp = client.post("/api/text/convert",
                       json={"text": "龙", "height": 64})
    d = json.loads(resp.data)
    assert d["ok"] and d["height"] == 64
    assert 56 <= d["rows"] <= 64


def test_convert_cjk_height_over_limit_clamped(client):
    resp = client.post("/api/text/convert",
                       json={"text": "龙", "height": 100})
    d = json.loads(resp.data)
    assert d["height"] == 64


def test_convert_cjk_height_auto_shrink_for_long_text(client):
    resp = client.post("/api/text/convert",
                       json={"text": "你好世界万物更新", "height": 64})
    d = json.loads(resp.data)
    assert d["height"] == 10  # 8 字 × 2 列/行 → 收缩到宽度红线内
