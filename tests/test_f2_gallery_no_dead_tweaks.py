"""F-2：画廊页整块 Tweaks 面板是无效设计，删掉面板与其专属 JS。

症状：templates/gallery.html 里的 #tweaksToggle / #tweaksPanel 在该页没有人驱动——
画廊页只加载 js/gallery_page.js，连写变量的 app.js 都不加载（虽然 gallery_page.js
后来也补了同名绑定，但那整块 UI 与首页重复，且该页没有上传/调参场景）。
修法：删掉画廊页的按钮 + 面板 + gallery_page.js 里的整套绑定；
.tweaks-* / .tweak-* CSS 类与首页共用，一律保留。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GALLERY_JS = ROOT / "static" / "js" / "gallery_page.js"
APP_CSS = ROOT / "static" / "css" / "app.css"


def _client():
    from app import app as flask_app
    return flask_app.test_client()


@pytest.fixture(scope="module")
def gallery_html():
    r = _client().get("/gallery")
    assert r.status_code == 200
    return r.get_data(as_text=True)


@pytest.fixture(scope="module")
def index_html():
    r = _client().get("/")
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_gallery_page_has_no_tweaks_panel(gallery_html):
    assert 'id="tweaksPanel"' not in gallery_html
    assert 'id="tweaksToggle"' not in gallery_html
    assert 'id="tweaksClose"' not in gallery_html
    assert 'class="tweaks-panel' not in gallery_html
    assert "data-tweak=" not in gallery_html


def test_gallery_js_has_no_tweaks_bindings():
    src = GALLERY_JS.read_text(encoding="utf-8")
    for needle in ("tweaksToggle", "tweaksPanel", "tweaksClose", "bindTweak", "data-tweak", "theme-amber"):
        assert needle not in src, needle


def test_gallery_js_still_initialises_the_page():
    """删绑定不能顺手动掉页面的正常入口。"""
    src = GALLERY_JS.read_text(encoding="utf-8")
    for needle in ("readStateFromUrl()", "syncSortTabs()", "syncCustomTagSelection()", "fetchGallery()"):
        assert needle in src, needle


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 做语法检查")
def test_gallery_js_still_parses():
    r = subprocess.run(["node", "--check", str(GALLERY_JS)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_index_page_keeps_its_tweaks_panel(index_html):
    assert 'id="tweaksPanel"' in index_html
    assert 'id="tweaksToggle"' in index_html
    for sel in ("grid-on", "grid-off", "scan-on", "scan-off"):
        assert 'data-tweak="%s"' % sel in index_html, sel


def test_shared_tweaks_css_is_retained():
    css = APP_CSS.read_text(encoding="utf-8")
    for sel in (".tweaks-toggle", ".tweaks-panel", ".tweak-group", ".tweak-opt"):
        assert sel in css, sel
