#!/usr/bin/env python3
"""One-off builder: split ui-mockup.html into templates/ + static/."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ui-mockup.html"
TEMPLATES = ROOT / "templates"
CSS = ROOT / "static" / "css"
JS = ROOT / "static" / "js"


def extract_tag(html: str, tag: str) -> str:
    m = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html, re.S)
    if not m:
        raise RuntimeError(f"<{tag}> block not found in {SRC}")
    return m.group(1)


def find_block_end(src: str, start: int) -> int:
    """Given start just after an opening '{', return index just after its matching '}'."""
    depth = 1
    i = start
    while i < len(src) and depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return i


def main() -> None:
    html = SRC.read_text(encoding="utf-8")
    style_block = extract_tag(html, "style")
    script_block = extract_tag(html, "script")

    # ---- Split CSS into tokens (:root) + the rest ----
    root_match = re.search(r":root\s*\{", style_block)
    if not root_match:
        raise RuntimeError("No :root block in <style>")
    root_close = find_block_end(style_block, root_match.end())
    tokens = style_block[root_match.end() : root_close - 1].strip()
    tokens_css = ":root {\n" + "\n".join("  " + ln for ln in tokens.splitlines()) + "\n}\n"

    before = style_block[: root_match.start()].strip()
    after = style_block[root_close:].strip()
    app_css = (before + "\n\n" + after).strip() + "\n"

    # ---- Extract <body> ----
    body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.S)
    body = body_match.group(1).strip() if body_match else ""

    # ---- Build index.html with CSS/JS wired via url_for (Flask-ready) ----
    NL = "\n"
    head_links = (
        f'    <link rel="stylesheet" href="{{{{ url_for(\'static\', filename=\'css/tokens.css\') }}}}">'
        + NL
        + f'    <link rel="stylesheet" href="{{{{ url_for(\'static\', filename=\'css/app.css\') }}}}">'
        + NL
    )
    script_tag = (
        f'    <script src="{{{{ url_for(\'static\', filename=\'js/app.js\') }}}}" defer></script>'
        + NL
    )
    index_html = (
        "<!DOCTYPE html>"
        + NL
        + '<html lang="zh-CN">'
        + NL
        + "<head>"
        + NL
        + '    <meta charset="UTF-8">'
        + NL
        + '    <meta name="viewport" content="width=device-width, initial-scale=1.0">'
        + NL
        + "    <title>Termify — 万物皆可终端</title>"
        + NL
        + '    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&amp;family=Space+Grotesk:wght@400;500;600;700&amp;display=swap" rel="stylesheet">'
        + NL
        + head_links
        + "</head>"
        + NL
        + "<body>"
        + NL
        + body
        + NL
        + script_tag
        + "</body>"
        + NL
        + "</html>"
        + NL
    )

    TEMPLATES.mkdir(parents=True, exist_ok=True)
    CSS.mkdir(parents=True, exist_ok=True)
    JS.mkdir(parents=True, exist_ok=True)

    (CSS / "tokens.css").write_text(tokens_css, encoding="utf-8")
    (CSS / "app.css").write_text(app_css, encoding="utf-8")
    (JS / "app.js").write_text(script_block.strip() + "\n", encoding="utf-8")
    (TEMPLATES / "index.html").write_text(index_html, encoding="utf-8")

    print(
        f"OK  templates/index.html  ({len(index_html)} bytes)\n"
        f"    static/css/tokens.css ({len(tokens_css)} bytes)\n"
        f"    static/css/app.css    ({len(app_css)} bytes)\n"
        f"    static/js/app.js      ({len(script_block)} bytes)"
    )


if __name__ == "__main__":
    main()
