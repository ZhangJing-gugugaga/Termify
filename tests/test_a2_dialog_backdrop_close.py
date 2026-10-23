"""A-2 — 自定义字符弹窗：点遮罩必须能关掉（回归锁）。

背景：templates/index.html 的 <dialog id="customCharsetModal"> 走 showModal()
进 top layer，遮罩由内层 <div id="customCharsetBackdrop"> 画。此前"关窗"只挂在
那个 div 上，遮罩区的命中有两个可能的目标（div / dialog 本身），谁接得住取决
于层叠与指针事件——一条关窗路径被拆成两条判断依据。

这份用例锁住修好后的契约：
1. 关窗判定挂在 <dialog> 自己身上（e.target === ccModal），不挂在里层 div；
2. 里层 div 只画遮罩、不参与命中测试（CSS pointer-events:none），
   所以遮罩区唯一的 e.target 就是 dialog；
3. Esc / 焦点还原不重复实现，仍由 app.js 的 layers/closeLayer 栈负责。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "templates"
STATIC_JS = REPO_ROOT / "static" / "js"
STATIC_CSS = REPO_ROOT / "static" / "css"

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # pragma: no cover
    _HAVE_FLASK = False


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def app_js() -> str:
    return read(STATIC_JS / "app.js")


def app_css() -> str:
    return read(STATIC_CSS / "app.css")


def test_dialog_keeps_inner_backdrop_painter():
    """内层 div 保留（画遮罩），dialog 仍然靠 .termify-modal 铺满视口。"""
    html = read(TEMPLATES / "index.html")
    assert re.search(r'<dialog[^>]*class="termify-modal"[^>]*id="customCharsetModal"', html), \
        "customCharsetModal 不再是 dialog.termify-modal"
    assert re.search(r'<div class="termify-modal-backdrop" id="customCharsetBackdrop">', html), \
        "画遮罩的内层 div 不见了（遮罩色会整块消失）"


def test_close_path_is_attached_to_dialog():
    js = app_js()
    assert re.search(r'if \(ccModal\) ccModal\.addEventListener\("click", function \(e\) \{\s*'
                     r'if \(e\.target === ccModal\) closeCustomCharsetModal\(\);', js), \
        "dialog 上缺少 e.target === ccModal 的关窗路径"


def test_inner_backdrop_div_does_not_handle_clicks():
    js = app_js()
    assert "ccBackdrop" not in js, \
        "里层 backdrop div 又被挂上监听：遮罩区出现两条关窗路径"


def test_inner_backdrop_is_not_hit_testable():
    css = app_css()
    assert re.search(r'dialog\.termify-modal > \.termify-modal-backdrop \{\s*pointer-events: none;', css), \
        "里层 backdrop 仍参与命中测试，遮罩区点到的就不是 dialog"


def test_esc_and_focus_restore_still_owned_by_layer_stack():
    """Esc 只关栈顶一层、关闭时焦点还原——这两件事不许在弹窗自己的代码里重做。"""
    js = app_js()
    assert 'function guardDialogCancel(dlg)' in js
    assert re.search(r'dlg\.addEventListener\("cancel", function \(e\) \{\s*e\.preventDefault\(\);\s*'
                     r'closeLayer\(dlg\);', js), "dialog 的 cancel（Esc）没有交给层栈"
    # 自定义字符弹窗只用 openLayer/closeLayer，不自己写 showModal/close
    assert re.search(r'function openCustomCharsetModal\(\) \{[^}]*openLayer\(ccModal,', js, re.S)
    assert re.search(r'function closeCustomCharsetModal\(\) \{\s*closeLayer\(ccModal\);', js)
    assert 'ccModal.showModal()' not in js
    assert 'ccModal.close()' not in js


@pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装")
def test_homepage_ships_the_wired_dialog():
    from app import app as flask_app
    html = flask_app.test_client().get("/").get_data(as_text=True)
    assert 'id="customCharsetModal"' in html
    assert 'id="customCharsetBackdrop"' in html
    assert re.search(r"js/app\.js\?v=20261030a", html), "app.js 资源版本串没升到 20261030a"
