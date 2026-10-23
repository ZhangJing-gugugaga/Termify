"""A-1：首页预览区的演示 GIF 加载失败时不再静默全黑。

缺陷形态（诊断结论，static/js/app.js:1721-1743）：
  · 点风格卡时 preview.textContent = "" 清掉模板直出的 █/░ 字画，再插入卡内
    演示 GIF —— 这是正常态（线上四张卡实测 312×234 都正常显示）；
  · 但 onload 与 onerror 共用同一个 handler，onerror 只调 previewSkeleton(false)
    摘掉骨架：图没来 = 字画已清空 + 骨架已摘 + 只留一个 alt 框，终端整块全黑，
    零文案零提示。

修法：
  · onerror 与 onload 拆开，onerror 里摘掉破图节点并写入 .preview-fallback 占位；
  · 占位节点绝对定位、四边贴合终端体内边距 —— 盒子不超过 #animPreview 的
    padding box，对 scrollWidth/scrollHeight 零贡献，所以 fitTerminalFontSize()
    与 fitStaticPreviewFont() 的实测宽度量尺都看不到它；
  · 另加一行常驻小字 #previewDemoHint，位置在 .terminal-body 之外（.terminal
    之外），不进任何字号测量。

没有 DOM 测试环境（不引 jsdom），这里用源码 / 模板 / CSS 静态断言锁定形态，
再加一条 test_client GET / 确认提示节点真的被服务端渲染出来。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "static" / "js" / "app.js"
APP_CSS = ROOT / "static" / "css" / "app.css"
INDEX_HTML = ROOT / "templates" / "index.html"

FALLBACK_TEXT = "演示动图没能加载出来"
HINT_ID = "previewDemoHint"
HINT_TEXT = "点风格卡看到的是演示动图，上传素材后这里预览你自己的作品。"


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _show_block() -> str:
    """showStyleDemo 的函数体（与 test_t42 同一切法，函数尾是 2 空格缩进的花括号）。"""
    src = _js()
    i = src.index("function showStyleDemo(")
    j = src.index("\n  }\n", i)
    return src[i:j]


def _css_block(selector: str) -> str:
    css = APP_CSS.read_text(encoding="utf-8")
    m = re.search(r"\n\s*" + re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    assert m, "app.css 里找不到 %s 规则" % selector
    return m.group(1)


def _client():
    from app import app as flask_app
    return flask_app.test_client()


# ── (c) 失败路径：onerror 里必须写回落占位，不能只摘骨架 ──

def test_onerror_no_longer_only_removes_skeleton() -> None:
    """旧的 onload/onerror 合并写法必须消失 —— 它正是"全黑零提示"的成因。"""
    blk = _show_block()
    assert "art.onload = art.onerror" not in blk
    assert "art.onload = function () { previewSkeleton(false); };" in blk
    assert "art.onerror = function () {" in blk
    # 两条路径各自摘骨架，各一次
    assert blk.count("previewSkeleton(false)") == 2


def test_onerror_appends_readable_fallback() -> None:
    """onerror 分支写入带文案的占位节点，并摘掉破图（不留 alt 框）。"""
    blk = _show_block()
    err_at = blk.index("art.onerror = function () {")
    err_body = blk[err_at:]
    assert 'miss.className = "preview-fallback";' in err_body
    assert 'miss.setAttribute("data-preview-fallback", "");' in err_body
    assert FALLBACK_TEXT in err_body
    assert "preview.appendChild(miss);" in err_body
    assert "art.parentNode.removeChild(art);" in err_body


def test_fallback_created_after_preview_is_cleared() -> None:
    """占位节点是 #animPreview 的子节点，下次点击的 textContent="" 会顺手清掉它，
    不会残留到下一张卡。"""
    blk = _show_block()
    clear_at = blk.index('preview.textContent = "";')
    assert clear_at < blk.index('miss.className = "preview-fallback";')


def test_fallback_css_does_not_grow_preview_box() -> None:
    """占位节点靠绝对定位 + 四边贴合，绝不改变 #animPreview 的盒子尺寸。"""
    blk = _css_block(".preview-fallback")
    assert "position: absolute;" in blk
    for side in ("top: 0;", "right: 0;", "bottom: 0;", "left: 0;"):
        assert side in blk, "缺 %s —— 四边不贴合就会撑出滚动区" % side
    assert "overflow: hidden;" in blk
    # 终端体是 white-space:pre，占位文案要能换行才读得全
    assert "white-space: normal;" in blk


def test_font_measurement_never_sees_fallback_or_hint() -> None:
    """两条字号量尺（fitTerminalFontSize / fitStaticPreviewFont）只量终端体自身，
    不许引用回落占位或提示条。"""
    js = _js()
    for fn in ("function fitTerminalFontSize()", "function fitStaticPreviewFont()"):
        i = js.index(fn)
        body = js[i:i + 2000]
        assert "preview-fallback" not in body
        assert "preview-hint" not in body
    # 量尺本身仍在原点：两个 scrollWidth 回缩循环各一处
    assert js.count("tb.scrollWidth > tb.clientWidth") == 2


# ── (b) 常驻提示条：在 .terminal-body 之外 ──

def test_hint_node_sits_outside_terminal_body() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    body_at = html.index('class="terminal-body" id="animPreview"')
    hint_at = html.index(HINT_ID)
    controls_at = html.index('class="animation-controls"')
    # 从 terminal-body 开标签到 controls 开标签这一段里含 body 的收尾标签，
    # 提示节点不在其中 = 它不在 .terminal-body 里面。
    assert not (body_at < hint_at < controls_at), "提示条还在 .terminal-body 里"
    assert hint_at > controls_at
    assert HINT_TEXT in html


def test_hint_css_is_a_quiet_line() -> None:
    blk = _css_block(".preview-hint")
    assert "font-size: 11px;" in blk
    assert "var(--text-dim)" in blk
    assert "font-family: var(--font-mono);" in blk


def test_home_page_renders_hint_node() -> None:
    html = _client().get("/").get_data(as_text=True)
    assert 'id="' + HINT_ID + '"' in html
    assert HINT_TEXT in html
