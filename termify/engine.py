"""Core conversion pipeline: extract -> scale -> map -> FrameSequence.

This is the public API that Phase 3's Flask routes will call directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from termify.charset import CHARSETS, render_frame
from termify.frames import extract_frames, fit_cells, scale_frame


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


def _pad_frame(lines: list[str], width: int, height: int) -> list[str]:
    """字符空间补齐：行内左右补空格、上下补空行（居中）。

    贴合后的画比网格小（网格比例 ≠ 源比例时），缺的部分必须是"没有字符"
    而不是"最暗的字符"——像素空间垫黑会被字符化成实心色块。
    """
    out = []
    row_pad = max(0, height - len(lines))
    top = row_pad // 2
    out.extend([" " * width] * top)
    for ln in lines:
        lead = max(0, (width - len(ln)) // 2)
        out.append(" " * lead + ln + " " * max(0, width - len(ln) - lead))
    out.extend([" " * width] * (height - top - len(lines)))
    return out


def convert(path: str, charset: str, width: int = 80, height: int = 24,
            fg_color=None, bg_color=None, charset_ramp=None,
            color_mode="mono", max_frames: int | None = None) -> FrameSequence:
    """Convert an image/GIF to a FrameSequence in the given charset.

    Pipeline (PRD §5.3):
      1. extract_frames  -> list[(RGBA, duration)]
      2. fit + scale     -> 等比贴合到字符格（1:2 格子比，见 frames.fit_cells）
      3. render_frame    -> pixel -> character lines
    All frames share one interval (first frame's, default 0.1s).
    fg_color / bg_color are optional (R,G,B) tuples; passed through to
    render_frame so non-block charsets can wrap each cell in TrueColor ANSI.
    charset_ramp is the user-supplied ramp for the "custom" charset.
    color_mode: "mono" / "source" / "source256" (see charset.render_frame).
    max_frames: 抽稀上限，在**缩放渲染前**对帧取等距子集（保留首尾），
    用来给大网格预览封顶内存（见 video.sequence_from_frames_dir 的说明）。
    """
    if charset not in CHARSETS:
        raise ValueError(
            f"Unknown charset: {charset!r} (expected one of {sorted(CHARSETS)})"
        )

    frames = extract_frames(path)
    src_w, src_h = frames[0][0].size
    fit_w, fit_h = fit_cells(src_w, src_h, width, height)
    if charset == "blocks":
        scale_w, scale_h = fit_w, fit_h * 2
    elif charset == "braille":
        scale_w, scale_h = fit_w * 2, fit_h * 4
    else:
        scale_w, scale_h = fit_w, fit_h
    if max_frames and len(frames) > max_frames and max_frames >= 2:
        last = len(frames) - 1
        step = max_frames - 1
        frames = [frames[int(round(i * last / step))] for i in range(max_frames)]
    # 比例已在字符空间贴合，像素画布直接拉到贴合尺寸（不再像素级 letterbox）
    scaled = [scale_frame(f, scale_w, scale_h, keep_aspect=False)
              for f, _ in frames]
    lines_per_frame = [
        _pad_frame(
            render_frame(s, charset, scale_w, scale_h,
                         fg_color=fg_color, bg_color=bg_color,
                         charset_ramp=charset_ramp, color_mode=color_mode),
            width, height)
        for s in scaled
    ]

    interval = frames[0][1] if frames and frames[0][1] > 0 else 0.1
    return FrameSequence(
        lines_per_frame=lines_per_frame,
        interval=interval,
        width=width,
        height=height,
        charset=charset,
    )
