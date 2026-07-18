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
_ANSI_OK = True  # set by play(); False => degrade blocks to monochrome

_ANSI_RE = re.compile('\\x1b[^\\x1b]*?m')
_STRIP_RE = re.compile('\\x1b[^\\x1b]*?m')


def _count_printable(line):
    """Count printable characters in an ANSI-encoded line."""
    return len(_STRIP_RE.sub('', line))


# ── ANSI helpers ──────────────────────────────────────────────

def _parse_ansi_line(line):
    """Parse an ANSI line into [(fg, bg, char), ...].

    Tracks both the 24-bit foreground (38;2;r;g;b) and background
    (48;2;r;g;b) SGR colours so half-block styles keep their colours.
    """
    chars = []
    fg = None
    bg = None
    i = 0
    n = len(line)
    while i < n:
        if line[i] == '\\033' and i + 1 < n and line[i + 1] == '[':
            j = line.find('m', i + 2)
            if j == -1:
                break
            fg, bg = _apply_sgr(line[i + 2:j], fg, bg)
            i = j + 1
        else:
            chars.append((fg, bg, line[i]))
            i += 1
    return chars


def _apply_sgr(codes, fg, bg):
    """Apply one SGR parameter group to (fg, bg) and return the pair."""
    toks = codes.split(';') if codes else ['0']
    k = 0
    while k < len(toks):
        t = toks[k]
        if t == '' or t == '0':
            fg = None
            bg = None
        elif t == '39':
            fg = None
        elif t == '49':
            bg = None
        elif t == '38' and k + 1 < len(toks) and toks[k + 1] == '2':
            if k + 4 < len(toks):
                try:
                    fg = (int(toks[k + 2]), int(toks[k + 3]), int(toks[k + 4]))
                except ValueError:
                    pass
                k += 4
        elif t == '48' and k + 1 < len(toks) and toks[k + 1] == '2':
            if k + 4 < len(toks):
                try:
                    bg = (int(toks[k + 2]), int(toks[k + 3]), int(toks[k + 4]))
                except ValueError:
                    pass
                k += 4
        k += 1
    return fg, bg


def _encode_ansi_line(parsed_chars):
    """Encode [(fg, bg, char), ...] back to an ANSI string.

    Re-emits both the 24-bit foreground and background SGR so half-block
    glyphs (e.g. ▀) show the correct top/bottom colours.
    """
    parts = []
    last_fg = None
    last_bg = None
    for fg, bg, ch in parsed_chars:
        if fg != last_fg or bg != last_bg:
            if fg is None and bg is None:
                parts.append('\\033[0m')
            else:
                if fg is not None:
                    parts.append('\\033[38;2;{{}};{{}};{{}}m'.format(fg[0], fg[1], fg[2]))
                if bg is not None:
                    parts.append('\\033[48;2;{{}};{{}};{{}}m'.format(bg[0], bg[1], bg[2]))
            last_fg = fg
            last_bg = bg
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
            text_only = [(fg, bg, ch) for fg, bg, ch in parsed if ord(ch) > 31]
            new_frame.append(_scale_ansi_line(text_only, target_w))
        fitted.append(new_frame)

    return fitted, target_w, target_h_chars


# ── Screen composition ────────────────────────────────────────

def _compose_screen(scaled_frame, cols, rows, bg_color=None):
    """Centre scaled_frame on a cols×rows screen with background fill.

    When colour ANSI is unavailable (_ANSI_OK is False) every SGR sequence is
    stripped and plain spaces are used, yielding a readable monochrome
    fallback instead of garbled escape text.
    """
    color = _ANSI_OK
    content_rows = len(scaled_frame)
    content_cols = _count_printable(scaled_frame[0]) if scaled_frame else 0

    top_pad = max(0, (rows - content_rows) // 2)
    left_pad = max(0, (cols - content_cols) // 2)

    if color:
        blank = '\\x1b[49m' + ' ' * cols + '\\x1b[0m'
    else:
        blank = ' ' * cols
    screen = []

    for _ in range(top_pad):
        screen.append(blank)

    for line in scaled_frame:
        if not color:
            line = _STRIP_RE.sub('', line)
        pw = _count_printable(line)
        rpad = max(0, cols - left_pad - pw)
        if color:
            screen.append(
                '\\x1b[49m' + ' ' * left_pad + line +
                (' ' * rpad if rpad > 0 else '') + '\\x1b[0m'
            )
        else:
            screen.append(' ' * left_pad + line + (' ' * rpad if rpad > 0 else ''))

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
    """Best-effort enable TrueColor / VT100 on the active output stream.

    Returns:
        True  - colour ANSI will be rendered. Either VT was enabled on a real
                console, or the stream is a non-console PTY/pipe whose terminal
                (e.g. MinTTY / git-bash) interprets ANSI natively.
        False - a classic console was detected but VT could NOT be enabled
                (very old Windows). Caller should degrade to monochrome.
    """
    if os.name != 'nt':
        return True
    try:
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        STD_OUTPUT_HANDLE = -11
        STD_ERROR_HANDLE = -12
        INVALID_HANDLE = 0xFFFFFFFFFFFFFFFF
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if handle == INVALID_HANDLE:
            handle = kernel32.GetStdHandle(STD_ERROR_HANDLE)
        if handle == INVALID_HANDLE:
            return False
        mode = ctypes.c_uint32()
        # GetConsoleMode fails for pipes/PTY streams -> the terminal itself
        # (MinTTY, tmux, etc.) handles ANSI, so treat that as "capable".
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return True
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        if kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING):
            return True
        return False
    except Exception:
        return False


def _setup_output_encoding():
    """Ensure stdout/stderr use UTF-8 so Unicode glyphs (▀ etc.) render.

    Setting the *encoding* only controls how characters become bytes; it does
    NOT affect ANSI escape interpretation (ESC is ASCII). Safe on Windows
    Terminal, conhost and PTYs alike.
    """
    if os.name != 'nt':
        return
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        try:
            import io
            sys.stdout = io.TextIOWrapper(
                sys.stdout.buffer, encoding='utf-8', errors='replace'
            )
            sys.stderr = io.TextIOWrapper(
                sys.stderr.buffer, encoding='utf-8', errors='replace'
            )
        except Exception:
            pass



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
    _setup_output_encoding()
    ansi_capable = _enable_windows_ansi()
    global _ANSI_OK
    _ANSI_OK = ansi_capable

    ansi_ok, unicode_ok = _detect_terminal_capabilities()

    if CHARSET == "blocks" and not ansi_capable:
        sys.stderr.write(
            "⚠ 终端未启用 TrueColor/ANSI 支持，blocks 风格已降级为单色显示。\\n"
            "  建议改用 HTML 格式下载，用浏览器打开可获得完整彩色效果。\\n"
            "  按 Ctrl+C 退出；或忽略本提示继续。\\n\\n"
        )
        sys.stderr.flush()
    elif CHARSET in ("geometric", "braille") and os.name == 'nt':
        # Windows terminals often lack proper Unicode font support
        if not unicode_ok:
            sys.stderr.write(
                "⚠ {charset} 风格需要终端支持 Unicode 字符。\\n"
                "  如果看到乱码或方框，请尝试以下方法：\\n"
                "  1. 使用 Windows Terminal（推荐）\\n"
                "  2. 安装 Nerd Font 字体（如 JetBrainsMono Nerd Font）\\n"
                "  3. 改用 HTML 格式下载，用浏览器打开\\n"
                "  按 Ctrl+C 退出；或忽略本提示继续。\\n\\n".format(charset=CHARSET)
            )
            sys.stderr.flush()
    
    _start_audio()

    if _ANSI_OK:
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
                if _ANSI_OK:
                    output = []
                    for i, line in enumerate(screen):
                        output.append('\\x1b[{{}};1H{{}}'.format(i + 1, line))
                    sys.stdout.write(''.join(output))
                    sys.stdout.write('\\x1b[0m')
                else:
                    sys.stdout.write('\\n'.join(screen) + '\\n')
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
        if _ANSI_OK:
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
