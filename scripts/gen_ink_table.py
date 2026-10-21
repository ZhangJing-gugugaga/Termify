"""Generate static/anim/ink.json -- per-glyph ink coverage used to derive a
brightness grid from baked piece characters.

Why: each baked piece stores the glyph and the palette color of every cell.
To re-render that artwork with another charset we need its brightness, which is
close to color luminance x glyph ink coverage. Font metrics give the coverage.

Braille has no glyphs in DejaVuSansMono (the font falls back to the same box for
every code point), so braille cells are measured from their dot count instead.

Usage: python scripts/gen_ink_table.py [chars...]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import ImageFont  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_PATH = os.path.join(ROOT, "static", "fonts", "DejaVuSansMono.ttf")
OUT_PATH = os.path.join(ROOT, "static", "anim", "ink.json")
SIZE = 40


def _braille_ink(ch: str) -> float:
    """Ink of a braille cell = share of the eight dots that are raised."""
    return bin(ord(ch) - 0x2800).count("1") / 8.0


def build(chars):
    font = ImageFont.truetype(FONT_PATH, SIZE)
    cell_w = font.getlength("M")
    ascent, descent = font.getmetrics()
    area = cell_w * (ascent + descent)
    table = {}
    for ch in chars:
        if 0x2800 <= ord(ch) <= 0x28FF:
            ratio = _braille_ink(ch)
        elif ch == " ":
            ratio = 0.0
        else:
            mask = font.getmask(ch)
            w, h = mask.size
            ratio = sum(bytes(mask)) / 255.0 / area if w and h else 0.0
        table[str(ord(ch))] = max(0, min(255, int(round(ratio * 255))))
    return table


def main(argv):
    if argv:
        chars = set()
        for path in argv:
            with open(path, encoding="utf-8") as f:
                for piece in json.load(f)["items"]:
                    chars.add(piece["slug"])
        raise SystemExit("pass character lists, not manifest")
    # Default: every glyph used by the baked pieces, read from the anim data.
    import glob
    import gzip

    chars = set()
    for path in glob.glob(os.path.join(ROOT, "static", "anim", "*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            data = json.load(f)
        for frame in data["frames"]:
            for row in frame["c"]:
                chars.update(row)
    table = build(sorted(chars))
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    print("chars %d -> %s (%d bytes)" % (len(table), OUT_PATH, os.path.getsize(OUT_PATH)))


if __name__ == "__main__":
    main(sys.argv[1:])
