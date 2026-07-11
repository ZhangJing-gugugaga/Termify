#!/usr/bin/env python3
"""End-to-end smoke demo: convert a sample image in every charset and emit both output files.

Usage:
    python demo.py <image_path> [--charset NAME] [--width N] [--height N] [--out DIR]

Pass your own GIF/PNG/JPG as the first argument.  Or place a file named
sample.gif in the project root to use as the default.
"""
from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_SAMPLE = Path("sample.gif")  # place your own GIF here or pass path as arg


def main() -> None:
    ap = argparse.ArgumentParser(description="Termify Phase 1 smoke demo")
    ap.add_argument("image", nargs="?", default=str(DEFAULT_SAMPLE), help="input image/GIF")
    ap.add_argument("--charset", default="ascii", help="charset key or 'all'")
    ap.add_argument("--width", type=int, default=80)
    ap.add_argument("--height", type=int, default=24)
    ap.add_argument("--out", default="outputs")
    args = ap.parse_args()

    from termify import convert
    from termify.charset import CHARSETS
    from termify.output import render

    src = Path(args.image)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    charsets = list(CHARSETS) if args.charset == "all" else [args.charset]
    stem = src.stem

    for cs in charsets:
        print(f"\n=== charset={cs} ({CHARSETS[cs]['name']}) ===")
        seq = convert(str(src), cs, args.width, args.height)
        print(f"frames={len(seq.lines_per_frame)} interval={seq.interval}s "
              f"size={seq.width}x{seq.height}")
        if seq.lines_per_frame:
            print("first frame (may not render on Windows GBK console for unicode charsets):")
            for ln in seq.lines_per_frame[0]:
                try:
                    print(ln)
                except UnicodeEncodeError:
                    print(ln.encode("ascii", errors="replace").decode("ascii"))

        py_text = render(seq, "python")
        html_text = render(seq, "html")
        py_path = out_dir / f"{stem}_{cs}.py"
        html_path = out_dir / f"{stem}_{cs}.html"
        py_path.write_text(py_text, encoding="utf-8")
        html_path.write_text(html_text, encoding="utf-8")
        print(f"wrote {py_path} ({len(py_text)} bytes) and {html_path} ({len(html_text)} bytes)")


if __name__ == "__main__":
    main()
