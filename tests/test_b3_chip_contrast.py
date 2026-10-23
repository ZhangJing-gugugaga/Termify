"""B-3：上传后文件名 chip 的激活态对比度只有 1.59:1（绿底绿字，WCAG 远不达标）。

症状：static/css/app.css 的 .file-list-item.active 用 --green-dim（#00cc33）当实心底色，
文字又是 --green（#00ff41），实测 1.59:1，肉眼基本糊在一起。

修法：只动颜色，不改 DOM——改成仓库既有的 active 语汇（.palette-btn.active /
.sort-tab.active 同款：半透明 --green-glow 底 + 绿字 + 绿边），
合成底色 rgb(5,47,23) 上 #00ff41 的对比度 10.79:1。本文件把颜色契约与算式一起钉住。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_CSS = ROOT / "static" / "css" / "app.css"
TOKENS_CSS = ROOT / "static" / "css" / "tokens.css"
APP_JS = ROOT / "static" / "js" / "app.js"
INDEX_HTML = ROOT / "templates" / "index.html"


def _block(css_text, selector):
    m = re.search(r"\n\s*" + re.escape(selector) + r"\s*\{(.*?)\}", css_text, re.S)
    assert m, "app.css 里找不到 %s 规则" % selector
    return re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)


def _var(css_text, name):
    m = re.search(r"--" + re.escape(name) + r":\s*([^;]+);", css_text)
    assert m, "tokens.css 里找不到 --%s" % name
    return m.group(1).strip()


def _hex(value):
    v = value.lstrip("#")
    assert len(v) == 6, value
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def _rgba(value):
    m = re.match(r"rgba?\(([^)]+)\)", value)
    assert m, value
    parts = [p.strip() for p in m.group(1).split(",")]
    return tuple(int(float(p)) for p in parts[:3]), float(parts[3])


def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(rgb):
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def _contrast(fg, bg):
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _composite(fg, alpha, bg):
    return tuple(round(alpha * f + (1 - alpha) * b) for f, b in zip(fg, bg))


def _client():
    from app import app as flask_app
    return flask_app.test_client()


@pytest.fixture(scope="module")
def app_css():
    return APP_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tokens_css():
    return TOKENS_CSS.read_text(encoding="utf-8")


def test_active_chip_uses_glow_background(app_css):
    block = _block(app_css, ".file-list-item.active")
    assert re.search(r"background:\s*var\(--green-glow\)", block), block
    assert re.search(r"color:\s*var\(--green\)", block), block
    assert re.search(r"border-color:\s*var\(--green\)", block), block
    assert "--green-dim" not in block, "实心绿底正是 1.59:1 的来源，不能再出现"
    assert "!important" not in block


def test_active_chip_contrast_passes_wcag_aa(tokens_css):
    text = _hex(_var(tokens_css, "green"))
    glow, glow_alpha = _rgba(_var(tokens_css, "green-glow"))
    page = _hex(_var(tokens_css, "bg-deep"))
    bg = _composite(glow, glow_alpha, page)
    assert bg == (5, 47, 23), bg
    ratio = _contrast(text, bg)
    assert ratio >= 4.5, "激活 chip 对比度 %.2f:1 未达 WCAG AA 4.5:1" % ratio


def test_old_colors_would_have_failed(tokens_css):
    """守住这条用例的意义：别把底色改回 --green-dim。"""
    text = _hex(_var(tokens_css, "green"))
    old_bg = _hex(_var(tokens_css, "green-dim"))
    assert round(_contrast(text, old_bg), 2) == 1.59


def test_dom_shape_unchanged():
    js = APP_JS.read_text(encoding="utf-8")
    assert 'item.className = "file-list-item" + (i === S.selIdx ? " active" : "");' in js
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert '<div class="file-list" id="fileList"></div>' in html, "只改 CSS，容器标记不应被动过"


def test_served_css_carries_the_fix():
    resp = _client().get("/static/css/app.css")
    assert resp.status_code == 200
    css = resp.get_data(as_text=True)
    block = _block(css, ".file-list-item.active")
    assert "var(--green-glow)" in block
    assert "--green-dim" not in block
