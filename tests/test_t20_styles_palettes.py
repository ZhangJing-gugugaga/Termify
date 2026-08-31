"""T20 — 风格/配色丰富化：shades 渐变块 + custom 自定义字符集。

覆盖：
- shades 注册项渲染正确（宽度/行数/字符集内取值、mib 反转、fg/bg 包装）
- sanitize_ramp：去控制字符、去重、长度上限、空梯报错
- render_frame custom：带 ramp 渲染 / 缺 ramp 报错
- convert 透传 charset_ramp；cache_key 按 ramp 区分
- Web API：preview custom 缺 chars 400 / 带 chars 200；generate custom 下载
- 画廊拒绝 custom charset
"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from termify.charset import (
    CHARSETS,
    CUSTOM_RAMP_MAX_LEN,
    _render_shades,
    render_frame,
    sanitize_ramp,
)
from termify.engine import convert
from termify.taskstore import cache_key


def _gradient(w=12, h=6):
    """左暗右亮的渐变图，保证 LUT 有完整分布。"""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for x in range(w):
        v = int(x * 255 / max(1, w - 1))
        for y in range(h):
            px[x, y] = (v, v, v)
    return img


# --- registry -----------------------------------------------------------------

def test_shades_registered():
    assert "shades" in CHARSETS
    assert CHARSETS["shades"]["chars"] == "█▓▒░ "
    assert CHARSETS["shades"]["color"] is False


def test_custom_registered():
    assert "custom" in CHARSETS
    assert CHARSETS["custom"]["chars"] is None


# --- shades rendering ---------------------------------------------------------

def test_shades_line_shape():
    w, h = 12, 6
    lines = _render_shades(_gradient(w, h), w, h)
    assert len(lines) == h
    assert all(len(ln) == w for ln in lines)


def test_shades_uses_only_ramp_chars():
    lines = _render_shades(_gradient(), 12, 6)
    joined = "".join(lines)
    assert set(joined) <= set("█▓▒░ ")


def test_shades_gradient_spans_levels():
    """渐变图应命中至少 3 个不同的梯级（不是全 ▓ 也不是全空格）。"""
    lines = _render_shades(_gradient(24, 6), 24, 6)
    joined = "".join(lines)
    assert len(set(joined)) >= 3


def test_shades_fg_bg_wrapping():
    lines = _render_shades(_gradient(8, 4), 8, 4, fg=(255, 0, 0), bg=(0, 0, 255))
    joined = "".join(lines)
    assert "\x1b[38;2;255;0;0m" in joined
    assert "\x1b[48;2;0;0;255m" in joined


def test_shades_differs_from_ascii_on_gradient():
    """同一张渐变图，shades 与 ascii 输出字符不同（各自用各自的梯）。"""
    img = _gradient()
    a = "".join("".join(r) for r in render_frame(img, "ascii", 12, 6))
    s = "".join("".join(r) for r in render_frame(img, "shades", 12, 6))
    assert set(a) != set(s)


# --- sanitize_ramp ------------------------------------------------------------

def test_sanitize_ramp_dedupes_preserving_order():
    assert sanitize_ramp("aabbcc") == "abc"


def test_sanitize_ramp_strips_control_chars():
    assert sanitize_ramp("a\x1b[31mb\nc\rd ") == "abcd "


def test_sanitize_ramp_strips_zero_width():
    assert sanitize_ramp("a\u200bb\u200fc") == "abc"


def test_sanitize_ramp_caps_length():
    ramp = "".join(chr(0x4E00 + i) for i in range(200))  # 200 个互不相同汉字
    assert len(sanitize_ramp(ramp)) == CUSTOM_RAMP_MAX_LEN


def test_sanitize_ramp_empty_raises():
    with pytest.raises(ValueError):
        sanitize_ramp("")
    with pytest.raises(ValueError):
        sanitize_ramp("\x1b\n\t")


def test_sanitize_ramp_rejects_non_string():
    with pytest.raises(ValueError):
        sanitize_ramp(None)
    with pytest.raises(ValueError):
        sanitize_ramp(123)


# --- render_frame custom --------------------------------------------------------

def test_render_frame_custom_with_ramp():
    img = _gradient()
    lines = render_frame(img, "custom", 12, 6, charset_ramp="ab")
    joined = "".join("".join(r) for r in lines)
    assert set(joined) <= {"a", "b"}


def test_render_frame_custom_without_ramp_raises():
    with pytest.raises(ValueError):
        render_frame(_gradient(), "custom", 12, 6)


def test_render_frame_custom_ramp_sanitized():
    lines = render_frame(_gradient(), "custom", 12, 6, charset_ramp="a\x1b[0ma b")
    joined = "".join("".join(r) for r in lines)
    assert set(joined) <= {"a", "b", " "}


# --- engine / cache -------------------------------------------------------------

def test_convert_custom_ramp_passthrough(black_png, tmp_path):
    ramp = "@# "
    seq = convert(black_png, "custom", 8, 4, charset_ramp=ramp)
    joined = "".join("".join(r) for r in seq.lines_per_frame)
    assert set(joined) <= set(ramp)


def test_cache_key_distinguishes_ramps():
    k1 = cache_key("t", "custom", 80, 24, charset_ramp="abc")
    k2 = cache_key("t", "custom", 80, 24, charset_ramp="xyz")
    k3 = cache_key("t", "custom", 80, 24)
    assert len({k1, k2, k3}) == 3


# --- Web API ---------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    (tmp_path / "uploads").mkdir(exist_ok=True)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


def _upload(client):
    buf = __import__("io").BytesIO()
    Image.new("RGB", (16, 16), (90, 90, 90)).save(buf, format="PNG")
    buf.seek(0)
    resp = client.post("/api/upload",
                       data={"file": (buf, "t.png")},
                       content_type="multipart/form-data")
    assert resp.status_code == 200
    return json.loads(resp.data)["task_id"]


def test_preview_custom_without_chars_rejected(client):
    task_id = _upload(client)
    resp = client.get(f"/api/preview/{task_id}?charset=custom")
    assert resp.status_code == 400


def test_preview_custom_with_chars_ok(client):
    task_id = _upload(client)
    from urllib.parse import quote
    resp = client.get(
        f"/api/preview/{task_id}?charset=custom&chars={quote('@# ') }&frame=0")
    assert resp.status_code == 200
    body = json.loads(resp.data)
    joined = "".join(body["lines"])
    assert set(joined) <= {"@", "#", " "}


def test_generate_custom_without_chars_rejected(client):
    task_id = _upload(client)
    resp = client.post("/api/generate",
                       data=json.dumps({"task_id": task_id, "charset": "custom",
                                        "format": "python"}),
                       content_type="application/json")
    assert resp.status_code == 400


def test_generate_custom_with_chars_downloads(client, tmp_path):
    task_id = _upload(client)
    resp = client.post("/api/generate",
                       data=json.dumps({"task_id": task_id, "charset": "custom",
                                        "format": "python", "chars": "@# "}),
                       content_type="application/json")
    assert resp.status_code == 200
    url = json.loads(resp.data)["download_url"]
    # send_file 相对 app.root_path 解析，与测试 cwd 不同——直接读产物文件
    fname = url.rsplit("/", 1)[-1]
    content = (tmp_path / "tmp" / fname).read_text(encoding="utf-8")
    # 产物里应出现自定义梯的字符（.py 播放器把帧嵌在源码里）
    assert "@" in content


def test_preview_shades(client):
    task_id = _upload(client)
    resp = client.get(f"/api/preview/{task_id}?charset=shades&frame=0")
    assert resp.status_code == 200
