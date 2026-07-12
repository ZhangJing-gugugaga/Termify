"""Flask entry point — wires the Phase 1 termify engine to the Phase 2 frontend.

Implements PRD §6's three endpoints plus a download route and the page route.
All heavy lifting (frame extract / charset map / bundling) is delegated to the
Phase 1 termify APIs; this file is just HTTP glue.
"""

from __future__ import annotations

import os
import threading
import re
import uuid

from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # PRD §7.1

TASKS: dict[str, dict] = {}  # task_id -> metadata + conversion cache
TASKS_LOCK = threading.Lock()

VALID_EXT = {".gif", ".png", ".jpg", ".jpeg"}
VALID_FORMATS = {"python", "html"}

def _parse_rgb(value):
    """Parse 'rgb(R,G,B)' string into (R,G,B) tuple, or None if invalid/empty."""
    if not value:
        return None
    m = re.match(r"rgb\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)", value)
    if not m:
        return None
    r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
        return None
    return (r, g, b)



def _original_size(path: str) -> dict:
    """Read width/height of the source without decoding every frame."""
    from PIL import Image

    with Image.open(path) as im:
        w, h = im.size
    return {"width": w, "height": h}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in VALID_EXT:
        return jsonify({"error": "Unsupported format"}), 400

    task_id = uuid.uuid4().hex[:12]
    save_path = os.path.join("uploads", f"{task_id}_{file.filename}")
    file.save(save_path)

    try:
        from termify import convert

        seq = convert(save_path, "ascii", 80, 24)
    except Exception as exc:  # noqa: BLE001 — surface any conversion failure
        os.remove(save_path)
        return jsonify({"error": f"Conversion failed: {exc}"}), 500

    with TASKS_LOCK:
        TASKS[task_id] = {
            "filepath": save_path,
            "original_size": _original_size(save_path),
            "target_size": {"width": seq.width, "height": seq.height},
            "frames_count": len(seq.lines_per_frame),
            "interval": seq.interval,
            "cache": {"ascii:80x24": seq},
        }

    return jsonify({
        "task_id": task_id,
        "frames_count": len(seq.lines_per_frame),
        "original_size": TASKS[task_id]["original_size"],
        "target_size": TASKS[task_id]["target_size"],
    })


def _get_sequence(task_id: str, charset: str, width: int, height: int, fg_color=None, bg_color=None):
    """Return a converted FrameSequence, converting+caching on first miss."""
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if task is None:
        return None

    fg_part = f"rgb({fg_color[0]},{fg_color[1]},{fg_color[2]})" if fg_color else "none"
    bg_part = f"rgb({bg_color[0]},{bg_color[1]},{bg_color[2]})" if bg_color else "none"
    key = f"{charset}:{width}x{height}:{fg_part}:{bg_part}"
    seq = task.get("cache", {}).get(key)
    if seq is not None:
        return seq

    from termify import convert

    seq = convert(task["filepath"], charset, width, height, fg_color=fg_color, bg_color=bg_color)
    with TASKS_LOCK:
        if task_id in TASKS:
            TASKS[task_id].setdefault("cache", {})[key] = seq
    return seq


@app.route("/api/preview/<task_id>")
def preview(task_id):
    charset = request.args.get("charset", "ascii").lower().strip()

    from termify.charset import CHARSETS

    if charset not in CHARSETS:
        return jsonify({"error": f"Unknown charset: {charset}"}), 400

    try:
        width = int(request.args.get("width", 80))
        height = int(request.args.get("height", 24))
    except ValueError:
        return jsonify({"error": "width/height must be integers"}), 400

    frame = request.args.get("frame")
    try:
        frame = int(frame) if frame is not None else None
    except ValueError:
        return jsonify({"error": "frame must be an integer"}), 400

    fg_color = _parse_rgb(request.args.get("fg"))
    bg_color = _parse_rgb(request.args.get("bg"))
    seq = _get_sequence(task_id, charset, width, height, fg_color=fg_color, bg_color=bg_color)
    if seq is None:
        return jsonify({"error": "Task not found"}), 404

    frame_count = len(seq.lines_per_frame)
    if frame is not None and not (0 <= frame < frame_count):
        return jsonify({"error": f"frame {frame} out of range (0-{frame_count-1})"}), 400

    # No `frame` requested -> return ALL frames so the player can loop them.
    if frame is None:
        return jsonify({
            "frames": seq.lines_per_frame,
            "frame_count": frame_count,
            "interval": seq.interval,
            "charset": charset,
            "width": seq.width,
            "height": seq.height,
        })

    # `frame=N` -> single frame (1D), matching the PRD §6.2 documented shape.
    return jsonify({
        "lines": seq.lines_per_frame[frame],
        "frame_count": frame_count,
        "interval": seq.interval,
        "charset": charset,
        "width": seq.width,
        "height": seq.height,
    })


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id")
    charset = (data.get("charset") or "ascii").lower().strip()
    fmt = data.get("format")

    from termify.charset import CHARSETS

    if not task_id or task_id not in TASKS:
        return jsonify({"error": "Task not found"}), 404
    if charset not in CHARSETS:
        return jsonify({"error": f"Unknown charset: {charset}"}), 400
    if fmt not in VALID_FORMATS:
        return jsonify({"error": f"Unknown format: {fmt}"}), 400

    try:
        width = int(data.get("width", 80))
        height = int(data.get("height", 24))
    except ValueError:
        return jsonify({"error": "width/height must be integers"}), 400

    fg_color = _parse_rgb(data.get("fg"))
    bg_color = _parse_rgb(data.get("bg"))
    seq = _get_sequence(task_id, charset, width, height, fg_color=fg_color, bg_color=bg_color)

    from termify.output import render

    content = render(seq, fmt)

    ext = "py" if fmt == "python" else "html"
    filename = f"{task_id}_{charset}.{ext}"
    out_path = os.path.join("tmp", filename)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(content)

    size_bytes = len(content.encode("utf-8"))
    if size_bytes >= 1024:
        file_size = f"{size_bytes // 1024}KB"
    else:
        file_size = f"{size_bytes}B"

    return jsonify({
        "download_url": f"/api/download/{filename}",
        "file_size": file_size,
    })


@app.route("/api/download/<path:filename>")
def download(filename):
    if ".." in filename or os.sep in filename or "/" in filename:
        return jsonify({"error": "Invalid filename"}), 400

    path = os.path.join("tmp", filename)
    if not os.path.isfile(path):
        return jsonify({"error": "File not found"}), 404

    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("tmp", exist_ok=True)
    # ponytail: reloader off — the app stores tasks in memory, so a watchdog
    # restart (e.g. on each /api/generate writing tmp/*.py) would wipe them.
    app.run(debug=True, use_reloader=False, port=5000)
