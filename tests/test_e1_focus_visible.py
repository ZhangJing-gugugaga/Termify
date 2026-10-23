"""E-1：Tab 键导航时焦点元素没有可见轮廓（浏览器默认 outline auto 1px rgb(16,16,16)）。

症状：键盘 Tab 遍历首页 14 个可聚焦元素，实测 outlineStyle/outlineColor 全是
浏览器默认的 `auto 1px rgb(16, 16, 16)`；画在 --bg-deep #060a10 上对比度只有
1.04:1，等于隐形。grep 全仓 `:focus-visible` 出现 0 次。

根因是回归：static/css/text_art.css:599-605 的注释仍在写「这个 (0,2,0) 的 outline
会顶掉 app.css:35 :focus-visible 的全局焦点环」——说明 app.css 顶部确实曾有过这条
全局规则，后来丢了，注释成了孤儿。

修法：在 app.css 恢复全局 `:focus-visible`（2px solid var(--green) + 2px offset），
与仓库既有的焦点语汇 .export-theme-dot（app.css:2688-2696 的 2px outline + 2px
offset + --green）保持一致。#00ff41 对 #060a10 是 14.53:1。本文件把这条契约与
算式一起钉住，并守住它依赖的下游注释不再变成孤儿。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_CSS = ROOT / "static" / "css" / "app.css"
TOKENS_CSS = ROOT / "static" / "css" / "tokens.css"
TEXT_ART_CSS = ROOT / "static" / "css" / "text_art.css"

# 修复前浏览器画在深底上的默认焦点环颜色，对比度 1.04:1，正是本条问题的现场
BROWSER_DEFAULT_RING = (16, 16, 16)
MIN_CONTRAST = 3.0  # WCAG 2.1 非文本对比度下限（焦点指示器属于 UI 组件边界）


def _block(css_text, selector):
    # 允许命中组选择器（如 ".gallery-form-input:focus, .gallery-form-textarea:focus {…}"）
    m = re.search(r"\n\s*" + re.escape(selector) + r"(?:\s*,\s*[^{]*)?\s*\{(.*?)\}", css_text, re.S)
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


def _outline_parts(block):
    """把 outline 简写拆成 (width_px, style, color)，颜色可能是 var(--x)。"""
    m = re.search(r"outline:\s*([^;]+);", block)
    assert m, "焦点规则里没有 outline 简写：%r" % block
    parts = m.group(1).split()
    width = 0.0
    style = "none"
    color = None
    for p in parts:
        wm = re.match(r"^([\d.]+)px$", p)
        if wm:
            width = float(wm.group(1))
        elif p in ("solid", "dashed", "dotted", "double", "auto", "none"):
            style = p
        else:
            color = p
    return width, style, color


def _resolve_color(css_token, tokens_css):
    m = re.match(r"var\(\s*--([\w-]+)\s*(?:,\s*([^)]+))?\)", css_token)
    if m:
        name, fallback = m.group(1), m.group(2)
        value = _var(tokens_css, name)
        return _hex(value if value.startswith("#") else fallback.strip())
    return _hex(css_token)


def _client():
    from app import app as flask_app
    return flask_app.test_client()


@pytest.fixture(scope="module")
def app_css():
    return APP_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tokens_css():
    return TOKENS_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def focus_block(app_css):
    return _block(app_css, ":focus-visible")


def test_global_focus_visible_rule_exists(focus_block):
    """回归点：这条全局规则曾经存在（text_art.css:600 还在依赖），后来丢了。"""
    assert focus_block.strip(), ":focus-visible 规则存在但为空"
    assert re.search(r"outline:\s*[^;]+;", focus_block), focus_block
    assert "!important" not in focus_block


def test_ring_is_thick_and_offsets_off_the_box(focus_block):
    width, style, color = _outline_parts(focus_block)
    assert style == "solid", "焦点环不该是 %r（默认 auto 就是 1px 隐形的来源）" % style
    assert width >= 2, "焦点环宽度 %.0fpx 低于 2px" % width
    assert color is not None, "焦点环没写颜色，会退回浏览器默认的 rgb(16,16,16)"
    offset = re.search(r"outline-offset:\s*(-?[\d.]+)px", focus_block)
    assert offset, "缺 outline-offset，焦点环会贴着元素边框画"
    assert float(offset.group(1)) >= 2, "outline-offset 应 >= 2px，才能脱离深色元素底色"


def test_ring_contrast_passes_against_every_dark_background(app_css, tokens_css, focus_block):
    _width, _style, color_token = _outline_parts(focus_block)
    ring = _resolve_color(color_token, tokens_css)
    for token in ("bg-deep", "bg-base", "bg-surface"):
        bg = _hex(_var(tokens_css, token))
        ratio = _contrast(ring, bg)
        assert ratio > MIN_CONTRAST, "焦点环 %s 对 --%s 只有 %.2f:1" % (
            color_token, token, ratio)
    # 主页面底色上的数值即体检口径：#00ff41 vs #060a10 = 14.53:1
    assert round(_contrast(ring, _hex(_var(tokens_css, "bg-deep"))), 2) >= 14.0


def test_browser_default_ring_would_have_failed(tokens_css):
    """守住本条用例的意义：默认焦点环颜色必须是被判定为不可见的那个。"""
    bg = _hex(_var(tokens_css, "bg-deep"))
    assert round(_contrast(BROWSER_DEFAULT_RING, bg), 2) == 1.04


def test_no_global_focus_reset_kills_the_ring(app_css):
    """别用 *:focus{outline:none} 这种重置把刚恢复的键盘可见性再抹掉。"""
    for selector in ("*:focus", ":focus", "*:focus-visible"):
        m = re.search(r"\n\s*" + re.escape(selector) + r"\s*\{(.*?)\}", app_css, re.S)
        if m:
            assert not re.search(r"outline:\s*(none|0)", m.group(1)), m.group(0)


def test_custom_focus_styles_still_only_touch_border_color(app_css):
    """既有自定义 :focus 权重更高；它们只改 border-color，所以焦点环照样可见。"""
    for selector in (".url-fetch-input:focus", ".gallery-form-input:focus",
                     ".admin-login-input:focus"):
        block = _block(app_css, selector)
        assert re.search(r"border-color:\s*var\(--green\)", block), (selector, block)
        assert "outline" not in block, "%s 不该覆盖全局焦点环" % selector


def test_url_input_base_rule_does_not_swallow_the_ring(app_css):
    """.url-fetch-input 的 outline: none 与 :focus-visible 同权重、位置更靠后，会赢。"""
    block = _block(app_css, ".url-fetch-input")
    assert not re.search(r"outline:\s*(none|0)", block), block
    assert re.search(r"border:\s*1px solid var\(--border-dim\)", block), "输入框边框不该被动过"


def test_text_art_css_comment_dependency_is_real(app_css):
    """text_art.css 的色点靠 :not(:focus-visible) 避让全局环——这条注释不能变孤儿。"""
    text_art = TEXT_ART_CSS.read_text(encoding="utf-8")
    assert re.search(r"app\.css:\d+\s+:focus-visible", text_art), (
        "text_art.css 里指向 app.css :focus-visible 的注释丢了")
    assert ".ta-theme-dot.active:not(:focus-visible)" in text_art, (
        "色点必须继续用 :not(:focus-visible) 避让键盘焦点环")
    assert ":focus-visible {" in app_css, "注释指向的规则不存在，又会变成孤儿注释"


def test_served_app_css_carries_the_fix():
    resp = _client().get("/static/css/app.css")
    assert resp.status_code == 200
    block = _block(resp.get_data(as_text=True), ":focus-visible")
    width, style, color = _outline_parts(block)
    assert (style, width, color) == ("solid", 2.0, "var(--green)"), block
