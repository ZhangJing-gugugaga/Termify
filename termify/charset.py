"""Character-set definitions and per-frame pixel -> character mapping.

Authoritative spec: PRD.md §5.5 (CHARSETS) and §5.3 (mapping pipeline).
"""

from __future__ import annotations

import re

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
    "shades": {
        "name": "明暗渐变块",
        "chars": "█▓▒░ ",  # dense -> sparse, block-element shading ramp
        "color": False,
        "description": "块状明暗字符的平滑灰度渐变，比标点更有质感",
    },
    "custom": {
        "name": "自定义字符集",
        "chars": None,  # per-request ramp, see charset_ramp in render_frame()
        "color": False,
        "description": "用你自己的字符序列做灰度映射，密->疏排列",
    },
}

# Bounds for user-supplied custom ramps (see sanitize_ramp).
CUSTOM_RAMP_MAX_LEN = 64


import re

# ANSI CSI sequences (e.g. ESC[31m) must die as a whole — stripping only the
# ESC byte would leave "[31m" as bogus ramp characters.
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def sanitize_ramp(ramp: str) -> str:
    """Clean a user-supplied character ramp for the ``custom`` charset.

    Removes ANSI escape sequences and control characters (newlines,
    zero-width junk), collapses duplicate characters keeping first-occurrence
    order (a ramp level repeated is meaningless and only wastes resolution),
    and enforces a length cap. Raises ValueError when nothing usable remains.
    """
    if not isinstance(ramp, str):
        raise ValueError("custom charset ramp must be a string")
    ramp = _ANSI_CSI_RE.sub("", ramp)
    seen = set()
    out = []
    for ch in ramp:
        code = ord(ch)
        if code < 0x20 or code == 0x7F or 0x200B <= code <= 0x200F:
            continue
        if ch in seen:
            continue
        seen.add(ch)
        out.append(ch)
        if len(out) >= CUSTOM_RAMP_MAX_LEN:
            break
    if not out:
        raise ValueError("custom charset ramp is empty after cleaning")
    return "".join(out)


def _luminance(r: int, g: int, b: int) -> int:
    # ITU-R BT.601 luma
    return round(0.299 * r + 0.587 * g + 0.114 * b)


def _luminance_array(img) -> list[int]:
    """All pixel luminances in ONE pass (row-major).

    Single-pass optimization for video-length workloads: the previous code
    walked every pixel three separate times (adaptive LUT histogram, Otsu
    collection, render mapping), each with a _luminance() function call.
    ``tobytes()`` extracts raw RGB at C speed; the comprehension keeps the
    exact same rounding as _luminance so rendered output is byte-identical.
    """
    if img.mode == "RGBA":
        buf = img.tobytes()
        step = 4
    elif img.mode == "RGB":
        buf = img.tobytes()
        step = 3
    else:
        img = img.convert("RGB")
        buf = img.tobytes()
        step = 3
    return [round(0.299 * buf[i] + 0.587 * buf[i + 1] + 0.114 * buf[i + 2])
            for i in range(0, len(buf), step)]


def _hist_from_lums(lums: list[int]) -> list[int]:
    hist = [0] * 256
    for v in lums:
        hist[v] += 1
    return hist


def _adaptive_lut_from_lums(lums: list[int]) -> list[int]:
    """CDF-based adaptive LUT from a precomputed luminance array."""
    hist = _hist_from_lums(lums)
    total = len(lums)
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


def _adaptive_lut(img) -> list[int]:
    """Build a CDF-based luminance lookup table for adaptive grayscale bucketing.

    Maps pixel luminance through the cumulative distribution function so that
    the full character range is utilised regardless of the image's brightness
    histogram. Uniform images (min == max) fall back to identity.
    """
    return _adaptive_lut_from_lums(_luminance_array(img))


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
    _, mib = _otsu_threshold(_luminance_array(img))
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


def _render_ramp(img, width, height, fg, bg, chars):
    """Shared grayscale-ramp renderer (ascii / shades / custom).

    ``chars`` is ordered dense -> sparse (black -> white); the adaptive LUT
    and minority-is-bright inversion decide which end a pixel maps to.
    Single luminance pass + per-level index table (byte-identical output to
    the per-pixel formulation).
    """
    n = len(chars)
    lums = _luminance_array(img)
    lut = _adaptive_lut_from_lums(lums)
    _, mib = _otsu_threshold(lums)
    if mib:
        cell = [chars[(n - 1) - lut[g] * (n - 1) // 255] for g in range(256)]
    else:
        cell = [chars[lut[g] * (n - 1) // 255] for g in range(256)]
    colorize = fg is not None or bg is not None
    lines = []
    pos = 0
    for _y in range(height):
        if colorize:
            row = [_emit(cell[lums[pos + x]], fg, bg) for x in range(width)]
            row.append("\x1b[0m")
        else:
            row = [cell[v] for v in lums[pos:pos + width]]
        pos += width
        lines.append("".join(row))
    return lines


def _render_ascii(img, width, height, fg=None, bg=None):
    return _render_ramp(img, width, height, fg, bg, CHARSETS["ascii"]["chars"])


def _render_shades(img, width, height, fg=None, bg=None):
    return _render_ramp(img, width, height, fg, bg, CHARSETS["shades"]["chars"])


def _render_blocks(img, width, height):
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = img.tobytes()
    src_w, src_h = img.size
    out_lines = []
    for y_top in range(0, src_h, 2):
        y_bot = y_top + 1 if y_top + 1 < src_h else y_top
        row_top = y_top * src_w * 3
        row_bot = y_bot * src_w * 3
        parts = []
        last_fg = None
        last_bg = None
        for x in range(src_w):
            o = row_top + x * 3
            ob = row_bot + x * 3
            fg = (buf[o], buf[o + 1], buf[o + 2])
            bg = (buf[ob], buf[ob + 1], buf[ob + 2])
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


# Cell -> (flat source offset, dot mask) tables, cached per source/output
# geometry: the coordinates are frame-independent, so video-length workloads
# only pay for this once per size instead of per frame.
_BRAILLE_COORD_CACHE: dict[tuple, list[tuple[int, int]]] = {}


def _braille_coord_table(src_w: int, src_h: int, out_w: int, out_h: int) -> list[tuple[int, int]]:
    key = (src_w, src_h, out_w, out_h)
    table = _BRAILLE_COORD_CACHE.get(key)
    if table is not None:
        return table
    dots = [
        (0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04),
        (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20),
        (0, 3, 0x40), (1, 3, 0x80),
    ]
    table = []
    for by in range(out_h):
        for bx in range(out_w):
            for dx, dy, mask in dots:
                sx = int((bx * 2 + dx) * src_w / (out_w * 2))
                sy = int((by * 4 + dy) * src_h / (out_h * 4))
                if sx >= src_w:
                    sx = src_w - 1
                if sy >= src_h:
                    sy = src_h - 1
                table.append((sy * src_w + sx, mask))
    if len(_BRAILLE_COORD_CACHE) > 8:
        _BRAILLE_COORD_CACHE.clear()
    _BRAILLE_COORD_CACHE[key] = table
    return table


def _render_braille(img, width, height, fg=None, bg=None):
    src_w, src_h = img.size
    cell_w, cell_h = 2, 4
    out_w = max(1, width // cell_w)
    out_h = max(1, height // cell_h)
    # Single luminance pass; Otsu decides which side is the subject.
    lums = _luminance_array(img)
    threshold, minority_is_bright = _otsu_threshold(lums)
    coord_table = _braille_coord_table(src_w, src_h, out_w, out_h)

    lines = []
    pos = 0
    for _by in range(out_h):
        row = []
        for _bx in range(out_w):
            bits = 0
            for offset, mask in coord_table[pos:pos + 8]:
                lum = lums[offset]
                if minority_is_bright:
                    # Subject is bright → dots for bright pixels
                    if lum >= threshold:
                        bits |= mask
                else:
                    # Subject is dark → dots for dark pixels
                    if lum < threshold:
                        bits |= mask
            pos += 8
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
    lums = _luminance_array(img)
    _, mib = _otsu_threshold(lums)
    if mib:
        cell = [chars[(n - 1) - g * (n - 1) // 255] for g in range(256)]
    else:
        cell = [chars[g * (n - 1) // 255] for g in range(256)]
    colorize = fg is not None or bg is not None
    lines = []
    pos = 0
    for _y in range(height):
        if colorize:
            row = [_emit(cell[v], fg, bg) for v in lums[pos:pos + width]]
            row.append("\x1b[0m")
        else:
            row = [cell[v] for v in lums[pos:pos + width]]
        pos += width
        lines.append("".join(row))
    return lines


def _render_binary(img, width, height, fg=None, bg=None):
    # Otsu threshold + minority-is-subject (same logic as braille),
    # single luminance pass.
    lums = _luminance_array(img)
    threshold, minority_is_bright = _otsu_threshold(lums)
    colorize = fg is not None or bg is not None
    if minority_is_bright:
        cell = ["█" if g >= threshold else " " for g in range(256)]
    else:
        cell = ["█" if g < threshold else " " for g in range(256)]

    lines = []
    pos = 0
    for _y in range(height):
        if colorize:
            row = [_emit(cell[v], fg, bg) for v in lums[pos:pos + width]]
            row.append("\x1b[0m")
        else:
            row = [cell[v] for v in lums[pos:pos + width]]
        pos += width
        lines.append("".join(row))
    return lines


_RENDERERS = {
    "ascii": _render_ascii,
    "blocks": _render_blocks,
    "braille": _render_braille,
    "geometric": _render_geometric,
    "binary": _render_binary,
    "shades": _render_shades,
}


def render_frame(img, charset_name, width, height, fg_color=None, bg_color=None,
                 charset_ramp=None):
    """Map a PIL.Image (already scaled to width x height) to text lines.

    fg_color / bg_color are (R, G, B) tuples or None. When provided, non-block
    charsets wrap each character in TrueColor ANSI so the user can override
    the default look. blocks ignores these (pixel colour wins).
    charset_ramp is the user-supplied character sequence, required when
    charset_name is "custom" (ignored otherwise).
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
    if charset_name == "custom":
        ramp = sanitize_ramp(charset_ramp or "")
        return _render_ramp(img, width, height, fg_color, bg_color, ramp)
    return _RENDERERS[charset_name](img, width, height, fg_color, bg_color)
