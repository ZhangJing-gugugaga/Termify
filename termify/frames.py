"""Frame extraction (GIF / still image) and terminal-fit scaling."""

from __future__ import annotations

import os

from PIL import Image, ImageSequence

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # PRD §7.1


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
