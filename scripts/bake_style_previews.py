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

Reproducible: run "python scripts/bake_style_previews.py" from the repo root;
output is byte-identical across runs (same inputs, same Pillow encoder).
"""
import os
import sys

from PIL import Image, ImageChops, ImageSequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from termify.output.video import _measure_cell, frame_to_image, pick_font  # noqa: E402
from termify.pieces import read_piece, sequence  # noqa: E402

PIECE = "tethered-astronaut"    # gallery work 8YTQvqMV, "Tethered Spacewalk"
PIECE_INTERVAL = 0.125          # the piece's own frame interval (8 fps)
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "static", "img")

# The 10th card (data-style="custom") is a user-supplied ramp: nothing to pre-bake.
STYLES = ["ascii", "ascii-lite", "blocks", "braille", "geometric",
          "binary", "shades", "halftone", "dither"]

COLS, ROWS = 52, 18             # 4:3 in terminal cells (cells are ~1:2)
FONT_SIZE = 10                  # DejaVu cell = 6x13px -> 312x234, exactly 4:3
FRAMES = 12                     # of the piece's 16; keeps each GIF under 120 KB
COLORS = 256


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


def main():
    piece = read_piece(PIECE)
    if piece is None:
        raise SystemExit("unknown piece: %s" % PIECE)
    font = pick_font(FONT_SIZE)
    char_w, char_h, out_w, out_h = _canvas(font)
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


if __name__ == "__main__":
    main()
