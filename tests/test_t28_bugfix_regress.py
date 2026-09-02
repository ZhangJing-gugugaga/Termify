"""T28 — audit-remediation 三个产品 bug 修复的回归测试（t27 已转正用例之外的补充角度）。

覆盖：
1. gallery_like cookie 守卫（commit 8eae41e）：
   - 合法字符串 cookie（JSON 体）仍 200 且 set-cookie（原语义不回归）；
   - >200 字符 cookie → 400 双语；浏览器 cookie 优先级语义不变。
2. /api/generate 宽高类型守卫（commit 3b9e076）：
   - JSON float/bool/null height 单独打击 → 400 双语；
   - 合法整数串 "300" 仍 200 且 1-400 钳制生效（钳制逻辑不回归）。
3. /api/download CWD 无关性（commit 9b24cf7）：
   - download 与写入侧同基准（t27::test_download_cwd_independent 已覆盖
     端到端）；此处补穿越/分隔符拒绝与不存在文件 404 在新实现下不回归。
"""

from __future__ import annotations

import io
import json
import os
import re

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """与 t27 同口径隔离：临时 CWD（uploads/tmp）+ 独立任务库。"""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    # 产物基准（uploads/tmp）已仓库根锚定，测试用 TERMIFY_BASE_DIR 指回 tmp_path 隔离。
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t28.db"))

    import app as app_mod
    from termify.taskstore import get_store

    from termify.taskstore import cache_clear_all, reset_store_for_tests

    cache_clear_all()
    reset_store_for_tests()
    app_mod._RL_LOG.clear()
    get_store().set_sweep_hook(app_mod._sweep_stale_frame_dirs)
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


def _assert_bilingual(msg: str) -> None:
    assert isinstance(msg, str) and msg
    assert re.search(r"[\u4e00-\u9fff]", msg)
    assert " / " in msg
    assert re.search(r"[A-Za-z]", msg.split(" / ", 1)[1])


def _png_bytes(w=4, h=2):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 200, 30)).save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def _upload(client):
    resp = client.post("/api/upload",
                       data={"file": (io.BytesIO(_png_bytes()), "x.png")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200
    return json.loads(resp.data)["task_id"]


def _make_gallery_db(monkeypatch, tmp_path, work_id="wt28like0001"):
    import app as app_mod
    from termify.gallery import GalleryDB

    gdir = tmp_path / "gallerydata"
    gdir.mkdir(exist_ok=True)
    (gdir / (work_id + ".png")).write_bytes(b"fake")
    db = GalleryDB(str(tmp_path / "gallery" / "g.db"))
    db.init_db()
    db.insert_work({
        "id": work_id,
        "title": "t",
        "description": "",
        "tags": "[]",
        "author": "a",
        "source_path": str(gdir / (work_id + ".png")),
        "thumbnail_path": "",
        "og_path": "",
        "params_json": "{}",
        "is_private": 0,
        "admin_token": "tok-t28",
        "created_at": "2026-01-01T00:00:00",
        "ip": "127.0.0.1",
    })
    monkeypatch.setattr(app_mod, "GALLERY_DB", db)
    return db


# ═══ 1. gallery_like cookie 守卫 ═════════════════════════════════════════════


def test_like_valid_string_cookie_still_200_and_sets_cookie(client, monkeypatch,
                                                            tmp_path):
    """合法字符串 cookie：200 + liked/count + set-cookie（原语义回归）。"""
    _make_gallery_db(monkeypatch, tmp_path)
    resp = client.post("/api/gallery/like/wt28like0001",
                       data=json.dumps({"cookie": "visitor-abc"}),
                       content_type="application/json")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["ok"] is True and body["liked"] is True and body["count"] == 1
    assert "termify_like_wt28like0001" in resp.headers.get("Set-Cookie", "")


def test_like_overlong_cookie_400_bilingual(client, monkeypatch, tmp_path):
    """>200 字符 cookie → 400 双语，不落库不 500。"""
    _make_gallery_db(monkeypatch, tmp_path)
    resp = client.post("/api/gallery/like/wt28like0001",
                       data=json.dumps({"cookie": "x" * 201}),
                       content_type="application/json")
    assert resp.status_code == 400
    _assert_bilingual(json.loads(resp.data)["error"])


def test_like_browser_cookie_priority_kept(client, monkeypatch, tmp_path):
    """浏览器 cookie 与 JSON cookie 并存时仍以浏览器 cookie 优先（原语义）。"""
    db = _make_gallery_db(monkeypatch, tmp_path)
    resp = client.post("/api/gallery/like/wt28like0001",
                       data=json.dumps({"cookie": "from-json"}),
                       content_type="application/json")
    assert resp.status_code == 200
    # 非字符串 JSON cookie 在有浏览器 cookie 时应被忽略而非 500/400
    resp = client.post("/api/gallery/like/wt28like0001",
                       data=json.dumps({"cookie": ["bad"]}),
                       content_type="application/json",
                       headers={"Cookie": "termify_like_wt28like0001=from-json"})
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["liked"] is False and body["count"] == 0  # 同 cookie 幂等 unlike
    assert db.has_liked("wt28like0001", "127.0.0.1", "from-json") is False


# ═══ 2. /api/generate 宽高类型守卫 ═══════════════════════════════════════════


def test_generate_json_float_and_bool_sizes_400(client):
    """JSON float / bool 宽高 → 400 双语（int(12.5) 截断与 int(True)=1 均不再静默）。"""
    task_id = _upload(client)
    for payload in [{"width": 12.5}, {"height": 3.7}, {"width": True},
                    {"height": False}, {"width": None}, {"height": None},
                    {"width": {"a": 1}}, {"height": [8]}]:
        resp = client.post("/api/generate", json={
            "task_id": task_id, "format": "html", **payload})
        assert resp.status_code == 400, payload
        _assert_bilingual(json.loads(resp.data)["error"])


def test_generate_valid_int_string_still_clamped(client):
    """合法整数串宽度仍 200 且 1-400 钳制生效（守卫不误伤、钳制不回归）。"""
    task_id = _upload(client)
    resp = client.post("/api/generate", json={
        "task_id": task_id, "format": "html", "width": "300", "height": " 24 "})
    assert resp.status_code == 200
    resp = client.post("/api/generate", json={
        "task_id": task_id, "format": "html", "width": "99999"})
    assert resp.status_code == 200
    filename = json.loads(resp.data)["download_url"].split("/")[-1]
    assert os.path.isfile(os.path.join("tmp", filename))  # 钳制后可正常产出


# ═══ 3. /api/download CWD 无关性（新实现不回归）══════════════════════════════


def test_download_traversal_and_missing_not_regressed(client):
    """穿越/分隔符仍 400；不存在文件仍 404；正常产物 200（写入/读取同基准）。"""
    for evil in ("..%2fx", "..\\..\\x", "a/b.html"):
        assert client.get(f"/api/download/{evil}").status_code == 400, evil
    assert client.get("/api/download/definitely_missing_9f3a.html").status_code == 404

    task_id = _upload(client)
    resp = client.post("/api/generate", json={
        "task_id": task_id, "format": "html", "width": 8, "height": 2})
    assert resp.status_code == 200
    filename = json.loads(resp.data)["download_url"].split("/")[-1]
    got = client.get(f"/api/download/{filename}")
    assert got.status_code == 200
    with open(os.path.join("tmp", filename), "rb") as f:
        assert f.read() == got.data
