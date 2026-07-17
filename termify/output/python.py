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

播放自动适应终端大小，拖拽窗口边缘实时跟随。
可选音频：同目录放 music.mp3 自动播放（零依赖，调系统播放器）。

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
  - 终端窗口越大，显示效果越好。窗口改变时动画自动缩放。
  - 将 music.mp3 放在本文件同目录下，播放时自动伴奏。
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

import time, os, shutil, re, subprocess as _sp

FRAMES = {frames}

FRAME_INTERVAL = {interval:.4f}
W, H = {w}, {h}
CHARSET = "{charset}"

_ANSI_RE = re.compile(r'\\033\\[[0-9;]*m')
_STRIP_RE = re.compile(r'\\033\\[[0-9;]*m')


def _count_printable(line):
    """Count printable characters in an ANSI-encoded line."""
    return len(_STRIP_RE.sub('', line))


# ── ANSI helpers ──────────────────────────────────────────────

def _parse_ansi_line(line):
    """Parse ANSI line into [(fg_tuple_or_None, char), ...]."""
    chars = []
    fg = None
    i = 0
    while i < len(line):
        if line[i] == '\\033' and i + 1 < len(line) and line[i + 1] == '[':
            j = line.index('m', i + 2) if 'm' in line[i + 2:] else -1
            if j == -1:
                break
            j += i + 2
            codes = line[i + 2:j]
            if codes == '0':
                fg = None
            elif codes.startswith('38;2;'):
                parts = codes.split(';')
                if len(parts) >= 5:
                    try:
                        fg = (int(parts[2]), int(parts[3]), int(parts[4]))
                    except ValueError:
                        pass
            i = j + 1
        else:
            chars.append((fg, line[i]))
            i += 1
    return chars


def _encode_ansi_line(parsed_chars):
    """Encode [(fg, char), ...] back to ANSI string."""
    parts = []
    last_fg = None
    for fg, ch in parsed_chars:
        if fg != last_fg:
            if fg is None:
                parts.append('\\033[0m')
            else:
                parts.append('\\033[38;2;{{}};{{}};{{}}m'.format(fg[0], fg[1], fg[2]))
            last_fg = fg
        parts.append(ch)
    return ''.join(parts)


def _scale_ansi_line(parsed_chars, target_w):
    """Nearest-neighbour scale a parsed line to target_w printable chars."""
    n = len(parsed_chars)
    if n == 0:
        return ''
    if target_w == n:
        return _encode_ansi_line(parsed_chars)
    step = n / target_w
    sampled = [parsed_chars[int(i * step)] for i in range(target_w)]
    return _encode_ansi_line(sampled)


# ── Frame fitting ─────────────────────────────────────────────

def _fit_frames(frames, target_cols, target_rows, charset):
    """Bidirectional proportional fit. Returns (fitted_frames, fw, fh)."""
    if not frames:
        return frames, 0, 0

    src_cols = _count_printable(frames[0][0]) if frames[0] else 0
    src_rows = len(frames[0])
    if src_cols == 0 or src_rows == 0:
        return frames, 0, 0

    char_rows = src_rows // 2 if charset == "blocks" else src_rows

    fit_scale = min(target_cols / src_cols, target_rows / char_rows)
    target_w = max(1, int(src_cols * fit_scale))
    target_h_chars = max(1, int(char_rows * fit_scale))

    if charset == "blocks":
        target_pixel_rows = target_h_chars * 2
    else:
        target_pixel_rows = target_h_chars

    fitted = []
    for frame in frames:
        # vertical sample
        pixel_indices = [int(i * src_rows / target_pixel_rows)
                         for i in range(target_pixel_rows)]
        pixel_indices = [min(idx, src_rows - 1) for idx in pixel_indices]
        new_frame = []
        for ri in pixel_indices:
            line = frame[ri] if ri < len(frame) else ''
            parsed = _parse_ansi_line(line)
            text_only = [(fg, ch) for fg, ch in parsed if ord(ch) > 31]
            new_frame.append(_scale_ansi_line(text_only, target_w))
        fitted.append(new_frame)

    return fitted, target_w, target_h_chars


# ── Screen composition ────────────────────────────────────────

def _compose_screen(scaled_frame, cols, rows, bg_color=None):
    """Centre scaled_frame on a cols×rows screen with background fill."""
    content_rows = len(scaled_frame)
    content_cols = _count_printable(scaled_frame[0]) if scaled_frame else 0

    top_pad = max(0, (rows - content_rows) // 2)
    left_pad = max(0, (cols - content_cols) // 2)

    blank = '\\x1b[49m' + ' ' * cols + '\\x1b[0m'
    screen = []

    for _ in range(top_pad):
        screen.append(blank)

    for line in scaled_frame:
        pw = _count_printable(line)
        rpad = max(0, cols - left_pad - pw)
        screen.append(
            '\\x1b[49m' + ' ' * left_pad + line +
            (' ' * rpad if rpad > 0 else '') + '\\x1b[0m'
        )

    bottom_done = top_pad + content_rows
    for _ in range(rows - bottom_done):
        screen.append(blank)

    return screen


# ── Windows ANSI ──────────────────────────────────────────────

def _detect_terminal_capabilities():
    """Probe environment for ANSI + Unicode support signals.

    Returns (ansi_ok, unicode_ok). On non-Windows we assume both are fine.
    On Windows we check environment hints (WT_SESSION / COLORTERM) and the
    console codepage to decide whether to warn the user.
    """
    if os.name != 'nt':
        return True, True
    unicode_ok = stdout_codepage() in ('cp65001', 'utf-8', 'utf8')
    ansi_ok = bool(os.environ.get('WT_SESSION') or os.environ.get('COLORTERM'))
    return ansi_ok, unicode_ok


def stdout_codepage():
    """Return the active output codepage string, or None on failure."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        return 'cp%d' % k32.GetConsoleOutputCP()
    except Exception:
        try:
            import subprocess as _sp
            out = _sp.check_output(['chcp'], shell=True, stderr=_sp.DEVNULL)
            token = out.decode('ascii', errors='ignore').strip().split(':')[-1].strip().rstrip('.')
            return 'cp' + token
        except Exception:
            return None


def _enable_windows_ansi():
    """Enable VT100 processing on Windows consoles. Returns True on success."""
    if os.name != 'nt':
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.GetStdHandle(-11)
        m = ctypes.c_ulong()
        k32.GetConsoleMode(h, ctypes.byref(m))
        k32.SetConsoleMode(h, m.value | 0x0004)
        return True
    except Exception:
        return False



def _get_terminal_size():
    """Get terminal size. Uses Windows API on nt for reliability."""
    if os.name == 'nt':
        try:
            import ctypes as _ct
            k32 = _ct.windll.kernel32
            h = k32.GetStdHandle(-11)
            class _CSBI(_ct.Structure):
                _fields_ = [
                    ('dwSize', _ct.c_long * 2),
                    ('dwCursorPosition', _ct.c_long * 2),
                    ('wAttributes', _ct.c_ushort),
                    ('srWindow', _ct.c_short * 4),
                    ('dwMaximumWindowSize', _ct.c_long * 2),
                ]
            csbi = _CSBI()
            if k32.GetConsoleScreenBufferInfo(h, _ct.byref(csbi)):
                w = csbi.srWindow[2] - csbi.srWindow[0] + 1
                r = csbi.srWindow[3] - csbi.srWindow[1] + 1
                if w > 0 and r > 0:
                    return os.terminal_size((w, r))
        except Exception:
            pass
    return shutil.get_terminal_size((80, 24))
# ── Audio ─────────────────────────────────────────────────────

_audio_proc = None


def _find_audio_player():
    """Return a player command prefix, or None."""
    if sys.platform == 'darwin':
        return ['afplay']
    if os.name == 'nt':
        return 'powershell'
    for cmd in ['ffplay', 'mpv', 'mpg123', 'cvlc', 'play']:
        if shutil.which(cmd):
            return [cmd]
    return None


def _start_audio():
    """Look for music.mp3 next to this script and play it. Returns proc or None."""
    global _audio_proc
    script_dir = os.path.dirname(os.path.abspath(__file__))
    audio = os.path.join(script_dir, 'music.mp3')
    if not os.path.isfile(audio):
        return None
    player = _find_audio_player()
    if not player:
        return None
    try:
        if player == 'powershell':
            ps = (
                "$p = New-Object System.Windows.Media.MediaPlayer; "
                "$p.Open('{{}}'); "
                "$p.Play(); "
                "while($p.NaturalDuration.HasTimeSpan -eq $false) {{ Start-Sleep -Milliseconds 100 }}; "
                "$dur = $p.NaturalDuration.TimeSpan.TotalSeconds; "
                "Start-Sleep -Seconds $dur"
            ).format(audio.replace("'", "''"))
            _audio_proc = _sp.Popen(
                ['powershell', '-NoProfile', '-Command', ps],
                stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
        else:
            _audio_proc = _sp.Popen(
                player + [audio],
                stdin=_sp.DEVNULL, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
        return _audio_proc
    except Exception:
        return None


def _stop_audio():
    """Terminate audio playback."""
    global _audio_proc
    if _audio_proc is not None:
        try:
            _audio_proc.terminate()
            _audio_proc.wait(timeout=2)
        except Exception:
            try:
                _audio_proc.kill()
            except Exception:
                pass
        _audio_proc = None


# ── Main playback ─────────────────────────────────────────────

def play():
    ansi_ok, unicode_ok = _detect_terminal_capabilities()
    ansi_enabled = _enable_windows_ansi()
    if CHARSET == "blocks" and (not ansi_enabled or not unicode_ok):
        sys.stderr.write(
            "⚠ 终端未通过 ANSI/Unicode 检测，blocks 风格可能显示乱码。\\n"
            "  建议：改用 HTML 格式下载，用浏览器打开即可获得最佳效果。\\n"
            "  按 Ctrl+C 退出；或忽略本提示继续。\\n\\n"
        )
        sys.stderr.flush()
    _start_audio()

    sys.stdout.write('\\x1b[?25l\\x1b[?1049h\\x1b[2J')
    sys.stdout.flush()
    try:
        cached_size = None
        while True:
            size = _get_terminal_size()
            if size != cached_size:
                cached_size = size
                fitted, fw, fh = _fit_frames(
                    FRAMES, size.columns, size.lines - 1, CHARSET
                )

            for frame in fitted:
                new_size = _get_terminal_size()
                if new_size != cached_size:
                    cached_size = new_size
                    fitted, fw, fh = _fit_frames(
                        FRAMES, new_size.columns, new_size.lines - 1, CHARSET
                    )
                    break

                screen = _compose_screen(
                    frame, cached_size.columns, cached_size.lines
                )
                output = []
                for i, line in enumerate(screen):
                    output.append('\\x1b[{{}};1H{{}}'.format(i + 1, line))
                sys.stdout.write(''.join(output))
                sys.stdout.write('\\x1b[0m')
                sys.stdout.flush()
                # Sleep in small chunks, check for 'r' key (manual refresh)
                _elapsed = 0.0
                while _elapsed < FRAME_INTERVAL:
                    _chunk = min(0.05, FRAME_INTERVAL - _elapsed)
                    time.sleep(_chunk)
                    _elapsed += _chunk
                    if os.name == 'nt':
                        try:
                            import msvcrt as _m
                            while _m.kbhit():
                                if _m.getch() == b'r':
                                    cached_size = None
                                    break
                        except Exception:
                            pass
                    if cached_size is None:
                        break
    except KeyboardInterrupt:
        pass
    finally:
        _stop_audio()
        sys.stdout.write('\\x1b[?1049l\\x1b[?25h')
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
