"""Frame extraction (GIF / still image) and terminal-fit scaling."""

from __future__ import annotations

import os

from PIL import Image, ImageSequence

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # PRD §7.1

# 终端字符格的宽高比（em）：与前端 fitTerminalFontSize / fitViewFontSize 的
# charRatio=0.6 / lineHeightRatio=1.3 同源。引擎此前把 1 个字符格当 1×1
# 方块像素用——非方形素材一律纵向拉长 ~2.2 倍、两侧留下成对实心黑边条
# （用户截图里图片字符化"那两根竖着的东西"就是黑边条被字符化成最密字符）。
CELL_W_EM = 0.6
CELL_H_EM = 1.3


def fit_cells(src_w: int, src_h: int, width: int, height: int) -> tuple[int, int]:
    """源图在 width×height 字符网格里的等比贴合尺寸（单位：字符格）。

    网格本身假设装得下源比例（调用方给定的网格宽高比不必等于源比例，
    贴不满的部分由调用方在**字符空间**用空格补齐——绝不在像素空间垫黑，
    那会被字符化成实心色块）。
    """
    if src_w <= 0 or src_h <= 0 or width <= 0 or height <= 0:
        return max(1, width), max(1, height)
    # (fw·CELL_W) / (fh·CELL_H) = sw/sh  →  fw/fh = CELL_H·sw / (CELL_W·sh)
    cols_per_row = (CELL_H_EM * src_w) / (CELL_W_EM * src_h)
    fh = min(height, max(1, round(width / cols_per_row)))
    fw = min(width, max(1, round(fh * cols_per_row)))
    return max(1, fw), max(1, fh)


def extract_frames(path: str) -> list[tuple[Image.Image, float]]:
    """Return list of (RGBA frame, duration_seconds).

    - GIF: one entry per frame via ImageSequence.Iterator, duration from
      frame info["duration"] (ms). Falls back to 0.1s when unspecified.
    - Still image (PNG/JPG/etc): single frame with duration 0.0.
    Raises ValueError when the file exceeds MAX_UPLOAD_BYTES.
    """
    size = os.path.getsize(path)
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"File {size} bytes exceeds {MAX_UPLOAD_BYTES} byte cap (PRD §7.1)"
        )

    img = Image.open(path)
    frames: list[tuple[Image.Image, float]] = []

    n = getattr(img, "n_frames", 1)
    if n and n > 1:
        for frame in ImageSequence.Iterator(img):
            duration_ms = frame.info.get("duration", 100)
            frames.append((frame.convert("RGBA"), duration_ms / 1000.0))
    else:
        frames.append((img.convert("RGBA"), 0.0))

    return frames


def scale_frame(
    img: Image.Image,
    target_w: int,
    target_h: int,
    keep_aspect: bool = True,
) -> Image.Image:
    """Scale image to fit target_w x target_h.

    With keep_aspect=True, preserves aspect ratio and letterboxes in black
    (so the image is never stretched). Uses LANCZOS resampling.
    """
    if not keep_aspect:
        return img.resize((target_w, target_h), Image.LANCZOS)

    src_w, src_h = img.size
    scale = min(target_w / src_w, target_h / src_h)
    fit_w = max(1, round(src_w * scale))
    fit_h = max(1, round(src_h * scale))
    resized = img.resize((fit_w, fit_h), Image.LANCZOS)

    # Letterbox onto a black target_w x target_h canvas, centered.
    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 255))
    offset = ((target_w - fit_w) // 2, (target_h - fit_h) // 2)
    if resized.mode == "RGBA":
        canvas.paste(resized, offset, resized)
    else:
        canvas.paste(resized, offset)
    return canvas
