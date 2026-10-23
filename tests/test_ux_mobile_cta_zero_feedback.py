"""手机上首屏没有可做的事 + 无素材态控件零反馈（本 lane 两个可用性缺陷的回归钉）。

背景（改前实测，headless Chrome 375×812）：
  · `#uploadZone` 的 top = 890.67px，首屏（812px）里一个可做的事都没有——只有
    nav 的 5 个链接、一个大标题、一段副标题、一块 324px 高的假终端、以及半截
    「动画工坊」标题。主操作要滑过整屏才出现。
  · `#sizeSlider` 在没有素材（S.frames 为空）时照样能拖：拖动只改数字，松手
    toast 一句「切换尺寸将在上传后应用」，控件既没禁用也没有说明，用户以为坏了。
  · 演示态（没上传素材时点风格卡看示例）标题恒为 ascii：setTitleMeta 只读
    S.charset，而演示路径从不写 S.charset，于是标题停在上一次的字符集上。

修法（都取最小改动，桌面版式一个像素不碰）：
  · templates/index.html 的 .hero 里加一个只读既有按钮样式类的 <button>，
    `#heroUploadCta`，CSS 默认 display: none，只在 ≤640px 的媒体查询里显示
    —— ≥641px 连盒子都不生成；点击直接 uploadZone.click()，与点击上传区同一条
    事件路径（不是复制一份上传逻辑）。
  · 没有帧时 `#sizeSlider` 真 disabled + aria-disabled="true"，并显示
    `#sizeHint` 说明；applyPreview 拿到帧后自动解开。禁用观感靠 `:disabled`。
  · setTitleMeta(charset) 增加可选形参，showStyleDemo 传当前演示卡的字符集。

本文件只钉结构与接线（静态断言），像素级证据留给验收截图。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_CSS = ROOT / "static" / "css" / "app.css"
APP_JS = ROOT / "static" / "js" / "app.js"
INDEX_HTML = ROOT / "templates" / "index.html"

CTA_SELECTOR = ".hero-upload-cta"


def _css():
    return APP_CSS.read_text(encoding="utf-8")


def _js():
    return APP_JS.read_text(encoding="utf-8")


def _html():
    return INDEX_HTML.read_text(encoding="utf-8")


def _block(css_text, selector):
    """取选择器（允许组选择器）的声明块，剥掉块内注释。"""
    m = re.search(r"\n\s*" + re.escape(selector) + r"(?:\s*,\s*[^{]*)?\s*\{(.*?)\}", css_text, re.S)
    assert m, "app.css 里找不到 %s 规则" % selector
    return re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)


def _decl(block, prop):
    m = re.search(r"(?:^|[;{])\s*" + re.escape(prop) + r":\s*([^;]+);", block)
    return m.group(1).strip() if m else None


def _media_blocks(css_text):
    """把每个 @media 的 (条件, 内部文本) 取出来（只处理本文件用到的单层嵌套）。"""
    out = []
    for m in re.finditer(r"@media([^{]+)\{", css_text):
        i = m.end() - 1
        depth = 0
        for j in range(i, len(css_text)):
            if css_text[j] == "{":
                depth += 1
            elif css_text[j] == "}":
                depth -= 1
                if depth == 0:
                    out.append((m.group(1).strip(), css_text[i + 1 : j]))
                    break
    return out


# ── 任务 1：手机首屏主操作 ──────────────────────────────────────────────

def test_mobile_cta_is_a_real_button_in_the_hero():
    """首屏 CTA 必须是 <button>（原生键盘可达），且落在 .hero 内、副标题之后。"""
    html = _html()
    m = re.search(r"<button[^>]*id=\"heroUploadCta\"[^>]*>(.*?)</button>", html, re.S)
    assert m, "index.html 里找不到 #heroUploadCta 按钮"
    tag = m.group(0)
    assert "type=\"button\"" in tag, tag
    assert m.group(1).strip(), "按钮必须有可见文案"
    hero_start = html.index('<section class="hero">')
    hero_end = html.index("</section>", hero_start)
    assert hero_start < m.start() < hero_end, "CTA 没有落在 .hero 里"
    assert html.index("hero-subtitle", hero_start) < m.start(), "CTA 应在副标题之后"


def test_mobile_cta_reuses_an_existing_button_class_only():
    """复用既有按钮样式，且不能借 .download-btn —— 两个脚本都靠
    document.querySelector('.download-btn') 找导出按钮，抢到的是文档里第一个，
    CTA 在 .hero 里会更靠前，导出按钮会直接失去点击处理。"""
    html = _html()
    tag = re.search(r"<button[^>]*id=\"heroUploadCta\"[^>]*>", html).group(0)
    cls = re.search(r'class="([^"]+)"', tag).group(1)
    assert "url-fetch-btn" in cls.split(), cls
    assert "download-btn" not in cls.split(), "CTA 不能复用 .download-btn（会抢走 querySelector）"
    assert _decl(_block(_css(), ".url-fetch-btn"), "background") == "var(--green)"


def test_mobile_cta_is_invisible_above_640px():
    """≥641px 必须完全不参与布局：默认 display:none，只在小屏媒体查询里露出。"""
    css = _css()
    assert _decl(_block(css, CTA_SELECTOR), "display") == "none"
    shown = [
        cond
        for cond, body in _media_blocks(css)
        if re.search(r"\n\s*" + re.escape(CTA_SELECTOR) + r"\s*\{", body)
    ]
    assert shown, "没有任何媒体查询把 CTA 显示出来"
    for cond in shown:
        nums = [int(n) for n in re.findall(r"\d+", cond)]
        assert "max-width" in cond and nums and max(nums) <= 640, cond
    mobile_body = dict(_media_blocks(css))[shown[0]]
    assert _decl(_block("\n" + mobile_body, CTA_SELECTOR), "display") == "flex"


def test_mobile_cta_click_path_equals_upload_zone():
    """点击 CTA 与点上传区完全同一条路径：不复制上传逻辑，只转发一次 click。"""
    js = _js()
    m = re.search(r'var cta = byId\("heroUploadCta"\);(.*?)\n', js, re.S)
    assert m, "app.js 里没有接 #heroUploadCta"
    assert "uploadZone.click()" in js[m.start() : m.start() + 400]


def test_mobile_cta_does_not_suppress_the_focus_ring():
    """新按钮不能自带 outline:none —— 键盘用户要看得见焦点。"""
    css = _css()
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if CTA_SELECTOR in m.group(1) and ":focus" not in m.group(1):
            assert "outline: none" not in m.group(2).replace("  ", " "), m.group(0)[:120]


# ── 任务 2 D3：无素材态滑杆零反馈 ──────────────────────────────────────

def test_size_slider_is_disabled_and_explained_while_frames_are_empty():
    js = _js()
    m = re.search(r"function syncSizeSliderAvailability\(\)\s*\{(.*?)\n  \}", js, re.S)
    assert m, "app.js 里没有 syncSizeSliderAvailability()"
    body = m.group(1)
    assert "slider.disabled = idle" in body, body
    assert 'slider.setAttribute("aria-disabled", "true")' in body, body
    assert "hint.hidden = !idle" in body, body
    assert "var idle = !S.frames.length;" in body, body


def test_size_slider_availability_is_synced_on_boot_and_on_first_frames():
    js = _js()
    assert js.count("syncSizeSliderAvailability();") == 2, "只在 init 与 applyPreview 各挂一次"
    # 挂到 applyPreview（帧到手）里
    m = re.search(r"function applyPreview\(d\)\s*\{(.*?)\n  \}", js, re.S)
    assert m and "syncSizeSliderAvailability();" in m.group(1)
    # 挂到 initSizeSlider 结尾（开局还没有帧）
    m2 = re.search(r"\(function initSizeSlider\(\) \{(.*?)\n  \}\)\(\);", js, re.S)
    assert m2 and "syncSizeSliderAvailability();" in m2.group(1)


def test_size_hint_markup_is_hidden_by_default():
    html = _html()
    m = re.search(r'<div[^>]*id="sizeHint"[^>]*>(.*?)</div>', html, re.S)
    assert m, "index.html 里找不到 #sizeHint"
    tag = re.search(r'<div[^>]*id="sizeHint"[^>]*>', html).group(0)
    assert re.search(r"\bhidden\b", tag), tag
    assert "size-meta" in tag, "复用既有说明文字样式，别新造一套"
    assert "先上传素材" in m.group(1) and "Upload material first" in m.group(1), m.group(1)


def test_disabled_slider_looks_disabled():
    """外观要跟着状态走，否则「拖了没反应」只是换成「看着能拖但拖不动」。"""
    block = _block(_css(), '.size-slider-row input[type="range"]:disabled')
    assert _decl(block, "opacity") == "0.45", block
    assert _decl(block, "cursor") == "not-allowed", block


# ── 任务 2 D1：演示态标题跟随实际字符集 ────────────────────────────────

def test_set_title_meta_accepts_the_charset_being_shown():
    js = _js()
    m = re.search(r"function setTitleMeta\(([^)]*)\)\s*\{(.*?)\n  \}", js, re.S)
    assert m, "找不到 setTitleMeta"
    assert m.group(1).strip() == "charset", m.group(1)
    body = m.group(2)
    assert "var cs = charset || S.charset;" in body, body
    assert '"动画预览 - " + cs + " 风格' in body, body


def test_style_demo_passes_the_demo_charset_to_the_title():
    js = _js()
    m = re.search(r"function showStyleDemo\(style, preferText\)\s*\{(.*?)\n  \}", js, re.S)
    assert m, "找不到 showStyleDemo"
    body = m.group(1)
    assert "setTitleMeta(style);" in body, "演示态标题必须传这张卡实际展示的字符集"
    assert "setTitleMeta();" not in body, "残留的无参调用会把标题再打回 S.charset"


def test_real_generation_path_still_uses_the_task_charset():
    """真实生成态一个字都不变：applyPreview / requestPreview 仍按 S.charset 写标题。"""
    js = _js()
    m = re.search(r"function applyPreview\(d\)\s*\{(.*?)\n  \}", js, re.S)
    assert re.search(r"^\s*setTitleMeta\(\);", m.group(1), re.M), "applyPreview 里应仍是无参调用"
