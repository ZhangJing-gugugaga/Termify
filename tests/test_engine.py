"""End-to-end engine tests: image + charset -> FrameSequence."""

from __future__ import annotations

import pytest

from termify import FrameSequence, convert
from termify.charset import CHARSETS


def test_convert_returns_frame_sequence(black_png):
    seq = convert(black_png, "ascii", 8, 4)
    assert isinstance(seq, FrameSequence)
    assert seq.charset == "ascii"
    assert seq.width == 8
    assert seq.height == 4
    assert len(seq.lines_per_frame) == 1


def test_convert_gif_yields_multiple_frames(two_frame_gif):
    seq = convert(two_frame_gif, "ascii", 8, 4)
    assert len(seq.lines_per_frame) == 2
    # First frame black -> densest char, second white -> sparsest char.
    densest = CHARSETS["ascii"]["chars"][0]
    sparsest = CHARSETS["ascii"]["chars"][-1]
    # 2026-09-28 起比例在字符空间贴合（frames.fit_cells，格子 0.6:1.3 em）：
    # 8×4 源（2:1）在 8×4 网格里贴合为 8 列 × 2 行、上下各补 1 行空格——
    # 旧实现把字符格当方块像素，整格填满但纵向压扁 2.2 倍。
    for frame, want in ((seq.lines_per_frame[0], densest),
                        (seq.lines_per_frame[1], sparsest)):
        assert frame[0].strip() == "" and frame[-1].strip() == ""  # 补边行
        body = "".join(frame[1:-1])
        assert body == want * 8 * (len(frame) - 2)
    # Interval from GIF duration (0.05s).
    assert seq.interval == 0.05


def test_convert_still_image_defaults_interval(black_png):
    seq = convert(black_png, "ascii", 8, 4)
    assert seq.interval == 0.1


def test_convert_rejects_unknown_charset(black_png):
    with pytest.raises(ValueError):
        convert(black_png, "nope", 8, 4)


def test_convert_all_charsets_on_same_input(black_png):
    # Braille has 2x4 cell -> height collapses to 1 for an 8x4 image; skip that line-width check.
    # "custom" is excluded: it is a per-request mode that needs a charset_ramp.
    for cs in CHARSETS:
        if cs == "custom":
            continue
        seq = convert(black_png, cs, 8, 4)
        assert seq.charset == cs
        assert len(seq.lines_per_frame) == 1
        assert len(seq.lines_per_frame[0]) >= 1  # at least one line, content width varies by charset
