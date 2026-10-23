"""人工测试（2026-11）反馈的四条缺陷的回归护栏。

1. 作品页「复制链接」在 clipboard API 被拒时直接报「复制失败」→ 必须回退 execCommand。
2. 画廊「自定义标签」面板被卡片盖住 → .gallery-filters 必须自成更高层级；窄屏面板限高。
3. /text-art 中英字体墙卡片互相重叠 → 网格行高必须 max-content，不许被压成等分。
4. 点风格卡瞬间跳转 → scrollIntoView 必须 behavior: "smooth"。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_copy_link_falls_back_to_execcommand() -> None:
    """clipboard.writeText 的拒绝分支必须落到 execCommand，而不是只弹「复制失败」。"""
    src = read("static/js/view_work_page.js")
    assert "function copyText(text, okMsg, failMsg)" in src
    helper = src[src.index("function copyText(text, okMsg, failMsg)") :]
    helper = helper[: helper.index("// copy link")]
    assert "document.execCommand(\"copy\")" in helper
    assert "ta.select();" in helper
    assert ".then(ok, legacy)" in helper, "writeText 被拒后必须走 legacy"
    # 复制链接按钮本身只允许调用 copyText，不许再裸调 clipboard
    btn = src[src.index('document.getElementById("copyLinkBtn")') :]
    btn = btn[: btn.index("// report modal")]
    assert "copyText(" in btn
    assert "navigator.clipboard" not in btn


def rules(css: str, selector: str) -> list[str]:
    return re.findall(re.escape(selector) + r"\s*\{[^}]*\}", css)


def test_gallery_filters_above_cards_and_panel_scrolls() -> None:
    """面板被盖住的根因是两个 .container 同为 z-index:1，过滤区必须抬层；面板限高。"""
    css = read("static/css/app.css")
    filters = " ".join(rules(css, ".gallery-filters"))
    assert "position: relative;" in filters
    z = int(re.search(r"z-index:\s*(\d+)", filters).group(1))
    container = re.search(r"\.container\s*\{[^}]*\}", css).group(0)
    container_z = int(re.search(r"z-index:\s*(\d+)", container).group(1))
    assert z > container_z, "过滤区层级必须高于 .container，否则画廊卡片会压住标签面板"
    panel = " ".join(rules(css, ".custom-tags-panel"))
    assert "max-height:" in panel, "窄屏标签面板必须限高，否则会长到视口之外"
    assert "overflow-y: auto" in panel


def test_fontwall_grid_rows_are_max_content() -> None:
    """.ta-fw-grid 有确定高度时浏览器会把 auto 行压成等分，卡片互相重叠。"""
    css = read("static/css/text_art.css")
    grid = re.search(r"\.ta-fw-grid\s*\{[^}]*\}", css).group(0)
    assert "grid-auto-rows: max-content;" in grid
    assert "overflow-y: auto" in grid, "行高放开后必须仍可滚动"


def test_style_card_scroll_is_smooth() -> None:
    src = read("static/js/app.js")
    assert 'previewEl.scrollIntoView({ behavior: "smooth", block: "start" })' in src
    assert 'behavior: "instant"' not in src, "点卡不许瞬间跳转"
