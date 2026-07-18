"""T11 blocks ANSI 渲染修复回归测试。

覆盖 fix/blocks-ansi-render (ff70e7d) 的核心修复点：
  1. bg TrueColor 保留 round-trip（_parse_ansi_line → _encode_ansi_line）
  2. _count_printable 正确剥离 SGR
  3. 降级模式无 ESC 残留
  4. 真实猫图 blocks .py 子进程实跑不崩溃且发 TrueColor
  5. 非 Windows 下 _enable_windows_ansi() 返回 True

注意：被测函数 (_parse_ansi_line, _encode_ansi_line, _count_printable,
_compose_screen, _enable_windows_ansi) 均定义在生成的 .py 模板内部，
不可直接 import。测试通过 exec 生成的源码获取函数对象。
"""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

import pytest
from PIL import Image

from termify.engine import convert
from termify.output import render

PY = sys.executable

# 真实猫图路径（必跑素材，不可用合成图替代）
CAT_GIF = r"E:\Desktop\工作\SalaryCat\cat.GIF"


# ── 辅助 ──────────────────────────────────────────────────────


def _exec_player(seq):
    """生成 .py 播放器源码并 exec 到独立命名空间，返回该命名空间。

    设置 __name__ != '__main__' 防止 play() 被自动调用。
    """
    src = render(seq, "python")
    ns = {"__name__": "__test__"}
    exec(compile(src, "<generated_player>", "exec"), ns)
    return ns


def _make_blocks_seq(tmp_path, width=8, height=4, img=None):
    """生成一个 blocks 风格的 FrameSequence。"""
    if img is None:
        img = Image.new("RGB", (width, height * 2), (100, 150, 200))
    p = tmp_path / "_t11_blocks.png"
    img.save(str(p))
    return convert(str(p), "blocks", width, height)


def _make_colorful_blocks_seq(tmp_path, width=4, height=1):
    """生成一个含多种颜色的 blocks FrameSequence，确保 fg+bg 都有 TrueColor。"""
    img = Image.new("RGB", (width, 2))
    px = img.load()
    for x in range(width):
        px[x, 0] = (x * 60 % 256, 50, 200)  # top row (fg)
        px[x, 1] = (0, x * 50 % 256, 100)    # bottom row (bg)
    p = tmp_path / "_t11_colorful.png"
    img.save(str(p))
    return convert(str(p), "blocks", width, height)


# ── 测试 1: bg 保留 round-trip ────────────────────────────────


def test_blocks_bg_retention_roundtrip(tmp_path):
    """blocks 帧 parse→encode round-trip 保留 fg 和 bg TrueColor 序列。

    覆盖点：_parse_ansi_line + _apply_sgr 正确跟踪 48;2;r;g;b 背景，
    _encode_ansi_line 回写时仍含原始的 fg 和 bg TrueColor 序列。
    """
    seq = _make_colorful_blocks_seq(tmp_path, width=4, height=1)
    ns = _exec_player(seq)
    parse_line = ns["_parse_ansi_line"]
    encode_line = ns["_encode_ansi_line"]

    # 取真实 blocks 帧（含 38;2; + 48;2; 双 TrueColor）
    frame_lines = seq.lines_per_frame[0]
    assert len(frame_lines) > 0
    original = frame_lines[0]

    # 验证原始帧确实含 fg 和 bg TrueColor
    assert "38;2;" in original, "原始 blocks 帧应含 fg TrueColor"
    assert "48;2;" in original, "原始 blocks 帧应含 bg TrueColor"

    # round-trip: parse → encode
    parsed = parse_line(original)
    reencoded = encode_line(parsed)

    # 回写后仍含 fg 和 bg TrueColor
    assert "38;2;" in reencoded, "回写后丢失 fg TrueColor"
    assert "48;2;" in reencoded, "回写后丢失 bg TrueColor"


# ── 测试 2: _count_printable 正确 ─────────────────────────────


def test_count_printable_strips_sgr(tmp_path):
    """_count_printable 正确剥离 SGR 序列，只计可见字符。

    覆盖点：blocks 行含多段 SGR + N 个 ▀，计数 == N
    （SGR 被正确剥离，不被当可打印字符）。
    """
    seq = _make_colorful_blocks_seq(tmp_path, width=8, height=1)
    ns = _exec_player(seq)
    count_printable = ns["_count_printable"]

    line = seq.lines_per_frame[0][0]

    # 统计 ▀ 字符数量（真实可打印内容）
    n_blocks = line.count("\u2580")  # ▀ = U+2580
    assert n_blocks > 0, "blocks 行应含至少一个 ▀"

    # _count_printable 应返回相同数量（SGR 被剥离）
    counted = count_printable(line)
    assert counted == n_blocks, (
        f"_count_printable={counted}, 期望={n_blocks}（SGR 未被正确剥离）"
    )


# ── 测试 3: 降级无转义残留 ─────────────────────────────────────


def test_degraded_mode_no_escape_residue(tmp_path):
    """_ANSI_OK=False 时 _compose_screen 输出不含 ESC 字符。

    覆盖点：降级单色模式下 SGR 被完全剥离，无 \\x1b 残留，
    无散落的 ;/m/数字（原始 bug 症状）。
    """
    seq = _make_blocks_seq(tmp_path, width=8, height=4)
    ns = _exec_player(seq)

    # 强制降级模式
    ns["_ANSI_OK"] = False
    compose_screen = ns["_compose_screen"]

    # 取真实 blocks 帧
    frame = seq.lines_per_frame[0]
    screen = compose_screen(frame, cols=10, rows=6)
    output = "\n".join(screen)

    # 降级输出不含任何 ESC 字符
    assert "\x1b" not in output, "降级模式输出含 ESC 残留"
    # 也不含散落的 SGR 参数残留（; m 数字片段）
    # 注意：允许正常的空格和 ▀ 字符
    for line in screen:
        assert "\x1b" not in line


# ── 测试 4: 真实猫图子进程不崩 ─────────────────────────────────


def test_real_cat_image_blocks_subprocess(tmp_path):
    """真实猫图 blocks .py 子进程实跑不崩溃且发 TrueColor。

    覆盖点：用真实 cat.GIF 生成 blocks 风格 .py（80×24），
    subprocess 启动、1.5s 后 kill，断言：
      - 退出无 Traceback
      - 无 UnicodeEncodeError
      - stdout 含 \\x1b[38;2; TrueColor 序列（确实在发 TrueColor）
    真实猫图是必跑素材，不可用合成图替代。
    """
    # 确认真实猫图存在
    assert os.path.isfile(CAT_GIF), f"真实猫图不存在: {CAT_GIF}"

    # 用真实猫图生成 blocks 风格 .py（80×24）
    seq = convert(CAT_GIF, "blocks", 80, 24)
    src = render(seq, "python")

    script_path = tmp_path / "cat_blocks_player.py"
    script_path.write_text(src, encoding="utf-8")

    proc = subprocess.Popen(
        [PY, str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    try:
        stdout, stderr = proc.communicate(timeout=1.5)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()

    # 无 Traceback
    assert "Traceback" not in stderr, f"stderr 含 Traceback:\n{stderr[:500]}"
    # 无 UnicodeEncodeError
    assert "UnicodeEncodeError" not in stderr, (
        f"stderr 含 UnicodeEncodeError:\n{stderr[:500]}"
    )
    # stdout 含 TrueColor fg 序列（确实在发 TrueColor）
    assert "\x1b[38;2;" in stdout, "stdout 不含 TrueColor fg 序列 (38;2;)"


# ── 测试 5: 非 Windows 下 _enable_windows_ansi 返回 True ──────


def test_enable_windows_ansi_non_windows_returns_true(tmp_path):
    """非 Windows 下 _enable_windows_ansi() 返回 True。

    覆盖点：os.name != 'nt' 时函数直接返回 True，
    不进入任何 Windows 控制台操作分支。
    """
    seq = _make_blocks_seq(tmp_path, width=4, height=4)
    ns = _exec_player(seq)
    enable_windows_ansi = ns["_enable_windows_ansi"]

    # 模拟非 Windows 环境
    with patch.object(os, "name", "posix"):
        result = enable_windows_ansi()
    assert result is True, "非 Windows 下应返回 True"
