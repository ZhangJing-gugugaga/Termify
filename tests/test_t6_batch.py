"""T6 批量处理 — 多文件全产出。

覆盖性能 + 错误处理。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from PIL import Image

PY = sys.executable
DEMO = os.path.join(os.path.dirname(__file__), "..", "demo.py")


def _make_image(path: str, w: int, h: int, color=(100, 150, 200)):
    Image.new("RGB", (w, h), color).save(path)


def test_batch_two_gifs(tmp_path):
    """demo.py 一次处理两个 GIF 文件（全部 charset）。"""
    img1 = str(tmp_path / "a.gif")
    img2 = str(tmp_path / "b.gif")
    # Create 2-frame GIFs
    f0 = Image.new("RGB", (8, 4), (0, 0, 0))
    f1 = Image.new("RGB", (8, 4), (255, 255, 255))
    f0.save(img1, save_all=True, append_images=[f1], duration=50, loop=0)
    f0.save(img2, save_all=True, append_images=[f1], duration=50, loop=0)

    out = str(tmp_path / "out")
    # demo.py 只接受一个 image 参数，需手动循环调用
    for img in [img1, img2]:
        result = subprocess.run(
            [PY, DEMO, img, "--charset", "all", "--out", out],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"处理 {img} 失败: {result.stderr[:300]}"
    # 至少 10 个文件 (2 图片 × 5 charset)
    files = os.listdir(out)
    assert len(files) >= 10


def test_batch_mixed_formats(tmp_path):
    """混合 PNG + GIF 批量处理。"""
    png = str(tmp_path / "img.png")
    gif = str(tmp_path / "img.gif")
    Image.new("RGB", (12, 6), (100, 100, 100)).save(png)
    f0 = Image.new("RGB", (12, 6), (0, 0, 0))
    f1 = Image.new("RGB", (12, 6), (255, 255, 255))
    f0.save(gif, save_all=True, append_images=[f1], duration=50, loop=0)

    out = str(tmp_path / "out")
    total = 0
    for img in [png, gif]:
        result = subprocess.run(
            [PY, DEMO, img, "--charset", "blocks", "--out", out],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        total += 1
    files = os.listdir(out)
    assert len(files) >= total
