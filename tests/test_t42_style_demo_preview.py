"""T42 无素材时点风格卡 -> 预览区显示该风格成品 GIF（黑屏 bug 回归）。

bug 形态：风格卡的 .style-preview 里是 <img> 时，textContent 只剩换行+缩进空白，
旧代码把它当有效演示文本存进 styleDemos，showStyleDemo 再写给 #animPreview，
静态示例被替换成空白 -> 终端深色底看起来就是全黑。

没有 DOM 测试环境（不引 jsdom），这里用静态断言锁定实现形态。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "js" / "app.js"
APP_CSS = ROOT / "static" / "css" / "app.css"


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _between(src: str, start: str, end: str) -> str:
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


def _capture_block() -> str:
    return _between(_js(), "var styleDemos = {};", "function customDemoArt()")


def _show_block() -> str:
    return _between(_js(), "function showStyleDemo(", "\n  }\n")


def test_capture_trims_blank_and_collects_img():
    """(a) 捕获处 trim 判空，并额外抓卡内 <img> 的 src/alt。"""
    blk = _capture_block()
    assert ".trim()" in blk, "捕获文本必须 trim，否则空白串会被当有效演示"
    assert 'p.querySelector("img")' in blk, "必须抓卡内 <img>（改成 GIF 后的卡）"
    assert 'img.getAttribute("src")' in blk
    assert 'text: (p.textContent || "").trim()' in blk


def test_show_style_demo_has_img_branch():
    """(b) showStyleDemo 有 img 分支：把 <img> 放进 #animPreview，带 class 与 alt。"""
    blk = _show_block()
    assert 'document.createElement("img")' in blk
    assert 'art.className = "preview-demo-art"' in blk
    assert "art.src = demo.img" in blk
    assert "art.alt = demo.alt" in blk or 'art.alt =' in blk
    assert "preview.appendChild(art)" in blk


def test_css_demo_image_rule_fits_terminal():
    """(c) CSS 里有演示图类：object-fit: contain + max-width/max-height 100% + pixelated。"""
    css = APP_CSS.read_text(encoding="utf-8")
    assert ".animation-terminal .terminal-body .preview-demo-art {" in css
    blk = _between(css, ".animation-terminal .terminal-body .preview-demo-art {", "}")
    assert "object-fit: contain;" in blk
    assert "max-width: 100%;" in blk
    assert "max-height: 100%;" in blk
    assert "image-rendering: pixelated;" in blk


def test_blank_text_never_assigned_to_preview():
    """(d) 空白文本不再直接赋给 preview.textContent：只允许 trim 后的 demo.text。"""
    js = _js()
    assert "preview.textContent = styleDemos" not in js
    assert "preview.textContent = demo.text" in _show_block()


def test_custom_text_fallback_kept():
    """custom 卡（纯文本形态）仍走 customDemoArt()，且改字符梯时不被演示图盖住。"""
    blk = _show_block()
    assert "preview.textContent = customDemoArt();" in blk
    assert 'showStyleDemo("custom", true)' in _js()


def test_demo_state_does_not_request_network():
    """演示态不发请求：img 分支只碰 DOM，没有 fetch/requestPreview 调用。"""
    blk = _show_block()
    assert "requestPreview" not in blk
    assert "fetch(" not in blk
    assert ".ajax" not in blk
