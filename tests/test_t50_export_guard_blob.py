"""T50 — 文本艺术导出：跨站写守卫不放宽 + 前端改走 fetch/blob 下载。

背景（线上 P0）：/text-art 行 4 个导出按钮（PNG/HTML/TXT/PY）在真实浏览器里全部
403、零文件落盘。根因是 Referrer-Policy: no-referrer（app.py 的安全响应头）让前端
原先把隐藏 form POST 当下载器的做法变成一次"无 Referer、Origin: null"的导航式请求，
而 _write_origin_guard() 明确把 Origin: null 判为跨源并 403 —— 守卫合理，是前端下载
方式用错了。实测（Chrome 155，页面 /text-art，抓 Fetch.requestPaused）表单 POST 发
送的请求头里连 Sec-Fetch-Site 都没有，只有 Origin: null，因此"靠 Sec-Fetch-Site:
same-origin 放行"这条路在真实浏览器里无据可依，唯一正确的修法是前端不再产生
Origin: null：postDownload() 改为 fetch + blob + a[download]。

本文件把两个方向都钉住：
1. 守卫侧不许被这次修法带松 —— 跨站 Origin、Origin: null 仍然 403，同源 Origin 才 200。
2. 前端不许退回导航式 form POST —— static/js/text_art.js 里不得出现 form.submit()，
   必须走 createObjectURL + a.download，且 ?v= 版本串已随改动升级（缓存失效）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    import flask  # noqa: F401

    _HAVE_FLASK = True
except ImportError:  # pragma: no cover
    _HAVE_FLASK = False

pytestmark = pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装")

REPO_ROOT = Path(__file__).resolve().parents[1]
TEXT_ART_JS = REPO_ROOT / "static" / "js" / "text_art.js"
TEXT_ART_HTML = REPO_ROOT / "templates" / "text_art.html"

EXPORT_ENDPOINTS = ("/api/text/export-png", "/api/text/export-html",
                    "/api/text/export-txt", "/api/text/export-py")

# 导出接口只认 art 正文，其余字段可选；"HELLO\nWORLD" 是 t33 已用的合法艺术字
ART = "HELLO\nWORLD"
PAYLOAD = {"art": ART, "theme": "green", "name": "hotfix_art"}

ASSET_VERSION = "20261031c"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """画廊库 / 任务库 / 限流计数全部指向 tmp，避免跨用例污染（同 t4x 做法）。"""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t50.db"))
    monkeypatch.setenv("TERMIFY_ADMIN_PWD", "t50-admin-pwd")

    import app as app_mod
    from termify import gallery as gallery_mod

    gdata = tmp_path / "gallery_data"
    gdata.mkdir()
    db = gallery_mod.GalleryDB(str(gdata / "termify.db"))
    db.init_db()
    monkeypatch.setattr(app_mod, "GALLERY_DATA_DIR", str(gdata))
    monkeypatch.setattr(app_mod, "GALLERY_DB", db)
    monkeypatch.setattr(app_mod, "_ADMIN_PWD_FAILS", {})
    monkeypatch.setattr(app_mod, "_ADMIN_PWD_BLOCKED", {})
    app_mod._RL_LOG.clear()
    yield
    app_mod._RL_LOG.clear()


@pytest.fixture
def client():
    import app as app_mod

    app_mod.app.config.update(TESTING=True)
    return app_mod.app.test_client()


# ── 1. 跨站写守卫：这次修法一点都没放宽 ──────────────────────────

@pytest.mark.parametrize("url", EXPORT_ENDPOINTS)
def test_export_rejects_cross_site_origin(client, url):
    resp = client.post(url, json=PAYLOAD,
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403, url
    body = resp.get_json()
    assert "跨站请求已拒绝" in body["error"]
    assert resp.mimetype == "application/json"


@pytest.mark.parametrize("url", EXPORT_ENDPOINTS)
def test_export_rejects_null_origin(client, url):
    """Origin: null 一律拒（浏览器下载修法不能再依赖这条放行）。

    真实浏览器里 Origin: null 只可能来自 no-referrer 的导航式请求或沙箱 iframe，
    两者都不是本站的合法写来源；放行它等于对任意页面开放写接口。
    这也是本次把前端改成 fetch 的原因：不是放宽守卫，而是让浏览器不再发 null。
    """
    resp = client.post(url, json=PAYLOAD, headers={"Origin": "null"})
    assert resp.status_code == 403, url
    assert "跨站请求已拒绝" in resp.get_json()["error"]


@pytest.mark.parametrize("url,ctype,magic", [
    ("/api/text/export-png", "image/png", b"\x89PNG"),
    ("/api/text/export-html", "text/html", b"<!DOCTYPE html"),
    ("/api/text/export-txt", "text/plain", b"HELLO"),
    ("/api/text/export-py", "text/x-python", b"#!"),
])
def test_export_accepts_same_origin(client, url, ctype, magic):
    """fetch 方案下浏览器发的是真实同源 Origin，必须 200 且带附件名。"""
    resp = client.post(url, json=PAYLOAD,
                       headers={"Origin": "http://localhost"})
    assert resp.status_code == 200, url
    assert resp.mimetype == ctype
    assert resp.data.startswith(magic), resp.data[:32]
    cd = resp.headers["Content-Disposition"]
    assert cd.startswith("attachment;")
    # name 不带扩展名时只追加一次：前端 exportName() 负责去掉扩展名
    assert 'filename="hotfix_art.' in cd
    assert ".png.png" not in cd and ".html.html" not in cd


# ── 2. 前端契约：不许退回导航式 form POST ────────────────────────

def _js_without_comments(path: Path) -> str:
    """去掉 /* */ 块注释，免得"别用 form.submit()"这类说明文字骗过断言。"""
    src = path.read_text(encoding="utf-8")
    return re.sub(r"/\*[\s\S]*?\*/", "", src)


def test_text_art_js_has_no_form_submit():
    code = _js_without_comments(TEXT_ART_JS)
    assert "form.submit" not in code, "导航式 form 提交会重新产生 Origin: null"
    assert not re.search(r"<form", code), "下载路径不应再动态造 form"


def test_text_art_js_downloads_via_blob():
    src = TEXT_ART_JS.read_text(encoding="utf-8")
    assert "URL.createObjectURL" in src
    assert ".download" in src
    assert "application/json" in src, "fetch 必须显式声明 JSON body"
    assert "Content-Disposition" in src, "文件名从响应头派生，避免二次扩展名"


def test_text_art_page_version_bumped():
    """JS 改了就必须升版本串，否则浏览器继续用缓存里的旧下载逻辑。"""
    html = TEXT_ART_HTML.read_text(encoding="utf-8")
    m = re.search(r"text_art\.js[^?]*\?v=([0-9a-z]+)", html)
    assert m, "页面里找不到 text_art.js 的版本串"
    assert m.group(1) == ASSET_VERSION
