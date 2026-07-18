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
        "chars": "■●◆▪▫◇○ ",  # dense → sparse (black → white, space = invisible background)
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


def _adaptive_lut(img) -> list[int]:
    """Build a CDF-based luminance lookup table for adaptive grayscale bucketing.

    Maps pixel luminance through the cumulative distribution function so that
    the full character range is utilised regardless of the image's brightness
    histogram. Uniform images (min == max) fall back to identity.
    """
    px = img.load()
    w, h = img.size
    hist = [0] * 256
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y][:3]
            hist[_luminance(r, g, b)] += 1
    total = w * h
    cdf = 0
    cdf_min = None
    lut = [0] * 256
    for i in range(256):
        cdf += hist[i]
        if cdf_min is None and hist[i] > 0:
            cdf_min = cdf
        if cdf_min is None:
            lut[i] = 0
        elif total == cdf_min:
            lut[i] = i
        else:
            lut[i] = round((cdf - cdf_min) / (total - cdf_min) * 255)
    return lut


def _otsu_threshold(stretched):
    """Otsu 二值化 + 少数侧判定。

    返回 (threshold, minority_is_bright)：
    - threshold：Otsu 找到的最优分割点（最大化前景/背景类间方差）
    - minority_is_bright：少数侧（主体）是否是"亮"那一边

    用法：让"点/█"对应少数侧（主体），不论主体是亮（白猫在暗背景）
    还是暗（黑猫在亮背景），主体都会被点出来。
    """
    if not stretched:
        return 127, True
    hist = [0] * 256
    for v in stretched:
        if 0 <= v <= 255:
            hist[v] += 1
    total = len(stretched)
    if total == 0:
        return 127, True
    sum_all = sum(i * h for i, h in enumerate(hist))
    sum_bg = 0
    w_bg = 0
    max_var = 0
    threshold = 127
    for t in range(256):
        w_bg += hist[t]
        if w_bg == 0:
            continue
        w_fg = total - w_bg
        if w_fg == 0:
            break
        sum_bg += t * hist[t]
        m_bg = sum_bg / w_bg
        m_fg = (sum_all - sum_bg) / w_fg
        var = w_bg * w_fg * (m_bg - m_fg) ** 2
        if var > max_var:
            max_var = var
            threshold = t
    n_below = sum(hist[:threshold + 1])  # +1: Otsu loop includes hist[t] in w_bg
    n_above = total - n_below
    # 均匀图（全黑/全白）边界处理：没有真正的"少数侧"，回退到旧行为
    # "暗=█/点"，保证均匀色图的语义不反转（test_binary_black_maps_to_block 等
    # 测试依赖此行为）。
    if n_below == 0 or n_above == 0:
        return threshold, False
    minority_is_bright = n_above < n_below
    return threshold, minority_is_bright


def _minority_is_bright_for_img(img) -> bool:
    """Use Otsu to decide whether the bright or dark side is the subject.

    Returns True when the bright minority is the subject (e.g. white cat on
    dark background), False otherwise.
    """
    px = img.load()
    w, h = img.size
    lum_values = []
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y][:3]
            lum_values.append(_luminance(r, g, b))
    _, mib = _otsu_threshold(lum_values)
    return mib


def _ansi_fg(rgb):
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _ansi_bg(rgb):
    return f"\x1b[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def _emit(char: str, fg, bg) -> str:
    """Wrap a single char in optional TrueColor ANSI fg/bg codes."""
    if fg is None and bg is None:
        return char
    parts = []
    if fg is not None:
        parts.append(_ansi_fg(fg))
    if bg is not None:
        parts.append(_ansi_bg(bg))
    parts.append(char)
    return "".join(parts)


def _render_ascii(img, width, height, fg=None, bg=None):
    chars = CHARSETS["ascii"]["chars"]
    n = len(chars)
    lut = _adaptive_lut(img)
    mib = _minority_is_bright_for_img(img)
    px = img.load()
    lines = []
    for y in range(height):
        row = []
        for x in range(width):
            r, g, b = px[x, y][:3]
            gray = lut[_luminance(r, g, b)]
            if mib:
                idx = (n - 1) - gray * (n - 1) // 255
            else:
                idx = gray * (n - 1) // 255
            row.append(_emit(chars[idx], fg, bg))
        if fg is not None or bg is not None:
            row.append("\x1b[0m")
        lines.append("".join(row))
    return lines


def _render_blocks(img, width, height):
    px = img.load()
    src_w, src_h = img.size
    out_lines = []
    for y_top in range(0, src_h, 2):
        y_bot = y_top + 1 if y_top + 1 < src_h else y_top
        parts = []
        last_fg = None
        last_bg = None
        for x in range(src_w):
            rt, gt, bt = px[x, y_top][:3]
            rb, gb, bb = px[x, y_bot][:3]
            fg = (rt, gt, bt)
            bg = (rb, gb, bb)
            if fg != last_fg:
                parts.append(_ansi_fg(fg))
                last_fg = fg
            if bg != last_bg:
                parts.append(_ansi_bg(bg))
                last_bg = bg
            parts.append("▀")
        # No trailing reset: each ▀ has explicit fg/bg codes,
        # and reset would clear state causing next line's ▀ to render black.
        out_lines.append("".join(parts))
    return out_lines


def _render_braille(img, width, height, fg=None, bg=None):
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
    # Collect all luminance values and compute Otsu threshold
    lum_values = []
    for y in range(src_h):
        for x in range(src_w):
            r, g, b = px[x, y][:3]
            lum_values.append(_luminance(r, g, b))
    threshold, minority_is_bright = _otsu_threshold(lum_values)

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
                lum = _luminance(r, g, b)
                if minority_is_bright:
                    # Subject is bright → dots for bright pixels
                    if lum >= threshold:
                        bits |= mask
                else:
                    # Subject is dark → dots for dark pixels
                    if lum < threshold:
                        bits |= mask
            row.append(_emit(chr(0x2800 + bits), fg, bg))
        if fg is not None or bg is not None:
            row.append("\x1b[0m")
        lines.append("".join(row))
    return lines


def _render_geometric(img, width, height, fg=None, bg=None):
    chars = CHARSETS["geometric"]["chars"]
    n = len(chars)
    # Use direct linear luminance → index mapping (NOT adaptive LUT).
    # The adaptive LUT pre-stretches the histogram so dark always maps to 0
    # and bright to 255, which breaks the mib inversion (double-inversion).
    # Direct linear mapping: bright pixels → low idx (dense ■),
    # dark pixels → high idx (sparse □).  Then mib flips it when the
    # subject is dark-on-light so the dark subject still gets dense chars.
    mib = _minority_is_bright_for_img(img)
    px = img.load()
    lines = []
    for y in range(height):
        row = []
        for x in range(width):
            r, g, b = px[x, y][:3]
            lum = _luminance(r, g, b)
            if mib:
                # Bright subject: bright → dense, dark → sparse
                idx = (n - 1) - lum * (n - 1) // 255
            else:
                # Dark subject: dark → dense, bright → sparse
                idx = lum * (n - 1) // 255
            row.append(_emit(chars[idx], fg, bg))
        if fg is not None or bg is not None:
            row.append("\x1b[0m")
        lines.append("".join(row))
    return lines


def _render_binary(img, width, height, fg=None, bg=None):
    px = img.load()
    src_w, src_h = img.size
    # Otsu threshold + minority-is-subject (same logic as braille)
    lum_values = []
    for y in range(src_h):
        for x in range(src_w):
            r, g, b = px[x, y][:3]
            lum_values.append(_luminance(r, g, b))
    threshold, minority_is_bright = _otsu_threshold(lum_values)

    lines = []
    for y in range(height):
        row = []
        for x in range(width):
            r, g, b = px[x, y][:3]
            lum = _luminance(r, g, b)
            if minority_is_bright:
                # Subject is bright → █ for bright pixels
                ch = "█" if lum >= threshold else " "
            else:
                # Subject is dark → █ for dark pixels
                ch = "█" if lum < threshold else " "
            row.append(_emit(ch, fg, bg))
        if fg is not None or bg is not None:
            row.append("\x1b[0m")
        lines.append("".join(row))
    return lines


_RENDERERS = {
    "ascii": _render_ascii,
    "blocks": _render_blocks,
    "braille": _render_braille,
    "geometric": _render_geometric,
    "binary": _render_binary,
}


def render_frame(img, charset_name, width, height, fg_color=None, bg_color=None):
    """Map a PIL.Image (already scaled to width x height) to text lines.

    fg_color / bg_color are (R, G, B) tuples or None. When provided, non-block
    charsets wrap each character in TrueColor ANSI so the user can override
    the default look. blocks ignores these (pixel colour wins).
    """
    if charset_name not in CHARSETS:
        raise ValueError(
            f"Unknown charset: {charset_name!r} (expected one of {sorted(CHARSETS)})"
        )
    if img.size[0] != width:
        raise ValueError(
            f"Image width {img.size[0]} != target {width} -- scale first"
        )
    if charset_name == "blocks":
        return _render_blocks(img, width, height)
    return _RENDERERS[charset_name](img, width, height, fg_color, bg_color)
