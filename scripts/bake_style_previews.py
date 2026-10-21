"""Bake the homepage style-card previews into static/img/style-preview-<style>.gif.

Every card shows the *same* gallery piece (tethered-astronaut, gallery work
8YTQvqMV by ASCII Magic), re-rendered in that card's own charset - so the grid
reads as "one work, ten renderings" instead of ten hand-drawn ASCII sketches.

The pipeline is the product's own, not a re-implementation:

* termify/pieces.py:140 sequence()        piece frames -> ANSI lines per charset
* termify/output/video.py:194 frame_to_image() + :61 pick_font()
                                          ANSI lines -> pixels (MP4 exporter path)
* termify/gallery.py:117 make_thumbnail()  shaped the output: a looping GIF

GIF encoding: frames share ONE 256-colour palette (sampled from a montage of all
frames), which lets Pillow delta-encode each frame against the previous one -
about 3-7x smaller than per-frame palettes. Delta frames need disposal=1 (leave
the canvas in place); disposal=2 would wipe the previous frame and the animation
would flicker down to the background. Every file is decoded back and compared
against the rendered frames before the run reports success.

The tenth card (data-style="custom") has no piece behind it.  What that card
sells is "whatever chars you type -> whatever you want", so there is no source
image for termify/pieces.sequence() to re-render: baking it means drawing the
text first.  It therefore takes a separate path (bake_custom) that lays out the
font's own infinity sign - a lying 8 / Moebius loop, the "infinite
possibilities" the card stands for - and fills its stroke with a 12-glyph ramp
that winds round each lobe, one glyph per frame.  Those cells are drawn here
rather than by frame_to_image(): when this card was baked that function's
uniform-colour fast path sliced per-cell strips row-major and straddled cell
borders, so a one-colour card could not use it (since fixed - the fast path now
matches per-cell drawing byte for byte).  Drawing locally keeps the baked image
independent of that code path.  The shared palette, the encoder settings and the decode-back check are the same ones
the other nine cards use, so all ten stay 同规格.

Reproducible: run "python scripts/bake_style_previews.py" from the repo root
(only the custom card: "... --custom"); output is byte-identical across runs
(same inputs, same Pillow encoder).
"""
import math
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageSequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from termify.output.video import _measure_cell, frame_to_image, pick_font  # noqa: E402
from termify.pieces import read_piece, sequence  # noqa: E402

PIECE = "tethered-astronaut"    # gallery work 8YTQvqMV, "Tethered Spacewalk"
PIECE_INTERVAL = 0.125          # the piece's own frame interval (8 fps)
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "static", "img")

# The 10th card (data-style="custom") has no piece; see bake_custom().
STYLES = ["ascii", "ascii-lite", "blocks", "braille", "geometric",
          "binary", "shades", "halftone", "dither"]

COLS, ROWS = 52, 18             # 4:3 in terminal cells (cells are ~1:2)
FONT_SIZE = 10                  # DejaVu cell = 6x13px -> 312x234, exactly 4:3
FRAMES = 12                     # of the piece's 16; keeps each GIF under 120 KB
COLORS = 256

# Custom card: synthetic text frames instead of a piece rendering.
CUSTOM_CHARS = "@%#&WM8$*o=+"   # 12 readable, chunky glyphs - all of them show
CUSTOM_FG = (51, 255, 51)       # #33ff33, one project green, no gradient
CUSTOM_BG = (10, 14, 20)        # #0a0e14
CUSTOM_FONT_SIZE = 14           # DejaVu cell = 8x17px -> 39x13 usable cells
CUSTOM_GLYPH = "\u221e"          # the shape: the font's own infinity sign
CUSTOM_PROBE_SIZE = 220         # rasterised big, then scaled down to the canvas
CUSTOM_SCALE = 0.84             # symbol height as a fraction of the canvas
CUSTOM_INK = 140                # mask threshold: a cell centre above it is stroke
CUSTOM_RAMP_WRAPS = 6           # how often the 12-glyph ramp runs round each lobe


def _canvas(font):
    char_w, char_h = _measure_cell(font)
    return char_w, char_h, COLS * char_w, ROWS * char_h


def _palette(frames):
    """One shared 256-colour palette sampled from every frame (a 4-column sheet)."""
    tile_w, tile_h = frames[0].size
    cols = 4
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (tile_w * cols, tile_h * rows), (10, 12, 16))
    for index, frame in enumerate(frames):
        sheet.paste(frame, ((index % cols) * tile_w, (index // cols) * tile_h))
    return sheet.convert("P", palette=Image.ADAPTIVE, colors=COLORS)


def _verify(path, sources):
    """Decode the GIF back and check geometry, frame count and pixel agreement."""
    with Image.open(path) as gif:
        decoded = [frame.convert("RGB") for frame in ImageSequence.Iterator(gif)]
    if len(decoded) != len(sources):
        raise SystemExit("%s: %d frames, expected %d" % (path, len(decoded), len(sources)))
    if decoded[0].size != sources[0].size:
        raise SystemExit("%s: %s, expected %s" % (path, decoded[0].size, sources[0].size))
    worst = 0.0
    for src, out in zip(sources, decoded):
        hist = ImageChops.difference(src, out).convert("L").histogram()
        worst = max(worst, sum(i * n for i, n in enumerate(hist)) / sum(hist))
    if worst > 6.0:
        raise SystemExit("%s: decoded frames drift from source (mean %.2f)" % (path, worst))
    return len(decoded), decoded[0].size, worst


def bake(style, piece, font, char_w, char_h, out_w, out_h):
    seq = sequence(piece, style, COLS, ROWS, interval=PIECE_INTERVAL,
                   color_mode="source", max_frames=FRAMES)
    sources = [frame_to_image(lines, font, char_w, char_h, out_w, out_h).convert("RGB")
               for lines in seq.lines_per_frame]
    palette = _palette(sources)
    frames = [frame.quantize(palette=palette, dither=Image.NONE) for frame in sources]

    path = os.path.join(OUT_DIR, "style-preview-%s.gif" % style)
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:],
                   duration=[int(round(PIECE_INTERVAL * 1000))] * len(frames),
                   loop=0, disposal=1, optimize=True)
    n_frames, size, drift = _verify(path, sources)
    return path, n_frames, size, drift


def _custom_grid(out_w, out_h):
    """(font, char_w, char_h, cols, rows, ox, oy) - the card's own, larger cells.

    A one-cell-wide stroke is what makes the symbol read as a symbol, and the
    shared 52x18 grid draws it 2 cells thick with a solid knot at the crossing.
    Bigger cells buy the thinner stroke.  The grid is centred because 13 rows of
    17px leave 13px over at the bottom (39 columns of 8px fit exactly).
    """
    font = pick_font(CUSTOM_FONT_SIZE)
    char_w, char_h = _measure_cell(font)
    cols, rows = out_w // char_w, out_h // char_h
    return (font, char_w, char_h, cols, rows,
            (out_w - cols * char_w) // 2, (out_h - rows * char_h) // 2)


def _infinity_mask(out_w, out_h):
    """The card's shape: the font's infinity sign, rasterised large and fitted.

    A parametric lemniscate is the textbook lying 8, but both classic forms are
    wrong at this size: Bernoulli's arcs leave the waist as straight diagonals
    (the figure reads as a bowtie) and Gerono's lobes are almost triangular.  The
    glyph that people recognise as an infinity is rounder than either, so the
    shape is taken from the terminal font itself - exactly the symbol the card
    promises they can type - and only the cell sampling below is ours.
    """
    probe = pick_font(CUSTOM_PROBE_SIZE)
    ink = Image.new("L", (CUSTOM_PROBE_SIZE * 2, CUSTOM_PROBE_SIZE), 0)
    ImageDraw.Draw(ink).text((0, 0), CUSTOM_GLYPH, fill=255, font=probe)
    ink = ink.crop(ink.getbbox())
    height = int(out_h * CUSTOM_SCALE)
    width = int(round(ink.size[0] * height / float(ink.size[1])))
    limit = int(out_w * 0.96)              # a wide glyph is fitted by its width,
    if width > limit:                      # so it keeps a margin on both sides
        height = int(round(height * limit / float(width)))
        width = limit
    mask = Image.new("L", (out_w, out_h), 0)
    mask.paste(ink.resize((width, height), Image.LANCZOS),
               ((out_w - width) // 2, (out_h - height) // 2))
    return mask


def _glyph_cells(mask, geom):
    """Cells on the stroke -> (col, row, s), s = position round that cell's lobe.

    s is the polar angle of the cell about the centre of its own lobe, so the
    ramp winds once round each lobe in the same direction: neighbouring cells get
    neighbouring glyphs and the symbol looks written, not sprinkled.
    """
    _, char_w, char_h, cols, rows, ox, oy = geom
    mid_x, mid_y = cols * char_w / 2.0, rows * char_h / 2.0
    lobe_dx = cols * char_w / 4.0
    cells = []
    for row in range(rows):
        y = row * char_h + char_h / 2.0
        for col in range(cols):
            x = col * char_w + char_w / 2.0
            if mask.getpixel((ox + int(x), oy + int(y))) <= CUSTOM_INK:
                continue
            right = x >= mid_x
            dx = (x - (mid_x + lobe_dx)) if right else ((mid_x - lobe_dx) - x)
            angle = math.atan2(mid_y - y, dx)
            cells.append((col, row,
                          ((1 if right else 0) + (angle + math.pi) / (2.0 * math.pi)) / 2.0))
    return cells


def _raster(lines, geom, out_w, out_h):
    """Draw one text frame cell by cell on the card's canvas.

    frame_to_image()'s uniform-colour fast path used to concatenate per-cell
    strips and slice them a row at a time, which straddled cell borders and made
    a one-colour frame come out as scattered noise.  That path is fixed (it now
    matches per-cell drawing byte for byte), but this card keeps its own renderer
    so the baked image does not depend on it: the same ImageDraw.text call per
    cell the function's per-cell path makes, on the same canvas.  Only the geometry is local: the shared palette,
    the encoder and the decode-back check below are untouched.
    """
    font, char_w, char_h, _, _, ox, oy = geom
    img = Image.new("RGB", (out_w, out_h), CUSTOM_BG)
    draw = ImageDraw.Draw(img)
    for row, line in enumerate(lines):
        for col, ch in enumerate(line):
            if ch != " ":
                draw.text((ox + col * char_w, oy + row * char_h), ch,
                          fill=CUSTOM_FG, font=font)
    return img


def _custom_frames(geom, out_w, out_h):
    """The card's text frames: one glyph per stroke cell, flowing round the loop.

    The ramp is exactly FRAMES long, so each frame is the previous one with every
    glyph advanced by one and frame FRAMES == frame 0 - a seamless loop.
    """
    _, _, _, cols, rows, _, _ = geom
    n = len(CUSTOM_CHARS)
    assert n == FRAMES, "the glyph ramp must be FRAMES long for a seamless loop"
    cells = _glyph_cells(_infinity_mask(out_w, out_h), geom)
    frames = []
    for f in range(FRAMES):
        canvas = [[" "] * cols for _ in range(rows)]
        for col, row, s in cells:
            canvas[row][col] = CUSTOM_CHARS[(int(s * n * CUSTOM_RAMP_WRAPS) + f) % n]
        frames.append(["".join(line).rstrip() for line in canvas])
    return frames


def bake_custom(out_w, out_h):
    """Bake the custom card's GIF from text frames - same canvas, palette, encoder."""
    geom = _custom_grid(out_w, out_h)
    lines_per_frame = _custom_frames(geom, out_w, out_h)
    sources = [_raster(lines, geom, out_w, out_h).convert("RGB")
               for lines in lines_per_frame]
    palette = _palette(sources)
    frames = [frame.quantize(palette=palette, dither=Image.NONE) for frame in sources]

    path = os.path.join(OUT_DIR, "style-preview-custom.gif")
    frames[0].save(path, format="GIF", save_all=True, append_images=frames[1:],
                   duration=[int(round(PIECE_INTERVAL * 1000))] * len(frames),
                   loop=0, disposal=1, optimize=True)
    n_frames, size, drift = _verify(path, sources)
    glyphs = sorted({ch for lines in lines_per_frame for ch in "".join(lines) if ch != " "})
    if len(glyphs) < 8:
        raise SystemExit("%s: only %d distinct glyphs, expected >= 8" % (path, len(glyphs)))
    return path, n_frames, size, drift, glyphs


def main():
    only_custom = "--custom" in sys.argv[1:]
    unknown = [arg for arg in sys.argv[1:] if arg != "--custom"]
    if unknown:
        raise SystemExit("usage: %s [--custom]" % os.path.basename(sys.argv[0]))
    font = pick_font(FONT_SIZE)
    char_w, char_h, out_w, out_h = _canvas(font)

    if not only_custom:
        piece = read_piece(PIECE)
        if piece is None:
            raise SystemExit("unknown piece: %s" % PIECE)
        print("piece %s (%sx%s cell=%s), canvas %dx%d (cell %dx%d px), %d frames @ %.3fs"
              % (PIECE, piece.get("cols"), piece.get("rows"), piece.get("cell"),
                 out_w, out_h, char_w, char_h, FRAMES, PIECE_INTERVAL))

        total = 0
        for style in STYLES:
            path, n_frames, size, drift = bake(style, piece, font, char_w, char_h, out_w, out_h)
            nbytes = os.path.getsize(path)
            total += nbytes
            print("%-24s %6d B  %dx%d  %2d frames  drift %.2f"
                  % (os.path.basename(path), nbytes, size[0], size[1], n_frames, drift))
        print("total %d B (%.0f KB) across %d files" % (total, total / 1024.0, len(STYLES)))

    path, n_frames, size, drift, glyphs = bake_custom(out_w, out_h)
    print("%-24s %6d B  %dx%d  %2d frames  drift %.2f  %d glyphs %s"
          % (os.path.basename(path), os.path.getsize(path), size[0], size[1],
             n_frames, drift, len(glyphs), "".join(glyphs)))


if __name__ == "__main__":
    main()
