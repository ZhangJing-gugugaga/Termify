"""Python terminal-player generator (PRD §5.6)."""

from __future__ import annotations

import json

from termify.engine import FrameSequence


_PLAYER_TEMPLATE = '''#!/usr/bin/env python3
"""Termify generated terminal animation — {charset} style, {w}x{h}, {n} frames."""
import sys, time, os

FRAMES = {frames}

FRAME_INTERVAL = {interval:.4f}


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def play():
    try:
        while True:
            for frame in FRAMES:
                clear_screen()
                print('\\n'.join(frame))
                time.sleep(FRAME_INTERVAL)
    except KeyboardInterrupt:
        clear_screen()
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
