"""T21 — MP4 视频导出：SGR 解析、帧栅格化、时间预估、API 分支。

编码端到端测试在 ffmpeg 存在时才运行（与 test_t13 视频接入同约定）。
"""

from __future__ import annotations

import io
import json
import shutil

import pytest
from PIL import Image

from termify.engine import convert
from termify.output.video import (
    DEFAULT_BG,
    DEFAULT_FG,
    MAX_VIDEO_FRAMES,
    encode_mp4,
    estimate_seconds,
    frame_to_image,
    parse_ansi_line,
    pick_font,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None


# --- SGR 解析 -------------------------------------------------------------------

def test_parse_plain_text():
    parsed = parse_ansi_line("ab ")
    assert parsed == [(None, None, "a"), (None, None, "b"), (None, None, " ")]


def test_parse_fg_bg_sgr():
    line = "\x1b[38;2;255;0;0m\x1b[48;2;0;0;255m▀"
    parsed = parse_ansi_line(line)
    assert len(parsed) == 1
    fg, bg, ch = parsed[0]
    assert fg == (255, 0, 0)
    assert bg == (0, 0, 255)
    assert ch == "▀"


def test_parse_reset_restores_defaults():
    line = "\x1b[38;2;1;2;3mX\x1b[0mY"
    parsed = parse_ansi_line(line)
    assert parsed[0][0] == (1, 2, 3)
    assert parsed[1][0] is None  # reset 清掉 fg
    assert parsed[1][2] == "Y"


def test_parse_multiple_chars_share_state():
    line = "\x1b[38;2;9;9;9mab"
    parsed = parse_ansi_line(line)
    assert all(p[0] == (9, 9, 9) for p in parsed)
    assert [p[2] for p in parsed] == ["a", "b"]


# --- 帧栅格化 --------------------------------------------------------------------

def _seq_frame(width, height, charset="ascii", color=False):
    """构造一帧 ANSI 行：整屏 '█'，可选 TrueColor 包装。"""
    if color:
        row = (f"\x1b[38;2;255;176;0m\x1b[48;2;0;0;0m█" * width) + "\x1b[0m"
    else:
        row = "█" * width
    return [row] * height


def test_frame_to_image_dimensions():
    font = pick_font(14)
    from termify.output.video import _measure_cell
    cw, ch = _measure_cell(font)
    img = frame_to_image(_seq_frame(10, 5), font, cw, ch, 10 * cw, 5 * ch)
    assert img.size == (10 * cw, 5 * ch)


def test_frame_to_image_bg_fill_without_glyph():
    """空格 + SGR bg：整个 cell 应填 SGR 背景色，无字形干扰。"""
    font = pick_font(14)
    from termify.output.video import _measure_cell
    cw, ch = _measure_cell(font)
    line = "\x1b[48;2;0;0;255m " * 4
    img = frame_to_image([line, line], font, cw, ch, 4 * cw, 2 * ch)
    assert img.getpixel((0, 0)) == (0, 0, 255)
    assert img.getpixel((4 * cw - 1, 2 * ch - 1)) == (0, 0, 255)


def test_frame_to_image_default_colors_for_plain_text():
    font = pick_font(14)
    from termify.output.video import _measure_cell
    cw, ch = _measure_cell(font)
    img = frame_to_image(["    ", "    "], font, cw, ch, 4 * cw, 2 * ch)
    assert img.getpixel((0, 0)) == DEFAULT_BG
    assert img.getpixel((4 * cw - 1, 2 * ch - 1)) == DEFAULT_BG


def test_frame_to_image_draws_glyph_pixels():
    """满格字形 █ 应覆盖整个 cell：核心像素即 default 前景色。"""
    font = pick_font(14)
    from termify.output.video import _measure_cell
    cw, ch = _measure_cell(font)
    img = frame_to_image(["█"], font, cw, ch, cw, ch)
    colors = {c for _, c in (img.getcolors(maxcolors=1 << 24) or [])}
    assert DEFAULT_FG in colors  # 字形核心 = 前景色
    assert DEFAULT_BG not in colors or len(colors) > 1  # 至少有字形/抗锯齿像素


# --- 预估 / 上限 -------------------------------------------------------------------

def test_estimate_seconds_clamped():
    assert estimate_seconds(1, 10, 10) >= 2
    assert estimate_seconds(10**9, 200, 60) <= 600


def test_max_frames_constant_sane():
    assert 60 <= MAX_VIDEO_FRAMES <= 5000


# --- 端到端（需 ffmpeg）-----------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


def _upload_gif(client):
    buf = io.BytesIO()
    f0 = Image.new("RGB", (16, 16), (200, 40, 40))
    f1 = Image.new("RGB", (16, 16), (40, 40, 200))
    f0.save(buf, save_all=True, append_images=[f1], duration=100, loop=0,
            format="GIF")
    buf.seek(0)
    resp = client.post("/api/upload",
                       data={"file": (buf, "t.gif")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200
    return json.loads(resp.data)["task_id"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_generate_mp4_end_to_end(client, tmp_path):
    task_id = _upload_gif(client)
    resp = client.post("/api/generate",
                       data=json.dumps({"task_id": task_id, "charset": "blocks",
                                        "format": "mp4", "width": 40, "height": 20}),
                       content_type="application/json")
    assert resp.status_code == 200, resp.data[:300]
    body = json.loads(resp.data)
    assert body["download_url"].endswith(".mp4")
    fname = body["download_url"].rsplit("/", 1)[-1]
    out = tmp_path / "tmp" / fname
    assert out.is_file() and out.stat().st_size > 0
    # MP4 头 (ftyp box)
    assert out.read_bytes()[4:8] == b"ftyp"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_generate_mp4_custom_charset(client, tmp_path):
    task_id = _upload_gif(client)
    resp = client.post("/api/generate",
                       data=json.dumps({"task_id": task_id, "charset": "custom",
                                        "format": "mp4", "width": 40, "height": 20,
                                        "chars": "@# "}),
                       content_type="application/json")
    assert resp.status_code == 200, resp.data[:300]
    body = json.loads(resp.data)
    assert "_custom_" in body["download_url"]


def test_generate_mp4_no_ffmpeg_friendly(client, tmp_path, monkeypatch):
    """ffmpeg 不可用时返回 503 + 友好文案（强制 mock which）。"""
    import termify.output.video as vmod
    monkeypatch.setattr(vmod, "ffmpeg_available", lambda: False)
    task_id = _upload_gif(client)
    resp = client.post("/api/generate",
                       data=json.dumps({"task_id": task_id, "charset": "ascii",
                                        "format": "mp4"}),
                       content_type="application/json")
    assert resp.status_code == 503
    assert "ffmpeg" in json.loads(resp.data)["error"]
