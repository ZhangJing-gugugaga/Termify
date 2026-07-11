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
    assert all(ch == densest for ch in seq.lines_per_frame[0][0])
    assert all(ch == sparsest for ch in seq.lines_per_frame[1][0])
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
    for cs in CHARSETS:
        seq = convert(black_png, cs, 8, 4)
        assert seq.charset == cs
        assert len(seq.lines_per_frame) == 1
        assert len(seq.lines_per_frame[0]) >= 1  # at least one line, content width varies by charset
