"""G-2 修正：fork URL 恒定短小，正文一律由作品接口取回。

现场：线上作品 /v/CWhZ2QHX 的「用它做一版」把整幅正文拼进
`/text-art?fork=<id>&text=<正文>&font=<字体>`，URL 实测 12298 字节，
超过约 4096 的请求行上限，被前置代理回裸 400（二分：4079 字节 200 /
4088 字节 400）。接收侧 text_art.js 本来就有第二条来源
（GET /api/gallery/work/<id> 取 params.frames[0]），URL 里那段正文纯属冗余。

修法（两侧）：
1. static/js/view_work_page.js：fork 只带 ?fork=<id>&font=<字体>，正文一个字
   都不进 URL，连拼 URL 时都不再读它（FORK_TEXT_MAX 死代码一并删掉）；
2. static/js/text_art.js：applyForkParams 一律走作品接口，删掉 ?text= 读取分支；
   接口 404 / 网络失败 / 非 JSON → showEmpty() + 明确 toast，不静默、不崩。

可见性语义不放宽：正文来源只有接口，判定仍是接口既有的（不存在/已删除 404）。

行为断言由 tests/js_fork_short_url.mjs 在 node 沙箱里跑（切真源码 + 拦截
window.location.href + 编排 fetch），这里另跑 Flask 往返与结构契约。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEW_JS = REPO_ROOT / "static" / "js" / "view_work_page.js"
TA_JS = REPO_ROOT / "static" / "js" / "text_art.js"
STATIC_JS = REPO_ROOT / "static" / "js"
NODE_HARNESS = REPO_ROOT / "tests" / "js_fork_short_url.mjs"

URL_BUDGET = 512


def long_art(rows: int = 120, cols: int = 100) -> str:
    """与线上那件作品同量级的整幅字符画（入库红线内：MAX_ART_ROWS=120 / COLS=440）。"""
    return "\n".join(
        "".join(chr(97 + ((r * 7 + c * 3) % 26)) for c in range(cols))
        for r in range(rows)
    )


def fork_url(work_id: str, font: str) -> str:
    """跳转侧拼出的 URL 形状（view_work_page.js fork 分支）。"""
    return "/text-art?fork=" + quote(work_id, safe="") + "&font=" + quote(font, safe="")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """临时 CWD + 独立任务库 + 独立画廊库/数据目录（与 g1/t46 同口径）。"""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_g2u.db"))
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


def _upload_text(client, art: str, font: str = "standard") -> str:
    resp = client.post(
        "/api/gallery/upload-text",
        json={"art": art, "title": "g2u-long", "font": font, "tags": []},
    )
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)["id"]


# ── 1. 跳转侧：URL 里不带正文 ──────────────────────────────────────
def test_view_js_fork_url_carries_only_short_params():
    src = VIEW_JS.read_text(encoding="utf-8")
    assert 'window.location.href = "/text-art" + tq;' in src
    assert '"?fork=" + encodeURIComponent(WORK_ID)' in src
    assert '"&font=" + encodeURIComponent(String(params.font))' in src
    # 正文不再进 URL：拼接串里没有 text 参数
    assert '"&text="' not in src
    assert "encodeURIComponent(_body)" not in src


def test_view_js_fork_drops_dead_body_budget():
    """正文不再过 URL，FORK_TEXT_MAX 与那次读取一起成了死代码。"""
    src = VIEW_JS.read_text(encoding="utf-8")
    assert "FORK_TEXT_MAX" not in src
    assert "var _body = textArtString();" not in src


def test_text_art_string_helper_is_still_used_elsewhere():
    """只删死代码：textArtString() 在别处（导出/下载）仍在用，不许被顺手带走。"""
    src = VIEW_JS.read_text(encoding="utf-8")
    assert "function textArtString() {" in src
    assert src.count("textArtString()") >= 3


# ── 2. 接收侧：只认作品接口 ───────────────────────────────────────
def test_text_art_js_has_no_url_text_branch():
    src = TA_JS.read_text(encoding="utf-8")
    assert 'q.get("fork")' in src
    assert 'q.get("font")' in src
    assert 'q.get("text")' not in src, "URL 自带正文的第二来源必须删掉（可见性只认接口）"
    assert "FORK_TEXT_MAX" not in src
    assert "applyForkParams(fontsReady);" in src


def test_text_art_js_fetches_work_api_as_the_only_body_source():
    src = TA_JS.read_text(encoding="utf-8")
    assert '"/api/gallery/work/" + encodeURIComponent(fork)' in src
    assert "p.frames && p.frames[0]" in src


def test_text_art_js_degrades_on_api_failure():
    """404/私有/网络失败：空态 + 明确提示，不许静默失败、不许抛。"""
    src = TA_JS.read_text(encoding="utf-8")
    assert "if (!r.ok) throw new Error(" in src, "非 2xx 必须走降级分支"
    assert "showEmpty();" in src
    assert "读取失败" in src
    # 降级分支里没有任何 showArt/showForkedArt 调用
    catch_start = src.index(".catch(function () {", src.index("function loadForkedWork("))
    catch_block = src[catch_start : src.index("});", catch_start)]
    assert "showArt" not in catch_block
    assert "showForkedArt" not in catch_block


def test_text_param_has_no_producer_left_anywhere():
    """?text= 这条链整条删除后，前端不许再有任何一处生产它。"""
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in STATIC_JS.rglob("*.js")
        if '"&text="' in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


# ── 3. Flask 往返：长正文作品的 fork URL 仍在预算内 ────────────────
def test_long_text_work_fork_url_stays_short(client):
    art = long_art()
    assert len(art) > 6000, "正文长度要超过上一轮的 6000 字符上限"
    wid = _upload_text(client, art)

    # 作品接口带回同一份正文（接收侧唯一数据源，服务端按行 rstrip 尾随空格）
    detail = client.get("/api/gallery/work/" + wid)
    assert detail.status_code == 200
    stored = json.loads(detail.data)["params"]["frames"][0]
    assert len(stored) >= 6000
    assert [ln.rstrip() for ln in stored.split("\n")] == [ln.rstrip() for ln in art.split("\n")]

    url = fork_url(wid, "standard")
    assert "text=" not in url
    assert len(url) < URL_BUDGET, f"fork URL 太长：{len(url)} 字节"

    page = client.get(url)
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for needle in ('id="taInput"', 'id="taFont"', "js/text_art.js"):
        assert needle in html, needle

    # 对照：修复前的形状在同一份正文上会顶破约 4096 的请求行上限
    legacy = url + "&text=" + quote(art, safe="")
    assert len(legacy) > 4096, f"旧形状应超请求行上限，实测 {len(legacy)}"


def test_deleted_work_api_returns_404_for_frontend_degrade(client):
    """前端降级依赖的状态码：作品不存在/已删除 → 404。"""
    resp = client.get("/api/gallery/work/doesnotexist")
    assert resp.status_code == 404
    assert json.loads(resp.data)["error"] == "Work not found"


def test_private_work_body_only_comes_from_the_api(client):
    """可见性不放宽：私有作品的正文只能经接口判定拿到，URL 不再能自带正文。"""
    art = long_art(rows=6, cols=40)
    resp = client.post(
        "/api/gallery/upload-text",
        json={"art": art, "title": "g2u-private", "font": "standard", "is_private": 1},
    )
    assert resp.status_code == 200, resp.data
    wid = json.loads(resp.data)["id"]

    import app as app_mod

    assert app_mod.GALLERY_DB.get_work(wid)["is_private"] == 1
    # 私有作品不进公开列表，但详情接口仍按既有判定直链可取（本轮没有改动这条语义）
    listed = json.loads(client.get("/api/gallery/list").data)["items"]
    assert wid not in [it["id"] for it in listed]
    detail = client.get("/api/gallery/work/" + wid)
    assert detail.status_code == 200
    assert json.loads(detail.data)["params"]["frames"][0]
    # 不存在的 id 一律 404 —— 前端拿到 404 就走空态 + 提示，不会退化成假作品
    assert client.get("/api/gallery/work/" + wid + "zz").status_code == 404


# ── 4. 真行为：node 沙箱跑两侧源码 ─────────────────────────────────
@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 跑行为断言")
def test_fork_url_and_receiver_behaviour():
    proc = subprocess.run(
        ["node", str(NODE_HARNESS)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    failed = [c for c in report["checks"] if not c["ok"]]
    assert not failed, failed
    assert report["failed"] == 0
    names = [c["name"] for c in report["checks"]]
    for needle in ("跳转 URL 上界 < 512 字节", "打开短 URL 后编辑器拿到原字画（逐字符相同）", "接口 404 → 空态 + 提示，不静默"):
        assert needle in names, names
