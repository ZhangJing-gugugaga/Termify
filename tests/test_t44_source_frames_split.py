"""T44 — 视频源帧分帧下发（清单 + 单帧）与整包端点的字节契约。

缺陷（本任务修复）：/api/gallery/source-frames/<id> 把整段视频的帧（生产机上是
514 帧）base64 塞进一个 12.1MB JSON，作品页的 `#animPreview` 又被
`if (IS_VIDEO && !srcFrames && !srcLoadFailed) return;` 卡住 —— 首帧要等最后一帧
到达（实测 13.8s 白屏）。本文件锁定新链路的两条硬约束：

1. 清单只描述元数据（帧数/尺寸/间隔/版本），单帧返回 image/jpeg 原始字节，
   带长期缓存头（作品生成后帧内容不变）；
2. 单帧字节必须与整包 JSON 里同索引帧 base64 解码后的字节**完全一致**：
   两条链路同一编码参数（RGB + JPEG quality=80），否则拆分后首帧画面会与
   整包/embed 链路漂移。

整包端点的响应形状是既有契约（embed、导出、旧页面都依赖），因此默认行为
不许变，只允许新增。
"""

from __future__ import annotations

import base64
import json

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


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t44.db"))
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
    # 帧缓存是模块级 LRU：跨用例复用会让"字节一致"的断言读到上一轮的字节
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


def _insert_work(db, work_id, params, tmp_path, private=0):
    db.insert_work({
        "id": work_id,
        "title": "t44 work",
        "description": "",
        "tags": json.dumps(["video"]),
        "author": "tester",
        "source_path": "/nonexistent/source.png",
        "thumbnail_path": "/nonexistent/thumb.png",
        "og_path": "/nonexistent/og.png",
        "params_json": json.dumps(params),
        "is_private": private,
        "admin_token": "tok-" + work_id,
        "created_at": "2026-01-01T00:00:00",
        "ip": "203.0.113.10",
    })
    return work_id


@pytest.fixture
def video_work(db_holder, tmp_path):
    return _insert_work(db_holder, "t44video0001",
                        {"kind": "video", "interval": 0.1,
                         "frames_dir": str(_frames_dir(tmp_path))}, tmp_path)


@pytest.fixture
def db_holder(_isolate_env):
    return _isolate_env


def _client():
    from app import app as flask_app
    return flask_app.test_client()


# --- 清单 ------------------------------------------------------------------

def test_manifest_is_metadata_only(video_work):
    r = _client().get("/api/gallery/source-frames/%s/manifest" % video_work)
    assert r.status_code == 200
    body = r.get_json()
    assert set(body) == {"ok", "w", "h", "interval", "count", "version"}
    assert body["ok"] is True
    assert body["count"] == NFRAMES
    assert (body["w"], body["h"]) == (FRAME_W, FRAME_H)
    assert body["interval"] == pytest.approx(0.1)
    # 清单必须够小：几百字节，不能又把帧塞回来
    assert len(r.get_data()) < 400


def test_manifest_version_is_stable_across_calls(video_work):
    c = _client()
    v1 = c.get("/api/gallery/source-frames/%s/manifest" % video_work).get_json()["version"]
    v2 = c.get("/api/gallery/source-frames/%s/manifest" % video_work).get_json()["version"]
    assert v1 == v2
    assert len(v1) == 12


# --- 单帧 ------------------------------------------------------------------

def test_single_frame_is_jpeg_with_long_cache(video_work):
    r = _client().get("/api/gallery/source-frames/%s/frame/0" % video_work)
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert r.get_data()[:2] == b"\xff\xd8"  # JPEG SOI
    cc = r.headers["Cache-Control"]
    assert "immutable" in cc and "max-age=31536000" in cc
    assert r.headers["ETag"]
    # 条件请求：带 ETag 回来必须是 304 且零字节
    r2 = _client().get("/api/gallery/source-frames/%s/frame/0" % video_work,
                       headers={"If-None-Match": r.headers["ETag"]})
    assert r2.status_code == 304
    assert r2.get_data() == b""


def test_single_frame_bytes_match_blob_frame_bytes(video_work):
    """拆分链路与整包链路必须是同一份字节（同编码参数）——否则首帧会漂移。"""
    c = _client()
    blob = c.get("/api/gallery/source-frames/%s" % video_work).get_json()
    assert blob["count"] == NFRAMES
    for idx in (0, 1, NFRAMES - 1):
        single = c.get("/api/gallery/source-frames/%s/frame/%d" % (video_work, idx)).get_data()
        assert single == base64.b64decode(blob["frames"][idx]), "frame %d 与整包不一致" % idx


def test_single_frame_index_out_of_range(video_work):
    c = _client()
    for bad in (NFRAMES, NFRAMES + 10, 9999):
        r = c.get("/api/gallery/source-frames/%s/frame/%d" % (video_work, bad))
        assert r.status_code == 404


# --- 错误路径与既有契约 ------------------------------------------------------

def test_non_video_work_rejected(db_holder, tmp_path):
    wid = _insert_work(db_holder, "t44text00001",
                       {"kind": "text", "frames": ["ab", "cd"]}, tmp_path)
    c = _client()
    assert c.get("/api/gallery/source-frames/%s/manifest" % wid).status_code == 400
    assert c.get("/api/gallery/source-frames/%s/frame/0" % wid).status_code == 400


def test_missing_work_404():
    assert _client().get("/api/gallery/source-frames/nosuchwork/manifest").status_code == 404
    assert _client().get("/api/gallery/source-frames/nosuchwork/frame/0").status_code == 404


def test_missing_frames_dir_gone(db_holder, tmp_path):
    wid = _insert_work(db_holder, "t44gone00001",
                       {"kind": "video", "interval": 0.1,
                        "frames_dir": str(tmp_path / "nope")}, tmp_path)
    assert _client().get("/api/gallery/source-frames/%s/manifest" % wid).status_code == 410


def test_private_work_needs_auth(db_holder, tmp_path):
    wid = _insert_work(db_holder, "t44priv00001",
                       {"kind": "video", "interval": 0.1,
                        "frames_dir": str(_frames_dir(tmp_path))}, tmp_path, private=1)
    r = _client().get("/api/gallery/source-frames/%s/frame/0" % wid)
    assert r.status_code == 403


def test_blob_endpoint_shape_unchanged(video_work):
    """整包端点默认响应形状是既有契约：顶层键与帧数都不许变。"""
    body = _client().get("/api/gallery/source-frames/%s" % video_work).get_json()
    assert set(body) == {"ok", "w", "h", "interval", "count", "frames"}
    assert body["count"] == NFRAMES
    assert len(body["frames"]) == NFRAMES
    assert (body["w"], body["h"], body["interval"]) == (FRAME_W, FRAME_H, 0.1)
