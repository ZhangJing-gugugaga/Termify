"""Python terminal-player generator (PRD §5.6)."""

from __future__ import annotations

import json

from termify.engine import FrameSequence


_PLAYER_TEMPLATE = '''#!/usr/bin/env python3
"""Termify generated terminal animation — {charset} style, {w}x{h}, {n} frames."""
import sys, time, os, shutil

FRAMES = {frames}

FRAME_INTERVAL = {interval:.4f}
W, H = {w}, {h}


def play():
    # Check terminal size
    cols, rows = shutil.get_terminal_size((80, 24))
    if cols < W or rows < H + 1:
        print(f"Warning: terminal {{cols}}x{{rows}} is smaller than {{W}}x{{H}}. Output may be clipped.", file=sys.stderr)
        print("Tip: resize your terminal or regenerate with smaller dimensions.", file=sys.stderr)
        time.sleep(1.5)

    # Hide cursor, use alternate screen buffer for clean playback
    sys.stdout.write('\\033[?25l')  # hide cursor
    sys.stdout.write('\\033[?1049h')  # alternate screen
    sys.stdout.flush()
    try:
        while True:
            for frame in FRAMES:
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
