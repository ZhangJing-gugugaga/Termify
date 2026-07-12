"""Python terminal-player generator (PRD §5.6)."""

from __future__ import annotations

import json

from termify.engine import FrameSequence


_PLAYER_TEMPLATE = '''#!/usr/bin/env python3
"""Termify generated terminal animation — {charset} style, {w}x{h}, {n} frames."""
import sys, time, os, shutil, re

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

    # Auto-scale if terminal is too small
    play_frames = FRAMES
    if FRAMES and (W > cols or char_h > rows - 1):
        target_rows = rows - 1  # leave room for status line
        target_cols = cols
        play_frames, sw, sh = _scale_frames(FRAMES, target_cols, target_rows)
        print(f"Warning: scaled {{W}}x{{char_h}} -> {{sw}}x{{sh}} to fit {{cols}}x{{rows}} terminal", file=sys.stderr)
        time.sleep(1)

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
