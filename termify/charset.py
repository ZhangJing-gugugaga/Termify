"""Character-set definitions and per-frame pixel -> character mapping.

Authoritative spec: PRD.md §5.5 (CHARSETS) and §5.3 (mapping pipeline).
"""

from __future__ import annotations

CHARSETS: dict[str, dict] = {
    "ascii": {
        "name": "经典ASCII灰度",
        "chars": "@#%*+=-:. ",  # dense -> sparse (black -> white)
        "color": False,
        "description": "最复古的味道，任何终端都能显示",
    },
    "blocks": {
        "name": "Unicode色块",
        "chars": "█▀▄",  # used with TrueColor ANSI
        "color": True,
        "description": "视觉冲击力最强，需要终端支持24位色",
    },
    "braille": {
        "name": "Braille点阵",
        "chars": "⠁⠂⠄⡀⠈⠐⠠⢀⣀⠉⠠⠄⡁⢀⣀⠘⠒⠤⣀⣄⣆⣇⣧⣷⣿",
        "color": False,
        "description": "分辨率高，科技感十足",
    },
    "geometric": {
        "name": "几何图形",
        "chars": "■□▪▫●○◆◇",  # 8 levels
        "color": False,
        "description": "现代设计感",
    },
    "binary": {
        "name": "极简二值",
        "chars": "█ ",  # thresholded
        "color": False,
        "description": "纯黑白，像老式报纸印刷",
    },
}


def _luminance(r: int, g: int, b: int) -> int:
    # ITU-R BT.601 luma
    return round(0.299 * r + 0.587 * g + 0.114 * b)


def _render_ascii(img, width: int, height: int) -> list[str]:
    chars = CHARSETS["ascii"]["chars"]
    n = len(chars)
    px = img.load()
    lines = []
    for y in range(height):
        row = []
        for x in range(width):
            r, g, b = px[x, y][:3]
            gray = _luminance(r, g, b)
            idx = gray * (n - 1) // 255  # 0 -> densest char
            row.append(chars[idx])
        lines.append("".join(row))
    return lines


def _render_blocks(img, width: int, height: int) -> list[str]:
    """Upper-half block `▀` with TrueColor fg (top pixel) + bg (bottom pixel)."""
    px = img.load()
    src_w, src_h = img.size
    out_lines = []
    for row_idx in range(height):
        y_top = int(row_idx * src_h / height)
        y_bot = int((row_idx + 0.5) * src_h / height)
        if y_bot >= src_h:
            y_bot = src_h - 1
        row = []
        for col_idx in range(width):
            x = int(col_idx * src_w / width)
            if x >= src_w:
                x = src_w - 1
            rt, gt, bt = px[x, y_top][:3]
            rb, gb, bb = px[x, y_bot][:3]
            row.append(f"\x1b[38;2;{rt};{gt};{bt}m\x1b[48;2;{rb};{gb};{bb}m▀")
        row.append("\x1b[0m")
        out_lines.append("".join(row))
    return out_lines


def _render_braille(img, width: int, height: int) -> list[str]:
    """2x4 pixel block -> one Braille codepoint (U+2800 + bit mask)."""
    px = img.load()
    src_w, src_h = img.size
    cell_w, cell_h = 2, 4
    out_w = max(1, width // cell_w)
    out_h = max(1, height // cell_h)
    dots = [
        (0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04),
        (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20),
        (0, 3, 0x40), (1, 3, 0x80),
    ]
    lines = []
    for by in range(out_h):
        row = []
        for bx in range(out_w):
            bits = 0
            for dx, dy, mask in dots:
                sx = int((bx * cell_w + dx) * src_w / (out_w * cell_w))
                sy = int((by * cell_h + dy) * src_h / (out_h * cell_h))
                if sx >= src_w:
                    sx = src_w - 1
                if sy >= src_h:
                    sy = src_h - 1
                r, g, b = px[sx, sy][:3]
                if _luminance(r, g, b) < 128:
                    bits |= mask
            row.append(chr(0x2800 + bits))
        lines.append("".join(row))
    return lines


def _render_geometric(img, width: int, height: int) -> list[str]:
    chars = CHARSETS["geometric"]["chars"]
    n = len(chars)
    px = img.load()
    lines = []
    for y in range(height):
        row = []
        for x in range(width):
            r, g, b = px[x, y][:3]
            gray = _luminance(r, g, b)
            idx = gray * (n - 1) // 255
            row.append(chars[idx])
        lines.append("".join(row))
    return lines


def _render_binary(img, width: int, height: int) -> list[str]:
    px = img.load()
    lines = []
    for y in range(height):
        row = []
        for x in range(width):
            r, g, b = px[x, y][:3]
            row.append("█" if _luminance(r, g, b) < 128 else " ")
        lines.append("".join(row))
    return lines


_RENDERERS = {
    "ascii": _render_ascii,
    "blocks": _render_blocks,
    "braille": _render_braille,
    "geometric": _render_geometric,
    "binary": _render_binary,
}


def render_frame(img, charset_name: str, width: int, height: int) -> list[str]:
    """Map a PIL.Image (already scaled to width x height) to text lines.

    Returns list of line strings (no trailing newline; caller adds as needed).
    """
    if charset_name not in CHARSETS:
        raise ValueError(
            f"Unknown charset: {charset_name!r} (expected one of {sorted(CHARSETS)})"
        )
    if img.size != (width, height):
        raise ValueError(
            f"Image size {img.size} != target ({width}, {height}) — scale first"
        )
    return _RENDERERS[charset_name](img, width, height)
