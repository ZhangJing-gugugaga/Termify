"""Flask entry point — wires the Phase 1 termify engine to the Phase 2 frontend.

Implements PRD §6's three endpoints plus a download route and the page route.
All heavy lifting (frame extract / charset map / bundling) is delegated to the
Phase 1 termify APIs; this file is just HTTP glue.

T1.6 Online Gallery adds /api/gallery/* + /gallery /v/<id> /admin routes.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

from termify.video import VALID_VIDEO_EXTS
from termify import paths

from flask import (Flask, abort, jsonify, make_response, redirect,
                   render_template, request, send_file, url_for)

app = Flask(__name__)
# 从环境变量加载密钥，未设置时自动生成（每次重启会变，仅轻度会话场景安全）
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex())
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # PRD §7.1


@app.errorhandler(413)
def _request_entity_too_large(_e):
    """请求体超过 MAX_CONTENT_LENGTH 时返回 JSON（而非 Flask 默认 HTML 页）。"""
    return jsonify({"error": "文件过大（上限 20MB） / File too large (max 20MB)"}), 413

# --- T1.9 Task metadata store ------------------------------------------------
# Production bug fix (L1): under gunicorn 4 workers, a module-level TASKS dict
# lived in each worker's private memory, so a task created in worker A was
# invisible to B/C/D. Metadata is now persisted in a SQLite table; the
# per-worker conversion cache is still in-memory (cache misses just trigger
# a re-convert, which is harmless). See ``termify.taskstore``.
from termify.taskstore import (
    CACHE,
    get_store,
    cache_key as _cache_key,
    cache_get as _cache_get,
    cache_put as _cache_put,
)

VALID_EXT = {".gif", ".png", ".jpg", ".jpeg"}
VALID_FORMATS = {"python", "html", "mp4"}

# Video import/export: cap concurrent ffmpeg work so public-demo CPU stays
# responsive under load (负载限速).
_VIDEO_PROC_SLOTS = threading.Semaphore(2)
_VIDEO_IMPORT_SLOTS = threading.Semaphore(2)


def _sweep_stale_frame_dirs(max_age_hours: int = 24) -> None:
    """Best-effort removal of persisted video frame dirs past their TTL.

    Also removes stale per-task audio artifacts (audio_*.m4a / music_*.ext)
    — they share the uploads/ dir and the same lifetime as their task — and
    tmp/ 下的 gallery_* 下载产物（画廊生成、无 task 归属，同样 24h TTL）。
    路径基准统一走 termify.paths（仓库根锚定），与写入侧完全一致。
    """
    import shutil as _shutil

    cutoff = time.time() - max_age_hours * 3600
    uploads_base = paths.uploads_dir()
    try:
        entries = os.listdir(uploads_base)
    except OSError:
        entries = []
    for name in entries:
        is_stale_kind = name.startswith("frames_") \
            or name.startswith("audio_") or name.startswith("music_")
        if not is_stale_kind:
            continue
        path = os.path.abspath(os.path.join(uploads_base, name))
        if os.path.dirname(path) != uploads_base:
            continue
        try:
            if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                _shutil.rmtree(path, ignore_errors=True)
            elif os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            continue

    # tmp/ 下的画廊下载产物（gallery_<work>_<charset>.py/.html/.mp4）没有
    # task 归属，sweep_expired 清不到，这里按同一 24h TTL 兜底。
    tmp_base = paths.tmp_dir()
    try:
        tmp_entries = os.listdir(tmp_base)
    except OSError:
        return
    for name in tmp_entries:
        if not name.startswith("gallery_"):
            continue
        path = os.path.abspath(os.path.join(tmp_base, name))
        if os.path.dirname(path) != tmp_base or not path.startswith(tmp_base + os.sep):
            continue
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            continue


def _video_tmp_path(ext: str) -> str:
    """Server-side temp path for an uploaded video, containment-checked.

    ``ext`` must be one of the whitelisted video extensions; the file name
    itself is always server-generated (never derived from user input).
    """
    if ext not in (".mp4", ".webm", ".mov", ".avi", ".mkv"):
        raise ValueError(f"unsupported video extension: {ext!r}")
    if ".." in ext or "/" in ext or os.sep in ext:
        raise ValueError("video extension contains path separators")
    name = f"video_{uuid.uuid4().hex[:12]}{ext}"
    base = paths.uploads_dir()
    resolved = os.path.abspath(os.path.join(base, name))
    if os.path.dirname(resolved) != base or not resolved.startswith(base + os.sep):
        raise ValueError("generated video path escapes the uploads dir")
    return resolved


def _task_put(task_id: str, *, filepath, original_size, target_size,
              frames_count, interval) -> None:
    """Persist task metadata to the shared SQLite store."""
    get_store().put(
        task_id,
        filepath=filepath,
        original_size=original_size,
        target_size=target_size,
        frames_count=frames_count,
        interval=interval,
    )


def _task_get(task_id: str):
    """Return task metadata dict, or None if not found / unknown id."""
    if not task_id:
        return None
    return get_store().get(task_id)


def _task_get_or_404(task_id: str):
    """Return ``(task, None)`` if found, else ``(None, (json_resp, 404))``."""
    task = _task_get(task_id)
    if task is None:
        return None, (jsonify({"error": "任务不存在或已过期，请重新上传 / Task not found or expired, please re-upload"}), 404)
    return task, None

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


def _secret_equal(a, b) -> bool:
    """Constant-time comparison for admin tokens / passwords.

    ``secrets.compare_digest`` 要求两侧同为 str（或同为 bytes），这里先做
    真值守卫：任一侧非 str 或为空一律 False，避免异常与空口令误匹配。
    """
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    if not a or not b:
        return False
    return secrets.compare_digest(a, b)

# Rate limit: {ip: [(action_str, timestamp_s), ...]}
_RL_LOCK = threading.Lock()
_RL_LOG: dict[str, list[tuple[str, float]]] = {}
_RL_MAX_IPS = 8192  # 全局 IP 键数软上限，超出触发全表过期清扫


def _client_ip() -> str:
    """Best-effort client IP for rate limiting / dedup.

    仅当直连对端（remote_addr）是回环/私网地址——即应用确实部署在反向
    代理之后——才采信 X-Forwarded-For 的第一跳；否则一律使用 remote_addr，
    防止直连客户端伪造 X-Forwarded-For 头绕过限流/去重。

    X-Forwarded-For is only honoured when the direct peer (remote_addr) is a
    loopback/private address (i.e. the app actually sits behind a reverse
    proxy); otherwise remote_addr is used as-is so a direct client cannot
    forge its way past the rate limiter.
    """
    remote = request.remote_addr or "127.0.0.1"
    try:
        peer = ipaddress.ip_address(remote)
        trusted_proxy = peer.is_loopback or peer.is_private
    except ValueError:
        trusted_proxy = False
    if trusted_proxy:
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            first_hop = xff.split(",")[0].strip()
            if first_hop:
                return first_hop
    return remote


def _rate_check(ip: str, action: str, *, per_minute: int | None = None,
                per_day: int | None = None) -> tuple[bool, str]:
    """Return (allowed, reason). action like 'upload', 'like', 'report'."""
    now = time.time()
    with _RL_LOCK:
        # 全局键数上限：超过 _RL_MAX_IPS 时做一次全表过期清扫并丢弃空键，
        # 防止海量伪造 IP 把 _RL_LOG 的键数打爆（内存无界增长）。
        if len(_RL_LOG) >= _RL_MAX_IPS:
            for key in [k for k, v in _RL_LOG.items()
                        if not v or now - v[-1][1] >= 86400]:
                _RL_LOG.pop(key, None)
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


_COLOR_MODES = ("mono", "source", "source256")


def _rgb_or_none(value):
    """Accept [r,g,b] list/tuple (gallery params_json) or 'rgb(R,G,B)' string
    (query param); return a validated (R,G,B) tuple or None."""
    if isinstance(value, (list, tuple)):
        if len(value) != 3:
            return None
        try:
            r, g, b = int(value[0]), int(value[1]), int(value[2])
        except (TypeError, ValueError):
            return None
        if 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255:
            return (r, g, b)
        return None
    if not isinstance(value, str):
        return None
    return _parse_rgb(value)


def _parse_color_mode(value, default="mono"):
    """Validate a color-mode request field.

    Returns (mode, None) on success (absent value -> default), or
    (None, bilingual error message) on a bad value.
    """
    if value is None:
        return default, None
    v = str(value).strip().lower()
    if v in _COLOR_MODES:
        return v, None
    return None, ("color 仅支持 mono / source / source256 / "
                  "color must be one of mono, source, source256")


def _coerce_int_or_none(value):
    """Coerce a JSON-supplied numeric field to int, or None if not safely castable.

    接受 int 与可 ``int()`` 的整数字符串（如 "80" / " 24 "）；JSON 的
    None / bool / float / list / dict 及畸形字符串一律返回 None，由调用方
    回 400 —— 避免 ``int(None)`` 抛 TypeError 导致 500。
    """
    if isinstance(value, bool) or isinstance(value, (float, list, dict)):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None



def _tmp_out_path(filename: str, root: str | None = None) -> str:
    """Resolve a generated-artifact path under ``root`` and refuse traversal.

    Generated filenames combine task/gallery IDs with charset names that are
    whitelist-checked upstream; the checks below enforce that invariant at
    the file boundary instead of relying on it: no path separators, no
    ``..`` segments, and the resolved path must stay inside ``root``.

    返回绝对路径并在首次调用时确保 ``root`` 目录存在——所有产物写入与
    /api/download 读取都必须经由本函数取得同一路径基准（见 download）。
    ``root`` 缺省走 termify.paths.tmp_dir()（仓库根锚定，TERMIFY_BASE_DIR
    可覆盖），不再依赖调用时 CWD。
    """
    if ".." in filename or os.sep in filename or (os.altsep or "/") in filename:
        raise ValueError(f"generated filename contains path separators: {filename!r}")
    base = os.path.abspath(root if root is not None else paths.tmp_dir())
    resolved = os.path.abspath(os.path.join(base, filename))
    if os.path.dirname(resolved) != base or not resolved.startswith(base + os.sep):
        raise ValueError(f"generated filename escapes {base}: {filename!r}")
    os.makedirs(base, exist_ok=True)
    return resolved


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

    ip = _client_ip()
    allowed, _reason = _rate_check(ip, "image-upload", per_minute=10, per_day=100)
    if not allowed:
        return jsonify({"error": "上传太频繁，请稍后再试 (限 10 次/分钟) / "
                                 "Too many uploads, please try again later (10/min)"}), 429

    task_id = uuid.uuid4().hex[:12]
    # 服务端生成文件名（{task_id}{ext}，ext 已过 VALID_EXT 白名单）——
    # 用户可控的原始文件名绝不参与路径拼接，杜绝路径穿越。
    try:
        save_path = _safe_uploads_path(f"{task_id}{ext}")
    except ValueError:
        return jsonify({"error": "Unsupported format"}), 400
    file.save(save_path)

    try:
        from termify import convert

        seq = convert(save_path, "ascii", 80, 24)
    except Exception as exc:  # noqa: BLE001 — 不向客户端回显异常详情（含服务器路径）
        os.remove(save_path)
        app.logger.warning("upload conversion failed: %s", exc)
        return jsonify({"error": "转换失败，文件可能已损坏或格式不受支持 / "
                                 "Conversion failed: the file may be corrupted or unsupported"}), 400

    original_size = _original_size(save_path)
    target_size = {"width": seq.width, "height": seq.height}
    _task_put(
        task_id,
        filepath=save_path,
        original_size=original_size,
        target_size=target_size,
        frames_count=len(seq.lines_per_frame),
        interval=seq.interval,
    )
    # Seed per-worker cache so a same-worker preview hits.
    _cache_put(task_id, _cache_key(task_id, "ascii", 80, 24), seq)

    return jsonify({
        "task_id": task_id,
        "frames_count": len(seq.lines_per_frame),
        "original_size": original_size,
        "target_size": target_size,
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
        # 同 /api/upload：服务端生成文件名，ext 已过白名单，防路径穿越。
        try:
            save_path = _safe_uploads_path(f"{task_id}{ext}")
        except ValueError:
            errors.append({"filename": file.filename, "error": "Unsupported format"})
            continue
        file.save(save_path)

        try:
            seq = convert(save_path, "ascii", 80, 24)
        except Exception as exc:  # noqa: BLE001
            os.remove(save_path)
            errors.append({"filename": file.filename, "error": str(exc)})
            continue

        original_size = {"width": seq.width, "height": seq.height}
        target_size = {"width": seq.width, "height": seq.height}
        _task_put(
            task_id,
            filepath=save_path,
            original_size=original_size,
            target_size=target_size,
            frames_count=len(seq.lines_per_frame),
            interval=seq.interval,
        )
        _cache_put(task_id, _cache_key(task_id, "ascii", 80, 24), seq)

        results.append({
            "task_id": task_id,
            "filename": file.filename,
            "frames_count": len(seq.lines_per_frame),
            "original_size": original_size,
            "target_size": target_size,
        })

    return jsonify({"task_ids": results, "errors": errors})


@app.route("/api/upload-video", methods=["POST"])
def upload_video():
    """Upload a video (MP4/WEBM/MOV/AVI/MKV), extract frames via ffmpeg, convert.

    No duration cap — long videos are sampled at an adaptive fps (see
    termify.video.adaptive_fps). Guarded by per-IP rate limits plus a
    global conversion-slot semaphore (负载限速).
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    ip = _client_ip()
    allowed, _reason = _rate_check(ip, "video-upload", per_minute=4, per_day=40)
    if not allowed:
        return jsonify({"error": "视频上传太频繁，请稍后再试 (限 4 次/分钟)"}), 429

    from termify.video import (VALID_VIDEO_EXTS, validate_video,
                               convert_video_file, VideoError)

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in VALID_VIDEO_EXTS:
        return jsonify({"error": f"Unsupported video format: {ext}. 支持 MP4/WEBM/MOV/AVI/MKV"}), 400
    try:
        video_tmp = _video_tmp_path(ext)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    file.save(video_tmp)

    try:
        validate_video(video_tmp)
    except VideoError as exc:
        os.remove(video_tmp)
        return jsonify({"error": str(exc)}), 400

    # Grab the audio track before frame extraction deletes the source.
    task_id = uuid.uuid4().hex[:12]
    audio_file = None
    try:
        from termify.video import extract_audio
        audio_file = extract_audio(video_tmp, _safe_uploads_path(f"audio_{task_id}.m4a"))
    except ValueError:
        audio_file = None

    if not _VIDEO_IMPORT_SLOTS.acquire(blocking=False):
        if audio_file:
            os.remove(audio_file)
        os.remove(video_tmp)
        return jsonify({"error": "当前任务较多，请稍后再试"}), 429
    try:
        charset = "ascii"
        width, height = 80, 24
        # Persist frames under uploads/ so every charset/size can be
        # re-rendered on demand (fixes "Task not found" on style switch).
        _sweep_stale_frame_dirs()
        frames_dir = os.path.join(paths.uploads_dir(), f"frames_{task_id}")
        try:
            seq = convert_video_file(video_tmp, charset=charset, width=width,
                                     height=height, delete_source=True,
                                     frames_out_dir=frames_dir)
        except VideoError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("frame conversion failed: %s", exc)
            return jsonify({"error": "帧转换失败 / Frame conversion failed"}), 500
    finally:
        _VIDEO_IMPORT_SLOTS.release()

    target_size = {"width": width, "height": height}
    _task_put(
        task_id,
        filepath=frames_dir,  # persisted frames dir → charset/size switchable
        original_size=None,
        target_size=target_size,
        frames_count=len(seq.lines_per_frame),
        interval=seq.interval,
    )
    _cache_put(task_id, _cache_key(task_id, charset, width, height), seq)

    return jsonify({
        "task_id": task_id,
        "filename": file.filename,
        "frames_count": len(seq.lines_per_frame),
        "interval": seq.interval,
        "has_audio": bool(audio_file),
        "original_size": {"type": "video", "frame_count": len(seq.lines_per_frame)},
        "target_size": target_size,
    })


def _safe_uploads_path(name: str) -> str:
    """Resolve a server-generated file name inside uploads/, refusing traversal."""
    base = paths.uploads_dir()
    resolved = os.path.abspath(os.path.join(base, name or ""))
    if os.path.dirname(resolved) != base or not resolved.startswith(base + os.sep):
        raise ValueError("path escapes the uploads dir")
    if not name or ".." in name or "/" in name or os.sep in name:
        raise ValueError("unsafe uploads filename")
    return resolved


VALID_MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MAX_MUSIC_BYTES = 20 * 1024 * 1024

_MUSIC_MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
}


def _safe_remove_upload(path: str | None) -> None:
    """Delete a file only when it provably lives inside uploads/."""
    if not path:
        return
    uploads_base = paths.uploads_dir()
    resolved = os.path.abspath(path)
    if os.path.dirname(resolved) != uploads_base:
        return
    if not resolved.startswith(uploads_base + os.sep):
        return
    if ".." in resolved:
        return
    try:
        os.remove(resolved)
    except OSError:
        pass


def _find_uploaded_file(prefix: str, task_or_work: str) -> str | None:
    """Locate an existing uploads/<prefix>_<id>.<ext> artifact (any ext)."""
    base = paths.uploads_dir()
    if not os.path.isdir(base):
        return None
    marker = f"{prefix}_{task_or_work}."
    for name in os.listdir(base):
        if ".." in name or "/" in name or os.sep in name:
            continue
        if not name.startswith(marker):
            continue
        resolved = os.path.abspath(os.path.join(base, name))
        if os.path.dirname(resolved) != base:
            continue
        if not resolved.startswith(base + os.sep):
            continue
        if os.path.isfile(resolved):
            return resolved
    return None


def _valid_task_id(task_id: str) -> bool:
    """Task ids are uuid4().hex[:12] — enforce the strict charset."""
    return bool(re.fullmatch(r"[0-9a-f]{12}", task_id or ""))


def _task_audio_source(task_id: str) -> str | None:
    """Best audio for exports: user-uploaded music first, then video audio."""
    music = _find_uploaded_file("music", task_id)
    if music:
        return music
    return _find_uploaded_file("audio", task_id)


@app.route("/api/upload-music", methods=["POST"])
def upload_music():
    """Attach a background-music file to an existing task.

    multipart: task_id + file (mp3/wav/m4a/aac/ogg/flac, ≤20MB).
    Stored under uploads/ by the validated task id; takes priority over the
    video's own audio track in every export.
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400
    task_id = (request.form.get("task_id") or "").strip()
    if not _valid_task_id(task_id) or not get_store().exists(task_id):
        return jsonify({"error": "任务不存在或已过期，请重新上传 / Task not found or expired, please re-upload"}), 404

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in VALID_MUSIC_EXTS:
        return jsonify({"error": f"Unsupported audio format: {ext}. "
                                 f"支持 MP3/WAV/M4A/AAC/OGG/FLAC"}), 400

    ip = _client_ip()
    allowed, _reason = _rate_check(ip, "music-upload", per_minute=6, per_day=40)
    if not allowed:
        return jsonify({"error": "上传太频繁，请稍后再试"}), 429

    blob = file.read()
    if len(blob) > MAX_MUSIC_BYTES:
        return jsonify({"error": "音乐文件过大 (上限 20MB)"}), 413

    # One music file per task: drop previous uploads first.
    _safe_remove_upload(_find_uploaded_file("music", task_id))
    dest = _safe_uploads_path("music_" + task_id + ext)
    Path(dest).write_bytes(blob)

    return jsonify({
        "ok": True,
        "music": os.path.basename(dest),
        "size_kb": max(1, len(blob) // 1024),
    })


@app.route("/api/remove-music", methods=["POST"])
def remove_music():
    """Detach the uploaded background music from a task."""
    data = request.get_json(silent=True) or {}
    task_id = (data.get("task_id") or "").strip()
    if not _valid_task_id(task_id):
        return jsonify({"error": "No task_id"}), 400
    _safe_remove_upload(_find_uploaded_file("music", task_id))
    return jsonify({"ok": True})


@app.route("/api/audio-info/<task_id>", methods=["GET"])
def audio_info(task_id: str):
    """Which audio will be baked into exports for this task."""
    if not _valid_task_id(task_id) or not get_store().exists(task_id):
        return jsonify({"error": "任务不存在或已过期，请重新上传 / Task not found or expired, please re-upload"}), 404
    music = _find_uploaded_file("music", task_id)
    audio = _find_uploaded_file("audio", task_id)
    src = music or audio
    ext = os.path.splitext(src or "")[1].lower()
    return jsonify({
        "has_audio": bool(src),
        "kind": "music" if music else ("video" if audio else None),
        "mime": _MUSIC_MIME.get(ext) if src else None,
    })


@app.route("/api/fetch-video-url", methods=["POST"])
def fetch_video_url():
    """Resolve a Bilibili / Douyin / YouTube link into a conversion task.

    Hosts are allowlist-restricted (termify.videofetch); yt-dlp downloads a
    small MP4 server-side, then the same adaptive-fps extraction pipeline
    runs as for direct uploads.
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "No url provided"}), 400

    from termify.videofetch import VideoFetchError, download_video
    from termify.video import VideoError, convert_video_file

    ip = _client_ip()
    allowed, _reason = _rate_check(ip, "video-url", per_minute=2, per_day=12)
    if not allowed:
        return jsonify({"error": "链接解析太频繁，请稍后再试 (限 2 次/分钟)"}), 429

    try:
        downloaded_name = download_video(url, dest_dir=paths.uploads_dir())
    except VideoFetchError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        video_tmp = _safe_uploads_path(downloaded_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if not _VIDEO_IMPORT_SLOTS.acquire(blocking=False):
        return jsonify({"error": "当前任务较多，请稍后再试"}), 429
    try:
        task_id = uuid.uuid4().hex[:12]
        # Grab the audio track before frame extraction deletes the source.
        audio_file = None
        try:
            from termify.video import extract_audio
            audio_file = extract_audio(video_tmp, _safe_uploads_path(f"audio_{task_id}.m4a"))
        except ValueError:
            audio_file = None
        frames_dir = os.path.join(paths.uploads_dir(), f"frames_{task_id}")
        _sweep_stale_frame_dirs()
        try:
            seq = convert_video_file(video_tmp, charset="ascii", width=80,
                                     height=24, delete_source=True,
                                     frames_out_dir=frames_dir)
            frames_count = len(seq.lines_per_frame)
        except VideoError as exc:
            return jsonify({"error": str(exc)}), 422
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("frame conversion failed: %s", exc)
            return jsonify({"error": "帧转换失败 / Frame conversion failed"}), 500
    finally:
        _VIDEO_IMPORT_SLOTS.release()

    _task_put(
        task_id,
        filepath=frames_dir,  # persisted frames dir → charset/size switchable
        original_size=None,
        target_size={"width": seq.width, "height": seq.height},
        frames_count=frames_count,
        interval=seq.interval,
    )
    _cache_put(task_id, _cache_key(task_id, "ascii", seq.width, seq.height), seq)

    return jsonify({
        "task_id": task_id,
        "filename": "video-link",
        "frames_count": frames_count,
        "interval": seq.interval,
        "has_audio": bool(audio_file),
        "original_size": {"type": "video", "frame_count": frames_count},
        "target_size": {"width": seq.width, "height": seq.height},
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

    ip = _client_ip()
    allowed, _reason = _rate_check(ip, "fetch-url", per_minute=2, per_day=20)
    if not allowed:
        return jsonify({"error": "链接解析太频繁，请稍后再试 (限 2 次/分钟) / "
                                 "URL fetch too frequent, please try again later (2/min)"}), 429

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
        app.logger.warning("url-fetch conversion failed: %s", exc)
        return jsonify({"error": "转换失败，远端文件可能已损坏或格式不受支持 / "
                                 "Conversion failed: the remote file may be corrupted or unsupported"}), 400

    target_size = {"width": seq.width, "height": seq.height}
    _task_put(
        task_id,
        filepath=tmp_path,
        original_size=target_size,
        target_size=target_size,
        frames_count=len(seq.lines_per_frame),
        interval=seq.interval,
    )
    _cache_put(task_id, _cache_key(task_id, "ascii", 80, 24), seq)

    return jsonify({
        "task_id": task_id,
        "filename": os.path.basename(tmp_path),
        "frames_count": len(seq.lines_per_frame),
        "original_size": target_size,
        "target_size": target_size,
    })


def _get_sequence(task_id: str, charset: str, width: int, height: int, fg_color=None,
                  bg_color=None, charset_ramp=None, color_mode="mono"):
    """Return a converted FrameSequence, converting+caching on first miss.

    The metadata fetch is shared across workers (SQLite); the cache is
    per-worker — a cache miss is non-fatal: we re-convert from the
    persisted ``filepath`` and re-populate the cache.
    """
    task = _task_get(task_id)
    if task is None:
        return None
    filepath = task.get("filepath")
    key = _cache_key(task_id, charset, width, height, fg_color, bg_color,
                     charset_ramp=charset_ramp, color_mode=color_mode)

    if not filepath:
        # No backing file (e.g. video task whose temp frames were cleaned
        # up). Only serve from this worker's cache, if any.
        return _cache_get(task_id, key)

    seq = _cache_get(task_id, key)
    if seq is not None:
        return seq

    if os.path.isdir(filepath):
        # Video task: persisted per-task frame directory — re-render any
        # charset/size locally (no ffmpeg re-extraction).
        from termify.video import sequence_from_frames_dir

        seq = sequence_from_frames_dir(
            filepath, charset, width, height,
            interval=task.get("interval") or 0.1,
            charset_ramp=charset_ramp, color_mode=color_mode,
            fg_color=fg_color, bg_color=bg_color,
        )
        _cache_put(task_id, key, seq)
        return seq

    from termify import convert

    seq = convert(filepath, charset, width, height, fg_color=fg_color,
                  bg_color=bg_color, charset_ramp=charset_ramp,
                  color_mode=color_mode)
    _cache_put(task_id, key, seq)
    return seq


def _request_charset_ramp() -> str | None:
    """Extract + validate the ``chars`` param for the custom charset.

    Returns the raw ramp string (sanitised later in the renderer), or None
    when absent. Raises ValueError when unusable — callers turn that into a
    400 response.
    """
    from termify.charset import CUSTOM_RAMP_MAX_LEN

    chars = request.args.get("chars")
    if chars is None:
        return None
    chars = chars.strip()
    if not chars or len(chars) > CUSTOM_RAMP_MAX_LEN * 4:
        raise ValueError("custom charset 'chars' must be 1-64 usable characters")
    return chars


def _preview_payload_too_large(frame_count: int, width: int, height: int,
                               charset: str, color_mode: str = "mono") -> bool:
    """Guard against multi-hundred-MB preview JSONs on legacy clients.

    Measured per-character ANSI cost: ~21B for blocks (fg+bg SGR per cell),
    ~4B for ramp styles (color mostly per line), ~8B for source-color ramp
    styles (run-length merged per-cell SGR). Threshold 30MB — the
    client-side renderer is the intended path for heavy workloads.
    """
    if frame_count <= 0 or width <= 0 or height <= 0:
        return False
    rows = height * 2 if charset == "blocks" else height
    if charset == "blocks":
        per_char = 21
    elif color_mode != "mono":
        per_char = 8
    else:
        per_char = 4
    return frame_count * width * rows * per_char > 30 * 1024 * 1024


@app.route("/api/preview/<task_id>")
def preview(task_id):
    charset = request.args.get("charset", "ascii").lower().strip()
    from termify.charset import CHARSETS

    if charset not in CHARSETS:
        return jsonify({"error": f"Unknown charset: {charset}"}), 400

    charset_ramp = None
    if charset == "custom":
        try:
            charset_ramp = _request_charset_ramp()
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if not charset_ramp:
            return jsonify({"error": "custom charset requires a 'chars' ramp"}), 400

    try:
        width = int(request.args.get("width", 80))
        height = int(request.args.get("height", 24))
    except ValueError:
        return jsonify({"error": "width/height must be integers"}), 400
    # 钳制到 1-400（与 /api/gallery/preview 一致），防止超大尺寸 DoS。
    width = max(1, min(400, width))
    height = max(1, min(400, height))

    frame = request.args.get("frame")
    try:
        frame = int(frame) if frame is not None else None
    except ValueError:
        return jsonify({"error": "frame must be an integer"}), 400

    fg_color = _parse_rgb(request.args.get("fg"))
    bg_color = _parse_rgb(request.args.get("bg"))
    color_mode, err = _parse_color_mode(request.args.get("color"))
    if err:
        return jsonify({"error": err}), 400
    seq = _get_sequence(task_id, charset, width, height, fg_color=fg_color,
                        bg_color=bg_color, charset_ramp=charset_ramp,
                        color_mode=color_mode)
    if seq is None:
        return jsonify({"error": "任务不存在或已过期，请重新上传 / Task not found or expired, please re-upload"}), 404

    frame_count = len(seq.lines_per_frame)
    if frame is not None and not (0 <= frame < frame_count):
        return jsonify({"error": f"frame {frame} out of range (0-{frame_count-1})"}), 400

    # No `frame` requested -> return ALL frames so the player can loop them.
    if frame is None:
        if _preview_payload_too_large(frame_count, seq.width, seq.height,
                                      charset, color_mode):
            return jsonify({
                "error": "预览数据过大，请刷新页面使用新版播放器（本地渲染）",
                "too_large": True,
            }), 413
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


# --- T2.5 方案B 本地渲染：任务源帧直读 --------------------------------------

# 帧数 / payload 守卫：超出即拒绝（防一次性拉爆内存/带宽）。
TASK_FRAMES_MAX_COUNT = 600
TASK_FRAMES_MAX_PAYLOAD = 40 * 1024 * 1024
# 预览帧最大像素尺寸（与 termify.video 帧提取的 400x240 上限一致）。
PREVIEW_MAX_W, PREVIEW_MAX_H = 400, 240


def _fit_frame_to_preview(frame):
    """保持纵横比缩放到 ≤400×240（只缩小不放大，不加黑边）。

    前端负责 letterbox；这里只保证任何一维都不超过预览上限。
    """
    from PIL import Image

    fw, fh = frame.size
    if fw <= PREVIEW_MAX_W and fh <= PREVIEW_MAX_H:
        return frame
    scale = min(PREVIEW_MAX_W / fw, PREVIEW_MAX_H / fh)
    return frame.resize((max(1, int(fw * scale)), max(1, int(fh * scale))),
                        Image.LANCZOS)


@app.route("/api/task-frames/<task_id>", methods=["GET"])
def task_frames(task_id):
    """Serve a task's source frames as base64 JPEGs (方案B 本地渲染数据源).

    video 任务（filepath 是持久化帧目录）：直接读帧（ffmpeg 抽帧时已
    缩到 ≤400×240）。image 任务：PIL 从源文件抽帧（GIF 逐帧、静图 1 帧），
    每帧保持纵横比缩放到 ≤400×240（不加黑边，前端自行 letterbox）。
    每帧 RGB → JPEG quality=80 → base64。轻量直读，不进转换缓存。

    守卫：限 30 次/分钟/IP（前端每任务只拉一次，30 次余量覆盖多文件与
    手动重放）；帧数 > 600 或 payload 超 40MB → 413。
    """
    from PIL import Image
    import io as _io

    allowed, _reason = _rate_check(_client_ip(), "task-frames", per_minute=30)
    if not allowed:
        return jsonify({"error": "请求太频繁，请稍后再试 (限 30 次/分钟) / "
                                 "Too many requests, please try again later (30/min)"}), 429

    task = _task_get(task_id)
    if task is None:
        return jsonify({"error": "任务不存在 / Task not found"}), 404

    filepath = task.get("filepath")
    interval = task.get("interval") or 0.1

    def _payload_guard(total_b64: int):
        """Return a 413 response when the accumulated payload is too big."""
        if total_b64 > TASK_FRAMES_MAX_PAYLOAD:
            return jsonify({
                "too_large": True,
                "error": "预览帧数据过大 / Preview frame payload too large",
            }), 413
        return None

    frames: list[str] = []
    w = h = 0

    if filepath and os.path.isdir(filepath):
        # Video task: persisted frame dir (already ≤400×240 PNGs).
        from termify.video import frames_dir_to_images

        paths = frames_dir_to_images(filepath)
        if len(paths) > TASK_FRAMES_MAX_COUNT:
            return jsonify({
                "too_large": True,
                "error": "预览帧数据过大 / Preview frame payload too large",
            }), 413
        total = 0
        for p in paths:
            try:
                with Image.open(p) as im:
                    frame = _fit_frame_to_preview(im.convert("RGB"))
                    if w == 0:
                        w, h = frame.size
                    buf = _io.BytesIO()
                    frame.save(buf, format="JPEG", quality=80)
                    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception:  # noqa: BLE001 — 单帧损坏只跳过，不拖垮整体
                continue
            frames.append(b64)
            total += len(b64)
            guard = _payload_guard(total)
            if guard is not None:
                return guard
        return jsonify({
            "ok": True, "w": w, "h": h,
            "interval": interval, "count": len(frames), "frames": frames,
        })

    if not filepath or not os.path.isfile(filepath):
        # 背后文件已消失/不可读 → 任务实质不可用。
        return jsonify({"error": "任务不存在 / Task not found"}), 404

    # Image task: single file — GIF 逐帧，静图 1 帧。
    try:
        with Image.open(filepath) as im:
            n_frames = getattr(im, "n_frames", 1)
            if n_frames > TASK_FRAMES_MAX_COUNT:
                return jsonify({
                    "too_large": True,
                    "error": "预览帧数据过大 / Preview frame payload too large",
                }), 413
            total = 0
            for i in range(n_frames):
                im.seek(i)
                frame = _fit_frame_to_preview(im.convert("RGB"))
                if w == 0:
                    w, h = frame.size
                    # 粗估 payload：JPEG q80 ≈ 0.5B/px，base64 ×4/3。
                    est = n_frames * w * h * 0.5 * 4 / 3
                    if est > TASK_FRAMES_MAX_PAYLOAD:
                        return jsonify({
                            "too_large": True,
                            "error": "预览帧数据过大 / Preview frame payload too large",
                        }), 413
                buf = _io.BytesIO()
                frame.save(buf, format="JPEG", quality=80)
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                frames.append(b64)
                total += len(b64)
                guard = _payload_guard(total)
                if guard is not None:
                    return guard
    except Exception:  # noqa: BLE001 — 源文件无法解码 → 任务实质不可用
        return jsonify({"error": "任务不存在 / Task not found"}), 404

    return jsonify({
        "ok": True, "w": w, "h": h,
        "interval": interval, "count": len(frames), "frames": frames,
    })


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id")
    charset = (data.get("charset") or "ascii").lower().strip()
    fmt = data.get("format")

    ip = _client_ip()
    allowed, _reason = _rate_check(ip, "generate", per_minute=12, per_day=200)
    if not allowed:
        return jsonify({"error": "生成太频繁，请稍后再试 (限 12 次/分钟) / "
                                 "Too many generation requests, please try again later (12/min)"}), 429

    from termify.charset import CHARSETS

    if not task_id or not get_store().exists(task_id):
        return jsonify({"error": "任务不存在或已过期，请重新上传 / Task not found or expired, please re-upload"}), 404
    if charset not in CHARSETS:
        return jsonify({"error": f"Unknown charset: {charset}"}), 400
    if fmt not in VALID_FORMATS:
        return jsonify({"error": f"Unknown format: {fmt}"}), 400

    # JSON width/height 类型守卫：None/bool/float/list/dict/畸形串一律 400
    # （双语），防 int(None) 抛 TypeError → 500；缺失时才用默认值。
    width = _coerce_int_or_none(data.get("width", 80))
    height = _coerce_int_or_none(data.get("height", 24))
    if width is None or height is None:
        return jsonify({"error": "宽高必须是整数 / width and height must be integers"}), 400
    # 钳制到 1-400（与 /api/gallery/preview 一致），防止超大尺寸 DoS。
    width = max(1, min(400, width))
    height = max(1, min(400, height))

    fg_color = _parse_rgb(data.get("fg"))
    bg_color = _parse_rgb(data.get("bg"))
    color_mode, err = _parse_color_mode(data.get("color"))
    if err:
        return jsonify({"error": err}), 400
    charset_ramp = None
    if charset == "custom":
        raw_ramp = data.get("chars")
        if not isinstance(raw_ramp, str) or not raw_ramp.strip():
            return jsonify({"error": "custom charset requires a 'chars' ramp"}), 400
        charset_ramp = raw_ramp

    if fmt == "mp4":
        return _generate_video(task_id, charset, width, height,
                               fg_color, bg_color, charset_ramp, color_mode)

    seq = _get_sequence(task_id, charset, width, height, fg_color=fg_color,
                        bg_color=bg_color, charset_ramp=charset_ramp,
                        color_mode=color_mode)

    audio_b64 = audio_mime = None
    if fmt == "html":
        # 自包含 HTML 播放器：把音轨以 data-URI 内嵌（过大则放弃内嵌）。
        src = _task_audio_source(task_id)
        if src and os.path.getsize(src) <= 15 * 1024 * 1024:
            audio_b64 = base64.b64encode(Path(src).read_bytes()).decode("ascii")
            audio_mime = _MUSIC_MIME.get(os.path.splitext(src)[1].lower(),
                                         "audio/mpeg")

    from termify.output import render

    content = render(seq, fmt, audio_b64=audio_b64, audio_mime=audio_mime)

    # 产物名带配色模式段：不同 color_mode 的产物不互相覆盖（mono 缺省省略）
    color_tag = ""
    if color_mode == "source":
        color_tag = "_src"
    elif color_mode == "source256":
        color_tag = "_src256"
    ext = "py" if fmt == "python" else "html"
    filename = f"{task_id}_{charset}{color_tag}.{ext}"
    if charset == "custom":
        # Different ramps must not overwrite each other's artifacts.
        digest = hashlib.sha256((charset_ramp or "").encode("utf-8")).hexdigest()[:8]
        filename = f"{task_id}_custom_{digest}{color_tag}.{ext}"
    out_path = _tmp_out_path(filename)
    Path(out_path).write_text(content, encoding="utf-8")

    size_bytes = len(content.encode("utf-8"))
    if size_bytes >= 1024:
        file_size = f"{size_bytes // 1024}KB"
    else:
        file_size = f"{size_bytes}B"

    return jsonify({
        "download_url": f"/api/download/{filename}",
        "file_size": file_size,
    })


def _generate_video(task_id, charset, width, height, fg_color, bg_color,
                    charset_ramp, color_mode="mono"):
    """Sync MP4 export: rasterize the FrameSequence and encode with ffmpeg."""
    from termify.output import video as video_mod

    if not video_mod.ffmpeg_available():
        return jsonify({"error": "视频导出暂不可用，请稍后再试"}), 503

    ip = _client_ip()
    if not _rate_check(ip, "video-export", per_minute=6):
        return jsonify({"error": "视频导出太频繁，请稍后再试 (限 6 次/分钟)"}), 429

    seq = _get_sequence(task_id, charset, width, height, fg_color=fg_color,
                        bg_color=bg_color, charset_ramp=charset_ramp,
                        color_mode=color_mode)
    if seq is None:
        return jsonify({"error": "任务不存在或已过期，请重新上传 / Task not found or expired, please re-upload"}), 404
    if len(seq.lines_per_frame) > video_mod.MAX_VIDEO_FRAMES:
        return jsonify({"error": f"帧数过多 ({len(seq.lines_per_frame)})，"
                                 f"视频导出上限 {video_mod.MAX_VIDEO_FRAMES} 帧"}), 400

    color_tag = ""
    if color_mode == "source":
        color_tag = "_src"
    elif color_mode == "source256":
        color_tag = "_src256"
    ext = "mp4"
    filename = f"{task_id}_{charset}{color_tag}.{ext}"
    if charset == "custom":
        digest = hashlib.sha256((charset_ramp or "").encode("utf-8")).hexdigest()[:8]
        filename = f"{task_id}_custom_{digest}{color_tag}.{ext}"
    out_path = _tmp_out_path(filename)

    if not _VIDEO_PROC_SLOTS.acquire(blocking=False):
        return jsonify({"error": "当前任务较多，请稍后再试"}), 429
    try:
        video_mod.encode_mp4(seq, out_path,
                             audio_path=_task_audio_source(task_id))
    except video_mod.VideoEncodeError as e:
        return jsonify({"error": f"视频编码失败: {e}"}), 500
    finally:
        _VIDEO_PROC_SLOTS.release()

    size_bytes = os.path.getsize(out_path)
    if size_bytes >= 1024 * 1024:
        file_size = f"{size_bytes // (1024 * 1024)}MB"
    elif size_bytes >= 1024:
        file_size = f"{size_bytes // 1024}KB"
    else:
        file_size = f"{size_bytes}B"

    return jsonify({
        "download_url": f"/api/download/{filename}",
        "file_size": file_size,
        "format": "mp4",
    })


@app.route("/api/download/<path:filename>")
def download(filename):
    if ".." in filename or os.sep in filename or "/" in filename:
        return jsonify({"error": "Invalid filename"}), 400

    # 与写入侧（generate/_generate_video → _tmp_out_path）共用同一基准：
    # 取 CWD 内 tmp/ 的绝对路径。此前这里把相对路径 "tmp/<file>" 交给
    # send_file，Flask 会按 app.root_path 解析——CWD ≠ 仓库根时（systemd
    # WorkingDirectory 漂移、PyInstaller 启动器、隔离部署）与写入侧基准
    # 不一致 → FileNotFoundError 500。传绝对路径即绕过 root_path 解析，
    # 写入/读取恒定对齐。
    try:
        path = _tmp_out_path(filename)
    except ValueError:
        return jsonify({"error": "非法文件名 / Invalid filename"}), 400
    if not os.path.isfile(path):
        return jsonify({"error": "文件不存在或已过期 / File not found or expired"}), 404

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


def _gallery_remove_file(base: str, name: str) -> None:
    """Remove a file inside the gallery dir, containment-checked."""
    if not name or ".." in name or "/" in name or os.sep in name:
        return
    target = os.path.abspath(os.path.join(base, name))
    if os.path.dirname(target) != os.path.abspath(base):
        return
    try:
        if os.path.isfile(target):
            os.remove(target)
    except OSError:
        pass


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
    is_video = ext in VALID_VIDEO_EXTS
    if ext not in GALLERY_EXT and not is_video:
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
    if params.get("charset") not in CHARSETS or params.get("charset") == "custom":
        # custom needs its per-request ramp which the gallery does not store.
        params["charset"] = _gallery_mod.DEFAULT_CHARSET
    try:
        params["width"] = max(1, min(400, int(params["width"])))
        params["height"] = max(1, min(400, int(params["height"])))
    except (TypeError, ValueError):
        params["width"] = _gallery_mod.DEFAULT_WIDTH
        params["height"] = _gallery_mod.DEFAULT_HEIGHT
    if params.get("color") not in _COLOR_MODES:
        params.pop("color", None)

    # Persist file
    work_id = _make_unique_id()
    base = _gallery_mod.gallery_base(GALLERY_DATA_DIR)
    source_path = os.path.join(base, f"{work_id}{ext}")
    source_file.save(source_path)

    frames_dir = None
    if is_video:
        # Video works: extract frames once server-side and keep the frames
        # dir (the 200MB source video itself is NOT stored in the gallery).
        from termify.video import validate_video, convert_video_file, VideoError
        try:
            validate_video(source_path)
        except VideoError as exc:
            os.remove(source_path)
            return jsonify({"error": str(exc)}), 400
        if not _VIDEO_IMPORT_SLOTS.acquire(blocking=False):
            os.remove(source_path)
            return jsonify({"error": "当前任务较多，请稍后再试"}), 429
        audio_file = None
        try:
            # Grab the audio track before frame extraction deletes the source.
            try:
                from termify.video import extract_audio
                audio_file = extract_audio(
                    source_path, os.path.join(base, f"{work_id}_audio.m4a"))
            except OSError:
                audio_file = None
            frames_dir = os.path.join(base, f"{work_id}_frames")
            try:
                seq0 = convert_video_file(source_path, charset="ascii", width=80,
                                          height=24, delete_source=True,
                                          frames_out_dir=frames_dir)
            except VideoError as exc:
                return jsonify({"error": str(exc)}), 422
            except Exception as exc:  # noqa: BLE001
                app.logger.warning("frame conversion failed: %s", exc)
            return jsonify({"error": "帧转换失败 / Frame conversion failed"}), 500
        finally:
            _VIDEO_IMPORT_SLOTS.release()
        from termify.video import frames_dir_to_images
        first_frame = frames_dir_to_images(frames_dir)[0]
        # source_path stays NOT NULL; it now points at the first frame so
        # /gallery/file/<id>/source still serves something viewable.
        source_path = first_frame
    else:
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
            app.logger.warning("gallery invalid image: %s", exc)
            return jsonify({"error": "图片无效或已损坏 / Invalid or corrupted image"}), 400

    # Generate thumbnails + OG (video works: from the first frame)
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

    if is_video and frames_dir:
        params["kind"] = "video"
        params["frames_dir"] = frames_dir
        params["interval"] = seq0.interval
        if audio_file and os.path.isfile(audio_file):
            params["audio_file"] = os.path.basename(audio_file)

    # Optional user-uploaded music (multipart "music"): overrides video audio
    music_file = request.files.get("music")
    if is_video and music_file and music_file.filename:
        mext = os.path.splitext(music_file.filename)[1].lower()
        if mext in VALID_MUSIC_EXTS:
            mblob = music_file.read()
            if len(mblob) <= MAX_MUSIC_BYTES:
                music_path = os.path.join(base, f"{work_id}_audio{mext}")
                Path(music_path).write_bytes(mblob)
                # 视频原声轨作废，避免双份音频文件堆积
                if params.get("audio_file"):
                    _gallery_remove_file(base, params["audio_file"])
                params["audio_file"] = os.path.basename(music_path)

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
    out["is_authorized"] = (
        _secret_equal(request.cookies.get(f"termify_admin_{work_id}", ""),
                      work["admin_token"])
        or _secret_equal(request.headers.get("X-Termify-Admin-Pwd", ""), _admin_pwd())
    )
    # Has the current visitor (IP + cookie) liked this work?
    like_cookie = request.cookies.get(f"termify_like_{work_id}", "")
    if like_cookie:
        out["is_liked"] = GALLERY_DB.has_liked(work_id, _client_ip(), like_cookie)
    else:
        out["is_liked"] = False
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
    # request.json 可能是非 dict（如 JSON 数组/字符串），isinstance 守卫防 500。
    body = request.get_json(silent=True) if request.is_json else None
    json_cookie = body.get("cookie", "") if isinstance(body, dict) else ""
    cookie_val = existing_cookie or json_cookie
    # cookie 绑定进 sqlite 参数，必须是短字符串：非字符串（JSON 数组/嵌套
    # dict 等类型混淆）或超长值一律 400，防止 sqlite3.ProgrammingError 500。
    if cookie_val and (not isinstance(cookie_val, str) or len(cookie_val) > 200):
        return jsonify({"error": "点赞标识无效（须为不超过 200 字符的字符串） / "
                                 "Invalid like cookie (must be a string of at most 200 characters)"}), 400
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
    """Delete a work. Requires valid admin token (cookie or header) or global admin pwd via Header."""
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    token = request.cookies.get(f"termify_admin_{work_id}", "")
    hdr_token = request.headers.get("X-Termify-Admin", "")
    input_token = hdr_token or token
    is_admin = _secret_equal(request.headers.get("X-Termify-Admin-Pwd", ""), _admin_pwd())
    authorized = is_admin or _secret_equal(input_token, work["admin_token"])
    if not authorized:
        return jsonify({"error": "Unauthorized"}), 403
    deleted = GALLERY_DB.delete_work(work_id)
    if deleted:
        for key in ("source_path", "thumbnail_path", "og_path"):
            p = deleted.get(key)
            if p and os.path.isfile(p):
                os.remove(p)
        try:
            params = json.loads(deleted.get("params_json") or "{}")
            fd = params.get("frames_dir")
            if fd and os.path.isdir(fd):
                import shutil
                shutil.rmtree(fd, ignore_errors=True)
        except (ValueError, TypeError):
            pass
    return jsonify({"ok": True})


@app.route("/api/gallery/admin", methods=["GET"])
def gallery_admin_list():
    """Admin dashboard: list works + pending reports.

    Requires X-Termify-Admin-Pwd header.
    """
    hdr_pwd = request.headers.get("X-Termify-Admin-Pwd", "")
    if not _secret_equal(hdr_pwd, _admin_pwd()):
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
    hdr_pwd = request.headers.get("X-Termify-Admin-Pwd", "")
    if not _secret_equal(hdr_pwd, _admin_pwd()):
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
        try:
            params = json.loads(deleted.get("params_json") or "{}")
            fd = params.get("frames_dir")
            if fd and os.path.isdir(fd):
                import shutil
                shutil.rmtree(fd, ignore_errors=True)
        except (ValueError, TypeError):
            pass
    return jsonify({"ok": True})


@app.route("/api/gallery/admin/report/<int:report_id>", methods=["POST"])
def gallery_admin_resolve_report(report_id):
    """Mark a report resolved/dismissed."""
    hdr_pwd = request.headers.get("X-Termify-Admin-Pwd", "")
    if not _secret_equal(hdr_pwd, _admin_pwd()):
        return jsonify({"error": "Unauthorized"}), 403
    data = request.get_json(silent=True) or {}
    status = data.get("status", "resolved")
    if status not in ("resolved", "dismissed"):
        return jsonify({"error": "Invalid status"}), 400
    GALLERY_DB.admin_update_report(report_id, status)
    return jsonify({"ok": True})


# --- gallery preview + download (derived from stored source) ---

@app.route("/api/gallery/source-frames/<work_id>", methods=["GET"])
def gallery_source_frames(work_id):
    """Serve stored source frames of a VIDEO gallery work as base64 JPEGs.

    Lets the viewer page render every charset/size client-side (方案B),
    so switching is instant instead of waiting for a server re-render.
    Shape: {ok, w, h, interval, count, frames: ["<b64>", ...]}
    """
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    original = json.loads(work["params_json"]) if work["params_json"] else {}
    if original.get("kind") != "video":
        return jsonify({"error": "Not a video work"}), 400
    fd = original.get("frames_dir") or ""
    if not fd or not os.path.isdir(fd):
        return jsonify({"error": "Video frames missing"}), 410

    from termify.video import frames_dir_to_images
    from PIL import Image
    import base64
    import io as _io

    paths = frames_dir_to_images(fd)
    frames = []
    w = h = 0
    for p in paths:
        with Image.open(p) as im:
            im = im.convert("RGB")
            if w == 0:
                w, h = im.size
            buf = _io.BytesIO()
            im.save(buf, format="JPEG", quality=80)
            frames.append(base64.b64encode(buf.getvalue()).decode("ascii"))
    return jsonify({
        "ok": True,
        "w": w,
        "h": h,
        "interval": original.get("interval") or 0.1,
        "count": len(frames),
        "frames": frames,
    })


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
    if charset not in CHARSETS or charset == "custom":
        # custom is per-request (needs its ramp) and is never stored on works.
        return jsonify({"error": f"Invalid charset: {charset}"}), 400
    try:
        width = int(request.args.get("width", original.get("width", 80)))
        height = int(request.args.get("height", original.get("height", 24)))
    except (TypeError, ValueError):
        return jsonify({"error": "width/height must be integers"}), 400
    width = max(1, min(400, width))
    height = max(1, min(400, height))

    color_mode, err = _parse_color_mode(
        request.args.get("color", original.get("color")))
    if err:
        return jsonify({"error": err}), 400
    fg_color = _rgb_or_none(request.args.get("fg", original.get("fg")))
    bg_color = _rgb_or_none(request.args.get("bg", original.get("bg")))

    from termify import convert
    if original.get("kind") == "video":
        from termify.video import sequence_from_frames_dir
        fd = original.get("frames_dir") or ""
        if not fd or not os.path.isdir(fd):
            return jsonify({"error": "Video frames missing"}), 410
        try:
            seq = sequence_from_frames_dir(fd, charset, width, height,
                                           interval=original.get("interval") or 0.1,
                                           color_mode=color_mode,
                                           fg_color=fg_color, bg_color=bg_color)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("gallery conversion failed: %s", exc)
            return jsonify({"error": "转换失败 / Conversion failed"}), 400
    else:
        try:
            seq = convert(work["source_path"], charset, width, height,
                          fg_color=fg_color, bg_color=bg_color,
                          color_mode=color_mode)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("gallery conversion failed: %s", exc)
            return jsonify({"error": "转换失败 / Conversion failed"}), 400

    if _preview_payload_too_large(len(seq.lines_per_frame), seq.width,
                                  seq.height, charset, color_mode):
        return jsonify({
            "error": "预览数据过大，请刷新页面使用新版播放器（本地渲染）",
            "too_large": True,
        }), 413

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
    """Generate + serve a .py, .html or .mp4 download for a gallery work.

    Query params:
      charset = ascii|blocks|braille|geometric|binary|shades (default: work's original)
      width, height = int (default: work's original)
      format  = python|html|mp4 (required)
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
    if charset not in CHARSETS or charset == "custom":
        # custom is per-request (needs its ramp) and is never stored on works.
        return jsonify({"error": f"Invalid charset: {charset}"}), 400
    try:
        width = int(request.args.get("width", original.get("width", 80)))
        height = int(request.args.get("height", original.get("height", 24)))
    except (TypeError, ValueError):
        return jsonify({"error": "width/height must be integers"}), 400
    width = max(1, min(400, width))
    height = max(1, min(400, height))

    color_mode, err = _parse_color_mode(
        request.args.get("color", original.get("color")))
    if err:
        return jsonify({"error": err}), 400
    fg_color = _rgb_or_none(request.args.get("fg", original.get("fg")))
    bg_color = _rgb_or_none(request.args.get("bg", original.get("bg")))
    # 与 /api/generate 一致：产物名带配色段，防并发互覆（mono 缺省省略）
    color_tag = ""
    if color_mode == "source":
        color_tag = "_src"
    elif color_mode == "source256":
        color_tag = "_src256"

    from termify import convert
    from termify.output import render
    if original.get("kind") == "video":
        from termify.video import sequence_from_frames_dir
        fd = original.get("frames_dir") or ""
        if not fd or not os.path.isdir(fd):
            return jsonify({"error": "Video frames missing"}), 410
        seq = sequence_from_frames_dir(fd, charset, width, height,
                                       interval=original.get("interval") or 0.1,
                                       color_mode=color_mode,
                                       fg_color=fg_color, bg_color=bg_color)
    else:
        seq = convert(work["source_path"], charset, width, height,
                      fg_color=fg_color, bg_color=bg_color,
                      color_mode=color_mode)
    tmp_dir = paths.tmp_dir()
    os.makedirs(tmp_dir, exist_ok=True)

    if fmt == "mp4":
        # 视频版下载：同步编码，走并发槽位 + 限频
        from termify.output import video as video_mod

        if not video_mod.ffmpeg_available():
            return jsonify({"error": "视频导出暂不可用，请稍后再试"}), 503
        ip = _client_ip()
        allowed, _reason = _rate_check(ip, "gallery-mp4", per_minute=6)
        if not allowed:
            return jsonify({"error": "操作太频繁，请稍后再试"}), 429
        if len(seq.lines_per_frame) > video_mod.MAX_VIDEO_FRAMES:
            return jsonify({"error": f"帧数过多 ({len(seq.lines_per_frame)})，"
                                     f"视频导出上限 {video_mod.MAX_VIDEO_FRAMES} 帧"}), 400
        filename = f"gallery_{work_id}_{charset}{color_tag}.mp4"
        out_path = _tmp_out_path(filename, root=tmp_dir)
        work_audio = None
        if original.get("audio_file"):
            cand = os.path.join(_gallery_mod.gallery_base(GALLERY_DATA_DIR),
                                original["audio_file"])
            if os.path.isfile(cand):
                work_audio = cand
        if not _VIDEO_PROC_SLOTS.acquire(blocking=False):
            return jsonify({"error": "当前任务较多，请稍后再试"}), 429
        try:
            video_mod.encode_mp4(seq, out_path, audio_path=work_audio)
        except video_mod.VideoEncodeError as exc:
            return jsonify({"error": f"视频编码失败: {exc}"}), 500
        finally:
            _VIDEO_PROC_SLOTS.release()
        _record_download(work_id, _client_ip())
        return send_file(out_path, as_attachment=True, download_name=filename)

    content = None
    if fmt == "html" and original.get("audio_file"):
        audio_path = os.path.join(_gallery_mod.gallery_base(GALLERY_DATA_DIR),
                                  original["audio_file"])
        if os.path.isfile(audio_path) \
                and os.path.getsize(audio_path) <= 15 * 1024 * 1024:
            audio_b64 = base64.b64encode(Path(audio_path).read_bytes()).decode("ascii")
            mime = _MUSIC_MIME.get(os.path.splitext(audio_path)[1].lower(),
                                   "audio/mpeg")
            content = render(seq, fmt, audio_b64=audio_b64, audio_mime=mime)
    if content is None:
        content = render(seq, fmt)

    ext = "py" if fmt == "python" else "html"
    filename = f"gallery_{work_id}_{charset}{color_tag}.{ext}"
    out_path = _tmp_out_path(filename, root=tmp_dir)
    Path(out_path).write_text(content, encoding="utf-8")

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


@app.route("/gallery/file/<work_id>/audio")
def gallery_audio(work_id):
    """Stream a video work's extracted audio track (view-page playback)."""
    work = GALLERY_DB.get_work(work_id)
    if not work:
        abort(404)
    params = json.loads(work["params_json"]) if work["params_json"] else {}
    name = params.get("audio_file") or ""
    if not name or ".." in name or "/" in name or os.sep in name:
        abort(404)
    audio_path = os.path.join(_gallery_mod.gallery_base(GALLERY_DATA_DIR), name)
    if not os.path.isfile(audio_path):
        abort(404)
    mime = _MUSIC_MIME.get(os.path.splitext(audio_path)[1].lower(), "audio/mp4")
    return send_file(audio_path, mimetype=mime)


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


# --- T1.9 Task store bootstrap ------------------------------------------------
# Initialise the SQLite-backed task store once at import time. This creates
# the ``tasks`` table, sweeps any rows that expired while we were down, and
# starts the background TTL cleanup thread. The same code runs under both
# ``python app.py`` (single process) and ``gunicorn --workers 4`` (4 procs).
# 把 _sweep_stale_frame_dirs 挂进后台 sweep 循环：uploads/ 帧目录与
# tmp/gallery_* 下载产物共用同一套 24h TTL 生命周期管理。
get_store().set_sweep_hook(_sweep_stale_frame_dirs)

if __name__ == "__main__":
    os.makedirs(paths.uploads_dir(), exist_ok=True)
    os.makedirs(paths.tmp_dir(), exist_ok=True)
    os.makedirs(GALLERY_DATA_DIR, exist_ok=True)
    os.makedirs("data", exist_ok=True)
    # ponytail: reloader off — the task cache is process-local, so a watchdog
    # restart (e.g. on each /api/generate writing tmp/*.py) would just trigger
    # a cache miss. The metadata is durable in SQLite.
    app.run(debug=False, use_reloader=False, port=5000)
