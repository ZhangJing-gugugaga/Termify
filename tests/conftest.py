"""Test fixtures: build images/GIFs in-memory with Pillow (no binary fixtures committed)."""

from __future__ import annotations

import io

import pytest
from PIL import Image


@pytest.fixture
def black_png(tmp_path):
    """Pure black 8x4 image."""
    p = tmp_path / "black.png"
    Image.new("RGB", (8, 4), (0, 0, 0)).save(p)
    return str(p)


@pytest.fixture
def white_png(tmp_path):
    """Pure white 8x4 image."""
    p = tmp_path / "white.png"
    Image.new("RGB", (8, 4), (255, 255, 255)).save(p)
    return str(p)


@pytest.fixture
def gray_png(tmp_path):
    """Mid-gray 8x4 image."""
    p = tmp_path / "gray.png"
    Image.new("RGB", (8, 4), (128, 128, 128)).save(p)
    return str(p)


@pytest.fixture
def two_frame_gif(tmp_path):
    """2-frame 8x4 GIF, black then white, 50ms each."""
    p = tmp_path / "two.gif"
    f0 = Image.new("RGB", (8, 4), (0, 0, 0))
    f1 = Image.new("RGB", (8, 4), (255, 255, 255))
    f0.save(p, save_all=True, append_images=[f1], duration=50, loop=0)
    return str(p)


@pytest.fixture
def wide_png(tmp_path):
    """Wide 16x4 image to exercise aspect-ratio letterboxing."""
    p = tmp_path / "wide.png"
    Image.new("RGB", (16, 4), (0, 0, 0)).save(p)
    return str(p)


@pytest.fixture
def tall_png(tmp_path):
    """Tall 4x16 image to exercise aspect-ratio letterboxing."""
    p = tmp_path / "tall.png"
    Image.new("RGB", (4, 16), (0, 0, 0)).save(p)
    return str(p)
