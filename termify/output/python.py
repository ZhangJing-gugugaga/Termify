"""Python terminal-player generator (PRD §5.6)."""

from __future__ import annotations

import json

from termify.engine import FrameSequence


_PLAYER_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Termify 终端动画播放器
======================
风格: {charset} | 尺寸: {w}x{h} | {n} 帧

【运行前准备】
  需要 Python 3.6 或更高版本。
  检查方法: 打开终端/PowerShell，输入 python --version
  如果没有 Python: 前往 https://www.python.org/downloads/ 下载安装
   安装时务必勾选 "Add Python to PATH"

【如何运行】
  Windows:  打开 PowerShell，cd 到本文件所在目录，输入 python 本文件名.py
  macOS:    打开 Terminal，cd 到本文件所在目录，输入 python3 本文件名.py
  Linux:    打开 Terminal，cd 到本文件所在目录，输入 python3 本文件名.py

【如何停止】
  按 Ctrl+C 即可退出播放

【小贴士】
  - 终端窗口越大，显示效果越好。窗口太小时会自动缩放。
  - 如果看到乱码或颜色不对，试试换成 HTML 格式下载，用浏览器打开。
  - 本文件无任何第三方依赖，只需 Python 本身。
"""

import sys

# Python 版本检查
if sys.version_info < (3, 6):
    print("错误: 需要 Python 3.6 或更高版本。")
    print("当前版本: " + sys.version)
    print("请前往 https://www.python.org/downloads/ 下载安装最新版。")
    input("按 Enter 退出...")
    sys.exit(1)

import time, os, shutil, re

FRAMES = {frames}

FRAME_INTERVAL = {interval:.4f}
W, H = {w}, {h}
CHARSET = "{charset}"


def _count_printable(line):
    """Count printable characters in an ANSI-encoded line."""
    return len(re.subn('\\033\\\\[[0-9;]*m', '', line)[0])


def _scale_frames(frames, target_cols, target_rows):
    """Scale ANSI-encoded frames to fit terminal. Returns (scaled_frames, scaled_w, scaled_h)."""
    if not frames:
        return frames, 0, 0

    src_h = len(frames[0])
    src_w = _count_printable(frames[0][0]) if frames[0] else 0
    if src_w == 0 or src_h == 0:
        return frames, src_w, src_h

    # No scaling needed
    if src_w <= target_cols and src_h <= target_rows:
        return frames, src_w, src_h

    # Calculate scale ratios
    x_ratio = src_w / target_cols if src_w > target_cols else 1.0
    y_ratio = src_h / target_rows if src_h > target_rows else 1.0

    def _parse_line(line):
        """Parse ANSI line into list of (r,g,b_or_None, char) tuples."""
        chars = []
        fg = None
        remaining = line
        while remaining:
            m = re.match('\\033\\\\[([0-9;]*)m', remaining)
            if m:
                codes = m.group(1)
                if codes == '0':
                    fg = None
                elif codes.startswith('38;2;'):
                    parts = codes.split(';')
                    fg = (int(parts[2]), int(parts[3]), int(parts[4]))
                remaining = remaining[m.end():]
            else:
                ch = remaining[0]
                chars.append((fg, ch))
                remaining = remaining[1:]
        return chars

    def _sample_line(line, ratio):
        """Sample characters from an ANSI line by ratio."""
        if ratio <= 1.0:
            return line
        parsed = _parse_line(line)
        text_chars = [(fg, ch) for fg, ch in parsed if ord(ch) > 31]
        if not text_chars:
            return line
        step = len(text_chars) / target_cols
        sampled_indices = [int(i * step) for i in range(target_cols)]
        sampled_indices = [min(idx, len(text_chars) - 1) for idx in sampled_indices]
        result_parts = []
        last_fg = None
        for idx in sampled_indices:
            fg, ch = text_chars[idx]
            if fg != last_fg:
                if fg is None:
                    result_parts.append('\\033[0m')
                else:
                    result_parts.append(f'\\033[38;2;{{fg[0]}};{{fg[1]}};{{fg[2]}}m')
                last_fg = fg
            result_parts.append(ch)
        return ''.join(result_parts)

    # Scale height: pick row indices
    row_indices = [int(i * (src_h / target_rows)) for i in range(target_rows)]
    row_indices = [min(idx, src_h - 1) for idx in row_indices]

    scaled = []
    for frame in frames:
        scaled_frame = []
        for ri in row_indices:
            line = frame[ri] if ri < len(frame) else ''
            if x_ratio > 1.0:
                line = _sample_line(line, x_ratio)
            scaled_frame.append(line)
        scaled.append(scaled_frame)

    return scaled, target_cols, target_rows


def play():
    cols, rows = shutil.get_terminal_size((80, 24))
    # For blocks mode, each char row encodes 2 pixel rows, so char height = H//2
    char_h = H // 2 if CHARSET == "blocks" else H

    # Auto-scale to fit terminal — resolution is source quality, not output size
    play_frames = FRAMES
    if FRAMES and (W > cols or char_h > rows - 1):
        play_frames, _, _ = _scale_frames(FRAMES, cols, rows - 1)

    # Hide cursor, use alternate screen buffer for clean playback
    sys.stdout.write('\\033[?25l')  # hide cursor
    sys.stdout.write('\\033[?1049h')  # alternate screen
    sys.stdout.flush()
    try:
        while True:
            for frame in play_frames:
                sys.stdout.write('\\033[H')  # cursor home — no flicker
                sys.stdout.write('\\n'.join(frame))
                sys.stdout.write('\\033[K')  # clear to end of line
                sys.stdout.flush()
                time.sleep(FRAME_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write('\\033[?1049l')  # restore screen
        sys.stdout.write('\\033[?25h')  # show cursor
        sys.stdout.flush()
        print("Thanks for using Termify!")


if __name__ == '__main__':
    play()
'''


def render(sequence: FrameSequence) -> str:
    """Generate a self-contained .py player script as a string."""
    # JSON gives us correct escaping of every unicode char and backslash.
    frames_json = json.dumps(sequence.lines_per_frame, ensure_ascii=False, indent=2)
    return _PLAYER_TEMPLATE.format(
        charset=sequence.charset,
        w=sequence.width,
        h=sequence.height,
        n=len(sequence.lines_per_frame),
        frames=frames_json,
        interval=sequence.interval,
    )
