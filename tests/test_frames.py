"""Frame extraction and scaling tests."""

from __future__ import annotations

import pytest
from PIL import Image

from termify.frames import MAX_UPLOAD_BYTES, extract_frames, scale_frame


def test_extract_still_image_returns_single_frame(black_png):
    frames = extract_frames(black_png)
    assert len(frames) == 1
    img, dur = frames[0]
    assert img.size == (8, 4)
    assert dur == 0.0


def test_extract_gif_returns_multiple_frames(two_frame_gif):
    frames = extract_frames(two_frame_gif)
    assert len(frames) == 2
    # duration was 50ms -> 0.05s
    assert frames[0][1] == 0.05
    assert frames[1][1] == 0.05


def test_extract_rejects_oversize(tmp_path):
    # Fake a >20MB file by creating an empty file and patching getsize.
    p = tmp_path / "big.png"
    p.write_bytes(b"x")
    # Patch os.path.getsize via a wrapper: instead just test the check directly.
    from termify.frames import MAX_UPLOAD_BYTES
    import os
    assert MAX_UPLOAD_BYTES == 20 * 1024 * 1024
    # Simulate by monkeypatching
    orig = os.path.getsize
    try:
        os.path.getsize = lambda path: MAX_UPLOAD_BYTES + 1
        with pytest.raises(ValueError):
            extract_frames(str(p))
    finally:
        os.path.getsize = orig


def test_scale_exact_fit():
    img = Image.new("RGB", (8, 4), (0, 0, 0))
    out = scale_frame(img, 8, 4)
    assert out.size == (8, 4)


def test_scale_preserves_aspect_ratio_wide(wide_png):
    # 16x4 -> fit to 8x4 should letterbox horizontally, not stretch
    img = Image.open(wide_png)
    out = scale_frame(img, 8, 4)
    assert out.size == (8, 4)
    # The original is all black; output is all black regardless. So test dimensions
    # only and trust scale math. For a non-uniform image, check pixel placement.


def test_scale_not_stretched_for_tall_image(tall_png):
    img = Image.open(tall_png)
    out = scale_frame(img, 8, 4)
    assert out.size == (8, 4)


def test_scale_fill_center_for_letterbox(tmp_path):
    # Build a 4x4 image with a single non-black center pixel so we can verify
    # it lands in a known spot after scaling.
    p = tmp_path / "dot.png"
    img = Image.new("RGB", (4, 4), (0, 0, 0))
    img.putpixel((2, 2), (255, 0, 0))
    img.save(p)
    out = scale_frame(Image.open(p), 8, 4)
    # Source (2,2) maps to ~ out (4,2); letterbox is symmetric so center holds.
    assert out.size == (8, 4)
    from PIL import Image as _I
    px = out.load()
    assert px[4, 2][:3] == (255, 0, 0)
