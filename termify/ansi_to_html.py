"""ANSI TrueColor escape sequences -> HTML <span> markup.

Consumers:
  - termify/output/html.py embeds the converted HTML into the self-contained
    player page so colours render correctly in any browser (browsers do NOT
    interpret raw ANSI bytes the way a terminal does).
  - The live web preview uses the JS-side equivalent in static/js/app.js.

Only the subset this project emits is handled:
  ESC[38;2;R;G;Bm  foreground TrueColor
  ESC[48;2;R;G;Bm  background TrueColor
  ESC[0m            reset
  ESC[39m           default foreground
  ESC[49m           default background
"""
from __future__ import annotations

import html
import re

# Split on ESC[...m while keeping the delimiter with the following text.
_TOKEN_RE = re.compile(r"(\x1b\[[0-9;]*m)")


def ansi_to_html(
    text: str,
    default_fg: str = "#c9d1d9",
    default_bg: str = "#0a0e14",
) -> str:
    """Render a single ANSI-coloured string as HTML.

    Half-block `▀` gets a CSS gradient (top half = fg, bottom half = bg) which
    is the closest browser equivalent to the terminal's two-tone block. Other
    glyphs are wrapped in a span carrying the active foreground/background.
    """
    fg: str | None = None  # None => use stylesheet default (no inline colour)
    bg: str | None = None
    out: list[str] = []

    # Lead with a reset so preceding context can't bleed into this string.
    parts = _TOKEN_RE.split(text)
    # _TOKEN_RE.split yields ['', esc, 'text', esc, 'text', ...] or ['text', ...]
    for part in parts:
        if not part:
            continue
        if _TOKEN_RE.fullmatch(part):
            fg, bg = _apply_code(part, fg, bg, default_fg, default_bg)
            continue
        # Plaintext run -> HTML-escape and wrap with current colours.
        out.append(_wrap(part, fg, bg))

    return "".join(out)


def _apply_code(
    code: str,
    fg: str | None,
    bg: str | None,
    default_fg: str,
    default_bg: str,
) -> tuple[str | None, str | None]:
    inner = code[2:-1]  # strip ESC[ and trailing m
    if inner == "0":
        return None, None
    if inner == "39":
        return default_fg, bg
    if inner == "49":
        return fg, default_bg
    if inner.startswith("38;2;"):
        rgb = inner.split(";")
        if len(rgb) == 5:
            return f"rgb({rgb[2]},{rgb[3]},{rgb[4]})", bg
    if inner.startswith("48;2;"):
        rgb = inner.split(";")
        if len(rgb) == 5:
            return fg, f"rgb({rgb[2]},{rgb[3]},{rgb[4]})"
    return fg, bg  # leave unknown codes alone rather than crash


def _wrap(text: str, fg: str | None, bg: str | None) -> str:
    if not text:
        return ""
    first = text[0]

    # Upper-half block: top half = fg, bottom half = bg. Rendered with a
    # background-clip:text gradient so a single glyph carries two colours.
    if first == "▀" and (fg or bg):
        top = fg or "#000000"
        bot = bg or "#000000"
        style = (
            f"background:linear-gradient(to bottom,{top} 50%,{bot} 50%);"
            f"-webkit-background-clip:text;background-clip:text;"
            f"color:transparent;-webkit-text-fill-color:transparent;"
            f"background-repeat:no-repeat;"
        )
        return f'<span style="{style}">{html.escape(text)}</span>'

    # Other glyphs: inline colour on a plain span.
    styles = []
    if fg:
        styles.append(f"color:{fg}")
    if bg:
        styles.append(f"background-color:{bg}")
    if styles:
        return f'<span style="{";".join(styles)}">{html.escape(text)}</span>'
    return html.escape(text)
