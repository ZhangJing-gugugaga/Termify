"""Baked ascii-magic pieces: re-render a piece's character grid into any of
Termify's charsets.

A piece stores, for every cell, the original glyph and a palette key. To swap
charsets we need the brightness of each cell, which is

    luminance(palette color) x ink coverage of the glyph

Ink coverage comes from static/anim/ink.json (font metrics, braille from dot
counts). The resulting per-cell brightness becomes a small RGB image; the
brightness image keeps the palette hue so color modes still work, and it is fed
straight into charset.render_frame so pieces share the exact ramp/LUT/color
pipeline used by every other Termify image.
"""
import gzip
import json
import os

from PIL import Image

from termify.charset import render_frame
from termify.engine import FrameSequence, _pad_frame

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANIM_DIR = os.path.join(_ROOT, "static", "anim")
INK_PATH = os.path.join(ANIM_DIR, "ink.json")

DEFAULT_INTERVAL = 0.125  # 8 fps: pieces are not periodic, slower reads better
_CELL_RATIO = 2.0  # Termify cells are ~0.6em wide by ~1.3em tall

_ink_cache = None
_piece_cache = {}


def load_ink() -> dict:
    """Glyph ink coverage, keyed by decimal code point, 0-255."""
    global _ink_cache
    if _ink_cache is None:
        try:
            with open(INK_PATH, encoding="utf-8") as f:
                _ink_cache = {int(k): v for k, v in json.load(f).items()}
        except (OSError, ValueError):
            _ink_cache = {}
    return _ink_cache


def ink_value(ch: str) -> int:
    table = load_ink()
    if ch in table:
        return table[ch]
    return table.get(0x2588, 255) if ch.strip() else 0


def piece_path(slug: str) -> str:
    return os.path.join(ANIM_DIR, slug + ".json.gz")


def read_piece(slug: str) -> dict | None:
    """Load one baked piece, cached. None when the slug is unknown."""
    if not slug or os.sep in slug or ".." in slug:
        return None
    if slug in _piece_cache:
        return _piece_cache[slug]
    path = piece_path(slug)
    if not os.path.isfile(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if len(_piece_cache) > 8:
        _piece_cache.clear()
    _piece_cache[slug] = data
    return data


def _hex_rgb(value: str):
    try:
        return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))
    except (ValueError, TypeError, IndexError):
        return (255, 255, 255)


def cell_brightness(piece: dict, index: int) -> list:
    """Per-cell RGB rows where brightness = color luminance x glyph ink."""
    frame = piece["frames"][index % len(piece["frames"])]
    chars = frame["c"]
    keys = frame.get("k")
    palette = piece.get("palette") or {}
    rows = []
    for y, row in enumerate(chars):
        keys_row = keys[y] if keys and y < len(keys) else None
        cells = []
        for x, ch in enumerate(row):
            ratio = ink_value(ch) / 255.0
            if ratio <= 0:
                cells.append((0, 0, 0))
                continue
            hexv = palette.get(keys_row[x]) if keys_row and x < len(keys_row) else None
            rgb = _hex_rgb(hexv) if hexv else (255, 255, 255)
            cells.append(tuple(int(c * ratio) for c in rgb))
        rows.append(cells)
    return rows


def natural_height(piece: dict) -> int:
    """Rows the piece needs under Termify's ~1:2 cells.

    A piece grid is already a character grid, so tall-cell pieces map 1:1.
    Square-cell pieces have cells twice as tall as ours and halve.
    """
    rows = int(piece.get("rows") or 1)
    if piece.get("cell") == "square":
        return max(1, int(round(rows / _CELL_RATIO)))
    return rows


def fit_piece(piece: dict, width: int, height: int) -> tuple:
    """Grid scaled into the requested box, ratio kept in cell space.

    frames.fit_cells assumes square pixels; a piece is already character cells,
    so the ratio must be preserved here instead of being recomputed.
    """
    cols = max(1, int(piece.get("cols") or 1))
    rows = natural_height(piece)
    scale = min(width / cols, height / rows)
    return (max(1, min(width, int(round(cols * scale)))),
            max(1, min(height, int(round(rows * scale)))))


def _cell_image(piece: dict, index: int) -> Image.Image:
    cells = cell_brightness(piece, index)
    width = len(cells[0]) if cells else 1
    img = Image.new("RGB", (width, len(cells)))
    img.putdata([px for row in cells for px in row])
    return img


def sequence(piece: dict, charset: str, width: int, height: int, *,
             interval: float = 0.125, fg_color=None, bg_color=None,
             charset_ramp=None, color_mode: str = "mono",
             max_frames: int | None = None) -> FrameSequence:
    """Re-render every frame of a piece into the requested charset."""
    rows = []
    fit_w, fit_h = fit_piece(piece, width, height)
    if charset == "blocks":
        dims = (fit_w, fit_h * 2)
    elif charset == "braille":
        dims = (fit_w * 2, fit_h * 4)
    else:
        dims = (fit_w, fit_h)
    total = len(piece["frames"])
    if max_frames:
        total = min(total, max(1, int(max_frames)))
    for index in range(total):
        scaled = _cell_image(piece, index).resize(dims, Image.LANCZOS)
        lines = render_frame(scaled, charset, dims[0], dims[1],
                             fg_color=fg_color, bg_color=bg_color,
                             charset_ramp=charset_ramp, color_mode=color_mode)
        rows.append(_pad_frame(lines, width, height))
    return FrameSequence(lines_per_frame=rows, interval=interval,
                         width=width, height=height, charset=charset)


def ansi_lines(frame: dict, palette: dict) -> list:
    """One baked frame as Termify ANSI lines (thumbnail/OG rendering)."""
    chars = frame["c"]
    keys = frame.get("k")
    out = []
    for y, row in enumerate(chars):
        keys_row = keys[y] if keys and y < len(keys) else None
        parts = []
        current = None
        for x, ch in enumerate(row):
            if ch == " ":
                if current is not None:
                    parts.append("\x1b[0m")
                    current = None
                parts.append(" ")
                continue
            hexv = palette.get(keys_row[x]) if keys_row and x < len(keys_row) else None
            if not hexv:
                if current is not None:
                    parts.append("\x1b[0m")
                    current = None
                parts.append(ch)
                continue
            if hexv != current:
                r, g, b = _hex_rgb(hexv)
                parts.append("\x1b[38;2;%d;%d;%dm" % (r, g, b))
                current = hexv
            parts.append(ch)
        parts.append("\x1b[0m")
        out.append("".join(parts))
    return out
