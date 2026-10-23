"""T4y — 管理口令限流的三个绕过口已并入同一份失败计数（S-1 收尾）。

t4x 的文档头把这三个口子列为"刻意不断言的已知缺口"：gallery_work(GET) /
gallery_delete(DELETE) / _work_raw_file_authorized(GET) 各自直接 _secret_equal
比对 X-Termify-Admin-Pwd，绕过 _admin_check() 的失败计数与退避窗口，于是口令
爆破可以在这三个入口无限次重试（DELETE 还是"猜中即删任意作品"，且此前不在任何
_rate_check 配额内）。本文件把该缺口固化成回归断言：

1. 三处错口令都记入同一份按 IP 的失败计数，达到 ADMIN_PWD_FAIL_MAX 的那一次
   直接 429 + Retry-After（复用 _admin_locked_response，文案不在这里复制）；
2. 计数按 IP 隔离：换 IP 仍从 200 / 403 起步；
3. 不带该头的普通访客永远不被计数（否则任何人刷公开页就能把自己锁死）；
4. DELETE 挂上写接口配额（per_minute=12），第 13 次 429 —— 阈值刻意高于
   ADMIN_PWD_FAIL_MAX，避免配额 429 掩盖口令退避窗口。
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

ADMIN_PWD = "t4y-admin-pwd"
# DELETE 配额（app.py 里 gallery_delete 的 _rate_check 参数，双处对齐）
DELETE_PER_MINUTE = 12


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """把画廊库/任务库指向 tmp_path，并逐用例清零模块级计数。

    刻意不 chdir（见 tests/test_t7_web_api.py 的下载目录错位问题）。
    """
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t4y.db"))
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


def _insert_work(db, tmp_path, work_id, private=0):
    """每个作品一份自己的源文件：DELETE 会删文件，共享文件会互相 404。"""
    src = tmp_path / ("src_%s.bin" % work_id)
    src.write_bytes(b"x" * 64)
    db.insert_work({
        "id": work_id,
        "title": work_id,
        "description": "",
        "tags": json.dumps([]),
        "author": "tester",
        "source_path": str(src),
        "thumbnail_path": str(src),
        "og_path": str(src),
        "params_json": json.dumps({"kind": "text", "frames": ["a"]}),
        "is_private": private,
        "admin_token": "tok-" + work_id,
        "created_at": "2026-01-01T00:00:00",
        "ip": "203.0.113.10",
    })
    return work_id


def _hdr(ip, pwd=None):
    h = {"X-Forwarded-For": ip}
    if pwd is not None:
        h["X-Termify-Admin-Pwd"] = pwd
    return h


def _locked_assert(resp):
    from app import ADMIN_PWD_FAIL_LOCK

    assert resp.status_code == 429
    assert resp.headers["Retry-After"] == str(ADMIN_PWD_FAIL_LOCK)
    body = resp.get_json()
    assert body["retry_after"] == ADMIN_PWD_FAIL_LOCK
    assert body["remaining_attempts"] == 0


def test_work_detail_wrong_pwd_counted_and_locks(client, _isolate_env, tmp_path):
    """GET /api/gallery/work/<id>：错口令不再是无限次布尔 oracle。"""
    from app import ADMIN_PWD_FAIL_MAX

    work_id = _insert_work(_isolate_env, tmp_path, "t4ypriv1", private=1)
    url = "/api/gallery/work/" + work_id
    ip = "203.0.113.71"
    # 阈值前仍是 200，但 is_authorized 必须是 False（行为语义不变）
    for attempt in range(1, ADMIN_PWD_FAIL_MAX):
        resp = client.get(url, headers=_hdr(ip, "wrong-pwd"))
        assert resp.status_code == 200, "第 %d 次错口令：详情页仍应可看" % attempt
        assert resp.get_json()["is_authorized"] is False
    _locked_assert(client.get(url, headers=_hdr(ip, "wrong-pwd")))
    # 计数按 IP 隔离
    assert client.get(url, headers=_hdr("203.0.113.72", "wrong-pwd")).status_code == 200
    assert client.get(url, headers=_hdr("203.0.113.73", ADMIN_PWD)).get_json()["is_authorized"] is True


def test_work_delete_wrong_pwd_counted_and_locks(client, _isolate_env, tmp_path):
    """DELETE /api/gallery/work/<id>：错口令爆破到阈值即 429。"""
    from app import ADMIN_PWD_FAIL_MAX

    assert ADMIN_PWD_FAIL_MAX <= DELETE_PER_MINUTE, "配额必须先于口令锁定不生效"
    work_id = _insert_work(_isolate_env, tmp_path, "t4ydel1")
    url = "/api/gallery/work/" + work_id
    ip = "203.0.113.81"
    for attempt in range(1, ADMIN_PWD_FAIL_MAX):
        resp = client.delete(url, headers=_hdr(ip, "wrong-pwd"))
        assert resp.status_code == 403, "第 %d 次错口令应为 403" % attempt
        assert resp.get_json() == {"error": "Unauthorized"}, "错误结构不许变"
    _locked_assert(client.delete(url, headers=_hdr(ip, "wrong-pwd")))
    # 换 IP 后作品仍在、错口令仍 403
    assert client.delete(url, headers=_hdr("203.0.113.82", "wrong-pwd")).status_code == 403
    assert client.get(url).status_code == 200


def test_private_raw_source_wrong_pwd_counted_and_locks(client, _isolate_env, tmp_path):
    """GET /gallery/file/<id>/source：私有原作直链的 oracle 也被计数。"""
    from app import ADMIN_PWD_FAIL_MAX

    work_id = _insert_work(_isolate_env, tmp_path, "t4ypriv2", private=1)
    url = "/gallery/file/" + work_id + "/source"
    ip = "203.0.113.91"
    for attempt in range(1, ADMIN_PWD_FAIL_MAX):
        assert client.get(url, headers=_hdr(ip, "wrong-pwd")).status_code == 403
    _locked_assert(client.get(url, headers=_hdr(ip, "wrong-pwd")))
    # 换 IP：正确口令 200，无头 403
    assert client.get(url, headers=_hdr("203.0.113.92", ADMIN_PWD)).status_code == 200
    assert client.get(url, headers=_hdr("203.0.113.93")).status_code == 403


def test_absent_header_never_counts(client, _isolate_env, tmp_path):
    """普通访客（不带管理口令头）刷多少次都不该把自己锁死。"""
    work_id = _insert_work(_isolate_env, tmp_path, "t4ypriv3", private=1)
    ip = "203.0.113.101"
    for _ in range(20):
        assert client.get("/gallery/file/" + work_id + "/source",
                          headers=_hdr(ip)).status_code == 403
        assert client.get("/api/gallery/work/" + work_id,
                          headers=_hdr(ip)).get_json()["is_authorized"] is False
    # 同一 IP 之后用正确口令依然通
    assert client.get("/api/gallery/work/" + work_id,
                      headers=_hdr(ip, ADMIN_PWD)).get_json()["is_authorized"] is True


def test_delete_quota_429(client, _isolate_env, tmp_path):
    """DELETE 与其他写接口同等配额：第 DELETE_PER_MINUTE+1 次 429。"""
    ip = "203.0.113.111"
    ids = [_insert_work(_isolate_env, tmp_path, "t4yq%02d" % i)
           for i in range(DELETE_PER_MINUTE + 1)]
    for i in range(DELETE_PER_MINUTE):
        resp = client.delete("/api/gallery/work/" + ids[i], headers=_hdr(ip, ADMIN_PWD))
        assert resp.status_code == 200, "第 %d 次删除应成功" % (i + 1)
    resp = client.delete("/api/gallery/work/" + ids[DELETE_PER_MINUTE],
                         headers=_hdr(ip, ADMIN_PWD))
    assert resp.status_code == 429, "超出配额必须 429"
    assert "Too many deletes" in resp.get_json()["error"]
    # 作品还在（配额在鉴权之前拦下，未真的删掉）
    assert client.get("/api/gallery/work/" + ids[DELETE_PER_MINUTE]).status_code == 200


def test_correct_pwd_still_allowed_unlocked(client, _isolate_env, tmp_path):
    """正确口令在未锁定 IP 上三处都照常通过（改动不得收紧正常路径）。"""
    ip = "203.0.113.121"
    priv = _insert_work(_isolate_env, tmp_path, "t4ypriv4", private=1)
    pub = _insert_work(_isolate_env, tmp_path, "t4ypub4")
    assert client.get("/api/gallery/work/" + priv,
                      headers=_hdr(ip, ADMIN_PWD)).get_json()["is_authorized"] is True
    assert client.get("/gallery/file/" + priv + "/source",
                      headers=_hdr(ip, ADMIN_PWD)).status_code == 200
    assert client.delete("/api/gallery/work/" + pub,
                         headers=_hdr(ip, ADMIN_PWD)).status_code == 200
    # 公开作品的直链本来就放行（不过鉴权）
    assert client.get("/gallery/file/" + _insert_work(_isolate_env, tmp_path, "t4ypub5") + "/source").status_code == 200
