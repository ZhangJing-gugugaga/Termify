"""T2 端到端 HTML 渲染 — BeautifulSoup 解析验证结构完整性。

覆盖 B1/B2/B4（HTML 稀疏/溢出）。每个用例生成 HTML 后用 bs4 解析，
验证无语法错、FRAMES 非空、pre/canvas/JS 函数存在。
"""

from __future__ import annotations

import json

import pytest
from bs4 import BeautifulSoup
from PIL import Image

from termify.engine import convert
from termify.output import render


def _gen_html(charset: str, width: int, height: int, tmp_path) -> str:
    img = Image.new("RGB", (width, height), (60, 120, 180))
    p = tmp_path / f"_t2_{charset}_{width}x{height}.png"
    img.save(str(p))
    seq = convert(str(p), charset, width, height)
    return render(seq, "html")


# 5 charset × 2 尺寸 = 10 用例
@pytest.mark.parametrize(
    "charset,width,height",
    [
        ("ascii", 24, 12),
        ("ascii", 80, 40),
        ("blocks", 24, 12),
        ("blocks", 80, 40),
        ("braille", 24, 12),
        ("braille", 80, 40),
        ("geometric", 24, 12),
        ("geometric", 80, 40),
        ("binary", 24, 12),
        ("binary", 80, 40),
    ],
)
def test_html_is_valid_and_has_frames(charset, width, height, tmp_path):
    """HTML 可解析、DOCTYPE 存在、FRAMES 非空数组。"""
    src = _gen_html(charset, width, height, tmp_path)
    soup = BeautifulSoup(src, "html.parser")
    assert soup.find("!DOCTYPE") is not None or "<!DOCTYPE html>" in src
    # FRAMES 变量存在且为非空数组
    scripts = soup.find_all("script")
    assert len(scripts) >= 1
    script_text = "\n".join(s.get_text() for s in scripts)
    assert "var FRAMES = [" in script_text
    assert "]," in script_text or "];" in script_text


def test_html_has_pre_element(tmp_path):
    """非 blocks 模式应渲染 <pre> 元素。"""
    src = _gen_html("ascii", 24, 12, tmp_path)
    soup = BeautifulSoup(src, "html.parser")
    pre = soup.find("pre")
    assert pre is not None


def test_html_blocks_has_canvas(tmp_path):
    """blocks 模式应有 <canvas> 元素（JS 渲染目标）。"""
    src = _gen_html("blocks", 24, 12, tmp_path)
    soup = BeautifulSoup(src, "html.parser")
    assert soup.find("canvas") is not None


def test_html_blocks_hide_pre_when_blocks(tmp_path):
    """blocks 模式下 pre 应隐藏（display:none），canvas 显示。"""
    src = _gen_html("blocks", 24, 12, tmp_path)
    assert "display:none" in src


def test_html_self_contained_no_cdn(tmp_path):
    """HTML 完全自包含，无外部脚本/CSS。"""
    for cs in ["ascii", "blocks", "braille", "geometric", "binary"]:
        src = _gen_html(cs, 24, 12, tmp_path)
        assert "https://" not in src
        assert "<script src=" not in src
        assert "<link" not in src


def test_html_pre_has_max_height(tmp_path):
    """高分辨率下有动态字体计算防溢出（B4 修复）。"""
    src = _gen_html("ascii", 200, 60, tmp_path)
    # Check for dynamic font-size calculation function
    assert "fitTerminalFontSize" in src
    # Check for proper line-height
    assert "line-height:1.3" in src


def test_html_frames_parseable_as_json(tmp_path):
    """HTML 中 FRAMES 数据必须是合法 JSON。"""
    src = _gen_html("ascii", 16, 8, tmp_path)
    start = src.index("var FRAMES = ")
    start = src.index("[", start)
    end = src.index("];", start) + 1
    data = json.loads(src[start:end])
    assert len(data) >= 1
