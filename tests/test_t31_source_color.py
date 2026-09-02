"""T31 — 原色（source color）模式 + 256 色量化。

服务端契约（termify/charset.py + app.py color 参数）：
- quantize_256 已知值：纯色 → cube 端点，纯灰 → 灰阶层
- color_mode=source：非 block 字符集逐字符 38;2 SGR，run-length 同色合并，
  空格不上色，行尾 reset；纯黑图 binary 的 fg 提升到可见地板 (56,56,56)
- color_mode=source256：38;5;N SGR
- blocks 忽略 color_mode（本身即原色）
- mono 输出与不传 color_mode 字节一致（向后兼容硬保证）
- 非法 color_mode → ValueError
- API：/api/preview?color=source 200 帧含 38;2；color=bogus → 400 双语；
  /api/generate color=source256 → py 产物含 38;5；缓存按 color_mode 区分
"""

from __future__ import annotations

import importlib.util
import io
import json

import pytest
from PIL import Image

from termify.charset import quantize_256, render_frame
from termify.frames import scale_frame

pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("flask"),
    reason="flask 未安装",
)


# ── quantize_256 ─────────────────────────────────────────────

@pytest.mark.parametrize("rgb,want", [
    ((0, 0, 0), 16),
    ((255, 255, 255), 231),
    ((255, 0, 0), 196),
    ((0, 255, 0), 46),
    ((0, 0, 255), 21),
    ((128, 128, 128), 244),
])
def test_quantize_256_known_values(rgb, want):
    assert quantize_256(*rgb) == want


def test_quantize_256_output_range():
    for rgb in [(1, 2, 3), (90, 200, 30), (250, 250, 250), (33, 66, 99)]:
        idx = quantize_256(*rgb)
        assert 16 <= idx <= 255


# ── render_frame color_mode ──────────────────────────────────

def _img(w=4, h=4, color=(255, 0, 0)):
    return Image.new("RGB", (w, h), color)


def _four_square():
    """2x2 quartered image: red / green / near-black / blue."""
    img = Image.new("RGB", (2, 2))
    img.putpixel((0, 0), (255, 0, 0))
    img.putpixel((1, 0), (0, 255, 0))
    img.putpixel((0, 1), (10, 10, 10))
    img.putpixel((1, 1), (0, 0, 255))
    return img


@pytest.mark.parametrize("charset", ["ascii", "shades", "geometric", "binary"])
def test_source_mode_emits_truecolor_sgr(charset):
    lines = render_frame(_img(), charset, 4, 4, color_mode="source")
    assert any("\x1b[38;2;" in ln for ln in lines)
    assert all(ln.endswith("\x1b[0m") for ln in lines if "\x1b[38;2;" in ln)


@pytest.mark.parametrize("charset", ["ascii", "shades", "geometric", "binary"])
def test_source256_mode_emits_256_sgr(charset):
    lines = render_frame(_img(), charset, 4, 4, color_mode="source256")
    assert any("\x1b[38;5;" in ln for ln in lines)
    assert "\x1b[38;2;" not in "".join(lines)


def test_source_mode_run_length_merges_same_color():
    # 纯红图 → 每行只应有 1 个 fg SGR（run-length），而不是每字符一个
    lines = render_frame(_img(4, 4, (255, 0, 0)), "ascii", 4, 4,
                         color_mode="source")
    for ln in lines:
        assert ln.count("\x1b[38;2;") == 1


def test_source_mode_spaces_not_colored():
    # ascii 稀疏端是空格；白色图经自适应 LUT 后大部分映射到空格
    lines = render_frame(_img(4, 4, (255, 255, 255)), "ascii", 4, 4,
                         color_mode="source")
    assert any(ln.strip() == "" or " " in ln for ln in lines)
    for ln in lines:
        body = ln.replace("\x1b[0m", "")
        # 不应出现「SGR 紧跟空格」的组合（空格不上色）
        assert "\x1b[0m " not in ln


def test_source_mode_black_boosted_visible():
    # 纯黑图 binary：█ 全黑 fg 不可见 → 必须提升到地板亮度 56
    lines = render_frame(_img(4, 4, (0, 0, 0)), "binary", 4, 4,
                         color_mode="source")
    assert any("\x1b[38;2;56;56;56m" in ln for ln in lines)


def test_source_mode_braille_colored_and_reset():
    img = Image.new("RGB", (4, 8), (0, 0, 0))
    for y in range(8):
        for x in range(4):
            if (x + y) % 2 == 0:
                img.putpixel((x, y), (255, 0, 0))
    scaled = scale_frame(img, 8, 16)
    lines = render_frame(scaled, "braille", 8, 16, color_mode="source")
    assert any("\x1b[38;2;" in ln for ln in lines)
    # 红黑混合点亮的 cell 平均色为暗红 → 量化后 38;5;124
    l256 = render_frame(scaled, "braille", 8, 16, color_mode="source256")
    assert "\x1b[38;5;" in "".join(l256)


def test_blocks_ignores_color_mode():
    a = render_frame(_img(), "blocks", 4, 4)
    b = render_frame(_img(), "blocks", 4, 4, color_mode="source")
    assert a == b


def test_mono_mode_byte_identical_to_default():
    img = _four_square()
    for charset in ["ascii", "shades", "geometric", "binary"]:
        scaled = scale_frame(img, 4, 4)
        assert (render_frame(scaled, charset, 4, 4)
                == render_frame(scaled, charset, 4, 4, color_mode="mono"))


def test_mono_braille_byte_identical_with_fg_bg():
    # 审查指出的缺口：braille 单色路径（含 fg/bg 着色）必须与缺省字节一致
    img = Image.new("RGB", (4, 8), (0, 0, 0))
    for y in range(8):
        for x in range(4):
            if (x + y) % 2 == 0:
                img.putpixel((x, y), (255, 0, 0))
    scaled = scale_frame(img, 8, 16)
    for fg, bg in [(None, None), ((0, 255, 65), None), ((255, 176, 0), (10, 14, 20))]:
        assert (render_frame(scaled, "braille", 8, 16, fg_color=fg, bg_color=bg)
                == render_frame(scaled, "braille", 8, 16, fg_color=fg,
                                bg_color=bg, color_mode="mono"))


def test_rgb_or_none_rejects_out_of_range():
    from app import _rgb_or_none
    assert _rgb_or_none([0, 0, 0]) == (0, 0, 0)
    assert _rgb_or_none((255, 255, 255)) == (255, 255, 255)
    assert _rgb_or_none("rgb(1,2,3)") == (1, 2, 3)
    assert _rgb_or_none([256, 0, 0]) is None      # 越界
    assert _rgb_or_none([0, -1, 0]) is None       # 负值
    assert _rgb_or_none([1, 2]) is None           # 长度不足
    assert _rgb_or_none(["a", 2, 3]) is None      # 非数字
    assert _rgb_or_none("junk") is None           # 非 rgb() 字符串
    assert _rgb_or_none(None) is None


def test_invalid_color_mode_raises():
    with pytest.raises(ValueError, match="color_mode"):
        render_frame(_img(), "ascii", 4, 4, color_mode="bogus")


def test_custom_charset_source_mode():
    lines = render_frame(_img(), "custom", 4, 4, charset_ramp="@#",
                         color_mode="source")
    assert any("\x1b[38;2;" in ln for ln in lines)


# ── API 层 ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMIFY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TERMIFY_TASK_DB", str(tmp_path / "tasks_t31.db"))

    from termify.taskstore import cache_clear_all, reset_store_for_tests

    cache_clear_all()
    reset_store_for_tests()
    import app as app_mod

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


def _gif_bytes(n_frames=1, w=8, h=4):
    buf = io.BytesIO()
    frames = [Image.new("RGB", (w, h), (i * 60, 100, 150)) for i in range(n_frames)]
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:],
                   duration=50, loop=0)
    buf.seek(0)
    return buf


def _upload(client):
    resp = client.post(
        "/api/upload",
        data={"file": (_gif_bytes(), "anim.gif")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    return json.loads(resp.data)["task_id"]


def test_api_preview_color_source(client):
    task_id = _upload(client)
    resp = client.get(f"/api/preview/{task_id}?charset=ascii&width=8&height=4&color=source")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert any("\x1b[38;2;" in ln for frame in body["frames"] for ln in frame)


def test_api_preview_color_invalid_400_bilingual(client):
    task_id = _upload(client)
    resp = client.get(f"/api/preview/{task_id}?charset=ascii&color=bogus")
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert "color" in body["error"] and "mono" in body["error"]


def test_api_generate_color_source256_py_has_ansi256(client):
    task_id = _upload(client)
    resp = client.post("/api/generate", json={
        "task_id": task_id, "charset": "ascii", "format": "python",
        "width": 8, "height": 4, "color": "source256",
    })
    assert resp.status_code == 200
    filename = json.loads(resp.data)["download_url"].rsplit("/", 1)[-1]
    from termify.paths import tmp_dir
    content = open(f"{tmp_dir()}/{filename}", encoding="utf-8").read()
    # 帧数据是 zlib+Base85 压缩 blob → 解压后检查 ANSI-256 序列
    import base64
    import re
    import zlib
    m = re.search(r'FRAMES_B85 = "([^"]+)"', content)
    assert m, "FRAMES_B85 blob not found in py product"
    raw = zlib.decompress(base64.b85decode(m.group(1))).decode("utf-8")
    assert "38;5;" in raw
    # 播放器自带 xterm-256 调色板解析（自包含产物）
    assert "_xterm256_rgb" in content


def test_api_cache_distinguishes_color_modes(client):
    task_id = _upload(client)
    r1 = client.get(f"/api/preview/{task_id}?charset=ascii&width=8&height=4&color=mono")
    r2 = client.get(f"/api/preview/{task_id}?charset=ascii&width=8&height=4&color=source")
    b1, b2 = json.loads(r1.data), json.loads(r2.data)
    assert b1["frames"] != b2["frames"]
    # 再取一次 mono，命中缓存且仍是 mono（不被 source 覆盖）
    r3 = client.get(f"/api/preview/{task_id}?charset=ascii&width=8&height=4&color=mono")
    assert json.loads(r3.data)["frames"] == b1["frames"]


def test_api_generate_artifact_names_carry_color_tag(client):
    """source/source256 产物名带 _src/_src256 段，与 mono 互不覆盖。"""
    import os
    task_id = _upload(client)
    r_mono = client.post("/api/generate", json={
        "task_id": task_id, "charset": "ascii", "format": "html",
        "width": 8, "height": 4})
    r_src = client.post("/api/generate", json={
        "task_id": task_id, "charset": "ascii", "format": "html",
        "width": 8, "height": 4, "color": "source"})
    r_256 = client.post("/api/generate", json={
        "task_id": task_id, "charset": "ascii", "format": "html",
        "width": 8, "height": 4, "color": "source256"})
    assert all(r.status_code == 200 for r in (r_mono, r_src, r_256))
    names = [json.loads(r.data)["download_url"] for r in (r_mono, r_src, r_256)]
    assert names[0] == f"/api/download/{task_id}_ascii.html"
    assert names[1] == f"/api/download/{task_id}_ascii_src.html"
    assert names[2] == f"/api/download/{task_id}_ascii_src256.html"
    assert len(set(names)) == 3
    assert all(os.path.isfile(os.path.join("tmp", n.rsplit("/", 1)[-1]))
               for n in names)
