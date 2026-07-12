# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Termify** — "万物皆可终端". A web tool that converts GIF / PNG / JPG into terminal-playable animations. User uploads a file, picks a rendering style, and downloads a `.py` script or self-contained `.html` that plays the animation. No accounts, no database, no deployment for MVP — download-and-go.

Full product spec: `PRD.md`. UI mockup (static, not wired to backend): `ui-mockup.html`. Go-to-market notes: `growth-plan.md`, `demo-article.md`.

## Current state

**Phase 1 (backend) + Phase 2 (frontend) + Phase 3 (integration) all implemented.** MVP complete and pushed to GitHub.

- Backend: `termify/` package (charset → frames → engine → output) with `pytest`-green 42 unit tests. See `demo.py` for CLI end-to-end.
- Frontend: `templates/index.html` + `static/css/{tokens,app}.css` + `static/js/app.js` (IIFE, API-driven). MCU output panel is UI-only, labeled "v2 即将支持".
- Integration: `app.py` (Flask) wires the three PRD §6 endpoints directly to `termify.convert()` + `render()`. Server: `python app.py` → `http://127.0.0.1:5000`.
- Output rendering fix: browsers do not interpret raw ANSI, so `ansiToHtml` (JS-side) converts TrueColor ANSI → HTML `<span>` with CSS gradients. Half-block `▀` gets a top/bottom linear gradient on an empty `<span class="hb">` (no `background-clip:text` for performance).
- Performance: all frames pre-rendered to HTML strings on load (`S.htmlFrames`), playback via `requestAnimationFrame` (not `setInterval`), switching charsets uses `wasPlaying` flag to avoid progress bar jumps.
- CJK Windows fix: `▀` (U+2580) renders as double-width on Chinese Windows; `.hb` spans force `width:1ch;height:1.3em` to prevent overflow.
- Tests: `tests/` — 42 tests, all green.

## Tech stack (locked by PRD)

- **Backend:** Python 3.10+, Flask, Pillow (PIL). No ORM, no DB. Uploaded files are temp-only (`uploads/`, `tmp/` — gitignored), processed and deleted.
- **Frontend:** Vanilla HTML/CSS/JS. No framework for MVP. Terminal preview uses `<pre>` + monospace + ANSI color. Dark theme via CSS variables.
- **No video input, no user accounts, no sharing links in MVP** — these are explicitly deferred to v2 (see PRD §3.2).

## Architecture (from PRD — do not diverge without updating PRD)

```
Browser (upload / preview / player / download)
  │  HTTP API (JSON + multipart)
  └─ Flask app
       ├─ POST /api/upload        → receive file, extract frames, return task_id + meta
       ├─ GET  /api/preview/{id}  → render one frame in chosen charset (charset, frame params)
       └─ POST /api/generate      → bundle chosen charset+format, return download_url
```

Core conversion pipeline (single source of truth is `PRD.md` §5.3):
1. Frame extraction — `ImageSequence.Iterator` for GIF, single frame for images.
2. Scale to terminal size (default 80×24), preserve aspect ratio.
3. Pixel → charset mapping:
   - `ascii` (`@#%*+=-:. `) grayscale density, no color
   - `blocks` (`█▀▄`) + TrueColor 24-bit ANSI
   - `braille` (2×4 pixel block → Braille codepoint)
   - `geometric` (`■□▪▫●○◆◇`)
   - `binary` (`█ ` thresholded)
4. Emit frame sequence (lines + per-frame interval).
5. Bundle into one of two outputs — Python player script (embedded `FRAMES` + `time.sleep` loop + `KeyboardInterrupt` cleanup) or self-contained HTML (`<pre>` + JS timer).

Charset definitions and output templates live in PRD §5.5 and §5.6. Reference them when implementing — don't reinvent mappings.

Implementation notes:
- `blocks` engine scales frames to **(w, 2h)** so the half-block renderer can pair consecutive rows (fg≠bg vertical-resolution doubling). Other charsets scale to (w, h).
- `blocks` renderer uses **color delta encoding** (only emit ANSI escape when fg/bg changes vs previous cell) — matches the reference SalaryCat renderer and cuts output size ~70-75%.
- `braille` collapses to (w/2 × h/4) because each Braille codepoint covers a 2×4 cell — tests assert against this, don't "fix" it.

## Commands

```bash
pip install flask pillow        # install deps
python app.py                   # start server → http://127.0.0.1:5000
pytest tests/                   # run backend tests (42 tests, all green)
python demo.py your.gif --charset all  # CLI end-to-end: GIF → outputs/*.py + *.html
```

**⚠️ DO NOT run `python tools/build_frontend.py`** — it re-splits `ui-mockup.html`
into `static/` files, overwriting Phase 3 production code (API integration, upload
handler, rAF loop, pre-rendering) with the mockup's static-only JavaScript.
Edit `static/js/app.js` and `static/css/app.css` directly. The mockup is visual
reference only.

## Conventions

- Chinese (中文) for user-facing strings and comments is fine — this project targets a Chinese-speaking audience first.
- ANSI output in the Python script must use the most basic escape sequences; assume Windows VT100 + Linux terminfo + macOS. The HTML output is the fallback for incompatible terminals — keep it truly self-contained (no external JS, no CDN).
- File size cap: 20 MB upload (PRD §7.1). Processing timeout: 30 s.
- `uploads/`, `tmp/`, `*.pyc`, `venv/` are gitignored — don't fight this.

## When you write code here

- Re-read `PRD.md` before Phase 1 work. The 5 charsets, 2 output formats, and 3 API endpoints are the whole MVP — resist adding more.
- Match the existing mockup's look (`ui-mockup.html`) for the frontend; don't redesign it unless asked.
- Prefer the shortest working implementation. This is an MVP meant to validate demand, not a platform.
