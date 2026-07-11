"""Core conversion pipeline: extract -> scale -> map -> FrameSequence.

This is the public API that Phase 3's Flask routes will call directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from termify.charset import CHARSETS, render_frame
from termify.frames import extract_frames, scale_frame


@dataclass
class FrameSequence:
    """A fully-rendered animation, ready for bundling into player output.

    lines_per_frame: outer list is frames, inner list is text lines (no \n).
    interval: seconds between frames (from the GIF, or 0.1 for stills).
    width / height: terminal character dimensions.
    charset: key into CHARSETS.
    """

    lines_per_frame: list[list[str]]
    interval: float
    width: int
    height: int
    charset: str


def convert(path: str, charset: str, width: int = 80, height: int = 24) -> FrameSequence:
    """Convert an image/GIF to a FrameSequence in the given charset.

    Pipeline (PRD §5.3):
      1. extract_frames  -> list[(RGBA, duration)]
      2. scale_frame     -> resized to width x height (letterboxed)
      3. render_frame    -> pixel -> character lines
    All frames share one interval (first frame's, default 0.1s).
    """
    if charset not in CHARSETS:
        raise ValueError(
            f"Unknown charset: {charset!r} (expected one of {sorted(CHARSETS)})"
        )

    frames = extract_frames(path)
    # Half-block charsets (blocks) sample paired rows, so they need 2x the
    # vertical resolution to produce `height` terminal rows. Everything else
    # scales straight to (width, height).
    needs_double_height = (charset == "blocks")
    scale_h = height * 2 if needs_double_height else height
    scaled = [scale_frame(f, width, scale_h) for f, _ in frames]
    lines_per_frame = [render_frame(s, charset, width, scale_h) for s in scaled]

    interval = frames[0][1] if frames and frames[0][1] > 0 else 0.1
    return FrameSequence(
        lines_per_frame=lines_per_frame,
        interval=interval,
        width=width,
        height=height,
        charset=charset,
    )
