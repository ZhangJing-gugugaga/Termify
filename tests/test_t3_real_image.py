"""T3 真实图片 — 用 SalaryCat cat.GIF 跑全部 charset × 多尺寸。

覆盖 B1/B2/B3。真实猫图是本轮必跑素材，不可替代。生成 .py 和 .html，
QA 验文件可读且非空。
"""

from __future__ import annotations

import os

import pytest

CAT_GIF = r"E:\Desktop\工作\SalaryCat\cat.GIF"


pytestmark = pytest.mark.skipif(
    not os.path.isfile(CAT_GIF), reason=f"真实猫图不存在: {CAT_GIF}"
)


def _convert(charset: str, width: int, height: int):
    from termify.engine import convert
    return convert(CAT_GIF, charset, width, height)


def _render(seq, fmt: str):
    from termify.output import render
    return render(seq, fmt)


# 5 charset × 3 尺寸 = 15 用例
@pytest.mark.parametrize(
    "charset,width,height",
    [
        ("ascii", 80, 24),
        ("ascii", 120, 36),
        ("ascii", 200, 60),
        ("blocks", 80, 24),
        ("blocks", 120, 36),
        ("blocks", 200, 60),
        ("braille", 80, 24),
        ("braille", 120, 36),
        ("braille", 200, 60),
        ("geometric", 80, 24),
        ("geometric", 120, 36),
        ("geometric", 200, 60),
        ("binary", 80, 24),
        ("binary", 120, 36),
        ("binary", 200, 60),
    ],
)
def test_real_cat_image_generates_valid_output(charset, width, height):
    """真实猫图 × charset × 尺寸产出有效 .py 和 .html（非空、可读）。"""
    seq = _convert(charset, width, height)
    assert seq is not None
    assert len(seq.lines_per_frame) >= 1

    py_src = _render(seq, "python")
    assert len(py_src) > 100
    assert "FRAMES" in py_src

    html_src = _render(seq, "html")
    assert len(html_src) > 100
    assert "<!DOCTYPE html>" in html_src


def test_real_cat_all_charsets_nonempty_frames():
    """真实猫图 × 5 字符集，每帧至少 1 行非空输出。"""
    for cs in ["ascii", "blocks", "braille", "geometric", "binary"]:
        seq = _convert(cs, 80, 24)
        for frame in seq.lines_per_frame:
            assert len(frame) >= 1
            assert any(line.strip() for line in frame)
