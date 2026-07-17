"""Per-charset mapping sanity checks (PRD §5.5, §5.3)."""

from __future__ import annotations

import pytest
from PIL import Image

from termify.charset import CHARSETS, _adaptive_lut, render_frame

ALL_CHARSETS = list(CHARSETS)


def _new(width, height, color):
    return Image.new("RGB", (width, height), color)


@pytest.mark.parametrize("name", ALL_CHARSETS)
def test_render_frame_rejects_mismatched_size(name):
    img = _new(4, 4, (0, 0, 0))
    with pytest.raises(ValueError):
        render_frame(img, name, 8, 4)


@pytest.mark.parametrize("name", ALL_CHARSETS)
def test_render_frame_rejects_unknown_charset(name):
    img = _new(4, 4, (0, 0, 0))
    with pytest.raises(ValueError):
        render_frame(img, "does-not-exist", 4, 4)


def test_ascii_black_maps_to_densest_char():
    img = _new(4, 4, (0, 0, 0))
    lines = render_frame(img, "ascii", 4, 4)
    assert all(ln == CHARSETS["ascii"]["chars"][0] * 4 for ln in lines)


def test_ascii_white_maps_to_sparsest_char():
    img = _new(4, 4, (255, 255, 255))
    lines = render_frame(img, "ascii", 4, 4)
    sparsest = CHARSETS["ascii"]["chars"][-1]
    assert all(ln == sparsest * 4 for ln in lines)


def test_binary_black_maps_to_block():
    img = _new(4, 4, (0, 0, 0))
    lines = render_frame(img, "binary", 4, 4)
    assert all(ln == "█" * 4 for ln in lines)


def test_binary_white_maps_to_space():
    img = _new(4, 4, (255, 255, 255))
    lines = render_frame(img, "binary", 4, 4)
    assert all(ln == "    " for ln in lines)


def test_geometric_has_eight_levels():
    chars = CHARSETS["geometric"]["chars"]
    assert len(chars) == 8


def test_braille_on_prescaled_input_keeps_cell_mapping():
    # Engine pre-scales braille input to (w*2, h*4); render_frame then
    # collapses by its 2x4 cell to give (w, h) output. 8x4 target ->
    # 16x16 image -> 8x4 chars.
    import re
    _ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')
    img = _new(16, 16, (0, 0, 0))
    lines = render_frame(img, "braille", 16, 16)
    assert len(lines) == 4
    # 去掉 ANSI 转义后每行应为 8 个字符
    stripped = [_ANSI_RE.sub('', ln) for ln in lines]
    assert all(len(ln) == 8 for ln in stripped)
    # All black -> full Braille block (U+28FF).
    assert all(ch == "⣿" for ln in stripped for ch in ln)


def test_blocks_emits_ansi_truecolor():
    img = _new(4, 4, (255, 0, 0))  # red
    lines = render_frame(img, "blocks", 4, 4)
    joined = "".join(lines)
    assert "▀" in joined
    assert "38;2;255;0;0" in joined
    assert "48;2;255;0;0" in joined
    # No trailing reset: each ▀ has explicit fg/bg codes,
    # reset would clear state causing next line's ▀ to render black
    assert "\x1b[0m" not in joined


@pytest.mark.parametrize(
    "name",
    [c for c in ALL_CHARSETS if c not in ("blocks", "braille")],
)
def test_render_returns_right_line_count_and_width(name):
    w, h = 12, 6
    img = _new(w, h, (50, 100, 150))
    lines = render_frame(img, name, w, h)
    assert len(lines) == h
    assert all(len(ln) == w for ln in lines)


def test_adaptive_lut_identity_for_uniform_black():
    lut = _adaptive_lut(_new(4, 4, (0, 0, 0)))
    assert lut[0] == 0


def test_adaptive_lut_identity_for_uniform_white():
    lut = _adaptive_lut(_new(4, 4, (255, 255, 255)))
    assert lut[255] == 255


def test_adaptive_lut_spreads_biased_histogram():
    img = _new(100, 100, (20, 20, 20))
    px = img.load()
    for y in range(10):
        for x in range(10):
            px[x, y] = (240, 240, 240)
    lut = _adaptive_lut(img)
    assert lut[20] < lut[240]


def test_ascii_adaptive_uses_full_range_for_biased_image():
    img = _new(100, 100, (20, 20, 20))
    px = img.load()
    for y in range(10):
        for x in range(10):
            px[x, y] = (240, 240, 240)
    lines = render_frame(img, "ascii", 100, 100)
    used = {ch for ln in lines for ch in ln}
    assert len(used) >= 2
