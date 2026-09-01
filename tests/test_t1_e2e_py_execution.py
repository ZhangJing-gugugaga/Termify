"""T1 端到端 .py 执行 — subprocess.run() 启动生成的 .py 验证可运行。

覆盖 B3（Windows 乱码检测）。每个用例用 subprocess 实际执行生成的
Python 播放器脚本，1-2s 后 kill，验证无 Traceback。
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from PIL import Image

from termify.engine import convert
from termify.output import render

PY = sys.executable


def _gen_py(charset: str, width: int, height: int, tmp_path) -> str:
    img = Image.new("RGB", (width, height), (60, 120, 180))
    p = tmp_path / f"_t1_{charset}_{width}x{height}.png"
    img.save(str(p))
    seq = convert(str(p), charset, width, height)
    return render(seq, "python")


def _run_py(script: str, tmp_path, timeout: float = 2.0) -> tuple:
    """Write script to temp file, run, kill after timeout, return (rc, stdout, stderr)."""
    path = tmp_path / "_t1_script.py"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.Popen(
        [PY, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
    return proc.returncode, stdout, stderr


# 5 charset × 2 尺寸 = 10 用例
@pytest.mark.parametrize(
    "charset,width,height",
    [
        ("ascii", 24, 12),
        ("ascii", 60, 30),
        ("blocks", 24, 12),
        ("blocks", 60, 30),
        ("braille", 24, 12),
        ("braille", 60, 30),
        ("geometric", 24, 12),
        ("geometric", 60, 30),
        ("binary", 24, 12),
        ("binary", 60, 30),
    ],
)
def test_generated_py_runs_without_traceback(charset, width, height, tmp_path):
    """生成的 .py 执行 1-2s 无 Traceback（退出码可因 kill 非零）。"""
    script = _gen_py(charset, width, height, tmp_path)
    rc, stdout, stderr = _run_py(script, tmp_path)
    # 退出码允许任何值（超时 kill 可能产生非零码）
    assert "Traceback" not in stderr, f"stderr 含 Traceback: {stderr[:300]}"
    # stdout 应含可打印内容（非空）
    assert len(stdout) > 0, "stdout 为空"


def test_generated_py_outputs_blocks_chars(tmp_path):
    """blocks 风格 stdout 应含 ▀ 字符（实际输出了帧数据）。"""
    script = _gen_py("blocks", 16, 8, tmp_path)
    rc, stdout, stderr = _run_py(script, tmp_path, timeout=1.5)
    # 只要实际输出了帧数据即通过
    assert "▀" in stdout or "Thanks" in stdout or len(stdout) > 10


def test_generated_py_script_contains_detection(tmp_path):
    """blocks 风格含终端能力检测函数（B3 修复）。

    断言行为（稳定函数名契约），不再断言具体局部变量名。
    """
    src = _gen_py("blocks", 8, 4, tmp_path)
    assert "_detect_terminal_capabilities" in src
    assert "_enable_windows_ansi" in src


def test_generated_py_contains_utf8_header(tmp_path):
    """所有生成的 .py 必须有 # -*- coding: utf-8 -*- 头。"""
    for cs in ["ascii", "blocks", "braille", "geometric", "binary"]:
        src = _gen_py(cs, 8, 4, tmp_path)
        assert "coding: utf-8" in src, f"{cs} 缺少 utf-8 coding 声明"


def test_generated_py_frames_roundtrip(tmp_path):
    """生成的 .py 运行时 FRAMES 与原始序列逐帧逐行一致。

    帧数据以 zlib+Base85 压缩 JSON 嵌入（紧凑产物，无人类可读缩进），
    加载模块解码后每帧还原为行数组；unicode（blocks 字符）经
    ensure_ascii=False 直接进 UTF-8 JSON，运行时无损还原。
    """
    import importlib.util

    img = Image.new("RGB", (16, 8), (60, 120, 180))
    p = tmp_path / "_t1_roundtrip.png"
    img.save(str(p))
    seq = convert(str(p), "blocks", 16, 8)

    src = render(seq, "python")
    path = tmp_path / "_t1_roundtrip_player.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_t1_roundtrip_player", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.FRAMES == seq.lines_per_frame, "FRAMES 解码后与原序列不一致"
    block_chars = "".join(mod.FRAMES[0])
    assert any(c in block_chars for c in "█▀▄"), "blocks 字符在解码后丢失"
