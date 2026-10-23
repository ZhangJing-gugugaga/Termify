"""A-6：「背景网格」「扫描线」两个开关此前是死控件。

症状：static/js/app.js:2044-2045（及 index_page.js / gallery_page.js）只往 body
上写 --grid-opacity / --scanline-opacity，而 static/css/ 下这两个变量零消费，
body::before 硬编码 opacity: 0.3、body::after 干脆没有 opacity。于是点「隐藏」
只改了没人看的自定义属性，画面纹丝不动。

修法：在 body::before / body::after 上消费这两个变量，回退值取改动前的字面量
（0.3 / 1），默认态因此逐像素不变。本文件把这条接线钉住——任何一侧改名都会红。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_CSS = ROOT / "static" / "css" / "app.css"


def _block(css_text, selector):
    """取出某个顶层选择器的声明块（CSS 无嵌套，取到第一个右花括号即可）。"""
    m = re.search(r"\n\s*" + re.escape(selector) + r"\s*\{(.*?)\}", css_text, re.S)
    assert m, "app.css 里找不到 %s 规则" % selector
    return m.group(1)


def _quoted(src, name):
    """变量名在 JS 里以字符串出现即可（单双引号都算），值另测。"""
    return ('"%s"' % name) in src or ("'%s'" % name) in src


def _client():
    from app import app as flask_app
    return flask_app.test_client()


@pytest.fixture(scope="module")
def css_text():
    return APP_CSS.read_text(encoding="utf-8")


def test_grid_rule_consumes_grid_opacity_var(css_text):
    body = _block(css_text, "body::before")
    assert "--grid-opacity" in body, "body::before 必须消费 --grid-opacity，否则网格开关是死控件"
    assert re.search(r"opacity:\s*var\(--grid-opacity,\s*0\.3\)", body), body


def test_scanline_rule_consumes_scanline_opacity_var(css_text):
    body = _block(css_text, "body::after")
    assert "--scanline-opacity" in body, "body::after 必须消费 --scanline-opacity，否则扫描线开关是死控件"
    assert re.search(r"opacity:\s*var\(--scanline-opacity,\s*1\)", body), body


def test_fallbacks_equal_pre_fix_literals(css_text):
    """默认态视觉必须与改动前完全一致：回退值就是原来的 0.3 与 1。"""
    before = _block(css_text, "body::before")
    after = _block(css_text, "body::after")
    # 旧字面量不许再以裸值形式留在规则里（裸值会盖掉变量）
    assert not re.search(r"opacity:\s*0\.3\s*;", before), before
    assert not re.search(r"opacity:\s*1\s*;", after), after
    # 且回退值必须等于旧值
    assert re.search(r"var\(--grid-opacity,\s*0\.3\)", before), before
    assert re.search(r"var\(--scanline-opacity,\s*1\)", after), after


@pytest.mark.parametrize("js_path", [
    "static/js/app.js",
    "static/js/index_page.js",
    "static/js/gallery_page.js",
    "static/js/view_work_page.js",
])
def test_js_side_writes_the_same_variable_names(js_path):
    """JS 侧写的变量名与取值必须与 CSS 侧消费的一致（老值 0.3 / 1 / 0）。"""
    src = (ROOT / js_path).read_text(encoding="utf-8")
    assert _quoted(src, "--grid-opacity"), js_path
    assert _quoted(src, "--scanline-opacity"), js_path
    # 关闭态一律写 0，开启态写回 CSS 回退值（0.3 / 1）
    assert '"0.3"' in src or "'0.3'" in src, js_path
    assert '"1"' in src or "'1'" in src, js_path


def test_index_page_renders_the_four_toggle_buttons():
    html = _client().get("/").get_data(as_text=True)
    for sel in ("grid-on", "grid-off", "scan-on", "scan-off"):
        assert 'data-tweak="%s"' % sel in html, sel


def test_served_css_carries_the_wiring():
    """真正发出去的 app.css 必须带上接线（防缓存/构建漏更新）。"""
    r = _client().get("/static/css/app.css")
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert "var(--grid-opacity, 0.3)" in text
    assert "var(--scanline-opacity, 1)" in text