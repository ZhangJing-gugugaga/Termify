"""Output generation tests (python + html players)."""

from __future__ import annotations

import ast
import re

import pytest

from termify import convert
from termify.engine import FrameSequence
from termify.output import render


def _make_seq():
    fs = FrameSequence(
        lines_per_frame=[["abcd", "efgh"], ["1234", "5678"]],
        interval=0.12,
        width=4,
        height=2,
        charset="ascii",
    )
    return fs


def test_render_unknown_format_raises():
    with pytest.raises(ValueError):
        render(_make_seq(), "markdown")


def test_python_output_is_parseable_py():
    py_src = render(_make_seq(), "python")
    ast.parse(py_src)


def test_python_output_embeds_compact_frames_blob():
    """帧数据以 zlib+Base85 压缩 JSON 嵌入（每帧一个字符串，行以 \\n 相连），
    解码后与原始序列逐帧一致。"""
    py_src = render(_make_seq(), "python")
    m = re.search(r'^FRAMES_B85 = "([^"]+)"', py_src, re.M)
    assert m, "缺少 FRAMES_B85 嵌入"

    import base64 as _b64
    import json as _json
    import zlib as _zlib

    joined = _json.loads(_zlib.decompress(_b64.b85decode(m.group(1))).decode("utf-8"))
    assert joined == ["\n".join(frame) for frame in _make_seq().lines_per_frame]


def test_python_output_contains_frame_interval():
    py_src = render(_make_seq(), "python")
    assert "FRAME_INTERVAL = 0.1200" in py_src


def test_python_output_has_play_function_and_main():
    py_src = render(_make_seq(), "python")
    assert re.search(r"^def play\(\)", py_src, re.M)
    assert re.search(r"^if __name__ ==", py_src, re.M)


def test_html_output_has_doctype_and_pre():
    html_src = render(_make_seq(), "html")
    assert "<!DOCTYPE html>" in html_src
    assert "<pre" in html_src


def test_html_output_has_frames_and_interval_and_tick():
    html_src = render(_make_seq(), "html")
    assert "var FRAMES = [" in html_src
    assert "var INTERVAL = 0.12 * 1000" in html_src
    assert "function tick()" in html_src or "function tick() " in html_src


def test_html_output_is_self_contained():
    html_src = render(_make_seq(), "html")
    # No external scripts/styles (no CDN, no src= for remote JS).
    assert "https://" not in html_src
    assert "<script src=" not in html_src
    assert "<link" not in html_src


def test_output_end_to_end_with_real_engine(white_png):
    seq = convert(white_png, "ascii", 8, 4)
    py_src = render(seq, "python")
    ast.parse(py_src)
    html_src = render(seq, "html")
    assert "var FRAMES = [" in html_src
