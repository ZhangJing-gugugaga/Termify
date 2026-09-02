"""T25 — GET /api/task-frames/<task_id>：方案B 本地渲染的源帧直读端点。

契约：
- task 不存在 → 404 {"error": "任务不存在 / Task not found"}
- video 任务（filepath 是目录）→ 读持久化帧 PNG
- image 任务 → PIL 抽帧（GIF 逐帧/静图 1 帧），纵横比缩到 ≤400×240
- 每帧 RGB → JPEG q80 → base64
- 200: {ok, w, h, interval, count, frames}
- 帧数 > 600 或 payload > 40MB → 413 {"too_large": true, ...}
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json

import pytest
from PIL import Image

pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("flask"),
    reason="flask 未安装",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """隔离：临时 CWD（uploads/tmp）+ 独立任务库 + 清空限流/缓存。"""
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    # 产物基准（uploads/tmp）已仓库根锚定，测试用 TERMIFY_BASE_DIR 指回 tmp_path 隔离。
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t25.db"))

    from termify.taskstore import cache_clear_all, reset_store_for_tests

    cache_clear_all()
    reset_store_for_tests()
    import app as app_mod

    app_mod._RL_LOG.clear()  # /api/upload 已加限流，防止同 IP 顶到 429
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


def _gif_bytes(n_frames=2, w=8, h=4):
    buf = io.BytesIO()
    frames = [Image.new("RGB", (w, h), (i * 60, 100, 150)) for i in range(n_frames)]
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                   duration=50, loop=0)
    buf.seek(0)
    return buf


def _upload_gif(client, n_frames=2):
    resp = client.post(
        "/api/upload",
        data={"file": (_gif_bytes(n_frames), "anim.gif")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    return json.loads(resp.data)["task_id"]


def test_task_frames_unknown_task_404(client):
    """不存在的 task → 404 + 中英双语错误。"""
    resp = client.get("/api/task-frames/deadbeef1234")
    assert resp.status_code == 404
    body = json.loads(resp.data)
    assert body["error"] == "任务不存在 / Task not found"


def test_task_frames_gif_multiframe(client):
    """上传 GIF 后请求 → 200 结构完整，逐帧 JPEG base64。"""
    task_id = _upload_gif(client, n_frames=3)
    resp = client.get(f"/api/task-frames/{task_id}")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["ok"] is True
    assert body["count"] == 3
    assert len(body["frames"]) == 3
    assert body["w"] == 8 and body["h"] == 4  # 小图不放大
    assert body["interval"] > 0
    for b64 in body["frames"]:
        raw = base64.b64decode(b64)
        assert raw[:2] == b"\xff\xd8"  # JPEG magic


def test_task_frames_still_image_single_frame(client):
    """静图 → 1 帧。"""
    buf = io.BytesIO()
    Image.new("RGB", (16, 8), (10, 20, 30)).save(buf, format="PNG")
    buf.seek(0)
    resp = client.post("/api/upload",
                       data={"file": (buf, "still.png")},
                       content_type="multipart/form-data")
    task_id = json.loads(resp.data)["task_id"]
    body = json.loads(client.get(f"/api/task-frames/{task_id}").data)
    assert body["ok"] is True
    assert body["count"] == 1
    assert body["w"] == 16 and body["h"] == 8


def test_task_frames_aspect_fit_no_upscale(client):
    """大图按纵横比缩到 ≤400×240：800×100 → 400×50。"""
    buf = io.BytesIO()
    Image.new("RGB", (800, 100), (50, 60, 70)).save(buf, format="PNG")
    buf.seek(0)
    resp = client.post("/api/upload",
                       data={"file": (buf, "wide.png")},
                       content_type="multipart/form-data")
    task_id = json.loads(resp.data)["task_id"]
    body = json.loads(client.get(f"/api/task-frames/{task_id}").data)
    assert body["w"] == 400
    assert body["h"] == 50  # 保持纵横比，无黑边


def test_task_frames_video_task_frames_dir(client):
    """video 任务（filepath 是帧目录）→ 直接读目录内 PNG。"""
    import os

    from termify.taskstore import get_store

    frames_dir = os.path.join("uploads", "frames_aabbccddeeff")
    os.makedirs(frames_dir, exist_ok=True)
    for i in range(3):
        Image.new("RGB", (10, 6), (i * 50, 0, 0)).save(
            os.path.join(frames_dir, f"frame_{i:03d}.png"))

    get_store().put("aabbccddeeff", filepath=frames_dir,
                    target_size=(80, 24), frames_count=3, interval=0.1)

    resp = client.get("/api/task-frames/aabbccddeeff")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["ok"] is True
    assert body["count"] == 3
    assert body["w"] == 10 and body["h"] == 6
    assert body["interval"] == pytest.approx(0.1)
    assert len(body["frames"]) == 3


def test_task_frames_count_guard_413(client, monkeypatch):
    """帧数超过上限 → 413 too_large。"""
    import app as app_mod

    monkeypatch.setattr(app_mod, "TASK_FRAMES_MAX_COUNT", 1)
    task_id = _upload_gif(client, n_frames=2)
    resp = client.get(f"/api/task-frames/{task_id}")
    assert resp.status_code == 413
    body = json.loads(resp.data)
    assert body["too_large"] is True
    assert body["error"] == "预览帧数据过大 / Preview frame payload too large"


def test_task_frames_video_dir_missing_files_skipped(client):
    """video 任务帧目录里混入坏帧文件 → 跳过而不 500。"""
    import os

    from termify.taskstore import get_store

    frames_dir = os.path.join("uploads", "frames_bbaabbccddee")
    os.makedirs(frames_dir, exist_ok=True)
    Image.new("RGB", (10, 6), (0, 0, 0)).save(
        os.path.join(frames_dir, "frame_000.png"))
    with open(os.path.join(frames_dir, "frame_001.png"), "wb") as f:
        f.write(b"not a png")

    get_store().put("bbaabbccddee", filepath=frames_dir,
                    target_size=(80, 24), frames_count=2, interval=0.1)

    body = json.loads(client.get("/api/task-frames/bbaabbccddee").data)
    assert body["ok"] is True
    assert body["count"] == 1
