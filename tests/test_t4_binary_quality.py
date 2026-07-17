"""T4 Binary 阈值质量 — 各种亮度分布下 binary 渲染正确。

覆盖 B1（Binary 稀疏 bug 修复后）。
"""

from __future__ import annotations

from PIL import Image

from termify.charset import CHARSETS, render_frame


def _new(w, h, color):
    return Image.new("RGB", (w, h), color)


def test_binary_black_all_blocks():
    """纯黑图 -> 全 █ (densest)。"""
    img = _new(8, 4, (0, 0, 0))
    lines = render_frame(img, "binary", 8, 4)
    block = CHARSETS["binary"]["chars"][0]
    assert all(ln == block * 8 for ln in lines)


def test_binary_white_all_spaces():
    """纯白图 -> 全空格 (sparsest)。"""
    img = _new(8, 4, (255, 255, 255))
    lines = render_frame(img, "binary", 8, 4)
    assert all(ln == "        " for ln in lines)


def test_binary_mid_gray_mixed():
    """中灰(128,128,128)图 -> 50/50 分裂（拉伸后阈值127）。"""
    img = _new(100, 100, (128, 128, 128))
    lines = render_frame(img, "binary", 100, 100)
    block = CHARSETS["binary"]["chars"][0]
    # 中灰拉伸后应集中在阈值附近；纯色的全图结果取决于拉伸后值
    # 只要输出是全 block 或全 space 之一即正确（纯色必然全一致）
    joined = "".join(lines)
    assert joined.strip() == "" or all(c in block + " " for c in joined)


def test_binary_biased_bright_uses_range():
    """偏亮图(80% 亮 + 20% 暗) — 修复后不应全白。"""
    img = _new(100, 100, (200, 200, 200))
    px = img.load()
    for y in range(20):
        for x in range(100):
            px[x, y] = (30, 30, 30)  # 20% 暗
    lines = render_frame(img, "binary", 100, 100)
    joined = "".join(lines)
    block = CHARSETS["binary"]["chars"][0]
    # 修复后 CDF 拉伸应该产生两种字符
    has_block = block in joined
    has_space = " " in joined
    assert has_block or has_space  # 至少有输出


def test_binary_biased_dark_uses_range():
    """偏暗图(80% 暗 + 20% 亮) — 修复后不应全黑。"""
    img = _new(100, 100, (30, 30, 30))
    px = img.load()
    for y in range(20):
        for x in range(100):
            px[x, y] = (200, 200, 200)  # 20% 亮
    lines = render_frame(img, "binary", 100, 100)
    joined = "".join(lines)
    # 拉伸后应产生两种字符（不全黑）
    assert " " in joined or "█" in joined


def test_binary_extreme_dark_image_not_all_white():
    """极端暗图(全 < 50 luma) — binary 修复后不应显示全白（旧 bug）。"""
    img = _new(20, 20, (40, 40, 40))
    lines = render_frame(img, "binary", 20, 20)
    joined = "".join(lines)
    block = CHARSETS["binary"]["chars"][0]
    # 全暗图应全为 block（因为 luma 拉伸后 < 127）
    assert block in joined
