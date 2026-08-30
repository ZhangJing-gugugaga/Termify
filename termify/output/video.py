"""MP4 video export — rasterize a FrameSequence's characters to pixels and
encode via ffmpeg (rawvideo pipe). Pure characters on a solid background,
no terminal chrome (grid/scanlines deliberately excluded per product decision).
"""

from __future__ import annotations

import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

from termify.engine import FrameSequence


class VideoEncodeError(Exception):
    """Raised when the ffmpeg encode fails or ffmpeg is unavailable."""


# Monospace font candidates, per platform. First hit wins; fall back to the
# PIL bitmap default (ugly but always available) when none can load.
_FONT_CANDIDATES = [
    # Windows
    "consola.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/cour.ttf",
    # Linux
    "DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    # macOS
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
]

DEFAULT_FG = (235, 235, 235)
DEFAULT_BG = (10, 12, 16)

# Rough throughput constant for the sync-export time estimate:
# rasterize + x264 encode of ~120k character cells per second.
_CELLS_PER_SECOND = 120_000

MAX_VIDEO_FRAMES = 600  # hard guard for the public demo


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def estimate_seconds(frame_count: int, width: int, height: int) -> int:
    """Heuristic sync-export duration estimate, clamped to 2..600 s."""
    cells = max(1, frame_count) * max(1, width) * max(1, height)
    return int(min(600, max(2, round(cells / _CELLS_PER_SECOND))))


def pick_font(size: int = 14) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def parse_ansi_line(line: str) -> list[tuple[tuple | None, tuple | None, str]]:
    """Parse an ANSI line into [(fg, bg, char), ...].

    Tracks 24-bit SGR foreground (38;2;r;g;b) and background (48;2;r;g;b),
    same semantics as the generated .py player.
    """
    chars: list[tuple[tuple | None, tuple | None, str]] = []
    fg = None
    bg = None
    i = 0
    n = len(line)
    while i < n:
        if line[i] == "\x1b" and i + 1 < n and line[i + 1] == "[":
            j = line.find("m", i + 2)
            if j == -1:
                break
            fg, bg = _apply_sgr(line[i + 2:j], fg, bg)
            i = j + 1
        else:
            chars.append((fg, bg, line[i]))
            i += 1
    return chars


def _apply_sgr(codes: str, fg, bg):
    toks = codes.split(";") if codes else ["0"]
    k = 0
    while k < len(toks):
        t = toks[k]
        if t in ("", "0"):
            fg = None
            bg = None
        elif t == "39":
            fg = None
        elif t == "49":
            bg = None
        elif t == "38" and k + 1 < len(toks) and toks[k + 1] == "2":
            if k + 4 < len(toks):
                try:
                    fg = (int(toks[k + 2]), int(toks[k + 3]), int(toks[k + 4]))
                except ValueError:
                    pass
                k += 4
        elif t == "48" and k + 1 < len(toks) and toks[k + 1] == "2":
            if k + 4 < len(toks):
                try:
                    bg = (int(toks[k + 2]), int(toks[k + 3]), int(toks[k + 4]))
                except ValueError:
                    pass
                k += 4
        k += 1
    return fg, bg


def _measure_cell(font) -> tuple[int, int]:
    if hasattr(font, "getmetrics"):
        ascent, descent = font.getmetrics()
        char_h = max(1, ascent + descent)
    else:
        char_h = max(1, font.size + 2) if hasattr(font, "size") else 12
    if hasattr(font, "getlength"):
        char_w = max(1, round(font.getlength("M")))
    else:
        char_w = max(1, getattr(font, "size", 8))
    return char_w, char_h


def frame_to_image(lines: list[str], font, char_w: int, char_h: int,
                   out_w: int, out_h: int,
                   default_fg=DEFAULT_FG, default_bg=DEFAULT_BG) -> Image.Image:
    """Rasterize one ANSI frame (list of lines) onto an RGB image."""
    img = Image.new("RGB", (out_w, out_h), default_bg)
    draw = ImageDraw.Draw(img)
    for y, line in enumerate(lines):
        x = 0
        for fg, bg, ch in parse_ansi_line(line):
            if x >= out_w // char_w:
                break
            color = fg if fg is not None else default_fg
            bg_fill = bg if bg is not None else default_bg
            if bg != default_bg:
                draw.rectangle(
                    [x * char_w, y * char_h, (x + 1) * char_w - 1, (y + 1) * char_h - 1],
                    fill=bg_fill,
                )
            draw.text((x * char_w, y * char_h), ch, fill=color, font=font)
            x += 1
    return img


def encode_mp4(seq: FrameSequence, out_path: str, font_size: int = 14) -> str:
    """Rasterize every frame and pipe raw RGB into ffmpeg -> H.264 MP4.

    Returns the output path; raises VideoEncodeError on failure.
    """
    if not ffmpeg_available():
        raise VideoEncodeError("ffmpeg is not available on this host")

    font = pick_font(font_size)
    char_w, char_h = _measure_cell(font)
    width = max(1, min(200, seq.width))
    height = max(1, min(60, seq.height))
    # yuv420p needs even dimensions
    out_w = max(2, (width * char_w) // 2 * 2)
    out_h = max(2, (height * char_h) // 2 * 2)
    fps = int(min(30, max(1, round(1.0 / seq.interval)))) if seq.interval > 0 else 10

    lines_per_frame = seq.lines_per_frame[:MAX_VIDEO_FRAMES]

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{out_w}x{out_h}", "-pix_fmt", "rgb24",
        "-r", str(fps), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        assert proc.stdin is not None
        for lines in lines_per_frame:
            img = frame_to_image(lines, font, char_w, char_h, out_w, out_h)
            proc.stdin.write(img.tobytes())
        proc.stdin.close()
    except BrokenPipeError:
        pass
    stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    ret = proc.wait()
    if ret != 0:
        raise VideoEncodeError(f"ffmpeg exited {ret}: {stderr[:300]}")
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise VideoEncodeError("ffmpeg produced no output")
    return out_path
