"""T48 — 非排版 UI 回贴：风格卡三段态 / 画廊失败态 / 首屏脉冲 / 文案。

背景：上一轮"UI 回退到线上"把排版回退（应当）连同非排版的状态语言一起撤掉
（不该）。这份用例锁住回贴后的四条契约，防止下一次回退时再被顺手带走：

1. 画廊列表请求失败时，页面不再只剩 2 秒 toast：#galleryError 常驻可见，
   点 #galleryErrorRetry 会重新请求，成功后重新隐藏。
2. 风格卡三段态：请求在途 .is-loading、成功复位、失败落 .is-error、
   上传在途 aria-disabled="true"；CSS 按当前线上排版重写（绝对值字号，
   不引入 --fs-* token 体系）。
3. .terminal.is-flash 首屏脉冲一次，reduced-motion 下退化为静态描边。
4. 首页 .style-note 文案（10 张卡：前 9 张同作品 + 第 10 张自定义字符）。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "templates"
STATIC_JS = REPO_ROOT / "static" / "js"
STATIC_CSS = REPO_ROOT / "static" / "css"
NODE_HARNESS = REPO_ROOT / "tests" / "js_gallery_error_states.mjs"

ASSET_VERSION = "20261031c"
# 上一轮的版本串：拼出来而不是写死，免得这份用例自己成为"残留"的唯一命中
OLD_ASSET_VERSION = "2026" + "1027" + "a"
BACKTICK = chr(96)

VERSIONED_FILES = (
    "templates/404.html",
    "templates/405.html",
    "templates/admin.html",
    "templates/gallery.html",
    "templates/index.html",
    "templates/text_art.html",
    "templates/view_work.html",
    "static/css/tokens.css",
    "static/js/text_art.js",
    "tests/test_t46_csp_no_inline_script.py",
)

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".tmp", ".pytest_cache", "uploads"}


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def read_lines(rel: str) -> list[str]:
    return read(rel).splitlines()


def next_code_line(lines: list[str], start: int) -> str:
    """从 start（含）向下找到第一行有效代码（跳过空行与整行注释）。"""
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*")):
            continue
        return stripped
    return ""


def find_function_line(lines: list[str], signature: str) -> int:
    for i, line in enumerate(lines):
        if signature in line and "function" in line:
            return i
    raise AssertionError(f"找不到函数定义: {signature}")


def strip_js_comments(src: str) -> str:
    """粗剥 // 与 /* */：只用于"这些语法不许出现"的反向断言。

    它会把字符串里的 "//"（例如 URL）一起吃掉，但那只可能让断言更宽松，
    不会凭空造出箭头函数/const/反引号，因此对反向断言是安全的。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


# ── 1. 画廊持久错误态 + 重试 ─────────────────────────────────────────


def test_gallery_error_block_markup() -> None:
    html = read("templates/gallery.html")
    block = re.search(r'<div class="gallery-error" id="galleryError"[^>]*>', html)
    assert block, "gallery.html 缺少 .gallery-error#galleryError 块"
    tag = block.group(0)
    assert 'role="alert"' in tag
    assert re.search(r"\bhidden\b", tag), "错误块必须初始 hidden，由 JS 显形"

    retry = re.search(r'<button[^>]*id="galleryErrorRetry"[^>]*>', html)
    assert retry, "缺少 #galleryErrorRetry 重试按钮"
    btn = retry.group(0)
    assert 'type="button"' in btn
    assert "gallery-page-btn" in btn, "重试按钮复用 .gallery-page-btn（40px 热区）"
    assert "重试" in html and "Retry" in html
    assert "onclick" not in html, "CSP script-src 'self'：不许 on* 事件属性"

    # 错误块必须在空态之后、分页之前：失败时它替换掉网格与分页的位置
    assert html.index('id="galleryEmpty"') < html.index('id="galleryError"')
    assert html.index('id="galleryError"') < html.index('id="galleryPagination"')


def test_gallery_js_failure_path_is_persistent() -> None:
    src = read("static/js/gallery_page.js")
    assert "function setGalleryError(on)" in src
    # HTTP 4xx/5xx 不能被 r.json() 吞掉
    assert 'if (!r.ok) throw new Error("HTTP " + r.status);' in src
    # catch 里落错误态，而不是只 toast 一下
    catch_block = re.search(r"\.catch\(function \(\) \{(.*?)\}\);", src, flags=re.S)
    assert catch_block
    assert "setGalleryError(true)" in catch_block.group(1)
    assert 'toast("加载失败")' not in src, "老的 2 秒 toast 路径必须已经不在了"
    # 重试按钮重新拉列表
    assert re.search(
        r'getElementById\("galleryErrorRetry"\)[\s\S]{0,200}?addEventListener\("click"[\s\S]{0,80}?fetchGallery\(\)',
        src,
    )
    # 成功路径要把错误块收回
    assert re.search(r"function fetchGallery\(\)[\s\S]{0,400}?setGalleryError\(false\)", src)


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不可用")
def test_gallery_error_retry_behaviour() -> None:
    """真跑一遍 gallery_page.js：500 → 错误态；重试 502 → 仍错误态；再重试 200 → 收回。"""
    proc = subprocess.run(
        ["node", str(NODE_HARNESS)], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["failed"] == 0, [c for c in report["checks"] if not c["ok"]]
    passed = {c["name"]: c for c in report["checks"]}
    for name in (
        "error-visible-on-500",
        "grid-cleared-on-500",
        "pagination-cleared-on-500",
        "retry-refetches",
        "error-still-visible-on-502",
        "error-hidden-on-success",
        "grid-gets-cards",
    ):
        assert name in passed, f"harness 少了断言 {name}"
        assert passed[name]["ok"], passed[name]


# ── 2. 风格卡三段态 ─────────────────────────────────────────────────


def test_style_card_tristate_wiring() -> None:
    lines = read_lines("static/js/app.js")
    src = "\n".join(lines)
    for fn in ("styleCardFor", "setCardLoading", "cardRenderOk", "cardRenderFailed", "setCardsDisabled"):
        assert f"function {fn}(" in src, f"app.js 缺少 {fn}"

    # 成功：applyPreview 一进函数就复位 loading / error（所有渲染路径的收敛点）
    apply_at = find_function_line(lines, "function applyPreview(")
    first = next_code_line(lines, apply_at + 1)
    assert "cardRenderOk(S.charset)" in first, first

    # 在途：requestPreview 的两个分支各自亮 loading（本地渲染 / 服务端任务）
    assert len(re.findall(r"^\s*setCardLoading\(charset\);", src, flags=re.M)) == 2

    # 失败：本地 reject 分支 + 服务端 d.error + serverPreview catch，共 3 处
    assert len(re.findall(r"cardRenderFailed\(charset\);", src)) == 3
    assert "if (d.error) { cardRenderFailed(charset);" in src

    # disabled：上传在途开、两条收尾路径关
    assert src.count("setCardsDisabled(true);") == 1
    assert src.count("setCardsDisabled(false);") == 2
    upload_at = find_function_line(lines, "function uploadImages(")
    body = "\n".join(lines[upload_at : upload_at + 40])
    # 预检失败时直接 return，不该先把卡禁用；真正上传才开始锁
    assert body.index("uploadPreflight(") < body.index("setCardsDisabled(true);")


def test_style_card_states_css_matches_js() -> None:
    css = read("static/css/app.css")
    # JS 切的类名必须在 CSS 里有对应规则，否则三段态只是"类名在飘"
    assert ".style-card.is-loading {" in css
    assert ".style-card.is-error {" in css
    assert '.style-card[aria-disabled="true"] {' in css
    assert 'content: "渲染中 / rendering";' in css
    assert 'content: "渲染失败 / failed";' in css
    assert "border-color: var(--red);" in css
    # 追加在原有 .style-card 规则之后（不许整块替换设计版）
    assert css.index(".style-card.is-loading") > css.index(".style-card.selected")
    # 排版基线：文案/状态不许把 token 化的字号体系带回来
    all_css = "".join(p.read_text(encoding="utf-8") for p in STATIC_CSS.glob("*.css"))
    assert not re.search(r"--fs-[a-z]", all_css)
    # 三段态块的字号一律绝对值
    for block in (".style-card.is-loading::before", ".style-card.is-error::before"):
        start = css.index(block)
        rule = css[start : css.index("}", start)]
        if "font-size" in rule:
            assert re.search(r"font-size: \d+px;", rule), rule


def test_cards_disabled_keeps_tabindex_out_of_sequence() -> None:
    """aria-disabled 的卡不能还留在 Tab 序列里（syncStyleCards 得读懂 disabled）。"""
    src = read("static/js/app.js")
    assert 'getAttribute("aria-disabled") === "true"' in src
    assert 'setAttribute("tabindex", (checked && !off) ? "0" : "-1")' in src


# ── 3. 首屏脉冲（原 flashPreview） ──────────────────────────────────


def test_flash_wiring_and_first_paint() -> None:
    src = read("static/js/app.js")
    assert "function flashTerminal(el)" in src
    assert "function flashPreview()" in src
    assert "function flashHeroOnce()" in src
    # 重放动画必须强制重排
    assert "void el.offsetWidth;" in src
    assert 'el.classList.remove("is-flash");' in src
    # 首屏调用一次（IIFE 末尾）
    lines = read_lines("static/js/app.js")
    assert "flashHeroOnce();" in "\n".join(lines[-40:])
    assert src.count("flashHeroOnce();") == 1
    # 切风格时脉冲一次，且**不改**既有滚动交互：基线 scrollIntoView 原样保留
    select_at = find_function_line(lines, "function selectStyleCard(")
    body = "\n".join(lines[select_at : select_at + 120])
    assert "flashPreview();" in body
    baseline_scroll = '    if (previewEl) previewEl.scrollIntoView({ behavior: "smooth", block: "start" });'
    assert baseline_scroll in body, "点卡必须滚到 #preview 顶部（人工测试要求平滑滚动，不许 instant 跳转）"
    assert body.index(baseline_scroll) < body.index("flashPreview();")
    assert "matchMedia" not in body, "selectStyleCard 不该按断点分叉滚动行为"

    css = read("static/css/app.css")
    assert "@keyframes terminal-flash" in css
    assert ".terminal.is-flash {" in css
    assert "animation: terminal-flash 400ms ease-out;" in css
    reduced = re.search(
        r"@media \(prefers-reduced-motion: reduce\) \{\s*\.terminal\.is-flash \{[^}]*animation: none;[^}]*\}", css
    )
    assert reduced, "缺 .terminal.is-flash 的 prefers-reduced-motion 降级块"


def test_untasked_click_stays_idle() -> None:
    """没上传过文件时点卡不该出现 .is-loading —— 没有请求就没有在途状态。

    路径：selectStyleCard 先 showStyleDemo(s)，再 requestPreview(s, {silent: true})；
    requestPreview 的 !S.taskId 守卫在 setCardLoading 之前 return。这是设计如此，
    不是漏挂点：锁住顺序，免得以后有人把它当 bug 补成"空转 loading"。
    """
    lines = read_lines("static/js/app.js")
    src = "\n".join(lines)

    req_at = find_function_line(lines, "function requestPreview(")
    body = "\n".join(lines[req_at : req_at + 50])
    assert "if (!S.taskId) {" in body
    # 任务分支的 setCardLoading 是 4 空格缩进（本地视频分支是 6 空格，语义不同）
    task_loading = [m.start() for m in re.finditer(r"^    setCardLoading\(charset\);(?:\s|$)", body, flags=re.M)]
    assert len(task_loading) == 1
    assert body.index("if (!S.taskId) {") < task_loading[0]

    select_at = find_function_line(lines, "function selectStyleCard(")
    select_body = "\n".join(lines[select_at : select_at + 20])
    assert "if (!S.taskId) showStyleDemo(s);" in select_body
    assert "requestPreview(s, { silent: !S.taskId });" in select_body
    # 本地视频分支（S.localVideo）在 !S.taskId 之前，但它有独立的在途语义，不算漏挂
    assert src.count("    setCardLoading(charset);") == 2


@pytest.mark.xfail(
    strict=True,
    reason="已知缺口（本轮不修）：renderTaskFramesLocally 里 TermifyRender.renderFrames 若同步 throw，"
    "会从 ensureTaskFrames 的 onFulfilled 逃逸成 unhandled rejection，卡片停在 .is-loading。"
    "promise 失败本就有 L1038 catch 回退 serverPreview，加 try/catch→cardRenderFailed 会破坏回退语义；"
    "修好这条用例会从 xfail 变 pass，届时请连同本标记一起删掉。",
)
def test_known_gap_sync_render_throw_leaves_loading() -> None:
    lines = read_lines("static/js/app.js")
    req_at = find_function_line(lines, "function requestPreview(")
    # ensureTaskFrames 的 then 块：从 then( 到紧随的 ", function () {" 之间
    body = "\n".join(lines[req_at : req_at + 60])
    then_at = body.index("ensureTaskFrames(S.taskId).then(")
    then_block = body[then_at : body.index("}, function () {", then_at)]
    assert "catch (err)" in then_block


# ── 4. 文案 ────────────────────────────────────────────────────────


def test_style_note_copy() -> None:
    html = read("templates/index.html")
    assert (
        "以上前 9 张预览是同一件作品（Tethered Spacewalk），只换了字符集；"
        "最后一张「自定义字符」是字符拼出的图形 —— 更多作品，去"
    ) in html
    assert re.search(r'更多作品，去<a href="/gallery">画廊</a>逛逛 →', html)
    # 首页确实有 10 张卡（前 9 + 自定义字符），文案不是凭空的
    assert html.count('class="style-card') == 10


# ── 5. 资源版本串 ──────────────────────────────────────────────────


def test_asset_version_bumped_everywhere() -> None:
    offenders = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in {".html", ".js", ".css", ".py", ".md", ".mjs", ".json"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if OLD_ASSET_VERSION in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"仍有 {OLD_ASSET_VERSION} 残留: {offenders}"

    for rel in VERSIONED_FILES:
        assert ASSET_VERSION in read(rel), f"{rel} 未升到 {ASSET_VERSION}"


# ── 6. 结构与语法约束（CSP / ES5） ─────────────────────────────────


@pytest.mark.parametrize("name", ["gallery.html", "index.html"])
def test_touched_templates_keep_csp_shape(name: str) -> None:
    html = (TEMPLATES / name).read_text(encoding="utf-8")
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert "src=" in tag, f"{name} 出现内联 <script>: {tag}"
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", html), f"{name} 出现 on* 事件属性"


@pytest.mark.parametrize("name", ["app.js", "gallery_page.js"])
def test_touched_js_stays_es5(name: str) -> None:
    code = strip_js_comments((STATIC_JS / name).read_text(encoding="utf-8"))
    assert "=>" not in code, f"{name} 出现箭头函数"
    assert not re.search(r"\bconst\b", code), f"{name} 出现 const"
    assert not re.search(r"\blet\s", code), f"{name} 出现 let"
    assert BACKTICK not in code, f"{name} 出现模板字符串"
