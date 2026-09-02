"""T29 — 终审 blocker/major 修复回归（view_work.html 脚本注入防线）。

覆盖（与终审报告编号对应）：
1. [blocker-1] view_work.html:236 注释内字面双花括号被 Jinja 当表达式解析
   → TemplateSyntaxError 令 GET /v/<id> 全站 500：正常作品 /v/ 页必须 200。
2. [blocker-2] view_work.html 注释内字面 </script> 提前终止 script 块
   （HTML5 script 数据在第一个 </script 处结束）：数据脚本块必须完整，
   TERMIFY_GALLERY_WORK 赋值位于第一个内联 <script> 与其闭合之间。
3. [major-3] params_json / tags_json 原 |safe 裸内嵌的存储型 XSS：
   上传携带 </script><img onerror> 恶意 params 的作品 → /v/ 页 200 且
   响应体不含字面注入序列，tojson 转义形（\\u003c/script\\u003e）在位，
   JSON.parse 字面量可无损还原原始 params；tags 白名单外条目被丢弃。

隔离口径与 t27/t28 相同：临时 CWD + 独立任务库 + 独立画廊库/数据目录，
不触碰 data/termify.db 与 5000 端口服务。
"""

from __future__ import annotations

import io
import json
import os
import re

import pytest
from PIL import Image


def app_test_request_context(*args, **kwargs):
    """app.test_request_context 的模块级别名（XFF 测试用）。"""
    from app import app

    return app.test_request_context(*args, **kwargs)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """临时 CWD（uploads/tmp）+ 独立任务库 + 独立画廊库与数据目录。"""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t29.db"))

    import app as app_mod
    from termify.gallery import GalleryDB
    from termify.taskstore import cache_clear_all, get_store, reset_store_for_tests

    cache_clear_all()
    reset_store_for_tests()
    app_mod._RL_LOG.clear()
    get_store().set_sweep_hook(app_mod._sweep_stale_frame_dirs)

    # 画廊库/数据目录全部指到 tmp，绝不写仓库 data/
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


def _upload_work(client, title="t29", params="{}", tags="[]"):
    """经真实 /api/gallery/upload 通道入库（缩略图/OG 走生产代码路径）。"""
    resp = client.post(
        "/api/gallery/upload",
        data={
            "source": (io.BytesIO(_png_bytes()), f"{title}.png"),
            "title": title,
            "params": params,
            "tags": tags,
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)["id"]


XSS_PAYLOAD = {"inj": "</script><img src=x onerror=alert(1)>"}


# ═══ [blocker-1/2] /v/ 页数据脚本块完整性 ═══════════════════════════════════


def test_view_page_renders_200_for_normal_work(client):
    """[blocker-1] 正常作品 GET /v/<id> 必须 200（修复前全站 500）。"""
    wid = _upload_work(client, title="t29-normal",
                       params=json.dumps({"charset": "ascii", "width": 80,
                                          "height": 24}))
    resp = client.get(f"/v/{wid}")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "view-banner-title" in body
    assert "t29-normal" in body


def test_view_page_data_script_block_intact(client):
    """[blocker-2] 数据脚本块完整：赋值在第一个内联 script 内，全文恰 3 个闭合。

    修复前注释里的字面 </script> 把脚本块在 TERMIFY_GALLERY_WORK 赋值之前
    提前终止（闭合数 4），赋值降级为 body 文本、页面 JS 全断。
    """
    wid = _upload_work(client, title="t29-script",
                       params=json.dumps({"charset": "blocks"}))
    body = client.get(f"/v/{wid}").data.decode("utf-8")
    assert client.get(f"/v/{wid}").status_code == 200
    assert body.count("</script>") == 3

    # 第一个内联（非 src 引用）<script> … 其 </script> 之间必须包含完整赋值
    inline_start = body.find("<script>", body.find("</script>") + 1)
    assert inline_start != -1
    inline_close = body.find("</script>", inline_start)
    seg = body[inline_start:inline_close]
    assert "window.TERMIFY_GALLERY_WORK" in seg
    assert "JSON.parse" in seg
    # 赋值之前的注释区不得再含 </script / Jinja 花括号残留
    head = seg.split("window.TERMIFY_GALLERY_WORK", 1)[0]
    assert "</script" not in head
    assert "{{" not in head and "}}" not in head

    # 第二个（查看器）脚本块同样不得含字面 </script 或裸 Jinja 花括号
    rest = body[inline_close + len("</script>"):]
    viewer_close = rest.find("</script>")
    viewer = rest[:viewer_close]
    assert "{{" not in viewer and "}}" not in viewer
    assert "</script" not in viewer


# ═══ [major-3] params_json / tags_json tojson + JSON.parse 防线 ════════════


def test_view_page_params_json_injection_neutralized(client):
    """[major-3] 恶意 params 入库后 /v/ 页不逃逸脚本上下文，且可无损还原。"""
    wid = _upload_work(
        client, title="t29-xss",
        params=json.dumps({**XSS_PAYLOAD, "charset": "ascii",
                           "width": 80, "height": 24}))
    resp = client.get(f"/v/{wid}")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")

    # 注入序列不得以任何 HTML 形式出现
    assert "</script><img" not in body
    assert "<img src=x onerror=alert(1)>" not in body
    # tojson script-context 转义在位（</script> → \u003c/script\u003e）
    assert "\\u003c/script\\u003e" in body
    # 脚本块数量不变（注入未额外终止 script）
    assert body.count("</script>") == 3

    # JSON.parse 字面量须可无损还原原始 params（前端数据语义不回归）
    m = re.search(r"params: JSON\.parse\((\".*?\")\),", body)
    assert m, "params 字面量缺失"
    params = json.loads(json.loads(m.group(1)))
    assert params["inj"] == XSS_PAYLOAD["inj"]
    assert params["charset"] == "ascii"


def test_view_page_tags_tojson_and_whitelist(client):
    """[major-3] tags 走 tojson+JSON.parse：白名单外条目被丢弃，合法标签还原。"""
    wid = _upload_work(client, title="t29-tags",
                       tags=json.dumps(["动画", '<script>alert(1)</script>']))
    resp = client.get(f"/v/{wid}")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "<script>alert(1)</script>" not in body

    m = re.search(r"tags: JSON\.parse\((\".*?\")\)", body)
    assert m, "tags 字面量缺失"
    tags = json.loads(json.loads(m.group(1)))
    assert tags == ["动画"]


# ═══ [major-4] X-Forwarded-For 仅在反代后（回环/私网 remote_addr）采信 ════════


def test_client_ip_xff_honoured_only_behind_trusted_peer():
    """[major-4] _client_ip：loopback 对端采信 XFF 第一跳；公网对端忽略 XFF。"""
    from app import _client_ip

    # 反代场景：remote_addr 为回环 → 采信 XFF 第一跳
    with app_test_request_context(
        headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"},
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
    ):
        assert _client_ip() == "203.0.113.7"
    # 私网对端（如内网反代）同样采信
    with app_test_request_context(
        headers={"X-Forwarded-For": "198.51.100.9"},
        environ_base={"REMOTE_ADDR": "192.168.1.2"},
    ):
        assert _client_ip() == "198.51.100.9"
    # 直连场景：公网 remote_addr → 忽略伪造 XFF，按对端计
    # （注意别用 203.0.113.x 等 TEST-NET 段：Python ipaddress 视其为 is_private，
    #  会被误判为可信反代。）
    with app_test_request_context(
        headers={"X-Forwarded-For": "1.2.3.4"},
        environ_base={"REMOTE_ADDR": "8.8.8.8"},
    ):
        assert _client_ip() == "8.8.8.8"
    # 反代后但无 XFF → 退回 remote_addr
    with app_test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert _client_ip() == "127.0.0.1"


def _upload_png(client, remote_addr, xff=None):
    headers = {"X-Forwarded-For": xff} if xff else {}
    resp = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(_png_bytes()), "x.png")},
        content_type="multipart/form-data",
        headers=headers,
        environ_base={"REMOTE_ADDR": remote_addr},
    )
    return resp.status_code


def test_rate_limit_ignores_forged_xff_from_direct_clients(client):
    """[major-4] 直连客户端伪造不同 XFF 不能绕过限流：同一 remote_addr 计满即 429。"""
    codes = [
        _upload_png(client, "45.60.40.1", xff=f"10.1.1.{i}")
        for i in range(11)
    ]
    assert codes[:10] == [200] * 10, codes
    assert codes[10] == 429, codes  # 第 11 次：伪造 XFF 无效，仍按 remote_addr 计


def test_rate_limit_distinct_direct_clients_not_cross_limited(client):
    """[major-4] 不同 remote_addr 的直连客户端互不共享限额。"""
    codes = [
        _upload_png(client, f"45.60.41.{100 + i}", xff="1.2.3.4")
        for i in range(3)
    ]
    assert codes == [200, 200, 200], codes
