"""T5 CLI — demo.py 命令行批处理验证。

覆盖全场景端到端。
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


def test_cli_single_file_all_charsets(tmp_path):
    """demo.py file --charset all 一次生成全部风格文件。"""
    img = str(tmp_path / "test.png")
    _make_image(img, 16, 8)
    out = str(tmp_path / "out")
    result = subprocess.run(
        [PY, DEMO, img, "--charset", "all", "--out", out],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr[:300]}"
    files = os.listdir(out)
    assert len(files) >= 5  # 至少 5 个 charset 的输出


def test_cli_parametric_width_height(tmp_path):
    """--width / --height 参数化生效。"""
    img = str(tmp_path / "test.png")
    _make_image(img, 32, 16)
    out = str(tmp_path / "out")
    result = subprocess.run(
        [PY, DEMO, img, "--charset", "ascii", "--width", "40", "--height", "20", "--out", out],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"stderr: {result.stderr[:300]}"


def test_cli_default_charset(tmp_path):
    """不指定 --charset 时应使用默认值（ascii）。"""
    img = str(tmp_path / "test.png")
    _make_image(img, 12, 6)
    out = str(tmp_path / "out")
    result = subprocess.run(
        [PY, DEMO, img, "--out", out],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    files = os.listdir(out)
    assert any("ascii" in f for f in files)
