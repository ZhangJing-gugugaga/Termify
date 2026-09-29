"""T33 — 字符艺术（FIGlet 直转 + 中文点阵 + 字符作品入库）。

覆盖：
- textart 单元：精选字体/FIGlet 渲染/非 ASCII 过滤（lddgo 语义）/入库校验
- API：/api/text/fonts、/api/text/convert（含 CJK 自动分流）
- /api/gallery/upload-text：文字作品入库 → /v/ 页回放（frames 白名单）→ 私有直链鉴权
"""

from __future__ import annotations

import json
import os

import pytest
from tests.gallery_marks import requires_gallery

# 直接 import 探测（而非 importlib.util.find_spec）：全量长跑时 find_spec
# 会受进程内 import 状态污染而误报 flask 缺失，导致整个文件被静默跳过
# （2026-09-22 评估 §1.2 记录的测试基建瑕疵）。
try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # pragma: no cover — 无 flask 的裸环境
    _HAVE_FLASK = False

pytestmark = pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装")


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


@requires_gallery
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


@requires_gallery
def test_upload_text_private_auth(client):
    resp = _publish_text(client, private="1")
    assert resp.status_code == 200
    work_id = json.loads(resp.data)["id"]
    stranger = client.__class__(client.application)
    assert stranger.get(f"/gallery/file/{work_id}/source").status_code == 403
    assert client.get(f"/gallery/file/{work_id}/source").status_code == 200


@requires_gallery
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
    import zlib
    inner = cmd.split("b64decode('")[1].split("')")[0]
    return zlib.decompress(base64.b64decode(inner)).decode("utf-8")


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
    d = json.loads(resp.data)
    cmd = d["cmd"]
    assert cmd.startswith("python -c")
    assert "38;2;0;212;255" in _cmd_payload(cmd)  # cyan 主题 RGB
    assert d["cmd_len"] == len(cmd)
    assert d["too_long"] is False


def test_terminal_command_endpoint_source_art(client):
    art = "\x1b[38;2;9;8;7mX\x1b[0m"
    resp = client.post("/api/text/terminal-command",
                       json={"art": art, "theme": "green"})
    assert resp.status_code == 200
    assert _cmd_payload(json.loads(resp.data)["cmd"]) == art


def test_terminal_command_fits_under_cmd_limit():
    """真彩作品（run-length SGR，真实形态）要压进 cmd.exe 8191 命令行。"""
    from termify import textart

    rows = []
    for y in range(60):
        parts, last = [], None
        for x in range(200):
            c = ((x // 4) * 7 + (y // 3) * 3) % 256
            if c != last:
                parts.append("\x1b[38;2;%d;%d;%dm" % (c, 255 - c, c // 2))
                last = c
            parts.append("#" if c > 128 else " ")
        rows.append("".join(parts) + "\x1b[0m")
    art = "\n".join(rows)
    cmd = textart.render_terminal_command(art, "green")
    assert len(cmd) < textart.TERMINAL_CMD_MAX, len(cmd)
    assert _cmd_payload(cmd) == art


def test_terminal_command_endpoint_flags_too_long(client):
    """压不进命令行的（极端不可压缩负载）→ too_long，前端改推 .py。"""
    import random

    rnd = random.Random(7)
    art = "\n".join("".join(chr(33 + rnd.randrange(90)) for _ in range(400))
                    for _ in range(110))
    resp = client.post("/api/text/terminal-command",
                       json={"art": art, "theme": "green"})
    assert resp.status_code == 200, resp.data
    d = json.loads(resp.data)
    assert d["too_long"] is True
    assert len(d["cmd"]) > 7000
    # .py 导出对同样的负载照常可用（唯一的可靠路径）
    r2 = client.post("/api/text/export-py", json={"art": art, "theme": "green"})
    assert r2.status_code == 200 and b"PAYLOAD" in r2.data


# ── .py 导出：终端命令太大时的正解 ─────────────────────────────

def test_render_python_script_roundtrip():
    from termify import textart

    art = "HI\n\x1b[38;2;1;2;3mYO\x1b[0m"
    script = textart.render_python_script(art, "green", "作品 01")
    assert "PAYLOAD" in script and "zlib" in script
    # 脚本必须是可编译、可运行的（用 subprocess 真跑一遍）
    import subprocess
    import sys
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "art.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(script)
        out = subprocess.run([sys.executable, p], capture_output=True,
                             text=True, check=True).stdout
    assert out == art + "\n"


def test_export_py_endpoint(client):
    resp = client.post("/api/text/export-py",
                       json={"art": "HI\nWORLD", "theme": "amber",
                             "name": "我的作品"})
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "PAYLOAD" in body
    assert "text/x-python" in resp.headers["Content-Type"]
    assert "filename*=UTF-8''" in resp.headers["Content-Disposition"]


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
    # 8 字 × 2 列/行 → 400 列宽度预算下收缩到 25 行
    assert d["height"] == 25
    assert d["cols"] <= 440


def test_convert_cjk_50_rows_reachable(client):
    """用户诉求：字符高度至少能到 50 行（此前 3 字就被宽度红线压到 26）。"""
    from termify import textart

    for n in (1, 2, 3, 4):
        text = "龙腾四海"[:n]
        assert textart.cjk_effective_height(text, 64) >= 50, n
    resp = client.post("/api/text/convert",
                       json={"text": "龙腾", "height": 50})
    d = json.loads(resp.data)
    assert d["height"] == 50 and d["rows"] == 50 and d["cols"] == 200


def test_cjk_ink_box_no_cropped_edges():
    """回归：按 em box 压缩时首行整行空白、字头只剩半格（"字被吃了一半"）。

    现在按墨迹盒取样：首行与末行都必须有笔画，且列数 = 字数 × 高度 × 2。
    """
    from termify import textart

    art = textart.render_cjk_ttf("黑体", "heiti", 26)
    lines = art.split("\n")
    assert lines[0].strip(), "首行空白 = 字头被吃掉"
    assert lines[-1].strip(), "末行空白 = 字脚被吃掉"
    assert all(len(ln) == 2 * 26 * 2 for ln in lines)  # 2 字 × 2 列/行 × 26 行


def test_cjk_fontwall_endpoint(client):
    """中文字符墙（2026-09-29 起）：同一点阵 × 字符集，不再是字体墙。"""
    from termify import textart

    resp = client.post("/api/cjk/ttf/fontwall", json={"text": "你好"})
    assert resp.status_code == 200, resp.data
    cards = json.loads(resp.data)["charsets"]
    assert cards, "中文字符墙不能为空"
    for f in cards:
        # 卡片必须带完整作品 + 尺寸（前端据此等比缩放，不截行）
        assert f["art"] and f["full"] and f["cols"] > 0 and f["rows"] > 0
        assert f["art"] == f["full"]
        assert f["slug"] in {c[0] for c in textart.CJK_CHARSETS}
    # 点亮字符随卡片变化（字块/盲文/经典…共用同一份点阵骨架）
    assert {textart.cjk_on_char(f["slug"]) for f in cards} >= \
        {textart._CJK_ON, "#"}


def test_figlet_fontwall_not_row_truncated(client):
    """英文字体墙：任何字体都不得被砍行（旧实现统一截到 8 行）。"""
    from termify import textart

    resp = client.post("/api/text/fontwall", json={"text": "termify"})
    fonts = json.loads(resp.data)["fonts"]
    tall = 0
    for f in fonts:
        assert f["art"] == f["full"]          # 卡片与点选结果同源
        assert len(f["art"].split("\n")) == f["rows"]
        expect = textart.render_figlet("termify", f["slug"], 100)
        assert f["rows"] == len(expect.split("\n")), f["slug"]
        if f["rows"] > 8:
            tall += 1
    assert tall >= 5, "样本里应该有多款超过 8 行的字体（旧实现会截断）"


def test_imgwall_endpoint_covers_charsets(client):
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (48, 24), (200, 60, 40)).save(buf, format="PNG")
    buf.seek(0)
    resp = client.post("/api/text/imgwall", data={
        "file": (buf, "墙.png"), "width": "60", "height": "30"},
        content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data
    fonts = json.loads(resp.data)["fonts"]
    slugs = {f["slug"] for f in fonts}
    assert {"ascii", "braille", "shades", "binary"} <= slugs
    assert "custom" not in slugs  # 自定义字符无固定字形，不进墙
    for f in fonts:
        assert f["art"] and f["cols"] > 0 and f["rows"] > 0
        assert f["cols"] <= 60  # 墙卡缩略图，宽度已按比例收窄


def test_export_txt_endpoint(client):
    resp = client.post("/api/text/export-txt",
                       json={"art": "HELLO\nWORLD", "name": "我的作品"})
    assert resp.status_code == 200
    assert b"HELLO" in resp.data
    cd = resp.headers["Content-Disposition"]
    cd.encode("latin-1")
    assert "filename*=UTF-8''" in cd  # 中文名走 RFC 5987
