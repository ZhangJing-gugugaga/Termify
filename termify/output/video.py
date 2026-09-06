"""MP4 video export — rasterize a FrameSequence's characters to pixels and
encode via ffmpeg (rawvideo pipe). Pure characters on a solid background,
no terminal chrome (grid/scanlines deliberately excluded per product decision).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

from termify.engine import FrameSequence


class VideoEncodeError(Exception):
    """Raised when the ffmpeg encode fails or ffmpeg is unavailable."""


# Monospace font candidates, per platform. First hit wins; fall back to the
# PIL bitmap default (ugly but always available) when none can load.
_BUNDLED_FONT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "static", "fonts", "DejaVuSansMono.ttf")

_FONT_CANDIDATES = [
    # 内置 DejaVu Sans Mono（静态）优先：静态字体渲染比可变字体快 ~46 倍
    # （JetBrains Mono VF 一次整行 draw.text 28ms → 514 帧导出 7 分钟，
    # 2026-09-06 手机端/桌面端导出超时事故）。盲文已走矢量点阵，
    # 不依赖字体字形；块/盒线/几何/灰度块 DejaVu 全覆盖。
    _BUNDLED_FONT,
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

MAX_VIDEO_FRAMES = 600

# 单次导出的"格×帧"预算：超过直接拒绝并建议降列数——
# 400×240×514 帧 ≈ 5e7 在小规格 ECS 上 >900s 且内存抖动，会拖死整机
# （2026-09-07 事故）。3e7 ≈ ECS 实测 600s 内可完成的上限。
EXPORT_CELL_BUDGET = 3_000_000  # hard guard for the public demo


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
        elif t == "38" and k + 1 < len(toks) and toks[k + 1] == "5":
            # xterm-256 前景（source256 导出）→ 查调色板转 RGB
            if k + 2 < len(toks) and toks[k + 2].isdigit():
                fg = _xterm256_rgb(int(toks[k + 2]))
            k += 2
        elif t == "48" and k + 1 < len(toks) and toks[k + 1] == "5":
            if k + 2 < len(toks) and toks[k + 2].isdigit():
                bg = _xterm256_rgb(int(toks[k + 2]))
            k += 2
        k += 1
    return fg, bg


# xterm-256 palette: 6×6×6 cube (16-231) + 24 gray levels (232-255).
_Q256_LEVELS = (0, 95, 135, 175, 215, 255)


def _xterm256_rgb(idx: int) -> tuple[int, int, int]:
    idx = max(0, min(255, idx))
    if idx >= 232:
        g = 8 + (idx - 232) * 10
        return (g, g, g)
    n = idx - 16
    return (_Q256_LEVELS[n // 36], _Q256_LEVELS[(n // 6) % 6], _Q256_LEVELS[n % 6])


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


def _parse_line_cells(line: str) -> list[tuple]:
    """Parse one ANSI line into (fg, bg, char) triples (None = default)."""
    return parse_ansi_line(line)


# 盲文点阵：U+2800 块 → 2×4 点位掩码（与 termify.charset 的盲文表同源）。
# 视频渲染对盲文走矢量点阵而非字形——JetBrains Mono / DejaVu Sans Mono /
# consola 均无 Braille Patterns，靠字体渲染会整屏豆腐块（2026-09-06 报障）。
_BRAILLE_DOTS = (
    (0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04),
    (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20),
    (0, 3, 0x40), (1, 3, 0x80),
)


def _is_braille(ch: str) -> bool:
    return "\u2800" <= ch <= "\u28ff"


def _draw_braille_cell(draw, ch: str, x: int, y: int,
                       char_w: int, char_h: int, color) -> None:
    bits = ord(ch) - 0x2800
    if not bits:
        return
    sw = char_w / 2.0
    sh = char_h / 4.0
    for col, row, mask in _BRAILLE_DOTS:
        if bits & mask:
            draw.rectangle(
                [round(x + col * sw), round(y + row * sh),
                 round(x + (col + 1) * sw) - 1, round(y + (row + 1) * sh) - 1],
                fill=color,
            )


def frame_to_image(lines: list[str], font, char_w: int, char_h: int,
                   out_w: int, out_h: int,
                   default_fg=DEFAULT_FG, default_bg=DEFAULT_BG) -> Image.Image:
    """Rasterize one ANSI frame (list of lines) onto an RGB image.

    Fast paths dominate real workloads:
    - blocks: every cell is "▀" (top fg / bottom bg) → byte-level composite
      of the whole frame, no font rendering at all.
    - "█" full-block cells (binary) → byte-level composite.
    - uniform-color lines (ramp charsets) → 字形条带缓存 + 字节拼装。
    Anything else falls back to per-cell drawing on the composed frame.
    """
    _strip_cache: dict[tuple, bytes] = {}

    def _glyph_strip(ch: str, color, bg) -> bytes:
        key = (color, bg, ch)
        hit = _strip_cache.get(key)
        if hit is not None:
            return hit
        img = Image.new("RGB", (char_w, char_h), bg)
        ImageDraw.Draw(img).text((0, 0), ch, fill=color, font=font)
        data = img.tobytes()
        if len(_strip_cache) > 8192:
            _strip_cache.clear()
        _strip_cache[key] = data
        return data

    buf = bytearray(out_w * out_h * 3)
    bg_span = bytes(default_bg) * out_w
    for yy in range(out_h):
        off = yy * out_w * 3
        buf[off:off + out_w * 3] = bg_span

    max_cells = out_w // char_w
    half_h = char_h // 2
    text_lines: list[tuple[int, list[tuple]]] = []  # (row, cells) needing draw

    for y, line in enumerate(lines):
        cells = _parse_line_cells(line)
        if not cells:
            continue
        chars = [c for _, _, c in cells]
        y0 = y * char_h
        if all(c == "▀" for c in chars):
            for yy in range(y0, min(y0 + char_h, out_h)):
                off = yy * out_w * 3
                if yy < y0 + half_h:
                    row = bytearray()
                    for fg, _bg, _c in cells:
                        row += bytes(fg if fg is not None else default_fg) * char_w
                else:
                    row = bytearray()
                    for _fg, bg, _c in cells:
                        row += bytes(bg if bg is not None else default_bg) * char_w
                span = bytes(row[:out_w * 3])
                buf[off:off + len(span)] = span
            continue
        if all(c == "█" for c in chars):
            row = bytearray()
            for fg, _bg, _c in cells:
                row += bytes(fg if fg is not None else default_fg) * char_w
            span = bytes(row[:out_w * 3])
            for yy in range(y0, min(y0 + char_h, out_h)):
                off = yy * out_w * 3
                buf[off:off + len(span)] = span
            continue
        fgs = {fg for fg, _, _ in cells}
        bgs = {bg for _, bg, _ in cells}
        text = "".join(chars)[:max_cells]
        # uniform 非盲文行：字形条带拼装（每字符整格字节条带，行内拼接）
        if len(fgs) <= 1 and len(bgs) <= 1 \
                and not any(_is_braille(c) for c in text):
            fg = next(iter(fgs)) if fgs else None
            bg = next(iter(bgs)) if bgs else None
            color = fg if fg is not None else default_fg
            bg_fill = bg if bg is not None else default_bg
            block = b"".join(_glyph_strip(c, color, bg_fill)
                             for c in text)
            row_w = len(text) * char_w * 3
            for yy in range(y0, min(y0 + char_h, out_h)):
                r = yy - y0
                off = yy * out_w * 3
                span = block[r * row_w:(r + 1) * row_w]
                buf[off:off + len(span)] = span
            continue
        text_lines.append((y, cells))

    img = Image.frombytes("RGB", (out_w, out_h), bytes(buf))
    if not text_lines:
        return img
    draw = ImageDraw.Draw(img)
    for y, cells in text_lines:
        y0 = y * char_h
        # 该阶段只剩两类行：含盲文（矢量点阵）或逐字符异色（原色）
        # per-cell 行——同 (fg,bg) 连续段合并为一次 draw.text（游程合并，
        # 输出与逐格绘制逐字节一致的前提：等宽字体步进 == char_w 整数格，
        # 已由 runnable 守卫；DejaVu 在 10/14pt 实测步进恰为 6/8 整数）。
        runnable = (font.getlength("M") == char_w)
        x = 0
        i = 0
        n = len(cells)
        while i < n and x < max_cells:
            fg, bg, ch = cells[i]
            color = fg if fg is not None else default_fg
            bg_fill = bg if bg is not None else default_bg
            if _is_braille(ch):
                # 盲文格：矢量点阵单格绘制（不进游程）
                if bg_fill != default_bg:
                    draw.rectangle(
                        [x * char_w, y0, (x + 1) * char_w - 1, y0 + char_h - 1],
                        fill=bg_fill,
                    )
                _draw_braille_cell(draw, ch, x * char_w, y0,
                                   char_w, char_h, color)
                x += 1
                i += 1
                continue
            j = i
            while (j < n and cells[j][0] == fg and cells[j][1] == bg
                   and not _is_braille(cells[j][2])
                   and x + (j - i) <= max_cells):
                j += 1
            run_len = j - i
            if bg_fill != default_bg:
                draw.rectangle(
                    [x * char_w, y0, (x + run_len) * char_w - 1, y0 + char_h - 1],
                    fill=bg_fill,
                )
            if runnable:
                run_text = "".join(c for _, _, c in cells[i:j])
                draw.text((x * char_w, y0), run_text, fill=color, font=font)
            else:
                for k in range(i, j):
                    draw.text((k * char_w, y0), cells[k][2],
                              fill=color, font=font)
            x += run_len
            i = j
    return img


def encode_mp4(seq: FrameSequence, out_path: str, font_size: int = 14,
               audio_path: str | None = None) -> str:
    """Rasterize every frame and pipe raw RGB into ffmpeg -> H.264 MP4.

    When ``audio_path`` is given, the finished silent MP4 gets that track
    muxed in (video stream copied untouched; mux failure degrades to the
    silent file rather than failing the export). Returns the output path;
    raises VideoEncodeError on failure.
    """
    if not ffmpeg_available():
        raise VideoEncodeError("ffmpeg is not available on this host")

    # 网格尺寸跟随渲染结果（此前钳在 200×60——200 列以上的预览导出
    # 会被砍半重采样，内容错位、分辨率与预览不符）
    width = max(1, min(400, seq.width))
    height = max(1, min(240, seq.height))
    # 单元像素自适应：内容（网格）完整保留，光栅宽度封顶 ~1920px——
    # 400×240 @14pt 会到 3200×4080（13MP），小规格 ECS 上 ffmpeg/内存
    # 直接把主机打挂（2026-09-07 事故）。字符在播放器全屏下依旧清晰。
    font_size = min(font_size, max(7, min(14, 1920 // width)))
    font = pick_font(font_size)
    char_w, char_h = _measure_cell(font)
    # 光栅兜底：字号再小也要满足偶数与宽度预算
    while width * char_w > 1920 and font_size > 6:
        font_size -= 1
        font = pick_font(font_size)
        char_w, char_h = _measure_cell(font)
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
        "-profile:v", "main",
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

    if audio_path:
        from termify.video import mux_audio_file
        try:
            mux_audio_file(out_path, audio_path, out_path)
        except Exception as exc:  # noqa: BLE001 — degrade to silent MP4
            print(f"[termify] audio mux skipped: {exc}", file=sys.stderr)

    return out_path
