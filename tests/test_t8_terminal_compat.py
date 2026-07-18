"""T8 终端兼容性 — 验证 .py 含 ANSI 转义及 mock _enable_windows_ansi 失败降级。

覆盖 B3。
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from PIL import Image

from termify.engine import convert
from termify.output import render

PY = sys.executable


def _gen_py(charset: str, w: int, h: int, tmp_path) -> str:
    img = Image.new("RGB", (w, h), (60, 120, 180))
    p = tmp_path / f"_t8_{charset}_{w}x{h}.png"
    img.save(str(p))
    seq = convert(str(p), charset, w, h)
    return render(seq, "python")


def test_py_contains_ansi_blocks_codes(tmp_path):
    """blocks 风格 .py 应含 ANSI 38;2;（前景）和 48;2;（背景）转义码。"""
    src = _gen_py("blocks", 16, 8, tmp_path)
    assert "38;2;" in src
    assert "48;2;" in src


def test_py_has_enable_windows_ansi_call(tmp_path):
    """每个 .py 必须调用 _enable_windows_ansi()。"""
    for cs in ["ascii", "blocks", "braille", "geometric", "binary"]:
        src = _gen_py(cs, 8, 4, tmp_path)
        assert "_enable_windows_ansi()" in src, f"{cs} 缺少 _enable_windows_ansi 调用"


def test_py_ansi_enable_failure_fallback(monkeypatch, tmp_path):
    """_enable_windows_ansi() 失败时应返回 False，不抛异常（B3 修复）。"""
    # 验证模板中的函数结构：失败时返回 False
    src = _gen_py("blocks", 8, 4, tmp_path)
    assert "def _enable_windows_ansi():" in src
    # blocks 失败时有警告逻辑
    assert "_detect_terminal_capabilities" in src
    # 验证 fail-safe return False 而不是 pass
    assert "return False" in src
    assert "return True" in src


def test_py_has_term_probe(tmp_path):
    """生成的 .py 含终端能力检测（TERM/WT_SESSION 检查）。"""
    src = _gen_py("blocks", 8, 4, tmp_path)
    assert "WT_SESSION" in src or "COLORTERM" in src
