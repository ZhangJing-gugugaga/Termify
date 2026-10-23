"""G-1：/v/<id> 详情页那 1/3 的幽灵控件整块删掉，而不是继续 display:none 供着。

症状（/v/IJjRwMnl 实测）：DOM 297 节点、隐藏叶子 98、隐藏可交互控件 34 个、
服务端 HTML 27.7KB。成因是详情页模板从首页整段复制过来，再在 JS 里逐块
display:none：风格卡 + 字符梯、尺寸滑块、配色面板、导出按钮、导出弹窗、
嵌入弹窗、举报弹窗、Tweaks 面板全部照样进 DOM。

修法（能删就删，不留 display:none 的尸体）：
1. 文字作品（params.kind == "text"）页：风格/字符梯、尺寸卡、配色卡、导出
   按钮、导出弹窗、嵌入弹窗由模板条件渲染，服务端直接不输出；
2. Tweaks 面板在详情页没有任何使用场景（F-2 已在画廊页同样处理），
   按钮 + 面板 + view_work_page.js 的整套绑定整块删除；
3. 只补必要的 null 保护（字符梯“应用”按钮随字符梯一起去掉）。
.tweaks-* / .tweak-* / .style-card / .palette-btn 等与首页共用的 CSS 一律保留。
"""
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
VIEW_JS = ROOT / "static" / "js" / "view_work_page.js"
APP_CSS = ROOT / "static" / "css" / "app.css"

TEXT_ONLY_REMOVED = (
    'id="styles"',
    'id="viewRampRow"',
    'id="viewRampInput"',
    'id="viewRampApply"',
    'id="viewSizeCard"',
    'id="viewSizeSlider"',
    'id="animPaletteCard"',
    'id="viewPaletteRow"',
    'id="exportAnimBtn"',
    'id="exportBackdrop"',
    'id="exportConfirmBtn"',
    'id="embedSnippetBackdrop"',
    'id="embedSnippetText"',
)
TWEAKS_REMOVED = (
    'id="tweaksToggle"',
    'id="tweaksPanel"',
    'id="tweaksClose"',
    "data-tweak=",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """临时 CWD + 独立任务库 + 独立画廊库/数据目录（与 t46 同口径）。"""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_g1.db"))
    monkeypatch.delenv("TERMIFY_ADMIN_PWD", raising=False)

    import app as app_mod
    from termify.gallery import GalleryDB
    from termify.taskstore import cache_clear_all, get_store, reset_store_for_tests

    cache_clear_all()
    reset_store_for_tests()
    app_mod._RL_LOG.clear()
    get_store().set_sweep_hook(app_mod._sweep_stale_frame_dirs)

    gdir = tmp_path / "gallerydata"
    gdir.mkdir(exist_ok=True)
    monkeypatch.setattr(app_mod, "GALLERY_DATA_DIR", str(gdir))
    gdb = GalleryDB(str(tmp_path / "gallery" / "g.db"))
    gdb.init_db()
    monkeypatch.setattr(app_mod, "GALLERY_DB", gdb)
    yield
    cache_clear_all()
    reset_store_for_tests()
    app_mod._RL_LOG.clear()


@pytest.fixture
def client():
    from app import app

    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _png_bytes(w=8, h=4):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 200, 30)).save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def _anim_id(client):
    resp = client.post(
        "/api/gallery/upload",
        data={
            "source": (io.BytesIO(_png_bytes()), "g1.png"),
            "title": "g1-anim",
            "params": json.dumps({"charset": "blocks", "width": 80, "height": 24}),
            "tags": "[]",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)["id"]


def _text_id(client):
    art = "\n".join(["@@@@@@@@@@", "@########@", "@# G1 #@", "@########@", "@@@@@@@@@@"])
    resp = client.post(
        "/api/gallery/upload-text",
        json={"art": art, "title": "g1-text", "font": "mono", "tags": []},
    )
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)["id"]


@pytest.fixture(scope="module")
def pages():
    """(文字作品页 HTML, 动画作品页 HTML)。"""
    from app import app

    app.config["TESTING"] = True
    with app.test_client() as client:
        text_id = _text_id(client)
        anim_id = _anim_id(client)
        text_resp = client.get("/v/" + text_id)
        anim_resp = client.get("/v/" + anim_id)
    assert text_resp.status_code == 200
    assert anim_resp.status_code == 200
    return text_resp.get_data(), anim_resp.get_data()


# ── 文字作品页：幽灵块整块不存在 ────────────────────────────────
def test_text_work_page_has_no_ghost_control_blocks(pages):
    html = pages[0].decode("utf-8")
    for needle in TEXT_ONLY_REMOVED:
        assert needle not in html, needle
    assert ".style-card" not in html


def test_text_work_page_size_stays_under_12kb(pages):
    size = len(pages[0])
    assert size <= 12 * 1024, size


def test_no_tweaks_panel_on_any_detail_page(pages):
    for html in (p.decode("utf-8") for p in pages):
        for needle in TWEAKS_REMOVED:
            assert needle not in html, needle


def test_text_work_page_keeps_its_real_features(pages):
    """点赞 / fork / 复制链接 / 举报 / 文字工具都得留着。"""
    html = pages[0].decode("utf-8")
    for needle in ('id="likeBtn"', 'id="forkBtn"', 'id="copyLinkBtn"', 'id="reportBtn"',
                   'id="reportModal"', 'id="reportSubmitBtn"', 'id="textTools"',
                   'id="textCopyBtnV"', 'id="textDownloadBtnV"'):
        assert needle in html, needle
    # 文字工具这一页是可见功能，不能带 hidden
    assert '<div id="textTools">' in html


# ── 动画作品页：真实可用的块一律保留（不能为了删幽灵把功能删了）──
def test_anim_work_page_keeps_working_blocks(pages):
    html = pages[1].decode("utf-8")
    for needle in ('id="styles"', 'id="viewRampRow"', 'id="viewSizeCard"',
                   'id="viewSizeSlider"', 'id="animPaletteCard"', 'id="viewPaletteRow"',
                   'id="exportAnimBtn"', 'id="exportBackdrop"', 'id="embedSnippetBackdrop"'):
        assert needle in html, needle
    assert html.count('class="style-card"') == 10
    # 文字工具在动画作品页反而没有入口，也不该留在 DOM 里
    assert 'id="textTools"' not in html


def test_anim_work_page_has_no_tweaks(pages):
    html = pages[1].decode("utf-8")
    for needle in TWEAKS_REMOVED:
        assert needle not in html, needle


# ── JS：挂不上的绑定删干净，剩下的对 null 有保护 ────────────────
def test_view_js_has_no_tweaks_bindings():
    src = VIEW_JS.read_text(encoding="utf-8")
    for needle in ("tweaksToggle", "tweaksPanel", "tweaksClose", "bindTweak", "data-tweak"):
        assert needle not in src, needle


def test_view_js_drops_the_display_none_dead_code():
    src = VIEW_JS.read_text(encoding="utf-8")
    assert "_stylesSection" not in src
    assert '_paletteCard.style.display = "none"' not in src


def test_view_js_guards_the_ramp_apply_button():
    """字符梯“应用”按钮随字符梯一起去掉，绑定必须对 null 有保护。"""
    src = VIEW_JS.read_text(encoding="utf-8")
    assert 'var rampApply = document.getElementById("viewRampApply")' in src
    assert "if (rampInput && rampApply)" in src
    assert 'document.getElementById("viewRampApply").addEventListener' not in src


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 做语法检查")
def test_view_js_still_parses():
    r = subprocess.run(["node", "--check", str(VIEW_JS)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ── CSS：与首页共用的规则一律保留 ───────────────────────────────
def test_shared_css_is_retained():
    css = APP_CSS.read_text(encoding="utf-8")
    for sel in (".tweaks-toggle", ".tweaks-panel", ".tweak-group", ".tweak-opt",
                ".style-card", ".palette-btn", ".view-ramp-input", ".export-backdrop"):
        assert sel in css, sel


def test_index_page_keeps_its_tweaks_and_style_cards():
    from app import app

    with app.test_client() as c:
        html = c.get("/").get_data(as_text=True)
    assert 'id="tweaksPanel"' in html
    assert html.count('class="style-card') >= 9
