"""T45 — 源帧清单的短缓存 + /text-art 交互期零 layout-shift 的占位契约。

两处小修，各自锁一条可测量的事实：

1. GET /api/gallery/source-frames/<id>/manifest 每次都 stat 首末帧（实测
   15.8ms），却一个缓存头都没有 → 重复打开作品页必然重算。给
   `public, max-age=60`：够短（作品重新生成后 version 会变，客户端最迟
   1 分钟内换新），也不用 immutable/长缓存。清单本身仍是 200，不做 304
   逻辑；单帧端点（immutable + ETag）与旧整包端点的响应头一律不动。

2. /text-art 首次生成后交互期唯一的 layout-shift（0.0257 @ 2594ms）来自
   "字体墙插进来把下方内容顶下去"。修法是让可切换区块首屏就是**真内容**
   （结果行写占位文本 + 按钮 disabled、字体墙用默认样例渲染）再配固定框高，
   而不是用 [hidden]+visibility:hidden 预留空白（那样首屏多出 567px 死区，
   已否决）。下面几条断言盯住这几个不变量，免得后续改样式时把常驻内容退回
   成隐藏占位——本文件只做结构守卫，几何实测在浏览器里跑。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from PIL import Image

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # pragma: no cover
    _HAVE_FLASK = False

pytestmark = pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装")

NFRAMES = 4
FRAME_W, FRAME_H = 40, 24
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t45.db"))
    monkeypatch.delenv("TERMIFY_ADMIN_PWD", raising=False)

    from termify.taskstore import cache_clear_all, reset_store_for_tests

    cache_clear_all()
    reset_store_for_tests()
    import app as app_mod
    from termify import gallery as gallery_mod

    gdata = tmp_path / "gallery_data"
    gdata.mkdir()
    monkeypatch.setattr(app_mod, "GALLERY_DATA_DIR", str(gdata))
    db = gallery_mod.GalleryDB(str(gdata / "termify.db"))
    db.init_db()
    monkeypatch.setattr(app_mod, "GALLERY_DB", db)
    app_mod._SRC_FRAME_CACHE.clear()
    monkeypatch.setattr(app_mod, "_SRC_FRAME_CACHE_BYTES", 0)
    app_mod._RL_LOG.clear()
    yield db
    cache_clear_all()
    reset_store_for_tests()


def _frames_dir(tmp_path):
    fd = tmp_path / "frames"
    fd.mkdir()
    for i in range(NFRAMES):
        shade = 20 + i * 50
        Image.new("RGB", (FRAME_W, FRAME_H), (shade, 255 - shade, shade // 2)).save(
            fd / ("frame_%05d.png" % (i + 1)))
    return fd


@pytest.fixture
def video_work(_isolate_env, tmp_path):
    work_id = "t45video0001"
    _isolate_env.insert_work({
        "id": work_id,
        "title": "t45 work",
        "description": "",
        "tags": json.dumps(["video"]),
        "author": "tester",
        "source_path": "/nonexistent/source.png",
        "thumbnail_path": "/nonexistent/thumb.png",
        "og_path": "/nonexistent/og.png",
        "params_json": json.dumps({"kind": "video", "interval": 0.1,
                                   "frames_dir": str(_frames_dir(tmp_path))}),
        "is_private": 0,
        "admin_token": "tok-" + work_id,
        "created_at": "2026-01-01T00:00:00",
        "ip": "203.0.113.10",
    })
    return work_id


def _client():
    from app import app as flask_app
    return flask_app.test_client()


# --- 1. 清单短缓存 ---------------------------------------------------------

def test_manifest_has_short_public_cache(video_work):
    r = _client().get("/api/gallery/source-frames/%s/manifest" % video_work)
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "public, max-age=60"
    # 短缓存只能靠 max-age 过期：不许带 immutable / 长 max-age
    assert "immutable" not in r.headers["Cache-Control"]


def test_manifest_stays_200_on_repeat_requests(video_work):
    """两次连续请求都必须是 200 且整包重发（不做 304 协商）。"""
    c = _client()
    r1 = c.get("/api/gallery/source-frames/%s/manifest" % video_work)
    r2 = c.get("/api/gallery/source-frames/%s/manifest" % video_work)
    assert (r1.status_code, r2.status_code) == (200, 200)
    assert r1.get_data() == r2.get_data()
    # 没有 ETag / Last-Modified 就不会变成条件请求
    assert "ETag" not in r1.headers
    assert "Last-Modified" not in r1.headers
    assert r2.headers["Cache-Control"] == "public, max-age=60"


# --- 2. 帧端点 / 旧整包端点的头不许被顺手改掉 ------------------------------

def test_frame_endpoint_keeps_immutable_cache(video_work):
    r = _client().get("/api/gallery/source-frames/%s/frame/0" % video_work)
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    etag = r.headers["ETag"]
    assert etag
    r2 = _client().get("/api/gallery/source-frames/%s/frame/0" % video_work,
                       headers={"If-None-Match": etag})
    assert r2.status_code == 304 and r2.get_data() == b""


def test_legacy_blob_endpoint_headers_unchanged(video_work):
    """旧的整包端点保持原样：没有任何缓存头，字节长度跨请求稳定。"""
    c = _client()
    r = c.get("/api/gallery/source-frames/%s" % video_work)
    assert r.status_code == 200
    assert "Cache-Control" not in r.headers
    assert "ETag" not in r.headers
    assert r.mimetype == "application/json"
    n1 = len(r.get_data())
    n2 = len(c.get("/api/gallery/source-frames/%s" % video_work).get_data())
    assert n1 == n2 > 0


# --- 3. 前端占位契约（CLS 守卫） -------------------------------------------

def _css():
    return (ROOT / "static" / "css" / "text_art.css").read_text(encoding="utf-8")


def _html():
    return (ROOT / "templates" / "text_art.html").read_text(encoding="utf-8")


def _js():
    return (ROOT / "static" / "js" / "text_art.js").read_text(encoding="utf-8")


def test_result_blocks_are_resident_not_reserved():
    """结果行/字体墙靠「首屏真内容」零位移，不许再靠 [hidden] 预留空白。

    上一版把 #taResultMeta / #taFontwall 做成 visibility:hidden 的占位，
    首屏凭空多出 567px 死区（1440x900）——复核已否决，这里守住新做法。
    """
    html = _html()
    for frag in ('id="taResultMeta"', 'id="taFontwall"', 'id="taThemeRow"'):
        tag = re.search(re.escape(frag) + r"[^>]*>", html)
        assert tag, frag + " 缺失"
        assert " hidden" not in tag.group(0), frag + " 不许带 hidden（首屏即真内容）"
    # 文本位首屏就有占位内容，动作按钮首屏 disabled
    assert "— · 0 字符" in html
    assert html.count(" disabled") >= 8
    # 字体墙首屏有加载占位（懒包随后换成默认样例的真卡片）
    assert "ta-fw-loading" in html
    js = _js()
    assert 'var FW_SAMPLE = "Hello";' in js
    assert "loadFigletWall(FW_SAMPLE)" in js
    # 结果行的隐藏写入必须清干净
    assert "taResultMeta.hidden" not in js
    assert "taThemeRow.hidden" not in js
    # 只剩提示行保留 [hidden] 占位（一行 19px），其余占位规则全部撤掉
    css = _css()
    rule = re.search(r"#taInputHint\[hidden\]\s*\{[^}]*\}", css)
    assert rule and "visibility: hidden" in rule.group(0)
    for gone in (".ta-result-meta[hidden]", ".ta-theme-row[hidden]",
                 ".ta-fontwall[hidden]", "#taCjkHeightGroup[hidden]"):
        assert gone not in css, gone + " 占位规则应已删除"
    # 字符高度分组的 195px 英文态空白占位已撤
    assert "min-height: 195px" not in css


def test_output_and_fontwall_heights_are_fixed():
    """终端框高与字体墙高度必须与内容无关：只允许 height，不许 max-height 撑。"""
    css = _css()
    out = re.search(r"\.ta-output\s*\{[^}]*\}", css).group(0)
    assert "height: clamp(" in out
    assert "min-height" not in out
    fw = re.search(r"\.ta-fontwall\s*\{[^}]*\}", css).group(0)
    assert "height: 440px" in fw
    assert "display: flex" in fw
    grid = re.search(r"\.ta-fw-grid\s*\{[^}]*\}", css).group(0)
    assert "flex: 1 1 auto" in grid
    assert "max-height" not in grid


def test_one_line_text_blocks_do_not_rewrap():
    """会变长/变可见的单行文字必须锁成一行，否则换行会顶掉下面的区块。"""
    css = _css()
    for sel in (r"\.ta-meta-text\s*\{", r"\.ta-input-hint\s*\{"):
        body = re.search(sel + r"[^}]*}", css).group(0)
        assert "white-space: nowrap" in body, sel
        assert "text-overflow: ellipsis" in body, sel
    # 提示行空态(min-height)必须正好是一行行高，否则 hidden→显示时会长高
    hint = re.search(r"\.ta-input-hint\s*\{[^}]*\}", css).group(0)
    assert "min-height: 1.5em" in hint


def test_fit_output_font_does_not_touch_box_height():
    """JS 只许改字号/行高，不许改框高（改了就等于又把下方内容顶下去）。"""
    js = _js()
    fn = js[js.index("function fitOutputFont"):]
    fn = fn[:fn.index("\n  }\n")]
    assert "clientHeight" in fn
    assert ".style.height" not in fn
    assert ".style.minHeight" not in fn
    assert 'taOutput.style.fontSize' in fn
