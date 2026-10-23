"""G-2：「用它做一版」（fork）对文字作品是裸跳转 /text-art，落地空白编辑器。

症状：static/js/view_work_page.js 文字分支 `window.location.href = "/text-art"`，
不带任何参数；而 static/js/text_art.js 里 fork / URLSearchParams / location.search
全部 0 命中——接收侧根本没有预填逻辑。

修法（两侧）：
1. 跳转侧带 ?fork=<作品 id>&font=<字体>&text=<整幅字符画正文>
   （params.frames[0] 就是作品正文；单件可达十几 KB，超 6000 字符不带正文）；
2. 接收侧 text_art.js 新增 applyForkParams()：正文在输入上限内 → 预填 + 选中
   字体 + 生成一次；整幅字符画必然超限 → 把作品画进结果区（而不是留一个点不动
   的生成按钮）；正文没随 URL 来 → 回落已有的 /api/gallery/work/<id>。

约束：CSP 线上是 script-src 'self'（无 unsafe-inline），所以新逻辑只能写在外置
JS 里，不许内联事件 / eval。
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VIEW_JS = ROOT / "static" / "js" / "view_work_page.js"
TA_JS = ROOT / "static" / "js" / "text_art.js"

ART = "\n".join([
    " _          _ _       ",
    "| |__   ___| | | ___  ",
    "| '_ \\ / _ \\ | |/ _ \\ ",
    "| | | |  __/ | | (_) |",
    "|_| |_|\\___|_|_|\\___/ ",
])


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """临时 CWD + 独立任务库 + 独立画廊库/数据目录（与 g1/t46 同口径）。"""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_g2.db"))
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


def _text_id(client, art=ART, font="standard"):
    resp = client.post(
        "/api/gallery/upload-text",
        json={"art": art, "title": "g2-text", "font": font, "tags": []},
    )
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)["id"]


# ── 1. 跳转侧：文字作品的 fork 不再裸跳转 ──────────────────────────
def test_view_js_text_fork_carries_id_font_and_body():
    src = VIEW_JS.read_text(encoding="utf-8")
    assert 'window.location.href = "/text-art";' not in src, "裸跳转必须删掉"
    assert 'window.location.href = "/text-art" + tq;' in src
    assert '"?fork=" + encodeURIComponent(WORK_ID)' in src
    assert '"&font=" + encodeURIComponent(String(params.font))' in src
    assert '"&text=" + encodeURIComponent(_body)' in src
    # 正文取自作品数据本身（params.frames[0]），不是别处
    assert "var _body = textArtString();" in src


def test_view_js_text_fork_has_url_budget():
    """几 KB 的正文不能整段塞 URL：超 6000 字符只带 fork&font。"""
    src = VIEW_JS.read_text(encoding="utf-8")
    assert "var FORK_TEXT_MAX = 6000;" in src
    assert "_body.length <= FORK_TEXT_MAX" in src


# ── 2. 接收侧：text_art.js 读 URL 参数并预填/生成 ──────────────────
def test_text_art_js_reads_fork_params():
    src = TA_JS.read_text(encoding="utf-8")
    assert "new URLSearchParams(window.location.search)" in src
    assert 'q.get("fork")' in src
    assert 'q.get("font")' in src
    assert 'q.get("text")' in src
    assert "applyForkParams(fontsReady);" in src, "初始化里必须真的调用"


def test_text_art_js_prefills_font_and_generates_once():
    src = TA_JS.read_text(encoding="utf-8")
    assert "taInput.value = text;" in src          # 输入框预填
    assert "pickFont(font);" in src                # 字体选中
    assert "convertBtn.click();" in src            # 触发生成一次


def test_text_art_js_falls_back_to_work_api():
    """只有 fork 没有 text（正文超 6000 被省掉）时取已有作品接口，不新增后端。"""
    src = TA_JS.read_text(encoding="utf-8")
    assert '"/api/gallery/work/" + encodeURIComponent(fork)' in src
    assert "p.frames && p.frames[0]" in src
    assert "var FORK_TEXT_MAX = 6000;" in src


def test_fork_font_waits_for_font_tables():
    """字体表是异步 fetch，选字体必须等两套表到齐（否则恒落回 standard）。"""
    src = TA_JS.read_text(encoding="utf-8")
    assert "return Promise.all([figletReady, cjkReady]);" in src
    assert "var fontsReady = loadFonts();" in src


# ── 3. CSP：不内联、不 eval ───────────────────────────────────────
def test_fork_code_stays_csp_clean():
    for path in (VIEW_JS, TA_JS):
        src = path.read_text(encoding="utf-8")
        assert "eval(" not in src, path
        assert "new Function(" not in src, path
        assert not re.search(r'\son[a-z]+\s*=\s*"', src), path


# ── 4. 页面 + 接口实测 ────────────────────────────────────────────
def test_text_art_page_serves_fork_receiver_hooks(client):
    for url in ("/text-art",
                "/text-art?fork=abc123&text=hello&font=standard"):
        resp = client.get(url)
        assert resp.status_code == 200, url
        html = resp.get_data(as_text=True)
        for needle in ('id="taInput"', 'id="taFont"', 'id="taConvertBtn"'):
            assert needle in html, (url, needle)
        # 外置脚本（CSP 下唯一合法形态）
        assert "js/text_art.js" in html


def test_work_api_is_the_fork_fallback_source(client):
    """接收侧回落的数据源确实存在：/api/gallery/work/<id> 带 params.frames[0]。"""
    wid = _text_id(client)
    resp = client.get("/api/gallery/work/" + wid)
    assert resp.status_code == 200
    params = json.loads(resp.data)["params"]
    assert params["kind"] == "text"
    # 服务端按行 rstrip 掉尾随空格，其余逐行一致（正文就是字符画本身）
    stored = [ln.rstrip() for ln in params["frames"][0].split("\n")]
    assert stored == [ln.rstrip() for ln in ART.split("\n")]
    assert params["font"] == "standard"


def test_view_page_fork_button_survives(client):
    """文字作品页仍要露出 fork 按钮，且页面引用的是带预填逻辑的外置 JS。"""
    wid = _text_id(client)
    resp = client.get("/v/" + wid)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="forkBtn"' in html
    assert "js/view_work_page.js" in html


def test_fork_url_contract_roundtrip(client):
    """跳转侧拼出的 URL 形状，接收侧页面照单全收（服务端不做任何预处理）。"""
    wid = _text_id(client)
    from urllib.parse import quote

    url = ("/text-art?fork=" + quote(wid) +
           "&font=" + quote("standard") +
           "&text=" + quote(ART, safe=""))
    resp = client.get(url)
    assert resp.status_code == 200
    assert len(url) < 6000


def test_long_art_is_omitted_from_url_mirror():
    """跳转侧的长度判据：> 6000 字符的正文不带（与 text_art.js 同口径）。"""
    long_art = "x" * 6001
    assert not (len(long_art) <= 6000)
    assert len(ART) <= 6000
