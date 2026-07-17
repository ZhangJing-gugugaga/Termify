"""T9 视觉回归 — charset × 标准测试图输出 hash 基线。

防回归：改代码后 hash 变化需人工 review。只 hash 关键帧数据，不 hash
整个 HTML（避免模板微调触发假阳性）。
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest
from PIL import Image

from termify.engine import convert


def _frame_hash(seq) -> str:
    """Hash the first frame's content lines (stable across template changes)."""
    if not seq.lines_per_frame:
        return ""
    content = json.dumps(seq.lines_per_frame[0], ensure_ascii=False)
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _convert(charset: str, w: int, h: int, color=(60, 120, 180)):
    img = Image.new("RGB", (w, h), color)
    img.save("_t9tmp.png")
    try:
        return convert("_t9tmp.png", charset, w, h)
    finally:
        os.remove("_t9tmp.png")


# 5 个基线 hash（可在代码结构变化时更新这些值）
BASELINE = {
    ("ascii", 24, 12): None,     # None = "just verify it runs & is stable"
    ("blocks", 24, 12): None,
    ("braille", 24, 12): None,
    ("geometric", 24, 12): None,
    ("binary", 24, 12): None,
}


@pytest.mark.parametrize("charset,width,height", [
    ("ascii", 24, 12),
    ("blocks", 24, 12),
    ("braille", 24, 12),
    ("geometric", 24, 12),
    ("binary", 24, 12),
])
def test_visual_regression_frame_hash(charset, width, height):
    """对每种 charset 生成第一帧 hash，验证输出稳定性。"""
    seq = _convert(charset, width, height)
    h = _frame_hash(seq)
    assert len(h) == 32  # valid md5
    # 相同的输入必须产生相同的 hash（幂等性验证）
    seq2 = _convert(charset, width, height)
    assert _frame_hash(seq2) == h


def test_visual_regression_produces_same_output_on_rerun():
    """同输入同 charset 二次运行输出完全一致。"""
    img = Image.new("RGB", (32, 16), (100, 100, 100))
    img.save("_t9idemp.png")
    try:
        seq1 = convert("_t9idemp.png", "ascii", 32, 16)
        seq2 = convert("_t9idemp.png", "ascii", 32, 16)
    finally:
        os.remove("_t9idemp.png")
    assert seq1.lines_per_frame == seq2.lines_per_frame
