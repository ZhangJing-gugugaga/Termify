"""Flask entry point — wires the Phase 1 termify engine to the Phase 2 frontend.

Implements PRD §6's three endpoints plus a download route and the page route.
All heavy lifting (frame extract / charset map / bundling) is delegated to the
Phase 1 termify APIs; this file is just HTTP glue.

T1.6 Online Gallery adds /api/gallery/* + /gallery /v/<id> /admin routes.
"""

from __future__ import annotations

import json
import os
import threading
import re
import time
import uuid

from flask import (Flask, abort, jsonify, make_response, redirect,
                   render_template, request, send_file, url_for)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # PRD §7.1

TASKS: dict[str, dict] = {}  # task_id -> metadata + conversion cache
TASKS_LOCK = threading.Lock()

VALID_EXT = {".gif", ".png", ".jpg", ".jpeg"}
VALID_FORMATS = {"python", "html"}

# --- T1.6 Gallery wiring ----------------------------------------------------
from termify import gallery as _gallery_mod
from termify.charset import CHARSETS

GALLERY_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(GALLERY_DATA_DIR, exist_ok=True)
GALLERY_DB = _gallery_mod.GalleryDB(os.path.join(GALLERY_DATA_DIR, "termify.db"))
GALLERY_DB.init_db()

def _admin_pwd() -> str:
    """Read TERMIFY_ADMIN_PWD on each request (so tests can monkeypatch env)."""
    return os.environ.get("TERMIFY_ADMIN_PWD", "")

# Rate limit: {ip: [(action_str, timestamp_s), ...]}
_RL_LOCK = threading.Lock()
_RL_LOG: dict[str, list[tuple[str, float]]] = {}


def _client_ip() -> str:
    """Best-effort client IP, honouring X-Forwarded-For behind a proxy."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def _rate_check(ip: str, action: str, *, per_minute: int | None = None,
                per_day: int | None = None) -> tuple[bool, str]:
    """Return (allowed, reason). action like 'upload', 'like', 'report'."""
    now = time.time()
    with _RL_LOCK:
        entries = _RL_LOG.setdefault(ip, [])
        # Sweep old (> 24h)
        entries[:] = [(a, t) for a, t in entries if now - t < 86400]
        same_action = [(a, t) for a, t in entries if a == action]
        if per_minute is not None:
            recent = [t for _, t in same_action if now - t < 60]
            if len(recent) >= per_minute:
                return False, f"{action} rate limit: {per_minute}/min exceeded"
        if per_day is not None:
            if len(same_action) >= per_day:
                return False, f"{action} rate limit: {per_day}/day exceeded"
        entries.append((action, now))
        return True, ""

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


@app.route("/api/upload-batch", methods=["POST"])
def upload_batch():
    """Receive multiple files, return task_ids for each."""
    files = request.files.getlist("files")
    if not files or all(f.filename == "" for f in files):
        return jsonify({"error": "No files provided"}), 400

    from termify import convert

    results = []
    errors = []
    for file in files:
        if not file or not file.filename:
            continue
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in VALID_EXT:
            errors.append({"filename": file.filename, "error": "Unsupported format"})
            continue

        task_id = uuid.uuid4().hex[:12]
        save_path = os.path.join("uploads", f"{task_id}_{file.filename}")
        file.save(save_path)

        try:
            seq = convert(save_path, "ascii", 80, 24)
        except Exception as exc:  # noqa: BLE001
            os.remove(save_path)
            errors.append({"filename": file.filename, "error": str(exc)})
            continue

        with TASKS_LOCK:
            TASKS[task_id] = {
                "filepath": save_path,
                "original_size": {"width": seq.width, "height": seq.height},
                "target_size": {"width": seq.width, "height": seq.height},
                "frames_count": len(seq.lines_per_frame),
                "interval": seq.interval,
                "cache": {"ascii:80x24": seq},
            }

        results.append({
            "task_id": task_id,
            "filename": file.filename,
            "frames_count": len(seq.lines_per_frame),
            "original_size": TASKS[task_id]["original_size"],
            "target_size": TASKS[task_id]["target_size"],
        })

    return jsonify({"task_ids": results, "errors": errors})


@app.route("/api/upload-video", methods=["POST"])
def upload_video():
    """Upload a video (MP4/WEBM), extract frames via ffmpeg, convert."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    # Save video to temp path
    ext = os.path.splitext(file.filename)[1].lower()
    from termify.video import VALID_VIDEO_EXTS, validate_video, extract_frames, frames_dir_to_images, VideoError
    if ext not in VALID_VIDEO_EXTS:
        # Allow but flag — let validate reject
        pass

    video_tmp = os.path.join("uploads", f"video_{uuid.uuid4().hex[:12]}{ext}")
    file.save(video_tmp)

    try:
        validate_video(video_tmp)
    except VideoError as exc:
        os.remove(video_tmp)
        return jsonify({"error": str(exc)}), 400

    # Extract frames via ffmpeg
    try:
        frames_dir, fps = extract_frames(video_tmp)
    except VideoError as exc:
        os.remove(video_tmp)
        return jsonify({"error": str(exc)}), 422

    # Process each frame through the engine
    from termify.engine import render_frame, scale_frame
    from termify.charset import CHARSETS
    from PIL import Image

    task_id = uuid.uuid4().hex[:12]
    frame_paths = frames_dir_to_images(frames_dir)
    charset = "ascii"
    width, height = 80, 24

    lines_per_frame = []
    try:
        for fpath in frame_paths:
            img = Image.open(fpath).convert("RGB")
            if charset == "blocks":
                sw, sh = width, height * 2
            elif charset == "braille":
                sw, sh = width * 2, height * 4
            else:
                sw, sh = width, height
            scaled = scale_frame(img, sw, sh)
            lines = render_frame(scaled, charset, sw, sh)
            lines_per_frame.append(lines)
    except Exception as exc:  # noqa: BLE001
        os.remove(video_tmp)
        import shutil
        shutil.rmtree(frames_dir, ignore_errors=True)
        return jsonify({"error": f"Frame conversion failed: {exc}"}), 500

    # Cleanup
    os.remove(video_tmp)
    import shutil
    shutil.rmtree(frames_dir, ignore_errors=True)

    interval = 1.0 / fps  # fps=10 from ffmpeg extraction
    with TASKS_LOCK:
        TASKS[task_id] = {
            "filepath": None,
            "original_size": {"type": "video", "frame_count": len(frame_paths)},
            "target_size": {"width": width, "height": height},
            "frames_count": len(lines_per_frame),
            "interval": interval,
            "cache": {f"{charset}:80x24": None},  # placeholder; preview re-converts
        }

    # Store full sequence in cache for preview
    from termify.engine import FrameSequence
    seq = FrameSequence(
        lines_per_frame=lines_per_frame,
        interval=interval,
        width=width,
        height=height,
        charset=charset,
    )
    with TASKS_LOCK:
        if task_id in TASKS:
            TASKS[task_id]["cache"][f"{charset}:80x24"] = seq

    return jsonify({
        "task_id": task_id,
        "filename": file.filename,
        "frames_count": len(lines_per_frame),
        "original_size": {"type": "video", "frame_count": len(frame_paths)},
        "target_size": {"width": width, "height": height},
    })


@app.route("/api/fetch-url", methods=["POST"])
def fetch_url():
    """Download an image URL server-side and create a conversion task.

    SSRF protection: private IP blocked, Content-Type/Size validation,
    download timeout 15s, size cap 20MB, PIL verify.
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    from termify.urlfetch import fetch_url_to_temp, URLFetchError
    from termify import convert

    try:
        tmp_path = fetch_url_to_temp(url)
    except URLFetchError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        task_id = uuid.uuid4().hex[:12]
        seq = convert(tmp_path, "ascii", 80, 24)
    except Exception as exc:  # noqa: BLE001
        os.remove(tmp_path)
        return jsonify({"error": f"Conversion failed: {exc}"}), 500

    with TASKS_LOCK:
        TASKS[task_id] = {
            "filepath": tmp_path,
            "original_size": {"width": seq.width, "height": seq.height},
            "target_size": {"width": seq.width, "height": seq.height},
            "frames_count": len(seq.lines_per_frame),
            "interval": seq.interval,
            "cache": {"ascii:80x24": seq},
        }

    return jsonify({
        "task_id": task_id,
        "filename": os.path.basename(tmp_path),
        "frames_count": len(seq.lines_per_frame),
        "original_size": {"width": seq.width, "height": seq.height},
        "target_size": {"width": seq.width, "height": seq.height},
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


# ---------------------------------------------------------------------------
# T1.6 Online Gallery — API + pages
# ---------------------------------------------------------------------------

GALLERY_EXT = VALID_EXT  # source image extensions


def _gallery_public_dict(work: dict, request_host: str = "") -> dict:
    """Strip internals before returning to the client."""
    return {
        "id": work["id"],
        "title": work["title"],
        "description": work["description"],
        "tags": json.loads(work["tags"]) if work["tags"] else [],
        "author": work["author"],
        "thumbnail_url": url_for("gallery_thumb", work_id=work["id"]),
        "og_url": url_for("gallery_og", work_id=work["id"]),
        "source_url": url_for("gallery_source", work_id=work["id"]),
        "params": json.loads(work["params_json"]) if work["params_json"] else {},
        "view_count": work["view_count"],
        "like_count": work["like_count"],
        "download_count": work["download_count"],
        "created_at": work["created_at"],
    }


def _make_unique_id() -> str:
    """Generate a short ID, retry on collision."""
    for _ in range(64):
        sid = _gallery_mod.make_short_id()
        if not GALLERY_DB.id_collides(sid):
            return sid
    raise RuntimeError("Could not allocate unique short_id after 64 tries")


@app.route("/api/gallery/upload", methods=["POST"])
def gallery_upload():
    """Accept multipart upload (source image + JSON params + form fields).

    Returns {ok, id, admin_token, url, work}.
    Rate limit: 3/min, 10/day per IP.
    """
    if "source" not in request.files:
        return jsonify({"error": "No source file"}), 400
    source_file = request.files["source"]
    if not source_file.filename:
        return jsonify({"error": "Empty source filename"}), 400

    ext = os.path.splitext(source_file.filename)[1].lower()
    if ext not in GALLERY_EXT:
        return jsonify({"error": f"Unsupported file extension: {ext}"}), 400

    ip = _client_ip()
    ok, reason = _rate_check(ip, "upload", per_minute=3, per_day=10)
    if not ok:
        return jsonify({"error": reason}), 429

    # Form fields with validation
    title = _gallery_mod.sanitize(
        request.form.get("title") or os.path.splitext(source_file.filename)[0],
        _gallery_mod._TITLE_MAX,
    )
    description = _gallery_mod.sanitize(
        request.form.get("description"), _gallery_mod._DESC_MAX
    )
    author = _gallery_mod.sanitize(
        request.form.get("author") or "", _gallery_mod._AUTHOR_MAX
    ) or "匿名创作者"
    tags_raw = request.form.get("tags", "[]")
    try:
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        if not isinstance(tags, list):
            tags = []
    except (json.JSONDecodeError, TypeError):
        tags = []
    tags = [t for t in tags if t in _gallery_mod.VALID_TAGS][:3]
    tags_json = json.dumps(tags, ensure_ascii=False)

    is_private = 1 if (request.form.get("is_private") in ("1", "true", "on")) else 0

    # Params JSON (charset / width / height / format / interval / fg / bg)
    params_raw = request.form.get("params", "{}")
    try:
        params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
        if not isinstance(params, dict):
            params = {}
    except (json.JSONDecodeError, TypeError):
        params = {}
    # Sanitize core fields
    params.setdefault("charset", _gallery_mod.DEFAULT_CHARSET)
    params.setdefault("width", _gallery_mod.DEFAULT_WIDTH)
    params.setdefault("height", _gallery_mod.DEFAULT_HEIGHT)
    if params.get("charset") not in CHARSETS:
        params["charset"] = _gallery_mod.DEFAULT_CHARSET
    try:
        params["width"] = max(1, min(400, int(params["width"])))
        params["height"] = max(1, min(400, int(params["height"])))
    except (TypeError, ValueError):
        params["width"] = _gallery_mod.DEFAULT_WIDTH
        params["height"] = _gallery_mod.DEFAULT_HEIGHT

    # Persist file
    work_id = _make_unique_id()
    base = _gallery_mod.gallery_base(GALLERY_DATA_DIR)
    source_path = os.path.join(base, f"{work_id}{ext}")
    source_file.save(source_path)

    # Verify the file opens with Pillow
    try:
        from PIL import Image as _PILImage
        with _PILImage.open(source_path) as im:
            im.verify()
        # Reopen after verify (verify leaves the handle unusable)
        with _PILImage.open(source_path) as im:
            im.load()
    except Exception as exc:  # noqa: BLE001
        os.remove(source_path)
        return jsonify({"error": f"Invalid image: {exc}"}), 400

    # Generate thumbnails + OG
    thumb_path = os.path.join(base, f"{work_id}_thumb.gif")
    og_path = os.path.join(base, f"{work_id}_og.png")
    try:
        _gallery_mod.make_thumbnail(source_path, thumb_path)
        _gallery_mod.make_og_image(source_path, og_path, title, author)
    except Exception as exc:  # noqa: BLE001
        for p in (source_path, thumb_path, og_path):
            if os.path.isfile(p):
                os.remove(p)
        return jsonify({"error": f"Thumbnail generation failed: {exc}"}), 500

    # Insert into DB
    admin_token = _gallery_mod.make_admin_token()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    GALLERY_DB.insert_work({
        "id": work_id,
        "title": title,
        "description": description,
        "tags": tags_json,
        "author": author,
        "source_path": source_path,
        "thumbnail_path": thumb_path,
        "og_path": og_path,
        "params_json": json.dumps(params, ensure_ascii=False),
        "is_private": is_private,
        "admin_token": admin_token,
        "created_at": now_iso,
        "ip": ip,
    })

    work = GALLERY_DB.get_work(work_id)
    resp = make_response(jsonify({
        "ok": True,
        "id": work_id,
        "admin_token": admin_token,
        "url": url_for("gallery_view", work_id=work_id, _external=False),
        "work": _gallery_public_dict(work),
    }))
    # Set admin token cookie (30 days)
    resp.set_cookie(
        f"termify_admin_{work_id}",
        admin_token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="Lax",
    )
    return resp


@app.route("/api/gallery/list", methods=["GET"])
def gallery_list():
    """Paginated list of gallery works.

    Query: sort, tag, page, limit.
    Always returns public works only (unless authenticated via cookie).
    """
    sort = request.args.get("sort", "latest")
    if sort not in ("latest", "hot", "random"):
        sort = "latest"
    tag = request.args.get("tag") or None
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        limit = max(1, min(60, int(request.args.get("limit", 24))))
    except (TypeError, ValueError):
        limit = 24
    items, total = GALLERY_DB.list_works(sort=sort, tag=tag, page=page, limit=limit)
    # Admin cookie check: include is_authorized flag per item
    return jsonify({
        "items": [_gallery_public_dict(w) for w in items],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
    })


@app.route("/api/gallery/work/<work_id>", methods=["GET"])
def gallery_work(work_id):
    """Detail for one work. Bumps view count. Returns params for pre-fill."""
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    if work["is_private"]:
        # Check admin token cookie; else allow view if URL is direct link (private only hides from list)
        pass  # private works are still viewable at /v/<id>
    GALLERY_DB.increment_view(work_id)
    fresh = GALLERY_DB.get_work(work_id)
    out = _gallery_public_dict(fresh)
    # Omit sensitive when listing
    out["is_authorized"] = (
        request.cookies.get(f"termify_admin_{work_id}") == work["admin_token"]
        or (bool(_admin_pwd()) and request.args.get("pwd") == _admin_pwd())
    )
    return jsonify(out)


@app.route("/api/gallery/like/<work_id>", methods=["POST"])
def gallery_like(work_id):
    """Toggle like. IP + cookie double rate limit. Returns {liked, count}."""
    ip = _client_ip()
    ok, reason = _rate_check(ip, "like", per_day=50)
    if not ok:
        return jsonify({"error": reason}), 429
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    existing_cookie = request.cookies.get(f"termify_like_{work_id}", "")
    json_cookie = request.json.get("cookie", "") if request.is_json else ""
    cookie_val = existing_cookie or json_cookie
    if not cookie_val:
        cookie_val = _client_ip() + str(time.time())
    liked, count = GALLERY_DB.toggle_like(work_id, ip, cookie_val)
    resp = make_response(jsonify({"liked": liked, "count": count, "ok": True}))
    if not request.cookies.get(f"termify_like_{work_id}"):
        resp.set_cookie(f"termify_like_{work_id}", cookie_val, max_age=86400 * 365, httponly=True, samesite="Lax")
    return resp


@app.route("/api/gallery/report/<work_id>", methods=["POST"])
def gallery_report(work_id):
    """Submit a report. Rate limit 10/day per IP."""
    ip = _client_ip()
    ok, reason = _rate_check(ip, "report", per_day=10)
    if not ok:
        return jsonify({"error": reason}), 429
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    data = request.get_json(silent=True) or {}
    reason_str = data.get("reason", "")
    if reason_str not in _gallery_mod.VALID_REPORT_REASONS:
        return jsonify({"error": f"Invalid reason; expected one of {_gallery_mod.VALID_REPORT_REASONS}"}), 400
    desc = _gallery_mod.sanitize(data.get("description", ""), 300)
    report_id = GALLERY_DB.add_report(work_id, ip, reason_str, desc)
    return jsonify({"ok": True, "report_id": report_id})


@app.route("/api/gallery/work/<work_id>", methods=["DELETE"])
def gallery_delete(work_id):
    """Delete a work. Requires valid admin token (cookie or header) or global admin pwd."""
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    token = request.cookies.get(f"termify_admin_{work_id}", "")
    hdr_token = request.headers.get("X-Termify-Admin", "")
    input_token = hdr_token or token
    is_admin = bool(_admin_pwd()) and (
        request.args.get("pwd") == _admin_pwd()
        or request.headers.get("X-Termify-Admin-Pwd") == _admin_pwd()
    )
    authorized = is_admin or (input_token and input_token == work["admin_token"])
    if not authorized:
        return jsonify({"error": "Unauthorized"}), 403
    deleted = GALLERY_DB.delete_work(work_id)
    if deleted:
        for key in ("source_path", "thumbnail_path", "og_path"):
            p = deleted.get(key)
            if p and os.path.isfile(p):
                os.remove(p)
    return jsonify({"ok": True})


@app.route("/api/gallery/admin", methods=["GET"])
def gallery_admin_list():
    """Admin dashboard: list works + pending reports.

    Requires ?pwd=<_admin_pwd()> or X-Termify-Admin-Pwd header.
    """
    pwd = request.args.get("pwd", "") or request.headers.get("X-Termify-Admin-Pwd", "")
    if not _admin_pwd() or pwd != _admin_pwd():
        return jsonify({"error": "Unauthorized"}), 403
    works = GALLERY_DB.admin_list_works()
    reports = GALLERY_DB.admin_list_reports(status="pending")
    return jsonify({
        "works": [_gallery_public_dict(w) for w in works],
        "reports": reports,
    })


@app.route("/api/gallery/admin/<work_id>", methods=["DELETE"])
def gallery_admin_delete(work_id):
    """Admin hard delete."""
    pwd = request.args.get("pwd", "") or request.headers.get("X-Termify-Admin-Pwd", "")
    if not _admin_pwd() or pwd != _admin_pwd():
        return jsonify({"error": "Unauthorized"}), 403
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    deleted = GALLERY_DB.delete_work(work_id)
    if deleted:
        for key in ("source_path", "thumbnail_path", "og_path"):
            p = deleted.get(key)
            if p and os.path.isfile(p):
                os.remove(p)
    return jsonify({"ok": True})


@app.route("/api/gallery/admin/report/<int:report_id>", methods=["POST"])
def gallery_admin_resolve_report(report_id):
    """Mark a report resolved/dismissed."""
    pwd = request.args.get("pwd", "") or request.headers.get("X-Termify-Admin-Pwd", "")
    if not _admin_pwd() or pwd != _admin_pwd():
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json(silent=True) or {}
    status = data.get("status", "resolved")
    if status not in ("resolved", "dismissed"):
        return jsonify({"error": "Invalid status"}), 400
    GALLERY_DB.admin_update_report(report_id, status)
    return jsonify({"ok": True})


# --- gallery preview + download (derived from stored source) ---

@app.route("/api/gallery/preview/<work_id>", methods=["GET"])
def gallery_preview(work_id):
    """Render a gallery work's frames in the requested charset/size.

    Query params (all optional, default to the work's original params):
      charset = ascii|blocks|braille|geometric|binary
      width   = int (1-400)
      height  = int (1-400)
    Returns JSON {frames, interval, width, height, charset}.
    """
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    original = json.loads(work["params_json"]) if work["params_json"] else {}
    charset = request.args.get("charset", original.get("charset", "blocks")).strip().lower()
    if charset not in CHARSETS:
        return jsonify({"error": f"Invalid charset: {charset}"}), 400
    try:
        width = int(request.args.get("width", original.get("width", 80)))
        height = int(request.args.get("height", original.get("height", 24)))
    except (TypeError, ValueError):
        return jsonify({"error": "width/height must be integers"}), 400
    width = max(1, min(400, width))
    height = max(1, min(400, height))

    from termify import convert
    try:
        seq = convert(work["source_path"], charset, width, height)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": f"Conversion failed: {exc}"}), 500

    return jsonify({
        "frames": seq.lines_per_frame,
        "interval": seq.interval,
        "width": seq.width,
        "height": seq.height,
        "charset": charset,
        "frame_count": len(seq.lines_per_frame),
    })


@app.route("/api/gallery/download/<work_id>", methods=["GET"])
def gallery_download(work_id):
    """Generate + serve a .py or .html download for a gallery work.

    Query params:
      charset = ascii|blocks|braille|geometric|binary (default: work's original)
      width, height = int (default: work's original)
      format  = python|html (required)
    Increments download_count once per IP per 24h.
    """
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    fmt = request.args.get("format", "").lower().strip()
    if fmt not in VALID_FORMATS:
        return jsonify({"error": f"Invalid format: {fmt!r} (expected python or html)"}), 400
    original = json.loads(work["params_json"]) if work["params_json"] else {}
    charset = request.args.get("charset", original.get("charset", "blocks")).strip().lower()
    if charset not in CHARSETS:
        return jsonify({"error": f"Invalid charset: {charset}"}), 400
    try:
        width = int(request.args.get("width", original.get("width", 80)))
        height = int(request.args.get("height", original.get("height", 24)))
    except (TypeError, ValueError):
        return jsonify({"error": "width/height must be integers"}), 400
    width = max(1, min(400, width))
    height = max(1, min(400, height))

    from termify import convert
    from termify.output import render
    seq = convert(work["source_path"], charset, width, height)
    content = render(seq, fmt)

    ext = "py" if fmt == "python" else "html"
    filename = f"gallery_{work_id}_{charset}.{ext}"
    tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    out_path = os.path.join(tmp_dir, filename)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(content)

    # IP-based download dedup: count once per IP per 24h per work
    ip = _client_ip()
    _record_download(work_id, ip)

    return send_file(out_path, as_attachment=True, download_name=filename)


def _record_download(work_id: str, ip: str) -> None:
    """Increment download_count at most once per IP per 24h."""
    now = time.time()
    with _RL_LOCK:
        entry = _download_dedup.setdefault(work_id, {})
        last = entry.get(ip, 0.0)
        if now - last >= 86400:
            entry[ip] = now
            GALLERY_DB.increment_download(work_id)


_download_dedup: dict[str, dict[str, float]] = {}


# --- proxy routes for source/thumbnail/og (serve files outside static) ---

@app.route("/gallery/file/<work_id>/source")
def gallery_source(work_id):
    work = GALLERY_DB.get_work(work_id)
    if not work or not os.path.isfile(work["source_path"]):
        abort(404)
    return send_file(work["source_path"])


@app.route("/gallery/file/<work_id>/thumb")
def gallery_thumb(work_id):
    work = GALLERY_DB.get_work(work_id)
    if not work or not os.path.isfile(work["thumbnail_path"]):
        abort(404)
    return send_file(work["thumbnail_path"], mimetype="image/gif")


@app.route("/gallery/file/<work_id>/og")
def gallery_og(work_id):
    work = GALLERY_DB.get_work(work_id)
    if not work or not os.path.isfile(work["og_path"]):
        abort(404)
    return send_file(work["og_path"], mimetype="image/png")


# --- page routes ---

@app.route("/gallery")
def gallery_page():
    return render_template("gallery.html")


@app.route("/v/<work_id>")
def gallery_view(work_id):
    work = GALLERY_DB.get_work(work_id)
    if not work:
        abort(404)
    return render_template("view_work.html", work=work)


@app.route("/admin")
def gallery_admin_page():
    return render_template("admin.html")


if __name__ == "__main__":
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("tmp", exist_ok=True)
    os.makedirs(GALLERY_DATA_DIR, exist_ok=True)
    os.makedirs("data", exist_ok=True)
    # ponytail: reloader off — the app stores tasks in memory, so a watchdog
    # restart (e.g. on each /api/generate writing tmp/*.py) would wipe them.
    app.run(debug=True, use_reloader=False, port=5000)
