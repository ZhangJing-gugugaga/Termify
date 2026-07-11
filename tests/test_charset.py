"""Per-charset mapping sanity checks (PRD §5.5, §5.3)."""

from __future__ import annotations

import pytest
from PIL import Image

from termify.charset import CHARSETS, render_frame

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
    # Densest char in CHARSETS["ascii"]["chars"] is at index 0.
    img = _new(4, 4, (0, 0, 0))
    lines = render_frame(img, "ascii", 4, 4)
    assert all(ln == CHARSETS["ascii"]["chars"][0] * 4 for ln in lines)


def test_ascii_white_maps_to_sparsest_char():
    # Sparsest char in CHARSETS["ascii"]["chars"] is the last one.
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


def test_braille_output_half_width_quarter_height():
    # 8x4 source mapped via Braille (2x4 cell) -> 4x1 chars.
    img = _new(8, 4, (0, 0, 0))
    lines = render_frame(img, "braille", 8, 4)
    assert len(lines) == 1
    assert len(lines[0]) == 4
    # All black should give full Braille block (U+28FF).
    assert all(ch == "⣿" for ch in lines[0])


def test_blocks_emits_ansi_truecolor():
    img = _new(4, 4, (255, 0, 0))  # red
    lines = render_frame(img, "blocks", 4, 4)
    joined = "".join(lines)
    # Should carry the ANSI red fg+bg codes and the upper-half block char.
    assert "▀" in joined
    assert "38;2;255;0;0" in joined
    assert "48;2;255;0;0" in joined
    assert "\x1b[0m" in joined


@pytest.mark.parametrize("name", [c for c in ALL_CHARSETS if c not in ("braille", "blocks")])
def test_render_returns_right_line_count_and_width(name):
    w, h = 12, 6
    img = _new(w, h, (50, 100, 150))
    lines = render_frame(img, name, w, h)
    assert len(lines) == h
    assert all(len(ln) == w for ln in lines)


def test_braille_reduces_resolution_by_2x4_cell():
    # 12x6 source through Braille (2x4 cells) -> ceil(12/2) wide, ceil(6/4) high = 6x1.
    w, h = 12, 6
    img = _new(w, h, (50, 100, 150))
    lines = render_frame(img, "braille", w, h)
    assert len(lines) == max(1, h // 4)
    assert all(len(ln) == w // 2 for ln in lines)
