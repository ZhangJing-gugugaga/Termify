"""T38 — 画廊与主页的预览一致性。

覆盖本轮修复的回归点：
- 预览体积超预算改为**均匀抽稀**而不是整份 413（旧实现让 blocks /
  大网格作品在作品页永远预览不出来，而主页走本地渲染能看——两页不一致）
- 作品页支持自定义字符梯（此前服务端硬拒，主页有、作品页没有）
- 缩略图尺寸与卡片展示分辨率对齐
- 盲文/二值字符集的 Otsu 极性（阈值取灰度级而非分割线时退化）
"""

from __future__ import annotations

import io
import json
import os

import pytest
from PIL import Image, ImageDraw
from tests.gallery_marks import requires_gallery

try:
    import flask  # noqa: F401
    _HAVE_FLASK = True
except ImportError:  # pragma: no cover
    _HAVE_FLASK = False

pytestmark = pytest.mark.skipif(not _HAVE_FLASK, reason="flask 未安装")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t38.db"))
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
    app_mod._RL_LOG.clear()
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


def _png(color=(20, 200, 80), size=(48, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def _publish(client, title="T38 作品"):
    return client.post(
        "/api/gallery/upload",
        data={"source": (io.BytesIO(_png()), "t38.png"), "title": title,
              "author": "tester", "tags": ["ASCII art"], "charset": "ascii",
              "width": "40", "height": "20", "is_private": "0"},
        content_type="multipart/form-data")


# ── 预览抽稀（替代硬 413）────────────────────────────────────────

def test_preview_max_frames_and_subsample():
    from app import (_preview_max_frames, _preview_payload_too_large,
                     _subsample_frames)

    # blocks 每格 ~21B、纵向行数翻倍：200×60 每帧 ≈ 504KB
    per = 200 * 120 * 21
    assert _preview_max_frames(200, 60, "blocks") == (30 * 1024 * 1024) // per
    assert _preview_max_frames(200, 60, "ascii") == (30 * 1024 * 1024) // (
        200 * 60 * 4)
    # 单帧永远放得下（400×400 blocks ≈ 6.7MB < 30MB）→ 抽稀后不会 413
    assert _preview_max_frames(400, 400, "blocks") >= 1
    assert _preview_payload_too_large(1, 400, 400, "blocks") is False

    class _Seq:
        width, height = 200, 60
        lines_per_frame = [[f"f{i}"] for i in range(514)]

    seq = _Seq()
    frames, total = _subsample_frames(seq, "blocks")
    assert total == 514
    assert 0 < len(frames) < 514          # 抽稀了
    # 均匀取样：首尾都在，且下标严格递增
    picked = [int(f[0][1:]) for f in frames]
    assert picked[0] == 0 and picked[-1] == 513
    assert picked == sorted(set(picked))


@requires_gallery
def test_gallery_preview_subsamples_instead_of_413(client):
    """大网格 + blocks：作品页必须能预览（哪怕抽稀），不能是 413。"""
    wid = json.loads(_publish(client).data)["id"]
    resp = client.get(f"/api/gallery/preview/{wid}?charset=blocks"
                      "&width=400&height=400")
    assert resp.status_code == 200, resp.data
    d = json.loads(resp.data)
    assert d["ok"] if "ok" in d else True
    assert d["charset"] == "blocks" and d["width"] == 400


@requires_gallery
def test_gallery_preview_subsample_flag(client):
    """帧数超预算时响应带 subsampled / total_frames，前端据此提示。"""
    from app import _preview_max_frames

    wid = json.loads(_publish(client).data)["id"]
    # 单帧作品必然不抽稀
    d = json.loads(client.get(f"/api/gallery/preview/{wid}?charset=ascii"
                              "&width=40&height=20").data)
    assert d["subsampled"] is False
    assert d["total_frames"] == d["frame_count"]
    assert _preview_max_frames(40, 20, "ascii") > 1


# ── 自定义字符：作品页与主页对齐 ─────────────────────────────────

@requires_gallery
def test_gallery_preview_custom_charset(client):
    wid = json.loads(_publish(client).data)["id"]
    # 无 ramp → 400
    bad = client.get(f"/api/gallery/preview/{wid}?charset=custom")
    assert bad.status_code == 400
    # 带 ramp → 200 且用了该字符梯
    ok = client.get(f"/api/gallery/preview/{wid}?charset=custom"
                    "&chars=%40%25%23&width=40&height=20")
    assert ok.status_code == 200, ok.data
    body = json.loads(ok.data)["frames"][0]
    joined = "".join(body)
    assert any(ch in joined for ch in "@%#"), joined[:80]
    assert "X" not in joined  # 没用默认 ascii 梯


@requires_gallery
def test_gallery_download_custom_charset(client):
    wid = json.loads(_publish(client).data)["id"]
    resp = client.get(f"/api/gallery/download/{wid}?charset=custom"
                      "&chars=%40%25%23&width=40&height=20&format=python")
    assert resp.status_code == 200, resp.data
    # 动画 .py 播放器模板（静态作品才走 PAYLOAD 内嵌）
    assert b"def play()" in resp.data and len(resp.data) > 2000


# ── 缩略图分辨率 ────────────────────────────────────────────────

def test_thumb_size_matches_card_display():
    """卡片约 268px 宽 → 缩略图出 2×，避免浏览器双线性拉伸糊掉。"""
    from termify import gallery

    assert gallery.THUMB_W == 400 and gallery.THUMB_H == 300
    assert abs(gallery.THUMB_W / gallery.THUMB_H - 4 / 3) < 0.01


@requires_gallery
def test_published_thumb_is_2x(client):
    wid = json.loads(_publish(client).data)["id"]
    resp = client.get(f"/gallery/file/{wid}/thumb")
    assert resp.status_code == 200
    import io as _io
    with Image.open(_io.BytesIO(resp.data)) as im:
        assert im.size == (400, 300)


# ── 盲文 / 二值极性（Otsu 阈值必须是分割线，不是灰度级）──────────

def test_otsu_threshold_is_midpoint_not_level():
    """白底黑圆：最优 Otsu 解落在端点 bin，阈值必须取中点。

    取灰度级会让 `lum < threshold` 恒假 → braille/binary 整幅空白
    （真彩反色时则是整幅全亮）。
    """
    from termify import charset

    im = Image.new("RGB", (120, 90), (255, 255, 255))
    ImageDraw.Draw(im).ellipse([30, 20, 90, 70], fill=(0, 0, 0))
    cut, mib = charset._otsu_threshold(charset._luminance_array(im))
    assert 0 < cut < 255, cut
    assert mib is False  # 暗色少数侧 = 圆


@pytest.mark.parametrize("bg,fg,expect_lit", [
    ((255, 255, 255), (0, 0, 0), True),      # 白底黑圆：圆要点亮
    ((0, 0, 0), (255, 255, 255), True),      # 黑底白圆：圆也要点亮
])
def test_braille_and_binary_render_subject(bg, fg, expect_lit):
    from termify import charset

    im = Image.new("RGB", (120, 90), bg)
    ImageDraw.Draw(im).ellipse([30, 20, 90, 70], fill=fg)
    small = im.resize((40, 20), Image.LANCZOS)   # 引擎同款预缩放
    for cs in ("braille", "binary"):
        lines = charset.render_frame(small, cs, 40, 20)
        lit = sum(1 for ln in lines for ch in ln if ch not in "⠀ ")
        assert lit > 0, f"{cs} 整幅空白（极性反了）"
        if expect_lit:
            # 圆心所在的中段必须有笔画，边缘必须空
            mid = lines[len(lines) // 2]
            assert any(ch not in "⠀ " for ch in mid), f"{cs} 圆心没点亮"
