"""T4x — 画廊列表瘦身（PF-8）与写接口同源校验 / 管理口令限流（S-1、S-2）。

四个闸门：
1. /api/gallery/list 不再内联 params.frames（整幅艺术字正文），但
   /api/gallery/work/<id> 必须仍然带着 frames —— 列表瘦身的唯一代价是不许
   把详情页一起瘦掉，否则 /v/ 页与作品页的预填会空白。
2. /api/gallery/admin 连续错口令到阈值后必须 429（且带 Retry-After），
   正确口令在未锁定的 IP 上仍 200 —— 阈值与 429 文案都取自 app.py 的
   ADMIN_PWD_FAIL_MAX / _admin_locked_response，不在这里复制常量。
3. 写接口的跨站来源（Origin / Referer / Sec-Fetch-Site 三条降级链）一律
   403，同源与"非浏览器客户端（无任何来源头）"放行。
4. 审计用例：遍历 url_map 里 /api/ 下所有 POST/PUT/DELETE，逐个打跨站
   Origin，断言全部被 before_request 守卫挡住 —— 防止新增写接口漏挂闸门。

曾经的三处缺口（已由 tests/test_t4y_admin_pwd_bypass_locked.py 覆盖）：
gallery_work(GET) / gallery_delete(DELETE) / _work_raw_file_authorized(GET)
原先直接比对 X-Termify-Admin-Pwd，绕过 _admin_check() 的按 IP 失败计数。
现在三者统一走 app.py 的 _admin_pwd_header_state()，与 _admin_check 共用同一份
计数与退避窗口；不带该头的普通访客不计失败。
"""

from __future__ import annotations

import json

import pytest

from tests.gallery_marks import requires_gallery

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # pragma: no cover
    _HAVE_FLASK = False

pytestmark = [
    pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装"),
    requires_gallery,
]

ADMIN_PWD = "t4x-admin-pwd"
# 三个帧，每个 2KB 级：列表内联时单件就能撑出几 KB，便于断言"瘦身生效"
FRAMES = ["ASCII-ART-FRAME-%d-" % i + ("#" * 2000) for i in range(3)]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """把画廊库/任务库指向 tmp_path。

    刻意不 chdir：tests/test_t7_web_api.py 的 chdir+TERMIFY_BASE_DIR 组合会让
    后续模块的下载目录错位（test_t27/test_t28 的 /api/download 404 即由此而来）。
    """
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t4x.db"))
    monkeypatch.setenv("TERMIFY_ADMIN_PWD", ADMIN_PWD)

    import app as app_mod
    from termify import gallery as gallery_mod

    gdata = tmp_path / "gallery_data"
    gdata.mkdir()
    db = gallery_mod.GalleryDB(str(gdata / "termify.db"))
    db.init_db()
    monkeypatch.setattr(app_mod, "GALLERY_DATA_DIR", str(gdata))
    monkeypatch.setattr(app_mod, "GALLERY_DB", db)
    # 口令失败计数与普通接口配额都是模块级全局，必须逐用例清零
    monkeypatch.setattr(app_mod, "_ADMIN_PWD_FAILS", {})
    monkeypatch.setattr(app_mod, "_ADMIN_PWD_BLOCKED", {})
    app_mod._RL_LOG.clear()
    yield db
    app_mod._RL_LOG.clear()


@pytest.fixture
def client():
    import app as app_mod

    app_mod.app.config.update(TESTING=True)
    return app_mod.app.test_client()


def _insert_text_work(db, work_id="t4xframes1"):
    db.insert_work({
        "id": work_id,
        "title": "text art work",
        "description": "",
        "tags": json.dumps(["text"]),
        "author": "tester",
        "source_path": "/nonexistent/source.png",
        "thumbnail_path": "/nonexistent/thumb.gif",
        "og_path": "/nonexistent/og.png",
        "params_json": json.dumps({"kind": "text", "frames": FRAMES,
                                   "font": "mono", "cols": 80}),
        "is_private": 0,
        "admin_token": "tok-" + work_id,
        "created_at": "2026-01-01T00:00:00",
        "ip": "203.0.113.10",
    })
    return work_id


def test_list_omits_frames_detail_keeps_them(client, _isolate_env):
    db = _isolate_env
    work_id = _insert_text_work(db)

    resp = client.get("/api/gallery/list?limit=60")
    assert resp.status_code == 200
    assert b'"frames"' not in resp.data, "列表响应不应再出现 frames 键"

    items = resp.get_json()["items"]
    assert len(items) == 1
    item = items[0]
    assert "frames" not in item["params"]
    # 角标分类依赖 params.kind，列表必须保留
    assert item["params"]["kind"] == "text"
    for key in ("id", "title", "author", "thumbnail_url", "og_url",
                "like_count", "view_count", "created_at"):
        assert key in item, f"列表项仍应保留 {key}"

    detail = client.get(f"/api/gallery/work/{work_id}")
    assert detail.status_code == 200
    assert detail.get_json()["params"]["frames"] == FRAMES, "详情接口必须原样返回 frames"


def test_admin_password_rate_limited(client):
    from app import ADMIN_PWD_FAIL_MAX, ADMIN_PWD_FAIL_LOCK

    headers = {"X-Termify-Admin-Pwd": "wrong-pwd", "X-Forwarded-For": "203.0.113.44"}
    for attempt in range(1, ADMIN_PWD_FAIL_MAX):
        resp = client.get("/api/gallery/admin", headers=headers)
        assert resp.status_code == 403, f"第 {attempt} 次错口令应为 403"
        assert resp.get_json()["remaining_attempts"] == ADMIN_PWD_FAIL_MAX - attempt

    locked = client.get("/api/gallery/admin", headers=headers)
    assert locked.status_code == 429, "达到阈值的这一次必须被锁定"
    assert locked.headers["Retry-After"] == str(ADMIN_PWD_FAIL_LOCK)
    payload = locked.get_json()
    assert payload["retry_after"] == ADMIN_PWD_FAIL_LOCK
    assert payload["remaining_attempts"] == 0

    # 锁定判定先于口令比对：锁定期内即使口令正确也是 429
    still = client.get("/api/gallery/admin",
                       headers={"X-Termify-Admin-Pwd": ADMIN_PWD,
                                "X-Forwarded-For": "203.0.113.44"})
    assert still.status_code == 429

    # 换 IP：正确口令 200，错口令 403 —— 计数按 IP 隔离
    other = {"X-Termify-Admin-Pwd": ADMIN_PWD, "X-Forwarded-For": "203.0.113.45"}
    assert client.get("/api/gallery/admin", headers=other).status_code == 200
    bad_other = dict(other, **{"X-Termify-Admin-Pwd": "wrong-pwd"})
    assert client.get("/api/gallery/admin", headers=bad_other).status_code == 403


def test_like_write_guard(client, _isolate_env):
    work_id = _insert_text_work(_isolate_env)
    url = f"/api/gallery/like/{work_id}"

    cross = client.post(url, headers={"Origin": "http://evil.example"})
    assert cross.status_code == 403
    assert "跨站请求已拒绝" in cross.get_json()["error"]

    assert client.post(url, headers={"Origin": "null"}).status_code == 403
    assert client.post(url, headers={"Referer": "http://evil.example/x"}).status_code == 403
    assert client.post(url, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403

    same = client.post(url, headers={"Origin": "http://localhost"})
    assert same.status_code == 200
    assert same.get_json()["liked"] is True

    # 非浏览器客户端（无任何来源头）放行：CLI / 脚本 / test_client 都走这条
    bare = client.post(url, headers={"X-Forwarded-For": "203.0.113.46"})
    assert bare.status_code == 200


def test_every_api_write_endpoint_passes_the_guard(client):
    """审计：/api/ 下所有 POST/PUT/DELETE 都必须被同源守卫拦在业务逻辑之前。"""
    import app as app_mod

    skipped = set()
    checked = []
    for rule in app_mod.app.url_map.iter_rules():
        methods = sorted(set(rule.methods or ()) & {"POST", "PUT", "DELETE"})
        if not methods or not rule.rule.startswith("/api/"):
            continue
        path = rule.rule
        for arg in list(rule.arguments):
            # 统一塞一个合法形态的占位值；守卫在 before_request 里先于匹配后的
            # 参数校验执行，占位值不影响 403 判定
            path = path.replace(f"<int:{arg}>", "1")
            path = path.replace(f"<{arg}>", "x")
        for method in methods:
            resp = client.open(path, method=method,
                               headers={"Origin": "http://evil.example"})
            if resp.status_code == 404:
                # 路由以 converter 语法未替换干净时退化为未匹配，不计入审计
                skipped.add(f"{method} {rule.rule}")
                continue
            assert resp.status_code == 403, (
                f"{method} {path} 未被同源守卫拦截，实际 {resp.status_code}")
            checked.append(f"{method} {path}")

    assert len(checked) >= 10, f"审计到的写接口过少：{checked}"
    assert not skipped, f"未能构造请求的写路由：{sorted(skipped)}"
