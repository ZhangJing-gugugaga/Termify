"""T30 — 遗留项清理回归（20MB 口径 / task-frames 限流 / 错误信息不泄露 / RL_LOG 容量）。

对应终审与全量审查的遗留清单：
1. 视频上限默认与 Flask 20MB 硬上限同口径（TERMIFY_MAX_VIDEO_MB 可调）。
2. /api/task-frames 加限流（30 次/分钟/IP），429 文案中英双语。
3. 损坏素材上传返回 4xx 且错误文案不含服务器绝对路径（历史上 500 + 泄露
   "cannot identify image file 'D:\\...'"）。
4. _rate_check 的 _RL_LOG IP 键数软上限：超过阈值触发全表过期清扫。

隔离口径：临时 CWD + TERMIFY_BASE_DIR/TERMIFY_TASK_DB 指向 tmp_path，
不触碰 data/ 与 5000 端口服务。
"""

from __future__ import annotations

import io
import json
import os

import pytest
from PIL import Image


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t30.db"))

    import app as app_mod
    from termify.taskstore import cache_clear_all, get_store, reset_store_for_tests

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


def _gif_bytes(frames=2, w=8, h=4):
    imgs = [Image.new("RGB", (w, h), (i * 40, 100, 200)) for i in range(frames)]
    buf = io.BytesIO()
    imgs[0].save(buf, format="GIF", save_all=True, append_images=imgs[1:],
                 duration=50, loop=0)
    buf.seek(0)
    return buf.read()


# ═══ 1. 视频上限默认 20MB ═══════════════════════════════════════════════════


def test_video_default_cap_matches_flask_20mb(monkeypatch):
    """TERMIFY_MAX_VIDEO_MB 缺省 20，与 Flask MAX_CONTENT_LENGTH 同口径。"""
    monkeypatch.delenv("TERMIFY_MAX_VIDEO_MB", raising=False)
    import importlib

    import termify.video as video_mod

    importlib.reload(video_mod)
    try:
        assert video_mod.MAX_VIDEO_BYTES == 20 * 1024 * 1024
    finally:
        importlib.reload(video_mod)  # 还原进程内单例，避免污染后续测试


def test_video_cap_env_overridable(monkeypatch):
    """自部署可通过 TERMIFY_MAX_VIDEO_MB 调高（用户决策：20MB 为公网口径）。"""
    monkeypatch.setenv("TERMIFY_MAX_VIDEO_MB", "200")
    import importlib

    import termify.video as video_mod

    importlib.reload(video_mod)
    try:
        assert video_mod.MAX_VIDEO_BYTES == 200 * 1024 * 1024
    finally:
        importlib.reload(video_mod)


# ═══ 2. task-frames 限流 ════════════════════════════════════════════════════


def test_task_frames_rate_limited_bilingual(client):
    """30 次/分钟：第 31 次起 429，文案中英双语。"""
    data = {"file": (io.BytesIO(_gif_bytes()), "t30.gif")}
    resp = client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200, resp.data
    task_id = json.loads(resp.data)["task_id"]

    codes = []
    for _ in range(31):
        resp = client.get(f"/api/task-frames/{task_id}")
        codes.append(resp.status_code)
    assert codes[:30] == [200] * 30, codes
    assert codes[30] == 429, codes
    body = json.loads(client.get(f"/api/task-frames/{task_id}").data)
    msg = body["error"]
    assert "请求太频繁" in msg and "/" in msg and "Too many requests" in msg


# ═══ 3. 错误信息不泄露服务器路径 ════════════════════════════════════════════


def test_corrupt_image_upload_4xx_without_path_leak(client):
    """损坏素材上传：4xx + 双语泛化文案，绝不回显服务器绝对路径。"""
    resp = client.post(
        "/api/upload",
        data={"file": (io.BytesIO(b"not an image at all"), "junk.gif")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400, (resp.status_code, resp.data)
    msg = json.loads(resp.data)["error"]
    assert "D:" not in msg and "ZhangJing" not in msg and "uploads" not in msg
    assert "转换失败" in msg and "Conversion failed" in msg


def test_corrupt_image_gallery_upload_4xx_without_path_leak(client, monkeypatch, tmp_path):
    """画廊通道同样 4xx + 不泄露路径（历史上 400 但带 'D:\\...jpg' 详情）。"""
    from termify.gallery import GalleryDB

    monkeypatch.setattr("termify.gallery.os.makedirs", os.makedirs, raising=False)
    gdir = tmp_path / "gallerydata"
    gdir.mkdir(exist_ok=True)
    monkeypatch.setattr("app.GALLERY_DATA_DIR", str(gdir))
    gdb = GalleryDB(str(tmp_path / "gallery" / "g.db"))
    gdb.init_db()
    monkeypatch.setattr("app.GALLERY_DB", gdb)

    resp = client.post(
        "/api/gallery/upload",
        data={"source": (io.BytesIO(b"junk"), "junk.gif"), "title": "t30"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    body = resp.data.decode("utf-8")
    assert "D:" not in body and "\\ZhangJing" not in body
    assert "图片无效" in body or "Invalid" in body


# ═══ 4. RL_LOG 全局容量 ═════════════════════════════════════════════════════


def test_rl_log_global_cap_prunes_stale_ips(client):
    """键数超软上限时全表清扫过期 IP 且丢弃空键；活跃键保留。"""
    import app as app_mod
    import time as _time

    app_mod._RL_LOG.clear()
    monkey_target = app_mod
    original_cap = monkey_target._RL_MAX_IPS
    monkey_target._RL_MAX_IPS = 5
    try:
        now = _time.time()
        # 4 个陈旧 IP（>24h）+ 1 个活跃 IP
        for i in range(4):
            app_mod._RL_LOG[f"10.9.0.{i}"] = [("upload", now - 2 * 86400)]
        app_mod._RL_LOG["10.9.9.9"] = [("upload", now)]

        # 写入第 6 个键 → 超过上限 5 → 陈旧键被清（_rate_check 不需要请求上下文）
        allowed, _ = app_mod._rate_check("10.8.8.8", "upload", per_minute=10)
        assert allowed
        assert all(not k.startswith("10.9.0.") for k in app_mod._RL_LOG)
        assert "10.9.9.9" in app_mod._RL_LOG          # 活跃键保留
        assert "10.8.8.8" in app_mod._RL_LOG          # 新键写入
    finally:
        monkey_target._RL_MAX_IPS = original_cap
        app_mod._RL_LOG.clear()
