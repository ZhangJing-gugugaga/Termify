"""T46 — CSP 收紧：script-src 去 'unsafe-inline' + 可执行内联脚本全外置。

锁住的契约（S-3）：
1. 响应头 Content-Security-Policy 的 script-src 只剩 'self'，不存在
   "script-src 'self' 'unsafe-inline'"；object-src/base-uri/form-action/
   frame-ancestors 四条新指令在位（frame-ancestors 'none' 与 X-Frame-Options
   DENY 对齐）。
2. style-src 仍含 'unsafe-inline'——模板有大量 style="--c:..." 内联样式属性，
   收紧它属于另一件事，此处显式记录为已知宽松项，防止被顺手当成已收口。
3. 5 个模板（admin/gallery/index/view_work/text_art）里不再有可执行内联
   <script>：只允许带 src 的外置引用与 type="application/json" 数据块。
4. /v/ 页的作品数据经 type="application/json" 块过河：块内是纯 JSON，
   </script> 闭合数与数据块一一对应（存储型 XSS 防线不退化为内联脚本）。
"""

from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path

import pytest
from PIL import Image
from tests.gallery_marks import requires_gallery

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # pragma: no cover — 无 flask 的裸环境
    _HAVE_FLASK = False

pytestmark = pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装")

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "templates"
STATIC_JS = REPO_ROOT / "static" / "js"

PAGE_TEMPLATES = ("admin.html", "gallery.html", "index.html", "view_work.html", "text_art.html")
EXTERNAL_SCRIPTS = (
    "admin_page.js",
    "gallery_page.js",
    "index_page.js",
    "view_work_page.js",
)
STATIC_VERSION = "?v=20261031d"

REQUIRED_DIRECTIVES = (
    "default-src 'self'",
    "img-src 'self' data:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
    "media-src 'self' data: blob:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """临时 CWD + 独立任务库 + 独立画廊库/数据目录（与 t29/t34 同口径）。"""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t46.db"))
    monkeypatch.delenv("TERMIFY_ADMIN_PWD", raising=False)

    import app as app_mod
    from termify.gallery import GalleryDB
    from termify.taskstore import cache_clear_all, get_store, reset_store_for_tests

    cache_clear_all()
    reset_store_for_tests()
    app_mod._RL_LOG.clear()
    get_store().set_sweep_hook(app_mod._sweep_stale_frame_dirs)

    gdir = tmp_path / "gallerydata"
    gdir.mkdir(exist_ok=True)
    monkeypatch.setattr(app_mod, "GALLERY_DATA_DIR", str(gdir))
    gdb = GalleryDB(str(tmp_path / "gallery" / "g.db"))
    gdb.init_db()
    monkeypatch.setattr(app_mod, "GALLERY_DB", gdb)
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


def _png_bytes(w=8, h=4):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 200, 30)).save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def _upload_work(client, title="t46", params="{}", tags="[]"):
    resp = client.post(
        "/api/gallery/upload",
        data={
            "source": (io.BytesIO(_png_bytes()), title + ".png"),
            "title": title,
            "params": params,
            "tags": tags,
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)["id"]


def _csp(resp):
    return resp.headers.get("Content-Security-Policy")


OPEN_TAG_RE = re.compile(r"<script\b([^>]*)>", re.IGNORECASE)


def _executable_inline_scripts(html):
    """返回可执行内联 <script> 的开标签列表（src 引用与数据块不算）。"""
    bad = []
    for m in OPEN_TAG_RE.finditer(html):
        attrs = m.group(1) or ""
        if re.search(r"\bsrc\s*=", attrs, re.IGNORECASE):
            continue
        type_m = re.search(r"""\btype\s*=\s*["']([^"']*)["']""", attrs, re.IGNORECASE)
        if type_m and type_m.group(1).strip().lower() in (
            "application/json",
            "application/ld+json",
        ):
            continue
        bad.append(m.group(0))
    return bad


# ═══ 1. 响应头：script-src 去 'unsafe-inline'，四条新指令在位 ═══════════════


def test_csp_header_script_src_is_self_only(client):
    csp = _csp(client.get("/"))
    assert csp, "CSP 头缺失"
    assert "script-src 'self' 'unsafe-inline'" not in csp, csp
    assert "unsafe-hashes" not in csp, csp
    assert "script-src 'self'" in csp, csp
    # nonce 不是本方案的绕行手段
    assert "nonce-" not in csp, csp


def test_csp_header_has_new_directives(client):
    csp = _csp(client.get("/"))
    for directive in REQUIRED_DIRECTIVES:
        assert directive in csp, (directive, csp)


def test_csp_style_src_unsafe_inline_is_known_loosening(client):
    """style-src 的 'unsafe-inline' 是已披露的宽松项，不是遗漏。"""
    csp = _csp(client.get("/"))
    assert "style-src 'self' 'unsafe-inline'" in csp, csp


@requires_gallery
def test_csp_header_on_every_page_route(client):
    wid = _upload_work(client, title="t46-csp")
    paths = ["/", "/gallery", "/text-art", "/v/" + wid, "/admin", "/no-such-page-t46"]
    seen = {}
    for path in paths:
        resp = client.get(path)
        csp = _csp(resp)
        assert csp, (path, resp.status_code)
        assert "script-src 'self';" in csp or csp.split("script-src")[1].startswith(" 'self';"), (
            path,
            csp,
        )
        assert "script-src 'self' 'unsafe-inline'" not in csp, (path, csp)
        for directive in REQUIRED_DIRECTIVES:
            assert directive in csp, (path, directive, csp)
        seen[path] = csp
    assert seen["/no-such-page-t46"], seen


# ═══ 2. 模板层：不再有可执行内联 <script> ═══════════════════════════════════


@pytest.mark.parametrize("name", PAGE_TEMPLATES)
def test_template_has_no_executable_inline_script(name):
    html = (TEMPLATES / name).read_text(encoding="utf-8")
    bad = _executable_inline_scripts(html)
    assert bad == [], (name, bad)


def test_view_work_template_uses_json_data_block():
    html = (TEMPLATES / "view_work.html").read_text(encoding="utf-8")
    assert 'type="application/json" id="termifyWorkData"' in html
    assert "window.TERMIFY_GALLERY_WORK" not in html
    assert "| tojson" in html


@pytest.mark.parametrize("name", EXTERNAL_SCRIPTS)
def test_external_script_file_exists_and_is_referenced(name):
    path = STATIC_JS / name
    assert path.is_file(), path
    referencing = [
        p for p in PAGE_TEMPLATES if name in (TEMPLATES / p).read_text(encoding="utf-8")
    ]
    assert referencing, name
    for tmpl in referencing:
        text = (TEMPLATES / tmpl).read_text(encoding="utf-8")
        assert "js/" + name + "') }}?v=20261031d" in text, tmpl


# ═══ 3. 渲染结果：无内联可执行脚本 + /v/ 数据块可无损还原 ═══════════════════


@requires_gallery
def test_rendered_pages_have_no_executable_inline_script(client):
    wid = _upload_work(client, title="t46-render")
    for path in ("/", "/gallery", "/text-art", "/v/" + wid, "/admin"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        html = resp.data.decode("utf-8")
        assert _executable_inline_scripts(html) == [], path


@requires_gallery
def test_view_page_data_block_roundtrips(client):
    params = {"charset": "blocks", "width": 80, "height": 24}
    wid = _upload_work(client, title="t46-data", params=json.dumps(params), tags='["动画"]')
    body = client.get("/v/" + wid).data.decode("utf-8")

    # 外置后只剩：数据 JSON 块 + termify-render.js + view_work_page.js 三个闭合
    assert body.count("</script>") == 3, body.count("</script>")
    m = re.search(
        r'<script type="application/json" id="termifyWorkData">(.*?)</script>',
        body,
        re.S,
    )
    assert m, body[:2000]
    data = json.loads(m.group(1))
    assert data["id"] == wid
    assert data["title"] == "t46-data"
    assert json.loads(data["params"]) == params
    assert json.loads(data["tags"]) == ["动画"]


@requires_gallery
def test_view_page_data_block_neutralizes_script_injection(client):
    """存储型 XSS 防线不变：params 里的 </script> 不能让数据块提前闭合。"""
    payload = {"inj": "</script><img src=x onerror=alert(1)>", "charset": "ascii",
               "width": 80, "height": 24}
    wid = _upload_work(client, title="t46-xss</script><img src=x>", params=json.dumps(payload))
    body = client.get("/v/" + wid).data.decode("utf-8")
    # 数据块不得被提前闭合：仍然只有 数据块 + termify-render.js + view_work_page.js
    assert body.count("</script>") == 3, body.count("</script>")
    assert "</script><img" not in body
    assert "<img src=x onerror=alert(1)>" not in body
    m = re.search(
        r'<script type="application/json" id="termifyWorkData">(.*?)</script>',
        body,
        re.S,
    )
    data = json.loads(m.group(1))
    # title 里的 </script> 被 tojson 转义成 \u003c/script\u003e（不可提前闭合）
    assert "\\u003c/script\\u003e" in m.group(1)
    assert "<" not in m.group(1)
    # params 白名单（_VIEW_PARAM_KEYS）整体丢弃白名单外的 inj 键
    parsed = json.loads(data["params"])
    assert "inj" not in parsed
    assert parsed["charset"] == "ascii"
