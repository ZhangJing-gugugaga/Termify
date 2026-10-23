"""F1：/v/ 作品详情页的列数滑杆（#viewSizeSlider）键盘聚焦时没有任何焦点环。

症状：键盘 Tab 到滑杆上，computed outline 是 `none 3px`、没有 box-shadow；同一页
其它控件都吃得到 app.css 顶部的全局 :focus-visible（`solid 2px rgb(0,255,65)`
+ outline-offset 2px）。原因是滑杆的底规则自己也写了 `outline: none`，特异性
(0,2,1) 压过全局 :focus-visible 的 (0,1,0)，文件里谁靠后都救不了——只能加一条
同族但更高的选择器把环重新写回去。

命中的是 `.size-slider-row input[type="range"]`（templates/index.html:343 与
templates/view_work.html:228 两支滑杆的容器都叫 .size-slider-row）。仓库里还有一条
同形状的 `.view-size-row input[type="range"]`（历史重复命名，/v/ 回放页注释里仍
写着它），没有模板命中它，但两者一起覆盖，免得再踩一次同样的坑。

修法：补 `…input[type="range"]:focus-visible`，值必须与全局环完全一致
（2px solid var(--green) + 2px offset）；用 :focus-visible 而不是 :focus，
这样指针点击不会冒出描边（:focus-visible 不匹配鼠标聚焦）。

本文件钉住四件事：环的值与全局一致、特异性真的赢、选择器真的命中模板里的滑杆、
全局规则没有被顺手改掉。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_CSS = ROOT / "static" / "css" / "app.css"
INDEX_HTML = ROOT / "templates" / "index.html"
VIEW_WORK_HTML = ROOT / "templates" / "view_work.html"

SLIDER_BASE = '.size-slider-row input[type="range"]'


def _block(css_text, selector):
    """取选择器（允许组选择器）声明块，剥掉块内注释。"""
    m = re.search(r"\n\s*" + re.escape(selector) + r"(?:\s*,\s*[^{]*)?\s*\{(.*?)\}", css_text, re.S)
    assert m, "app.css 里找不到 %s 规则" % selector
    return re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)


def _decl(block, prop):
    m = re.search(r"(?:^|[;{])\s*" + re.escape(prop) + r":\s*([^;]+);", block)
    return m.group(1).strip() if m else None


def _rules_containing(css_text, needle):
    """(选择器列表, 声明块) —— 线性扫描每条规则，避免回溯恶化的正则。"""
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css_text):
        if needle in m.group(2):
            selectors = [s.strip() for s in m.group(1).split(",") if s.strip()]
            out.append((selectors, m.group(2)))
    return out


def _specificity(selector):
    """(id, class-ish, type) 三元组：.x / [attr] / :pseudo 都算 class 级。"""
    s = re.sub(r"::[\w-]+", "", selector)          # 伪元素不进特异性
    ids = len(re.findall(r"#", s))
    classes = len(re.findall(r"\.[\w-]+", s)) + len(re.findall(r"\[[^\]]*\]", s)) \
        + len(re.findall(r"(?<!:):[\w-]+", s))
    types = len(re.findall(r"(?<![\w.#:\[])([a-z]+)(?=[\s>+~,\[]|$)", s.split("[")[0]))
    return (ids, classes, types)


def _client():
    from app import app as flask_app
    return flask_app.test_client()


def test_global_ring_values_are_the_reference():
    """参照系：全局 :focus-visible 就是 2px solid var(--green) + 2px offset。"""
    block = _block(APP_CSS.read_text(encoding="utf-8"), ":focus-visible")
    assert _decl(block, "outline") == "2px solid var(--green)", block
    assert _decl(block, "outline-offset") == "2px", block


def test_override_repeats_the_global_ring_exactly():
    """滑杆的键盘环必须和全局一模一样，不能自创一套。"""
    css = APP_CSS.read_text(encoding="utf-8")
    block = _block(css, SLIDER_BASE + ":focus-visible")
    global_block = _block(css, ":focus-visible")
    assert _decl(block, "outline") == _decl(global_block, "outline"), block
    assert _decl(block, "outline-offset") == _decl(global_block, "outline-offset"), block
    assert _decl(block, "box-shadow") is None, "环靠 outline，别再加 box-shadow 顶替"


def test_override_uses_focus_visible_not_focus():
    """指针点击不匹配 :focus-visible —— 用 :focus 会把鼠标用户也画上描边。"""
    css = APP_CSS.read_text(encoding="utf-8")
    slider_selectors = [
        s
        for selectors, _block_text in _rules_containing(css, "outline-offset: 2px;")
        for s in selectors
        if 'input[type="range"]' in s
    ]
    assert slider_selectors, "找不到滑杆的焦点环规则"
    for sel in slider_selectors:
        assert sel.endswith(":focus-visible"), sel
    assert (SLIDER_BASE + ":focus {") not in css, "别用裸 :focus 覆盖"
    assert (SLIDER_BASE + ":focus-visible") in css


def test_override_outranks_the_base_rule_that_resets_outline():
    """底规则的 outline:none 是 (0,2,1)，覆盖必须是同一条件的更高特异性。"""
    css = APP_CSS.read_text(encoding="utf-8")
    base = _block(css, SLIDER_BASE)
    assert _decl(base, "outline") == "none", "底规则变了，先确认这次修法是否还成立"
    override = _block(css, SLIDER_BASE + ":focus-visible")
    assert "!important" not in override, "别用 !important 抢权重"
    assert _specificity(SLIDER_BASE + ":focus-visible") > _specificity(SLIDER_BASE)


def test_override_covers_the_class_the_templates_really_use():
    """#viewSizeSlider 的父容器叫 .size-slider-row —— 只改 .view-size-row 修不到线上。"""
    css = APP_CSS.read_text(encoding="utf-8")
    for path in (INDEX_HTML, VIEW_WORK_HTML):
        # 主页滑杆 #sizeSlider、/v/ 回放页滑杆 #viewSizeSlider —— 同一支组件两处落地
        html = path.read_text(encoding="utf-8")
        m = re.search(
            r'<div class="([\w-]+)">\s*<input type="range" id="(?:view)?[sS]izeSlider"', html)
        assert m, "%s 里没找到包着滑杆的容器" % path.name
        wrapper = m.group(1)
        assert wrapper == "size-slider-row", (path.name, wrapper)
        assert "." + wrapper + ' input[type="range"]:focus-visible' in css, (
            "%s 用的 .%s 不在焦点环覆盖里" % (path.name, wrapper))


def test_served_app_css_carries_the_override():
    """真实发出去的 app.css 里必须带这条覆盖（防模板/静态路径错位）。"""
    resp = _client().get("/static/css/app.css")
    assert resp.status_code == 200
    block = _block(resp.get_data(as_text=True), SLIDER_BASE + ":focus-visible")
    assert _decl(block, "outline") == "2px solid var(--green)"
    assert _decl(block, "outline-offset") == "2px"
