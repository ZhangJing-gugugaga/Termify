"""Flask entry point — wires the Phase 1 termify engine to the Phase 2 frontend.

Implements PRD §6's three endpoints plus a download route and the page route.
All heavy lifting (frame extract / charset map / bundling) is delegated to the
Phase 1 termify APIs; this file is just HTTP glue.

T1.6 Online Gallery adds /api/gallery/* + /gallery /v/<id> /admin routes.
"""

from __future__ import annotations

import base64
import collections
import hashlib
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape as _xml_escape

from termify.video import VALID_VIDEO_EXTS
from termify import paths

from flask import (Flask, Response, abort, jsonify, make_response, redirect,
                   render_template, request, send_file, url_for)
from flask_compress import Compress
from werkzeug.exceptions import BadRequest, HTTPException, InternalServerError

app = Flask(__name__)

# 字体 MIME：Windows 注册表没有 .woff2/.ttf 的映射，werkzeug 会退成
# application/octet-stream；站点又发了 X-Content-Type-Options: nosniff，
# 严格的浏览器（Safari）会因此拒绝加载 @font-face，字体静默回退系统等宽字体。
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/ttf", ".ttf")

# 显式日志 handler：非 debug 模式下 app.logger.warning/error 若无 handler，
# 错误详情（如视频导入失败原因）会静默丢失，安全事件无从排查。
if not app.logger.handlers:
    _log_handler = logging.StreamHandler()
    _log_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    app.logger.addHandler(_log_handler)
    app.logger.setLevel(logging.INFO)
# 从环境变量加载密钥，未设置时自动生成（每次重启会变，仅轻度会话场景安全）
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex())
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # PRD §7.1

# PF-3 全站零压缩：此前 CSS/JS/HTML/JSON 全部裸传（实测 css 103929B + js 87979B）。
# 复用 flask-compress（MIT）在应用内做 gzip/br —— 生产走 Caddy 反代，仓库无 Caddyfile，
# 应用内自足实现才不依赖代理配置。flask-compress 会跳过已带 Content-Encoding 的响应
# （代理已压过则不会二次压），并自动补 Vary: Accept-Encoding。
app.config["COMPRESS_MIMETYPES"] = [
    # 只压文本类：png/gif/jpeg/webp/mp4/woff2 等已压缩格式不在白名单内，一律不压。
    "text/html", "text/css", "text/plain", "text/xml", "text/javascript",
    "application/javascript", "application/json", "application/xml",
    "application/xhtml+xml", "application/manifest+json", "image/svg+xml",
]
app.config["COMPRESS_MIN_SIZE"] = 500  # <500B 的小响应压缩不划算，跳过
app.config["COMPRESS_ALGORITHM"] = ["br", "gzip", "deflate"]
# br 默认 q4 反而比 gzip q6 更大（实测 app.css: gzip 16788B vs br-q4 17695B）；
# q9 是拐点（14123B / 23ms），q11 要 286ms 不值，故取 9。
app.config["COMPRESS_BR_LEVEL"] = 9
# 静态文件走 send_file 的流式响应：默认流式算法不含 gzip，
# 只发 Accept-Encoding: gzip 的客户端就拿不到压缩，故显式补上。
app.config["COMPRESS_ALGORITHM_STREAMING"] = ["br", "gzip", "deflate"]
Compress(app)


@app.after_request
def _skip_compress_partial(resp):
    """206 分段响应不压缩：Content-Range 描述的是未压缩表示的字节区间，
    压缩后区间就对不上了（RFC 9110 §8.4.1）。flask-compress 见到响应已带
    Content-Encoding 会跳过，故标记 identity（有效的 content-coding 记号）。
    本 hook 注册在 Compress 之后，Flask 按逆序执行，因此先于压缩逻辑运行。
    """
    if resp.status_code == 206 and resp.mimetype in app.config["COMPRESS_MIMETYPES"]:
        resp.headers.setdefault("Content-Encoding", "identity")
    return resp


# PF-3b 大响应体积闸门：flask-compress 对非流式响应是"整块压完再发"——先
# response.get_data() 拿全量字节，压完再 set_data() 并把 Content-Length 改成
# 压缩后长度，首字节要等全量编码结束。12.1MB 的 source-frames JSON 在 br q9
# 下要压 ~24s（实测 TTFB identity 3.53s / gzip 5.46s / br 27.65s），而真实
# Chrome 默认发 Accept-Encoding: br，即 100% 的用户命中这条最慢路径。
# 闸门：会走压缩（2xx + mimetype 在白名单 + 尚无 Content-Encoding）且未压缩
# 体积超阈值时预置 Content-Encoding: identity —— flask-compress 见到响应已带
# Content-Encoding 就直接 return（与 _skip_compress_partial 同一机制），于是
# 原始字节直发。小于阈值的响应行为完全不变，仍 br 优先。
# 阈值 1 MiB：br q9 编码 JSON 约 0.5 MB/s，1MiB 大致就是"压完要 1-2s"的分界；
# 再往下压不动这个瓶颈，再往上压缩收益已经抵不过首字节延迟。命中它的真实端点：
# source-frames 12.13MB、embed-frames 6.23MB、preview 6.26MB（三者都是整块
# jsonify，无分块 flush 的可能）。
COMPRESS_MAX_SIZE = 1024 * 1024


@app.after_request
def _skip_compress_large(resp):
    """超阈值响应不压缩（identity 直发）。

    体积按未压缩字节判定：jsonify 走 Response.set_data()、send_file 走文件
    大小，两者都会写 Content-Length，即 resp.content_length。取不到时退回
    calculate_content_length()——它只把已拼好的 body 各段 len() 相加，不会
    触发序列化或压缩。

    流式 / direct_passthrough 响应不设闸门：flask-compress 对它们走
    COMPRESS_ALGORITHM_STREAMING 的分块压缩（逐块 flush 并摘掉 Content-Length），
    首字节不等全量压完，本就没有这个延迟问题；在本 hook 里给流式响应预置
    identity 反而会把本可压缩的 CSS/JS 静态文件打回裸传。
    """
    if "Content-Encoding" in resp.headers:
        return resp
    if resp.mimetype not in app.config["COMPRESS_MIMETYPES"]:
        return resp
    if not 200 <= resp.status_code < 300:
        return resp
    if resp.is_streamed or resp.direct_passthrough:
        return resp
    size = resp.content_length
    if size is None:
        size = resp.calculate_content_length()
    if size is not None and size > COMPRESS_MAX_SIZE:
        resp.headers["Content-Encoding"] = "identity"
    return resp


# 资源直链（source/thumb/og/audio）404 也回 JSON：这些 URL 是 <img src> 与
# 播放器直接消费的，拿到的整页 404.html（约 2.3KB HTML）既解析不出错误，
# 又让"资源不存在"和"页面不存在"在前端长得一模一样。
_GALLERY_FILE_PREFIX = "/gallery/file/"


@app.errorhandler(404)
def _not_found(_e):
    """404 落到站点风格页面（此前是 Flask 默认英文页：无导航、无中文、无返回入口）。

    API 路径与画廊资源直链保持 JSON —— 前端拿 HTML 会解析失败，比报错信息更糟。
    """
    if request.path == "/api" or request.path.startswith("/api/"):
        return jsonify({"error": "接口不存在 / Not found"}), 404
    if request.path.startswith(_GALLERY_FILE_PREFIX):
        resp = jsonify({
            "error": "资源不存在 / Not found",
            "status": 404,
        })
        # 这些 URL 是 <img src>/播放器直链：补 no-store，免得"当年 404"被浏览器
        # 缓存住，作品补生成成功后图片位置还是破的。
        resp.headers.setdefault("Cache-Control", "no-store")
        return resp, 404
    return render_template("404.html"), 404


@app.errorhandler(405)
def _method_not_allowed(e):
    """405 落到站点风格页面；API 路径返回 JSON。

    此前没有 405 handler，方法不允许时漏到 Flask 默认英文页（无导航、
    无中文、无返回入口），/api/ 下还会把 HTML 塞给拿 JSON 的调用方。
    allowed 取自 Werkzeug 的 routing 判定，同时回填 Allow 头
    （RFC 9110 §15.5.6 要求 405 必须带 Allow）。
    """
    allowed = sorted(getattr(e, "valid_methods", None) or [])
    if request.path == "/api" or request.path.startswith("/api/"):
        resp = make_response(jsonify({
            "error": "方法不允许 / Method not allowed",
            "method": request.method,
            "allowed": allowed,
        }), 405)
    else:
        resp = make_response(render_template("405.html", allowed=allowed), 405)
    if allowed:
        resp.headers["Allow"] = ", ".join(allowed)
    return resp


@app.errorhandler(413)
def _request_entity_too_large(_e):
    """请求体超过 MAX_CONTENT_LENGTH 时返回 JSON（而非 Flask 默认 HTML 页）。"""
    return jsonify({"error": "文件过大（上限 20MB） / File too large (max 20MB)"}), 413


@app.errorhandler(500)
def _internal_server_error(_e):
    """未捕获异常统一收口：/api/ 下回 JSON，其余路径维持默认 HTML 页。

    此前没有 500 handler，/api/* 的 5xx 会回 Flask 默认 HTML（265 字节），
    前端 r.json() 直接抛错。异常原文仍由 Flask 的 handle_exception 先写日志
    （非 debug 下 app.logger 的 StreamHandler 落到 server.log），本 handler 只
    挑响应形态——不吞异常，也不回显 traceback / 路径 / 栈。
    """
    if request.path == "/api" or request.path.startswith("/api/"):
        return jsonify({
            "error": "服务器内部错误 / Internal server error",
            "status": 500,
        }), 500
    # 非 API 路径维持改动前的行为：werkzeug 的默认 HTML 错误页。
    return InternalServerError().get_response(request.environ)

# --- T51 请求体解析 ---------------------------------------------------------
# 此前 13 个端点都写 `request.get_json(silent=True) or {}`：坏 JSON、错
# Content-Type（表单/纯文本）全被当成空对象继续跑，最终报一个与真实原因无关的
# "缺字段"，或者干脆 200。统一收口到这里，语义只有三档：
#   无 body / 纯空白            → {}   老客户端不带 body 的调用行为不变
#   Content-Type 不是 JSON      → 400 + JSON 错误体
#   JSON 解析失败 / 不是对象    → 400 + JSON 错误体
#   合法 JSON 对象              → 原样返回
# 调用点一律写成 `data = _json_body()`，错误路径直接抛 HTTPException。
_MULTI_JSON_CT = "Content-Type: application/json"


def _json_body_failure(message: str):
    """把 400 打包成自带 JSON 响应的 HTTPException。

    Flask 对 HTTPException 直接取 `e.get_response()`，不经过 app.errorhandler(400)；
    因此不需要、也不该再注册一个全局 400 handler——注册了要么与这里的 JSON 体
    打架（模板类响应覆盖 JSON），要么被 Werkzeug 的子类匹配顺序绕过。
    /api/* 的 no-store 由 after_request 统一补，这里不用管。

    状态码必须写进 Response 对象本身：werkzeug 3.1 的 get_response() 在带了
    response 时直接 `return self.response`，不会把状态码改回 self.code——只传
    jsonify(...) 会得到一个 body 写着 400 而状态行是 200 的响应。
    """
    return BadRequest(
        response=make_response(jsonify({"error": message, "status": 400}), 400))


def _json_body():
    """统一的 JSON 请求体解析（T51）。返回 dict，失败时抛 400。"""
    raw = request.get_data(cache=True)
    if not raw.strip():
        return {}
    if not request.is_json:
        raise _json_body_failure(
            "请求体必须是 JSON（" + _MULTI_JSON_CT + "） / "
            "Request body must be JSON (" + _MULTI_JSON_CT + ")")
    try:
        data = request.get_json(silent=False)
    except BadRequest:
        raise _json_body_failure(
            "请求体 JSON 解析失败 / Malformed JSON body") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise _json_body_failure(
            "请求体必须是 JSON 对象 / JSON body must be an object")
    return data



# --- S-2 写接口同源校验（CSRF）---------------------------------------------
# 本站所有状态变更接口都是 /api/ 下的 POST/PUT/PATCH/DELETE，凭据只走请求头
# （X-Termify-Admin-Pwd / X-Termify-Admin），没有 Cookie 会话，因此只要浏览器
# 无法把跨站写请求打到这些路径上，CSRF 面就闭合。零依赖实现，不引入 Flask-WTF。
#
# 判定顺序 Origin -> Referer -> Sec-Fetch-Site：
# 1. 有 Origin 就必须与 request.host_url 同源（scheme+host+port 全等）；
#    "Origin: null"（沙箱 iframe、跨源重定向后的 POST）一律按跨源拒绝。
# 2. 无 Origin 退回 Referer，同样必须同源。
# 3. 两者都缺时不拒绝：浏览器对非 GET 请求必带 Origin，缺失即说明是非浏览器
#    客户端（curl / 脚本 / 老客户端 / 测试 test_client）。它们没有可被冒用的
#    环境凭据，一刀切拒绝只误伤 CLI 与测试、不增加安全性；此时若带了
#    Sec-Fetch-Site（现代浏览器必发）且不是 same-origin/none，仍然拒绝，
#    覆盖"中间件/代理剥掉 Origin 但浏览器仍发 Sec-Fetch-Site"的场景。
_WRITE_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_WRITE_GUARD_PREFIX = "/api/"
_SEC_FETCH_ALLOWED = frozenset({"same-origin", "none"})


def _url_origin(value: str) -> str:
    """把 Origin/Referer/URL 归一成 'scheme://host[:port]' 小写串；非法返回 ""。"""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".lower()


def _cross_site_reject(reason: str):
    """跨站写请求统一 403 + JSON（与 404/405/413 的 API 分支风格一致）。"""
    return jsonify({
        "error": "跨站请求已拒绝 / Cross-site request rejected",
        "reason": reason,
    }), 403


@app.before_request
def _write_origin_guard():
    """对 /api/ 下所有状态变更请求做同源校验（S-2）。"""
    if request.method in _WRITE_SAFE_METHODS:
        return None
    if not request.path.startswith(_WRITE_GUARD_PREFIX):
        return None
    self_origin = _url_origin(request.host_url)
    origin = request.headers.get("Origin")
    if origin is not None:
        if self_origin and _url_origin(origin) == self_origin:
            return None
        return _cross_site_reject("Origin 与本站不同源 / Origin not same-origin")
    referer = request.headers.get("Referer")
    if referer:
        if self_origin and _url_origin(referer) == self_origin:
            return None
        return _cross_site_reject("Referer 与本站不同源 / Referer not same-origin")
    site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if site and site not in _SEC_FETCH_ALLOWED:
        return _cross_site_reject(
            "Sec-Fetch-Site 指示跨站 / Sec-Fetch-Site not same-origin")
    return None


# --- D-3 补充：重复斜杠一次跳到位 -------------------------------------------
_MULTI_SLASH_RE = re.compile(r"/{2,}")


def _is_routable(path: str) -> bool:
    """目标路径是否命中一条真实路由（method 也参与判定）。

    只用于重定向前确认目标存在：不猜、不把 404 变成"先跳再 404"。
    """
    try:
        app.url_map.bind_to_environ(request.environ).match(
            path, method=request.method)
        return True
    except HTTPException:
        return False


@app.before_request
def _collapse_duplicate_slashes():
    """把 /text-art// 这类重复斜杠一次跳到规范地址，而不是让浏览器跑两跳。

    此前两次跳转都是 werkzeug/我们的规则各自为政：MAP 的 merge_slashes 先 308
    把 /text-art// 合成 /text-art/，再由显式规则 301 去掉尾斜杠。这里在路由
    匹配之前（before_request 早于 dispatch_request 抛 routing_exception）直接
    给出终点，跳法与 D-3 既有尾斜杠策略一致：纯 GET 页面 URL、301、无请求体
    语义。

    /api/* 不碰：那条前缀的跳转与错误形态属于 API 契约，不归页面 URL 归一管。
    """
    path = request.path
    if "//" not in path or path == "/":
        return None
    if path == "/api" or path.startswith("/api/"):
        return None
    collapsed = _MULTI_SLASH_RE.sub("/", path)
    for candidate in (collapsed.rstrip("/") or "/", collapsed):
        if candidate == path or not _is_routable(candidate):
            continue
        query = request.query_string.decode("latin-1")
        return redirect(candidate + ("?" + query if query else ""), 301)
    return None


_ABS_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|/(?:home|root|usr|var|opt|srv|tmp|mnt|Users|private)/)"
    r"[^\s'\"<>|;]*"
)


def _public_error_text(exc) -> str:
    """面向客户端的异常文案：先抹掉服务器绝对路径再回显。

    下游异常会把服务器路径写进 message（PIL 的
    "cannot identify image file 'C:\\...\\uploads\\x.png'"，ffmpeg 的
    stderr 里也带输入路径）。这些是内部布局信息，不该出现在响应里；
    完整原文保留在日志，客户端只拿到去掉路径的说明。
    """
    text = str(exc).strip()
    cleaned = _ABS_PATH_RE.sub("<path>", text)
    if cleaned != text:
        app.logger.warning(
            "client-facing error text had server paths removed: %s", text)
    return cleaned or "请求失败 / Request failed"


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

# .webp 自 2026-10-23 重测起收进白名单：text_art.html 早已向用户声明支持
# webp（那条文案不改），服务端却不认，用户照着提示传就被 400。
VALID_EXT = {".gif", ".png", ".jpg", ".jpeg", ".webp"}


def _webp_supported() -> bool:
    """PIL 是否带 WebP 解码（自建 Pillow 常被裁掉 libwebp）。

    白名单照收 .webp、能力缺失时给明确 400 而不是让 convert 抛异常落
    500——用户看到的应是"这个部署不支持 webp"，不是一个内部错误。
    """
    try:
        from PIL import features
        return bool(features.check("webp"))
    except Exception:  # noqa: BLE001 — 老版本 Pillow 没有 features 模块
        return False


_WEBP_UNAVAILABLE = ("当前部署未启用 WebP 解码，请改用 PNG / JPG / GIF / "
                     "WebP decoding is not available in this build, use PNG / JPG / GIF")

VALID_FORMATS = {"python", "html", "mp4", "embed"}

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
from termify.charset import CHARSETS, sanitize_ramp as _sanitize_ramp

# 单一画廊开关（docs/DECISION-LOCAL-FIRST-2026-09-22.md §1）：桌面包 launcher
# 注入 TERMIFY_ENABLE_GALLERY=0 → 启动时根本不注册画廊路由与页面，与云端 0
# 依赖；本地 Web / 线上缺省（未设或 =1）行为不变。
# 取值在 import 时确定（launcher 在 `from app import app` 之前注入），
# 因此不需要、也不支持运行期切换——双形态由进程级环境变量区分。
GALLERY_ENABLED = os.environ.get("TERMIFY_ENABLE_GALLERY", "1").strip().lower() \
    not in ("0", "false", "no", "off")


def gallery_route(rule: str, **options):
    """注册画廊路由；开关关闭时原样返回函数（不注册，端点不存在）。

    只影响注册、不触碰函数实现：关掉画廊后这些函数在进程内不可达，
    转换/预览/导出等非画廊链路完全不受影响。
    """
    def decorator(fn):
        if GALLERY_ENABLED:
            return app.route(rule, **options)(fn)
        return fn
    return decorator


@app.context_processor
def _inject_gallery_flag():
    """模板按 ``gallery_enabled`` 隐藏画廊入口与发布按钮。

    开关关闭时画廊路由根本不存在，模板若照旧渲染入口就会出现"点了报 404"
    的死按钮；所有模板（含 include 的发布弹窗）统一读这个标记。
    """
    return {"gallery_enabled": GALLERY_ENABLED}


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

# --- S-1 管理口令失败限流 -------------------------------------------------
# /api/gallery/admin 系列此前没有任何失败计数：错误口令可以无限连打。
# 这里给"管理口令校验"单独一层失败计数 + 退避窗口，不复用 _rate_check 的
# action 配额（正确口令不该被普通接口的配额连带惩罚）。
# 阈值 10 次/10 分钟、命中后锁 15 分钟：管理员手误 2~3 次远不到阈值，而爆破
# 被压到 10 次/10 分钟（约 1440 次/天），对人工口令已不可行。
ADMIN_PWD_FAIL_MAX = 10
ADMIN_PWD_FAIL_WINDOW = 600
ADMIN_PWD_FAIL_LOCK = 900
_ADMIN_PWD_MAX_IPS = 4096

_ADMIN_PWD_LOCK = threading.Lock()
_ADMIN_PWD_FAILS: dict[str, list[float]] = {}   # ip -> 窗口内失败时刻
_ADMIN_PWD_BLOCKED: dict[str, float] = {}       # ip -> 解锁时刻


def _admin_pwd_reset(ip: str) -> None:
    with _ADMIN_PWD_LOCK:
        _ADMIN_PWD_FAILS.pop(ip, None)
        _ADMIN_PWD_BLOCKED.pop(ip, None)


def _admin_pwd_wait(ip: str) -> int:
    """该 IP 距解锁还剩的秒数；0 表示未被锁定（顺带清掉过期记录）。"""
    now = time.time()
    with _ADMIN_PWD_LOCK:
        until = _ADMIN_PWD_BLOCKED.get(ip)
        if until is None:
            return 0
        if until <= now:
            _ADMIN_PWD_BLOCKED.pop(ip, None)
            return 0
        return int(until - now) + 1


def _admin_pwd_fail(ip: str) -> int:
    """记一次失败口令，返回剩余可尝试次数（0 = 本次已触发锁定）。"""
    now = time.time()
    with _ADMIN_PWD_LOCK:
        if len(_ADMIN_PWD_FAILS) >= _ADMIN_PWD_MAX_IPS:
            for key in [k for k, v in _ADMIN_PWD_FAILS.items()
                        if not v or now - v[-1] >= ADMIN_PWD_FAIL_WINDOW]:
                _ADMIN_PWD_FAILS.pop(key, None)
        fails = [t for t in _ADMIN_PWD_FAILS.get(ip, ())
                 if now - t < ADMIN_PWD_FAIL_WINDOW]
        fails.append(now)
        if len(fails) >= ADMIN_PWD_FAIL_MAX:
            _ADMIN_PWD_FAILS.pop(ip, None)  # 锁定期间窗口清零，解锁后重新计数
            _ADMIN_PWD_BLOCKED[ip] = now + ADMIN_PWD_FAIL_LOCK
            return 0
        _ADMIN_PWD_FAILS[ip] = fails
        return ADMIN_PWD_FAIL_MAX - len(fails)


def _admin_locked_response(wait: int):
    resp = jsonify({
        "error": "管理员口令失败次数过多，请稍后再试 / "
                 "Too many failed admin password attempts, try again later",
        "retry_after": wait,
        "remaining_attempts": 0,
    })
    resp.status_code = 429
    resp.headers["Retry-After"] = str(wait)
    return resp


def _admin_check() -> tuple[bool, object]:
    """校验 X-Termify-Admin-Pwd 并做失败限流。

    返回 (True, None) 表示口令正确（该 IP 计数已清零，调用方继续）；
    返回 (False, response) 表示已生成 403（附剩余次数）或 429（退避中，
    附 Retry-After / retry_after）。
    """
    ip = _client_ip()
    wait = _admin_pwd_wait(ip)
    if wait:
        return False, _admin_locked_response(wait)
    if _secret_equal(request.headers.get("X-Termify-Admin-Pwd", ""), _admin_pwd()):
        _admin_pwd_reset(ip)
        return True, None
    remaining = _admin_pwd_fail(ip)
    if remaining <= 0:
        return False, _admin_locked_response(_admin_pwd_wait(ip) or ADMIN_PWD_FAIL_LOCK)
    resp = jsonify({"error": "Unauthorized", "remaining_attempts": remaining})
    resp.status_code = 403
    return False, resp


def _admin_pwd_header_state() -> str:
    """比对 X-Termify-Admin-Pwd 并纳入同一份失败计数，返回状态串。

    作品详情 is_authorized / 作品删除 / 私有原作直链这三处此前直接
    _secret_equal 比对管理口令，绕过了 _admin_check 的失败计数 —— 等于给
    口令爆破开了三条不限次的布尔 oracle（其中删除是猜中即毁）。这里把它们并
    回同一份按 IP 的失败计数与退避窗口。

    返回：
      "absent" —— 未带该头（普通访客），不计失败；
      "ok"     —— 口令正确（该 IP 计数清零）；
      "bad"    —— 口令错误，已记一次失败；
      "locked" —— 该 IP 处于退避窗口内，调用方应回 429。
    """
    hdr = request.headers.get("X-Termify-Admin-Pwd", "")
    if not hdr:
        return "absent"
    ip = _client_ip()
    if _admin_pwd_wait(ip):
        return "locked"
    if _secret_equal(hdr, _admin_pwd()):
        _admin_pwd_reset(ip)
        return "ok"
    if _admin_pwd_fail(ip) <= 0:
        # 跨阈值的这一次就锁（与 _admin_check 一致的语义），不是下一次
        return "locked"
    return "bad"


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


# /v/ 回放页 params 白名单：允许进入页面源码的渲染参数（frames_dir 等服务器
# 内部字段绝不外发；audio_file 是构造音频直链所需的文件名，非路径）。
# font/frames 仅字符作品（kind=text）携带：frames 是艺术字文本本身，
# 经 tojson 转义进页面，与其他用户内容同责渲染端转义。
_VIEW_PARAM_KEYS = ("charset", "width", "height", "color", "kind", "interval",
                    "fg", "bg", "audio_file", "font", "frames", "piece")


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


def _video_original_size(frames_dir: str, frame_count: int) -> dict:
    """Source frame size for a video task, so the client can derive rows."""
    from termify.video import frames_dir_to_images

    size = {"type": "video", "frame_count": frame_count}
    if not os.path.isdir(frames_dir):
        return size
    frames = frames_dir_to_images(frames_dir)
    if frames:
        size.update(_original_size(frames[0]))
    return size


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/index.html")
def index_html_redirect():
    """老链接归位（D-4）：/index.html 落到 301 → /，此前是 404。

    站点首页只认 /（模板里的 url_for("index") 一直是 /），/index.html 只是
    外部硬编码与旧书签的写法；GET/HEAD 由同一条规则覆盖。
    """
    return redirect("/", 301)


@app.route("/text-art")
def text_art_page():
    """字符艺术独立页（FIGlet 直转 + 中文 TTF 点阵）。"""
    return render_template("text_art.html")


@app.route("/text-art/")
def text_art_trailing_slash():
    """尾斜杠归一（D-3）：/text-art/ 不是页面，301 到 /text-art。

    用 301 不用 308：这两个都是纯 GET 的页面 URL，没有需要保留的请求体
    语义。目标 /text-art 是显式规则、不会再被要求补斜杠，不存在重定向循环；
    HEAD 由 Flask 按 GET 规则自动应答。
    """
    return redirect("/text-art", 301)


# --- 站点级 SEO 端点（robots / sitemap） ------------------------------------

_SITEMAP_STATIC_PATHS = ("/", "/gallery", "/text-art")
_SITEMAP_MAX_WORKS = 100


def _public_base_url() -> str:
    """站点绝对根 URL（不带尾斜杠），供 robots/sitemap 拼绝对地址。

    ``request.host_url`` 反映的是"应用实际看到的那一段"：线上 Caddy 反代到
    127.0.0.1:5000 是 http，照抄会让 sitemap 全是 http:// 地址。与
    ``_client_ip`` 用同一套信任边界——只有直连对端是回环/私网（说明确实在
    反代之后）才采信 X-Forwarded-Proto，直连客户端伪造不了。
    """
    base = request.host_url.rstrip("/")
    try:
        peer = ipaddress.ip_address(request.remote_addr or "")
        trusted = peer.is_loopback or peer.is_private
    except ValueError:
        trusted = False
    if trusted:
        proto = (request.headers.get("X-Forwarded-Proto") or "")
        proto = proto.split(",")[0].strip().lower()
        if proto in ("http", "https"):
            base = proto + "://" + base.split("://", 1)[-1]
    return base


def _sitemap_lastmod(value) -> str | None:
    """works.created_at → <lastmod>。库里存的就是 ISO 串
    （"2026-01-01T00:00:00"），形如日期且够长就发，认不出来就不发——
    宁缺勿错。"""
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    if len(text) < 19 or text[4] != "-" or text[7] != "-" or text[10] != "T":
        return None
    return text[:19]


@app.route("/robots.txt")
def robots_txt():
    """允许全站抓取并指明 sitemap。此前该路径落 404 页，爬虫拿不到任何指令。"""
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "",
        f"Sitemap: {_public_base_url()}/sitemap.xml",
        "",
    ])
    resp = Response(body, mimetype="text/plain")
    resp.headers["Cache-Control"] = CRAWLER_CACHE_CONTROL
    return resp


@app.route("/sitemap.xml")
def sitemap_xml():
    """三张固定页 + 最近 100 条已发布作品的 /v/<id>。

    取库只读：``list_works`` 默认 ``include_private=False``，私密作品不进
    sitemap；不插入、不更新任何行。
    """
    base = _public_base_url()
    entries = [(base + path, None) for path in _SITEMAP_STATIC_PATHS]
    works = []
    if GALLERY_ENABLED:
        works, _total = GALLERY_DB.list_works(page=1, limit=_SITEMAP_MAX_WORKS)
    for work in works:
        entries.append((f"{base}/v/{quote(str(work['id']), safe='')}",
                        _sitemap_lastmod(work.get("created_at"))))
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in entries:
        parts.append("  <url>")
        parts.append(f"    <loc>{_xml_escape(loc)}</loc>")
        if lastmod:
            parts.append(f"    <lastmod>{_xml_escape(lastmod)}</lastmod>")
        parts.append("  </url>")
    parts.append("</urlset>")
    resp = Response("\n".join(parts) + "\n", mimetype="application/xml")
    resp.headers["Cache-Control"] = CRAWLER_CACHE_CONTROL
    return resp


@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in VALID_EXT:
        return jsonify({"error": "Unsupported format"}), 400
    if ext == ".webp" and not _webp_supported():
        return jsonify({"error": _WEBP_UNAVAILABLE}), 400

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
        app.logger.warning("upload conversion failed: %s", exc, exc_info=True)
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
        if ext == ".webp" and not _webp_supported():
            errors.append({"filename": file.filename, "error": _WEBP_UNAVAILABLE})
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
        except Exception as exc:  # noqa: BLE001 — 不向客户端回显异常详情（含服务器路径）
            os.remove(save_path)
            # 与 /api/upload 同一句泛化文案：PIL/ffmpeg 的原文里带
            # "C:\...\uploads\<id>.png" 这类服务器绝对路径。
            app.logger.warning("batch upload conversion failed: filename=%s %s",
                               file.filename, exc, exc_info=True)
            errors.append({"filename": file.filename,
                           "error": "转换失败，文件可能已损坏或格式不受支持 / "
                                    "Conversion failed: the file may be "
                                    "corrupted or unsupported"})
            continue

        # 源尺寸（不是 80x24 网格）：前端按它推导行数
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
        return jsonify({"error": _public_error_text(exc)}), 400
    file.save(video_tmp)

    try:
        validate_video(video_tmp)
    except VideoError as exc:
        os.remove(video_tmp)
        return jsonify({"error": _public_error_text(exc)}), 400

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
            # Report the source frame size so the client can derive its
            # own row count instead of assuming 80x24.
            original_size = _video_original_size(frames_dir, len(seq.lines_per_frame))
        except VideoError as exc:
            return jsonify({"error": _public_error_text(exc)}), 422
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("frame conversion failed: %s", exc)
            return jsonify({"error": "帧转换失败 / Frame conversion failed"}), 500
    finally:
        _VIDEO_IMPORT_SLOTS.release()

    target_size = {"width": width, "height": height}
    _task_put(
        task_id,
        filepath=frames_dir,  # persisted frames dir → charset/size switchable
        original_size=original_size,
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
        "original_size": original_size,
        "target_size": target_size,
    })


def _safe_uploads_path(name: str) -> str:
    """Resolve a server-generated file name inside uploads/, refusing traversal.

    首次写入时自愈 uploads/ 目录（与 _tmp_out_path 同一手法）：目录被清理或
    部署时漏建曾让 /api/upload 直接抛 FileNotFoundError 落 500（server.log 里 9
    条 traceback）。校验之后才建目录——非法文件名不会留下副作用。
    """
    base = paths.uploads_dir()
    resolved = os.path.abspath(os.path.join(base, name or ""))
    if os.path.dirname(resolved) != base or not resolved.startswith(base + os.sep):
        raise ValueError("path escapes the uploads dir")
    if not name or ".." in name or "/" in name or os.sep in name:
        raise ValueError("unsafe uploads filename")
    os.makedirs(base, exist_ok=True)
    return resolved


VALID_MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MAX_MUSIC_BYTES = 20 * 1024 * 1024

_MUSIC_MIME = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
}


def _safe_remove_task_upload(prefix: str, task_id: str) -> None:
    """Locate uploads/<prefix>_<id>.<ext> and delete it — one guarded flow.

    与 _find_uploaded_file + _safe_remove_upload 等效，但校验与删除
    在同一函数内可见：id 形状先 fail-closed，路径包含性再验一次。
    """
    if not re.fullmatch(r"[0-9a-f]{12}", task_id or ""):
        return
    base = paths.uploads_dir()
    if not os.path.isdir(base):
        return
    marker = f"{prefix}_{task_id}."
    for name in os.listdir(base):
        if not name.startswith(marker):
            continue
        resolved = os.path.abspath(os.path.join(base, name))
        if os.path.dirname(resolved) != base:
            continue
        if not resolved.startswith(base + os.sep) or ".." in resolved:
            continue
        try:
            os.remove(resolved)
        except OSError:
            pass


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
    if not re.fullmatch(r"[0-9a-f]{12}", task_or_work or ""):
        return None  # id 形状不符（含路径字符）直接 fail-closed
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
    _safe_remove_task_upload("music", task_id)
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
    data = _json_body()
    task_id = (data.get("task_id") or "").strip()
    if not _valid_task_id(task_id):
        return jsonify({"error": "No task_id"}), 400
    _safe_remove_task_upload("music", task_id)
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
    data = _json_body()
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
        return jsonify({"error": _public_error_text(exc)}), 400
    try:
        video_tmp = _safe_uploads_path(downloaded_name)
    except ValueError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400

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
            original_size = _video_original_size(frames_dir, frames_count)
        except VideoError as exc:
            return jsonify({"error": _public_error_text(exc)}), 422
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("frame conversion failed: %s", exc)
            return jsonify({"error": "帧转换失败 / Frame conversion failed"}), 500
    finally:
        _VIDEO_IMPORT_SLOTS.release()

    _task_put(
        task_id,
        filepath=frames_dir,  # persisted frames dir → charset/size switchable
        original_size=original_size,
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
        "original_size": original_size,
        "target_size": {"width": seq.width, "height": seq.height},
    })


@app.route("/api/fetch-url", methods=["POST"])
def fetch_url():
    """Download an image URL server-side and create a conversion task.

    SSRF protection: private IP blocked, Content-Type/Size validation,
    download timeout 15s, size cap 20MB, PIL verify.
    """
    data = _json_body()
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
        return jsonify({"error": _public_error_text(exc)}), 400

    try:
        task_id = uuid.uuid4().hex[:12]
        seq = convert(tmp_path, "ascii", 80, 24)
    except Exception as exc:  # noqa: BLE001
        os.remove(tmp_path)
        app.logger.warning("url-fetch conversion failed: %s", exc)
        return jsonify({"error": "转换失败，远端文件可能已损坏或格式不受支持 / "
                                 "Conversion failed: the remote file may be corrupted or unsupported"}), 400

    original_size = _original_size(tmp_path)
    target_size = {"width": seq.width, "height": seq.height}
    _task_put(
        task_id,
        filepath=tmp_path,
        original_size=original_size,
        target_size=target_size,
        frames_count=len(seq.lines_per_frame),
        interval=seq.interval,
    )
    _cache_put(task_id, _cache_key(task_id, "ascii", 80, 24), seq)

    return jsonify({
        "task_id": task_id,
        "filename": os.path.basename(tmp_path),
        "frames_count": len(seq.lines_per_frame),
        "original_size": original_size,
        "target_size": target_size,
    })


# ═══════════════ T33 字符艺术：FIGlet 直转 + 中文 TTF 点阵 ═══════════════
# 静态字符艺术作为第三种素材来源（与上传/链接抓取并列）：
#   1) 直转    —— 对齐 lddgo：输入英数 → pyfiglet → ASCII 艺术字；
#   2) 中文点阵 —— 含 CJK 输入走系统 TTF 光栅化（render_cjk_ttf，纯本地）。
# 字符作品入库 = art 渲染成终端风 PNG 作 source，复用缩略图/OG 管线；
# 艺术字文本存 params_json.frames，/v/ 页零渲染改动直接回放。
from termify import textart as _textart_mod


@app.route("/api/text/fonts", methods=["GET"])
def text_fonts():
    return jsonify({"ok": True, "fonts": _textart_mod.curated_fonts()})


@app.route("/api/text/convert", methods=["POST"])
def text_convert():
    """统一生成入口：{text, font?, width?} → 艺术字。

    服务端自动分流：含 CJK 字符走 TTF 点阵路径（font 解释为中文字体
    slug），纯 ASCII 走 FIGlet（font 为 FIGlet slug）。
    """
    data = _json_body()
    ip = _client_ip()
    ok, reason = _rate_check(ip, "text-convert", per_minute=120)
    if not ok:
        return jsonify({"error": reason}), 429
    if _textart_mod.cjk_has_glyph(data.get("text")):
        try:
            # 行高：用户可调（10-64 行，默认 CJK_DEFAULT_HEIGHT），超范围
            # 与宽度红线收缩统一走 textart.cjk_effective_height——前端要拿
            # 同一个值做「已自动收缩」提示，两边各算一份必然对不上。
            cjk_height = _textart_mod.cjk_effective_height(
                data.get("text"), data.get("height"))
            art = _textart_mod.render_cjk_ttf(
                data.get("text"), data.get("font"), cjk_height,
                data.get("charset"))
        except _textart_mod.TextArtError as exc:
            return jsonify({"error": _public_error_text(exc)}), 400
        cols, rows = _textart_mod.art_dims(art)
        clean_len = len(_textart_mod.filter_cjk_text(data.get("text")))
        avail = {f["slug"] for f in _textart_mod.cjk_available_fonts()
                 if f["available"]}
        font = data.get("font") if data.get("font") in avail \
            else _textart_mod.CJK_DEFAULT_FONT
        return jsonify({"ok": True, "mode": "cjk", "art": art,
                        "cols": cols, "rows": rows, "font": font,
                        "charset": data.get("charset")
                        if _textart_mod.known_cjk_charset(data.get("charset"))
                        else _textart_mod.CJK_DEFAULT_CHARSET,
                        "height": cjk_height,
                        "max_height": _textart_mod.CJK_MAX_HEIGHT,
                        "text": _textart_mod.filter_cjk_text(
                            data.get("text"))})
    try:
        art = _textart_mod.render_figlet(
            data.get("text"), data.get("font"), data.get("width"))
    except _textart_mod.TextArtError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400
    cols, rows = _textart_mod.art_dims(art)
    return jsonify({"ok": True, "mode": "figlet", "art": art, "cols": cols,
                    "rows": rows,
                    "font": data.get("font") if _textart_mod.known_font(
                        data.get("font")) else _textart_mod.DEFAULT_FONT,
                    "text": _textart_mod.filter_figlet_text(
                        data.get("text"))[:80]})


@app.route("/api/text/fontwall", methods=["POST"])
def text_fontwall():
    """字体墙：{text} → 全部精选字体的预览渲染（点击即换即看的数据源）。"""
    data = _json_body()
    ip = _client_ip()
    ok, reason = _rate_check(ip, "text-fontwall", per_minute=60)
    if not ok:
        return jsonify({"error": reason}), 429
    try:
        previews = _textart_mod.render_font_previews(data.get("text"))
    except _textart_mod.TextArtError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400
    return jsonify({"ok": True, "fonts": previews})


def _download_disposition(name: str, ext: str) -> str:
    """Content-Disposition 值：ASCII 回退名 + RFC 5987 UTF-8 名。

    HTTP 头仅限 latin-1——中文文件名直接放进 filename= 会让 werkzeug 在
    发送响应头时 UnicodeEncodeError 崩掉整个响应（浏览器表现为下载失败）。
    """
    from urllib.parse import quote

    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")         or "termify-art"
    return "attachment; filename=\"%s%s\"; filename*=UTF-8''%s" % (
        ascii_name, ext, quote(name + ext, safe=""))


@app.route("/api/text/export-png", methods=["POST"])
def text_export_png():
    """工作台直下 PNG（免发布）：art → 终端风图片，文件名含内容。"""
    # JSON（桌面 fetch）与表单 POST（移动端原生下载）双兼容
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body"}), 400
    ip = _client_ip()
    ok, reason = _rate_check(ip, "text-convert", per_minute=120)
    if not ok:
        return jsonify({"error": reason}), 429
    try:
        art = _textart_mod.validate_stored_art(data.get("art"))
    except _textart_mod.TextArtError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400
    # 优先 theme 映射；兼容直接传 fg 数组（画廊入库路径）
    fg = _rgb_or_none(data.get("fg")) or _textart_mod._theme_fg(
        data.get("theme", _textart_mod.DEFAULT_THEME))
    name = _gallery_mod.sanitize(data.get("name"),
                                 40).replace(" ", "_") or "textart"
    with tempfile.TemporaryDirectory() as td:
        png_path = os.path.join(td, "art.png")
        try:
            _textart_mod.render_art_png(art, png_path, fg=fg)
            with open(png_path, "rb") as fh:
                png_bytes = fh.read()
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("export-png failed: %s", exc)
            return jsonify({"error": "PNG 生成失败 / PNG generation "
                                     "failed"}), 500
    resp = make_response(png_bytes)
    resp.headers.set("Content-Type", "image/png")
    resp.headers.set("Content-Disposition",
                     _download_disposition(name, ".png"))
    return resp


@app.route("/api/text/export-ansi", methods=["POST"])
def text_export_ansi():
    """ANSI 彩色文本：art 套 truecolor 转义序列，粘贴到终端即显色。"""
    data = _json_body()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body"}), 400
    ip = _client_ip()
    ok, reason = _rate_check(ip, "text-convert", per_minute=120)
    if not ok:
        return jsonify({"error": reason}), 429
    try:
        art = _textart_mod.validate_stored_art(data.get("art"))
    except _textart_mod.TextArtError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400
    ansi = _textart_mod.render_ansi_art(art, data.get("theme", "green"))
    return jsonify({"ok": True, "ansi": ansi})


@app.route("/api/text/export-html", methods=["POST"])
def text_export_html():
    """HTML 单文件：自包含 <pre> + 内联样式，可直接发给任何人。"""
    # JSON（桌面 fetch）与表单 POST（移动端原生下载）双兼容
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body"}), 400
    ip = _client_ip()
    ok, reason = _rate_check(ip, "text-convert", per_minute=120)
    if not ok:
        return jsonify({"error": reason}), 429
    try:
        art = _textart_mod.validate_stored_art(data.get("art"))
    except _textart_mod.TextArtError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400
    html = _textart_mod.render_standalone_html(art, data.get("theme", "green"))
    name = _gallery_mod.sanitize(data.get("name"),
                                 40).replace(" ", "_") or "textart"
    resp = make_response(html)
    resp.headers.set("Content-Type", "text/html; charset=utf-8")
    resp.headers.set("Content-Disposition",
                     _download_disposition(name, ".html"))
    return resp


# --- T37 中文 TTF 点阵 ----------------------------------------------
# 中文输入走系统 TTF 光栅化（PIL），零外部依赖、零 token 成本。
# /api/text/convert 服务端自动分流：含 CJK → TTF 路径，否则 FIGlet。

@app.route("/api/cjk/ttf/fonts", methods=["GET"])
def cjk_ttf_fonts():
    """中文字体下拉数据源（含 available 标记，前端置灰不可用项）。"""
    return jsonify({"ok": True, "fonts": _textart_mod.cjk_available_fonts()})


@app.route("/api/cjk/ttf/fontwall", methods=["POST"])
def cjk_ttf_fontwall():
    """中文字符墙：{text?, font?} → 同一点阵 × 全部字符集的预览卡。

    （2026-09-29 产品调整：原"中文字体墙"下线，墙位改为字符集预览——
    字块/明暗/圆点/几何/盲文/经典，点卡片换"点亮"字符。）
    """
    data = _json_body()
    ip = _client_ip()
    ok, reason = _rate_check(ip, "text-fontwall", per_minute=60)
    if not ok:
        return jsonify({"error": reason}), 429
    try:
        previews = _textart_mod.render_cjk_charset_previews(
            data.get("text"), font=data.get("font"))
    except _textart_mod.TextArtError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400
    return jsonify({"ok": True, "charsets": previews})


@app.route("/api/cjk/ttf/render", methods=["POST"])
def cjk_ttf_render():
    """中文点阵：{text, font?, height?, charset?} → 字符画。纯本地。"""
    data = _json_body()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body"}), 400
    ip = _client_ip()
    ok, reason = _rate_check(ip, "cjk-ttf-render", per_minute=60)
    if not ok:
        return jsonify({"error": reason}), 429
    charset = data.get("charset")
    try:
        art = _textart_mod.render_cjk_ttf(
            data.get("text"), data.get("font"), data.get("height"), charset)
    except _textart_mod.TextArtError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400
    cols, rows = _textart_mod.art_dims(art)
    font_slug = data.get("font")
    if not isinstance(font_slug, str) or not any(
            f["slug"] == font_slug and f["available"]
            for f in _textart_mod.cjk_available_fonts()):
        font_slug = _textart_mod.CJK_DEFAULT_FONT
    return jsonify({"ok": True, "art": art, "cols": cols, "rows": rows,
                    "font": font_slug,
                    "charset": charset if _textart_mod.known_cjk_charset(
                        charset) else _textart_mod.CJK_DEFAULT_CHARSET,
                    "height": _textart_mod.cjk_effective_height(
                        data.get("text"), data.get("height")),
                    "max_height": _textart_mod.CJK_MAX_HEIGHT,
                    "text": _textart_mod.filter_cjk_text(
                        data.get("text"))})


# --- T38 图片字符化（lddgo image-ascii-converter 复刻）------------------------
# 图片上传 → 静态字符画。动图（GIF 多帧）/视频 → 引导去动画工坊。
# 复用 engine.convert 管线；同步转换（静态图单帧，耗时可控），不建任务。

# 图片字符化接受的静态格式（gif 动图在上传层即拦截引导）
IMGASCII_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

IMGASCII_MIN_W, IMGASCII_MAX_W = 20, 400   # 字符宽度（列）边界
IMGASCII_MIN_H, IMGASCII_MAX_H = 10, 400   # 字符高度（行）边界


def _imgascii_flip(img, flip: object):
    """翻转模式：hflip / vflip / hvflip / none（默认）。"""
    from PIL import Image
    if flip == "hflip":
        return img.transpose(Image.FLIP_LEFT_RIGHT)
    if flip == "vflip":
        return img.transpose(Image.FLIP_TOP_BOTTOM)
    if flip == "hvflip":
        return img.transpose(Image.ROTATE_180)
    return img


# 图片模式可选字符集（blocks 不提供：其字符本身即真彩色，与配色行冲突，
# 动画工坊专属）。顺序即字体墙卡片顺序。
IMGASCII_CHARSETS = ("ascii", "ascii-lite", "braille", "shades", "geometric",
                     "binary", "custom")
IMGASCII_CHARSET_NAMES = {
    "ascii": "默认字符", "ascii-lite": "简约字符", "braille": "盲文字符",
    "shades": "明暗渐变块", "geometric": "几何图形", "binary": "极简二值",
    "custom": "自定义字符",
}


def _has_unusable_image_payload() -> bool:
    """请求里带了图片数据、但没能变成可用文件（字段名不对 / base64 解析失败 /
    空文件上传）。用来把"数据坏了"和"根本没选图"分开报错——用户明明选了图却
    被告知"请选择图片"，是纯误导。
    """
    if request.is_json:
        body = request.get_json(silent=True)
        if isinstance(body, dict):
            for key in ("image", "image_data", "data", "file"):
                val = body.get(key)
                if isinstance(val, str) and val.strip():
                    return True
    for f in request.files.values():
        if f is not None and (f.filename or f.content_length):
            return True
    return False


def _imgascii_read(file):
    """multipart 图片 → (PIL RGB 画布, 错误响应 or None)。

    透明区合成到终端底色，与前端预览底一致；多帧动图拒绝（引导动画工坊）。
    """
    from PIL import Image

    if file is None or not file.filename:
        if _has_unusable_image_payload():
            return None, (jsonify({"error": "图片数据无法解析，请换一张 / "
                                            "Could not decode the image data"}),
                          400)
        return None, (jsonify({"error": "请选择图片 / Choose an image"}), 400)
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext == ".gif":
        return None, (jsonify({"error": "动图请移步「动画工坊」/ Animated "
                                        "images: use the Animation Lab",
                               "redirect": "/"}), 400)
    if ext not in IMGASCII_EXT:
        return None, (jsonify({"error": "不支持的格式（支持 png/jpg/bmp/webp）"
                                        " / Unsupported format"}), 400)
    task_id = uuid.uuid4().hex[:12]
    save_path = _safe_uploads_path(f"{task_id}{ext}")
    file.save(save_path)
    try:
        img = Image.open(save_path)
        n_frames = getattr(img, "n_frames", 1)
        if n_frames and n_frames > 1:
            raise _ImgAsciiGif()
        img = img.convert("RGBA")
        canvas = Image.new("RGB", img.size, (10, 14, 20))
        canvas.paste(img, mask=img.split()[3])
        return canvas, None
    finally:
        if os.path.exists(save_path):
            try:
                os.remove(save_path)
            except OSError:
                pass


class _ImgAsciiGif(Exception):
    """上传的是动图（多帧）——需要引导去动画工坊。"""


def _imgascii_render(canvas, charset, width, height, ramp, color_mode):
    """画布 → 单帧字符画 dict（art/cols/rows），源像素色走内嵌 TrueColor。"""
    from termify import engine as _engine

    tmp_path = _tmp_save(canvas, ".png")
    try:
        seq = _engine.convert(tmp_path, charset, width, height,
                              charset_ramp=ramp, color_mode=color_mode)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    lines = seq.lines_per_frame[0]
    plain_lines = [re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", ln) for ln in lines]
    cols = max((len(ln) for ln in plain_lines), default=0)
    # source 模式保留 TrueColor ANSI（lines 原样，终端粘贴即显色）；
    # 主题色模式产纯文本（预览前端着色、导出按钮按主题套色）
    art = "\n".join(lines) if color_mode == "source" else "\n".join(plain_lines)
    return {"art": art, "cols": cols, "rows": len(lines)}


@app.route("/api/text/imgascii", methods=["POST"])
def text_imgascii():
    """图片艺术化：multipart 图片 + 参数 → {art}。同步、纯本地。

    参数（配色/字符集复用动画工坊与字符艺术页的既有方案）：
    - palette: green|cyan|amber|magenta|red|white（单色主题，映射 fg）
               | source（原色：逐字符取源像素 TrueColor）
    - width / height: 字符画目标列数/行数
    - charset: ascii（默认）| braille | shades | geometric | binary | custom
      （blocks 不提供：其字符本身即真彩色，与配色行冲突，动画工坊专属）
    - charset_ramp: 自定义字符序列（密→疏，charset=custom 时必填）
    - flip: none | hflip | vflip | hvflip
    动图（GIF 多帧）→ 400 + redirect 引导去动画工坊。
    输出统一为 txt：原色保留内嵌 TrueColor ANSI，主题色纯文本由前端着色预览，
    彩色导出复用结果区的复制 ANSI 按钮。
    """
    ip = _client_ip()
    ok, reason = _rate_check(ip, "imgascii", per_minute=20)
    if not ok:
        return jsonify({"error": reason}), 429

    def _int_arg(name, lo, hi, default):
        try:
            v = int(request.form.get(name, default))
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    width = _int_arg("width", IMGASCII_MIN_W, IMGASCII_MAX_W, 80)
    height = _int_arg("height", IMGASCII_MIN_H, IMGASCII_MAX_H, 40)
    palette = request.form.get("palette", "green")
    valid_palettes = set(_textart_mod.ART_THEMES) | {"source"}
    if palette not in valid_palettes:
        palette = "green"
    charset = request.form.get("charset", "ascii")
    if charset not in IMGASCII_CHARSETS:
        charset = "ascii"
    ramp = request.form.get("charset_ramp") or None
    flip = request.form.get("flip", "none")
    color_mode = "source" if palette == "source" else "mono"
    # 主题色不嵌入 ANSI：mono 路径产纯文本，预览由前端 CSS 着色、
    # 彩色导出由结果区「复制 ANSI」按钮（export-ansi 按主题套色）完成。

    try:
        canvas, err = _imgascii_read(request.files.get("file"))
        if err:
            return err
        canvas = _imgascii_flip(canvas, flip)
        out = _imgascii_render(canvas, charset, width, height, ramp,
                               color_mode)
    except _ImgAsciiGif:
        return jsonify({"error": "检测到动图（多帧）——请移步「动画工坊」"
                                 " / Animated image detected: use the "
                                 "Animation Lab", "redirect": "/"}), 400
    except ValueError as exc:
        return jsonify({"error": _public_error_text(exc)[:200]}), 400
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("imgascii failed: %s", exc)
        return jsonify({"error": "转换失败，图片可能已损坏 / Conversion failed"}), 400
    return jsonify({"ok": True, "mode": color_mode, "palette": palette,
                    "art": out["art"], "cols": out["cols"],
                    "rows": out["rows"]})


@app.route("/api/text/imgwall", methods=["POST"])
def text_imgwall():
    """图片字体墙：同一张图 × 全部可选字符集 → 预览卡（点卡片即换即看）。

    和英文字体墙同一套卡片：一次上传、一次转换出所有变体，避免每点一张
    卡就重传一次图。custom（自定义字符）没有固定字形，不进墙。
    """
    ip = _client_ip()
    ok, reason = _rate_check(ip, "imgascii", per_minute=20)
    if not ok:
        return jsonify({"error": reason}), 429

    def _int_arg(name, lo, hi, default):
        try:
            v = int(request.form.get(name, default))
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    width = _int_arg("width", IMGASCII_MIN_W, IMGASCII_MAX_W, 80)
    height = _int_arg("height", IMGASCII_MIN_H, IMGASCII_MAX_H, 40)
    palette = request.form.get("palette", "green")
    if palette not in set(_textart_mod.ART_THEMES) | {"source"}:
        palette = "green"
    color_mode = "source" if palette == "source" else "mono"
    try:
        canvas, err = _imgascii_read(request.files.get("file"))
        if err:
            return err
        canvas = _imgascii_flip(canvas, request.form.get("flip", "none"))
        # 墙卡缩略图：整幅按比例缩到 60 列以内，卡片里才看得清
        scale = min(1.0, 60.0 / max(1, width))
        w_small = max(IMGASCII_MIN_W, int(width * scale))
        h_small = max(6, int(height * scale))
        previews = []
        for cs in IMGASCII_CHARSETS:
            if cs == "custom":
                continue
            try:
                out = _imgascii_render(canvas, cs, w_small, h_small, None,
                                       color_mode)
            except Exception:  # noqa: BLE001 — 个别字符集失败不影响整墙
                continue
            previews.append({"slug": cs, "name": IMGASCII_CHARSET_NAMES[cs],
                             "art": out["art"], "full": "",
                             "cols": out["cols"], "rows": out["rows"]})
    except _ImgAsciiGif:
        return jsonify({"error": "检测到动图（多帧）——请移步「动画工坊」"
                                 " / Animated image detected: use the "
                                 "Animation Lab", "redirect": "/"}), 400
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("imgwall failed: %s", exc)
        return jsonify({"error": "转换失败，图片可能已损坏 / Conversion failed"}), 400
    if not previews:
        return jsonify({"error": "没有可用字符集 / No charset available"}), 400
    return jsonify({"ok": True, "fonts": previews})


def _tmp_save(img, ext: str) -> str:
    """PIL Image → 临时文件路径（engine.convert 吃路径不吃 Image）。"""
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as fh:
        img.save(fh, format="PNG")
    return path


@app.route("/api/text/export-txt", methods=["POST"])
def text_export_txt():
    """纯文本 .txt 下载（表单 POST → 浏览器原生 attachment 下载）。

    移动端浏览器（UC/X5 等）对 blob+a.click 下载支持不可靠——txt 从
    blob 改走服务端 attachment，与 PNG/HTML 同一模式。
    """
    # JSON（桌面 fetch）与表单 POST（移动端原生下载）双兼容
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body"}), 400
    ip = _client_ip()
    ok, reason = _rate_check(ip, "text-convert", per_minute=120)
    if not ok:
        return jsonify({"error": reason}), 429
    try:
        art = _textart_mod.validate_stored_art(data.get("art"))
    except _textart_mod.TextArtError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400
    name = _gallery_mod.sanitize(data.get("name"),
                                 40).replace(" ", "_") or "textart"
    resp = make_response(art)
    resp.headers.set("Content-Type", "text/plain; charset=utf-8")
    resp.headers.set("Content-Disposition",
                     _download_disposition(name, ".txt"))
    return resp


@app.route("/api/text/terminal-command", methods=["POST"])
def text_terminal_command():
    """字符画 → python -c 单行命令（base64 免疫转义差异）。

    {art, theme} → {cmd}。theme 给定时无色 art 按主题着色；原色 art
    已含 TrueColor 转义则原样嵌入，终端直接显原色。
    """
    data = _json_body()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body"}), 400
    ip = _client_ip()
    ok, reason = _rate_check(ip, "text-convert", per_minute=120)
    if not ok:
        return jsonify({"error": reason}), 429
    try:
        # keep_ansi：原色 art 自带 SGR 转义，不能剥
        art = _textart_mod.validate_stored_art(data.get("art"), keep_ansi=True)
    except _textart_mod.TextArtError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400
    theme = data.get("theme")
    if theme is not None and (not isinstance(theme, str)
                              or theme not in _textart_mod.ART_THEMES):
        theme = _textart_mod.DEFAULT_THEME
    cmd = _textart_mod.render_terminal_command(art, theme)
    # 超长就别指望粘进终端了（cmd.exe 8191 字符硬上限）——前端据此把
    # 主推动作换成「下载 .py」，而不是让用户粘一坨然后失败。
    return jsonify({"ok": True, "cmd": cmd, "cmd_len": len(cmd),
                    "too_long": len(cmd) > _textart_mod.TERMINAL_CMD_MAX})


@app.route("/api/text/export-py", methods=["POST"])
def text_export_py():
    """可执行 .py：字符画内嵌成 zlib+base64，`python xxx.py` 即显示。

    终端命令（python -c 一行）在大作品上会撞上命令行长度上限，.py 是
    社区通行的正解（lddgo 一类工具也都是给文件而不是给一坨粘贴缓冲）。
    """
    # JSON（桌面 fetch）与表单 POST（移动端原生下载）双兼容
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body"}), 400
    ip = _client_ip()
    ok, reason = _rate_check(ip, "text-convert", per_minute=120)
    if not ok:
        return jsonify({"error": reason}), 429
    try:
        # keep_ansi：原色 art 自带 SGR 转义，不能剥
        art = _textart_mod.validate_stored_art(data.get("art"), keep_ansi=True)
    except _textart_mod.TextArtError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400
    theme = data.get("theme")
    if theme is not None and (not isinstance(theme, str)
                              or theme not in _textart_mod.ART_THEMES):
        theme = _textart_mod.DEFAULT_THEME
    name = _gallery_mod.sanitize(data.get("name"), 40).replace(" ", "_") or \
        "termify-art"
    script = _textart_mod.render_python_script(art, theme, name)
    resp = make_response(script)
    resp.headers.set("Content-Type", "text/x-python; charset=utf-8")
    resp.headers.set("Content-Disposition",
                     _download_disposition(name, ".py"))
    return resp


@gallery_route("/api/gallery/upload-text", methods=["POST"])
def gallery_upload_text():
    """字符艺术作品入库：art → 终端风 PNG source → 缩略图/OG → DB。

    艺术字文本存 params_json.frames（单帧），/v/ 页直接回放。
    """
    data = _json_body()
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON body"}), 400
    ip = _client_ip()
    ok, reason = _rate_check(ip, "upload", per_minute=3, per_day=10)
    if not ok:
        return jsonify({"error": reason}), 429
    # 配色：主题名（green/cyan/...）或 "source"（原色 TrueColor，art 内嵌
    # SGR 转义）。原色路径必须保留转义入库，否则作品回放丢彩色。
    palette = data.get("palette")
    is_source = palette == "source"
    if not is_source and palette not in _textart_mod.ART_THEMES:
        palette = None
    try:
        art = _textart_mod.validate_stored_art(
            data.get("art"), keep_ansi=is_source)
    except _textart_mod.TextArtError as exc:
        return jsonify({"error": _public_error_text(exc)}), 400
    cols, rows = _textart_mod.art_dims(
        re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", art) if is_source else art)
    font = data.get("font") if _textart_mod.known_font(data.get("font")) \
        else ""
    if is_source:
        fg = _textart_mod.ART_FG_DEFAULT  # 卡片缩略图兜底色；回放走内嵌 ANSI
    elif palette:
        fg = _textart_mod._theme_fg(palette)
    else:
        fg = _rgb_or_none(data.get("fg")) or _textart_mod.ART_FG_DEFAULT

    title = _gallery_mod.sanitize(data.get("title"), _gallery_mod._TITLE_MAX) \
        or "字符艺术"
    description = _gallery_mod.sanitize(data.get("description"),
                                        _gallery_mod._DESC_MAX)
    author = _gallery_mod.sanitize(data.get("author"),
                                   _gallery_mod._AUTHOR_MAX) or "匿名创作者"
    tags_raw = data.get("tags", [])
    _allowed_tags = set(_gallery_mod.VALID_TAGS) | set(
        _gallery_mod.TEXT_VALID_TAGS)
    tags = [t for t in tags_raw if isinstance(t, str)
            and t in _allowed_tags][:3] if isinstance(tags_raw, list) \
        else []
    tags = tags + _gallery_mod.sanitize_custom_tags(data.get("custom_tags"))
    is_private = 1 if data.get("is_private") in (1, "1", True, "true", "on") \
        else 0

    work_id = _make_unique_id()
    base = _gallery_mod.gallery_base(GALLERY_DATA_DIR)
    source_path = os.path.join(base, f"{work_id}.png")
    thumb_path = os.path.join(base, f"{work_id}_thumb.gif")
    og_path = os.path.join(base, f"{work_id}_og.png")
    # 缩略图/OG 用纯文本渲染（原色作品的 SGR 转义不能画进 PNG）；
    # 回放用原始 art（source 含彩色 ANSI）
    art_plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", art) if is_source else art
    try:
        _textart_mod.render_art_png(art_plain, source_path, fg=fg)
        _gallery_mod.make_thumbnail(source_path, thumb_path, fit=True)
        _gallery_mod.make_og_image(source_path, og_path, title, author)
    except Exception as exc:  # noqa: BLE001
        for p in (source_path, thumb_path, og_path):
            if os.path.isfile(p):
                os.remove(p)
        app.logger.warning("text-art thumbnail failed: %s", exc)
        return jsonify({"error": "缩略图生成失败 / Thumbnail generation "
                                 "failed"}), 500

    params = {"kind": "text", "charset": "text", "font": font,
              "color": "source" if is_source else "mono", "fg": list(fg),
              "width": cols, "height": rows, "frames": [art]}
    if palette and not is_source:
        params["palette"] = palette
    admin_token = _gallery_mod.make_admin_token()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    GALLERY_DB.insert_work({
        "id": work_id,
        "title": title,
        "description": description,
        "tags": json.dumps(tags, ensure_ascii=False),
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
    # admin_token 只经 httponly cookie 下发，绝不进响应体（XSS 可读 → 长期接管）
    resp = make_response(jsonify({
        "ok": True,
        "id": work_id,
        "url": url_for("gallery_view", work_id=work_id, _external=False),
        "work": _gallery_public_dict(work),
    }))
    resp.set_cookie(
        f"termify_admin_{work_id}",
        admin_token,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="Lax",
    )
    return resp


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


# 预览 JSON 的体积预算。超过即抽稀（均匀降采样），不再整份拒掉——
# 整份 413 的后果是作品页对大网格 / blocks 风格**永远**预览不出来
#（blocks 每格 ~21B，514 帧 × 80 列 × 48 行就已经 41MB > 预算），
# 而主页走本地渲染能看，同一个作品两页表现不一致。
PREVIEW_PAYLOAD_BUDGET = 30 * 1024 * 1024
# 渲染前的「格 × 帧」封顶：与体积预算是两道独立的闸——体积估算对 blocks
# 偏保守（实测真实载荷只有估算的 1/6），单靠它会放过 400 列 × 数百帧
# 这种能把渲染进程 OOM 掉的组合。这道闸卡的是**渲染量**，8e6 格 ≈ 最坏
# 数秒，与页面「渲染中…」的等待预期相符。
PREVIEW_CELL_BUDGET = 8_000_000


def _source_frame_count(path: str) -> int:
    """素材帧数（只读文件头，不解码像素）。"""
    from PIL import Image

    with Image.open(path) as im:
        return int(getattr(im, "n_frames", 1) or 1)


def _preview_per_char(charset: str, color_mode: str) -> int:
    """每字符的 ANSI 字节成本（实测值，见 _preview_payload_too_large）。"""
    if charset == "blocks":
        return 21          # 每格 fg+bg SGR
    return 8 if color_mode != "mono" else 4


def _preview_max_frames(width: int, height: int, charset: str,
                        color_mode: str = "mono",
                        budget: int = PREVIEW_PAYLOAD_BUDGET) -> int:
    """预算内最多能装多少帧（至少 1 帧；width/height 已钳制 1-400，
    单帧最大 400×800×21B ≈ 6.7MB < 预算，所以实际最小值远大于 1）。"""
    rows = height * 2 if charset == "blocks" else height
    per_frame = max(1, width * rows * _preview_per_char(charset, color_mode))
    return max(1, budget // per_frame)


def _preview_payload_too_large(frame_count: int, width: int, height: int,
                               charset: str, color_mode: str = "mono") -> bool:
    """单份全帧 JSON 是否超预算（抽稀后仍装不下时才会命中）。"""
    if frame_count <= 0 or width <= 0 or height <= 0:
        return False
    return frame_count > _preview_max_frames(width, height, charset, color_mode)


def _subsample_frames(seq, charset: str, color_mode: str = "mono"):
    """把 seq 均匀抽稀到预算内 → (frames, total_frames)。

    均匀取样（等距下标）而非截断头部：动画的起止姿态都保留，循环观感
    与原片一致。原 interval 不变——预览宁可放快，也不该变成 50 秒的慢放。
    """
    total = len(seq.lines_per_frame)
    maxf = _preview_max_frames(seq.width, seq.height, charset, color_mode)
    if total <= maxf:
        return seq.lines_per_frame, total
    if maxf <= 1:
        return [seq.lines_per_frame[0]], total
    # 端点必须落在首尾帧上：循环播放时最后一步跳回起点，少了尾帧就是
    # "动画到 98% 突然闪回开头"的接缝
    last = total - 1
    idx = [int(round(i * last / (maxf - 1))) for i in range(maxf)]
    return [seq.lines_per_frame[i] for i in idx], total


# --- PF-2 / PF-5：预览响应缓存 + 服务端成本/体积闸门 -------------------------
# 终端「一屏」有物理上限：横向 400 列与本文件 width/height 的 1-400 硬钳一致，
# 纵向 120 文本行（blocks 每格折算 2 行、braille 折算 4 行）。超限按上限截断，
# 但不静默——响应头 X-Preview-Clamped / X-Embed-Clamped 与 JSON 的
# clamped/requested 会写明被截到多少。
PREVIEW_MAX_ROWS = 120
# 单次响应体预算，按**实测**每格字节成本折算成帧数上限。取 3MB 的依据：
# (1) 必须大于正常尺寸的整段体积（mono ascii 80x24 = 1.03MB / 514 帧全给），
#     否则正常观看也会被抽稀；(2) 实测最坏请求 29-32MB，3MB 已是 10 倍收缩，
#     且 flask-compress 对这类 ANSI 文本再压约 10 倍，实际传输 ~300KB。
PREVIEW_BODY_BUDGET = 3 * 1024 * 1024
# 内容只由 URL 与作品内容决定，渲染一次即可复用。进程内 LRU：gunicorn 多
# worker 各自一份（未命中就重算，属可接受降级），重启即失效。
PREVIEW_CACHE_BYTES = 64 * 1024 * 1024
PREVIEW_CACHE_ENTRIES = 32
PREVIEW_MAX_AGE = 600


def _preview_row_mult(charset: str) -> int:
    """一「行」height 折成多少文本行（与渲染管线的换算一致）。"""
    if charset == "braille":
        return 4
    if charset == "blocks":
        return 2
    return 1


def _preview_body_cost(charset: str, color_mode: str) -> int:
    """每格字符在最终 JSON 里的字节数（实测均值）。

    blocks 每格 fg+bg 两条 SGR ≈21B；braille 3 字节 UTF-8 ≈5.4B；非 mono 的
    ascii 每格带 \x1b[38;2;r;g;bm ≈19.7B；mono 纯文本每格 1.04B（按 1 计）。
    """
    if charset == "blocks":
        return 21
    if charset == "braille":
        return 6
    return 20 if color_mode != "mono" else 1


def _preview_clamp_height(charset: str, height: int):
    """纵向截断到 PREVIEW_MAX_ROWS 文本行 → (height, clamped)。

    只截 height 不截 width：横向已由 1-400 入参硬钳兜住，横向再截会改变画面
    比例（预览与本地渲染对不上），纵向截只是少几行。
    """
    mult = _preview_row_mult(charset)
    if height * mult <= PREVIEW_MAX_ROWS:
        return height, False
    return max(1, PREVIEW_MAX_ROWS // mult), True


def _preview_clamp_note(requested_w: int, width: int, requested_h: int,
                        height: int) -> str:
    """只列出真被截掉的维度，如 "height:400>60"（没截的维度不进头，免得读成 400>400）。"""
    parts = []
    if width != requested_w:
        parts.append("width:%d>%d" % (requested_w, width))
    if height != requested_h:
        parts.append("height:%d>%d" % (requested_h, height))
    return ",".join(parts)


def _preview_body_frame_cap(charset: str, width: int, height: int,
                            color_mode: str) -> int:
    """响应体预算允许的帧数（至少 1 帧）。"""
    rows = height * _preview_row_mult(charset)
    per_frame = max(1, width * rows * _preview_body_cost(charset, color_mode))
    return max(1, PREVIEW_BODY_BUDGET // per_frame)


def _embed_body_frame_cap(charset: str, width: int, height: int) -> int:
    """embed 的帧上限：载荷是剥掉 ANSI 的纯文本，每格 ≈1.5B。

    实测每格 0.69B(braille) / 1.03B(ascii 80x24) / 1.44B(blocks 200x60)，与预览
    的 1-21B/格不是同一个成本模型；沿用预览的估算会把 blocks 的帧数砍到 1/10，
    故单独建模。
    """
    rows = height * _preview_row_mult(charset)
    return max(1, PREVIEW_BODY_BUDGET // int(max(1, width * rows * 1.5)))


class _PreviewCache:
    """进程内 LRU：key -> {etag, body, mimetype, cache_control, extra}。

    threading.Lock 是必须的：serve 用 threaded=True，同一 worker 并发处理请求。
    """

    def __init__(self, max_bytes: int, max_entries: int):
        self._lock = threading.Lock()
        self._entries = collections.OrderedDict()
        self._bytes = 0
        self._max_bytes = max_bytes
        self._max_entries = max_entries
        self.hits = 0
        self.misses = 0

    def get(self, key):
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return entry

    def put(self, key, entry) -> None:
        size = len(entry["body"])
        if size > self._max_bytes:
            return  # 单条就超总预算：不缓存（也不挤掉别人）
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._bytes -= len(old["body"])
            self._entries[key] = entry
            self._bytes += size
            while self._entries and (self._bytes > self._max_bytes
                                     or len(self._entries) > self._max_entries):
                _, dropped = self._entries.popitem(last=False)
                self._bytes -= len(dropped["body"])


_PREVIEW_CACHE = _PreviewCache(PREVIEW_CACHE_BYTES, PREVIEW_CACHE_ENTRIES)


def _preview_cache_key(kind: str, work, charset: str, width: int, height: int,
                       color_mode: str, fg_color, bg_color, charset_ramp):
    """(kind, 作品, 渲染参数) → 缓存键。

    params_json + created_at 一并进键：同一 work_id 被重新发布成不同作品时，
    只按 id 取会读到旧渲染。
    """
    sig = json.dumps([work["params_json"], work["created_at"], charset,
                      width, height, color_mode, fg_color, bg_color,
                      charset_ramp], separators=(",", ":"))
    return (kind, work["id"], hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16])


def _preview_cache_response(entry, label):
    """命中缓存 → 200（同一份字节）或 304（If-None-Match 命中）。"""
    etag = entry["etag"]
    inm = request.headers.get("If-None-Match", "")
    fresh = bool(inm) and etag in [part.strip() for part in inm.split(",")]
    if fresh:
        # mimetype 必须显式给：默认 text/html 会撞上 _security_headers 的
        # “HTML 禁缓存”规则，把 304 的 Cache-Control 改写成 no-cache。
        resp = app.response_class(status=304, mimetype=entry["mimetype"])
    else:
        resp = app.response_class(entry["body"], mimetype=entry["mimetype"])
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = entry["cache_control"]
    for name, value in entry["extra"].items():
        resp.headers[name] = value
    resp.headers["X-Preview-Cache"] = "HIT"
    app.logger.info("%s cache HIT work=%s %s bytes=%d", label[0], label[1],
                    "304" if fresh else "200", len(entry["body"]))
    return resp


def _preview_cache_store(key, resp, cache_control: str, extra: dict, label):
    """把 200 响应原样入缓存；ETag 只在这里算一次，命中时零成本复用。

    ETag 取未压缩字节的 sha1（flask-compress 在 after_request 才压，且同一
    URL 的 gzip/identity 两种编码共用该 ETag，靠 Vary: Accept-Encoding 区分）。
    """
    body = resp.get_data()
    etag = '"%s"' % hashlib.sha1(body).hexdigest()[:20]
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = cache_control
    for name, value in extra.items():
        resp.headers[name] = value
    resp.headers["X-Preview-Cache"] = "MISS"
    _PREVIEW_CACHE.put(key, {"etag": etag, "body": body,
                             "mimetype": resp.mimetype,
                             "cache_control": cache_control,
                             "extra": dict(extra)})
    app.logger.info("%s render work=%s bytes=%d", label[0], label[1], len(body))
    return resp

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
            return jsonify({"error": _public_error_text(e)}), 400
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
        frames, total_frames = _subsample_frames(seq, charset, color_mode)
        if len(frames) < total_frames and _preview_payload_too_large(
                1, seq.width, seq.height, charset, color_mode):
            return jsonify({
                "error": "单帧数据过大，请调小列数 / Single frame too large, "
                         "try fewer columns",
                "too_large": True,
            }), 413
        return jsonify({
            "frames": frames,
            "frame_count": len(frames),
            "total_frames": total_frames,
            "subsampled": len(frames) < total_frames,
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
    if not _valid_task_id(task_id):
        return jsonify({"error": "任务不存在 / Task not found"}), 404

    task = _task_get(task_id)
    if task is None:
        return jsonify({"error": "任务不存在 / Task not found"}), 404

    filepath = task.get("filepath")
    interval = task.get("interval") or 0.1
    from termify import paths as _pkg_paths
    if filepath and not os.path.abspath(filepath).startswith(
            os.path.abspath(_pkg_paths.uploads_dir() + os.sep)):
        return jsonify({"error": "任务不存在 / Task not found"}), 404

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
    data = _json_body()
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
    if charset == "custom":
        # Different ramps must not overwrite each other's artifacts.
        digest = hashlib.sha256((charset_ramp or "").encode("utf-8")).hexdigest()[:8]
        stem = f"{task_id}_custom_{digest}{color_tag}"
    else:
        stem = f"{task_id}_{charset}{color_tag}"
    if fmt == "embed":
        # 一行嵌入是另一种产物形态：与整页 HTML 分开命名，互不覆盖
        stem = f"{stem}_embed"
    filename = f"{stem}.{ext}"
    out_path = _tmp_out_path(filename)
    Path(out_path).write_text(content, encoding="utf-8")

    size_bytes = len(content.encode("utf-8"))
    if size_bytes >= 1024:
        file_size = f"{size_bytes // 1024}KB"
    else:
        file_size = f"{size_bytes}B"

    payload = {
        "download_url": f"/api/download/{filename}",
        "file_size": file_size,
    }
    if fmt == "embed":
        # 片段随响应直接给出（前端一键复制），同一份内容也已落盘可下载
        payload["format"] = "embed"
        payload["snippet"] = content
    return jsonify(payload)


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

    reject = _export_budget_reject(width, height, len(seq.lines_per_frame))
    if reject is not None:
        return reject
    if not _VIDEO_PROC_SLOTS.acquire(blocking=False):
        return jsonify({"error": "当前任务较多，请稍后再试"}), 429
    try:
        video_mod.encode_mp4(seq, out_path,
                             audio_path=_task_audio_source(task_id))
    except video_mod.VideoEncodeError as e:
        app.logger.warning("video encode failed: %s", e, exc_info=True)
        return jsonify({"error": f"视频编码失败: {_public_error_text(e)}"}), 500
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


def _gallery_public_dict(work: dict, request_host: str = "",
                         include_frames: bool = True) -> dict:
    """Strip internals before returning to the client.

    include_frames=False 是给列表接口用的（PF-8）：kind=text 作品的
    params.frames 就是整幅艺术字正文，单件最大 14.7KB，24 条列表内联 4 件
    即 31.9KB（limit=60 时 54KB）——而列表消费方 templates/gallery.html 只读
    params.kind 做角标分类，从不用正文。详情接口 /api/gallery/work/<id> 与
    /v/ 页仍原样携带 frames。
    """
    params = json.loads(work["params_json"]) if work["params_json"] else {}
    # 服务器绝对路径（视频作品抽帧目录）绝不外发——进页面源码即泄露部署环境
    params.pop("frames_dir", None)
    if not include_frames:
        params.pop("frames", None)
    return {
        "id": work["id"],
        "title": work["title"],
        "description": work["description"],
        "tags": json.loads(work["tags"]) if work["tags"] else [],
        "author": work["author"],
        "thumbnail_url": url_for("gallery_thumb", work_id=work["id"]),
        "og_url": url_for("gallery_og", work_id=work["id"]),
        "source_url": url_for("gallery_source", work_id=work["id"]),
        "params": params,
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


@gallery_route("/api/gallery/upload", methods=["POST"])
def gallery_upload():
    """Accept multipart upload (source image + JSON params + form fields).

    Returns {ok, id, url, work}; the per-work admin token is only set as an
    httponly cookie (never echoed in the body).
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
    # 自定义标签（T34）：用户自由输入，独立于预设，清洗后与预设合并存储
    custom_raw = request.form.get("custom_tags", "[]")
    try:
        custom_tags = json.loads(custom_raw) if isinstance(custom_raw, str) \
            else custom_raw
    except (json.JSONDecodeError, TypeError):
        custom_tags = []
    tags = tags + _gallery_mod.sanitize_custom_tags(custom_tags)
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
            return jsonify({"error": _public_error_text(exc)}), 400
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
                return jsonify({"error": _public_error_text(exc)}), 422
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
        app.logger.warning("gallery thumbnail failed: %s", exc, exc_info=True)
        return jsonify({"error": "Thumbnail generation failed: "
                                 f"{_public_error_text(exc)}"}), 500

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
    # admin_token 只经 httponly cookie 下发，绝不进响应体（XSS 可读 → 长期接管）
    resp = make_response(jsonify({
        "ok": True,
        "id": work_id,
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


@gallery_route("/api/gallery/custom-tags", methods=["GET"])
def gallery_custom_tags():
    """全站自定义标签热度（公开作品），供画廊「自定义标签」下拉筛选。"""
    return jsonify({"ok": True, "tags": GALLERY_DB.custom_tag_counts()})


@gallery_route("/api/gallery/list", methods=["GET"])
def gallery_list():
    """Paginated list of gallery works.

    Query: sort, tag, tags(逗号分隔多选，命中任意), page, limit.
    Always returns public works only (unless authenticated via cookie).
    """
    sort = request.args.get("sort", "latest")
    if sort not in ("latest", "hot", "random"):
        sort = "latest"
    tag = request.args.get("tag") or None
    tags_param = (request.args.get("tags") or "").strip()
    tags_any = [t.strip() for t in tags_param.split(",") if t.strip()][:10] \
        if tags_param else None
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        limit = max(1, min(60, int(request.args.get("limit", 24))))
    except (TypeError, ValueError):
        limit = 24
    items, total = GALLERY_DB.list_works(sort=sort, tag=tag, page=page, limit=limit,
                                         tags_any=tags_any)
    # Admin cookie check: include is_authorized flag per item
    return jsonify({
        "items": [_gallery_public_dict(w, include_frames=False) for w in items],
        "total": total,
        "page": page,
        "limit": limit,
        "has_more": page * limit < total,
    })


@gallery_route("/api/gallery/work/<work_id>", methods=["GET"])
def gallery_work(work_id):
    """Detail for one work. Bumps view count. Returns params for pre-fill."""
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    if work["is_private"]:
        # Check admin token cookie; else allow view if URL is direct link (private only hides from list)
        pass  # private works are still viewable at /v/<id>
    # 管理口令头也纳入失败计数（原先直接比对 → 一条不限次的布尔 oracle）。
    # 已凭 ownership cookie 认下的访客不再比对口令，也就不会累计失败。
    cookie_ok = _secret_equal(request.cookies.get(f"termify_admin_{work_id}", ""),
                              work["admin_token"])
    hdr_state = "absent" if cookie_ok else _admin_pwd_header_state()
    if hdr_state == "locked":
        return _admin_locked_response(_admin_pwd_wait(_client_ip()) or ADMIN_PWD_FAIL_LOCK)
    GALLERY_DB.increment_view(work_id)
    fresh = GALLERY_DB.get_work(work_id)
    out = _gallery_public_dict(fresh)
    out["is_authorized"] = cookie_ok or hdr_state == "ok"
    # Has the current visitor (IP + cookie) liked this work?
    like_cookie = request.cookies.get(f"termify_like_{work_id}", "")
    if like_cookie:
        out["is_liked"] = GALLERY_DB.has_liked(work_id, _client_ip(), like_cookie)
    else:
        out["is_liked"] = False
    return jsonify(out)


@gallery_route("/api/gallery/like/<work_id>", methods=["POST"])
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


@gallery_route("/api/gallery/report/<work_id>", methods=["POST"])
def gallery_report(work_id):
    """Submit a report. Rate limit 10/day per IP."""
    ip = _client_ip()
    ok, reason = _rate_check(ip, "report", per_day=10)
    if not ok:
        return jsonify({"error": reason}), 429
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    data = _json_body()
    reason_str = data.get("reason", "")
    if reason_str not in _gallery_mod.VALID_REPORT_REASONS:
        return jsonify({"error": f"Invalid reason; expected one of {_gallery_mod.VALID_REPORT_REASONS}"}), 400
    desc = _gallery_mod.sanitize(data.get("description", ""), 300)
    report_id = GALLERY_DB.add_report(work_id, ip, reason_str, desc)
    return jsonify({"ok": True, "report_id": report_id})


@gallery_route("/api/gallery/work/<work_id>", methods=["DELETE"])
def gallery_delete(work_id):
    """Delete a work. Requires valid admin token (cookie or header) or global admin pwd via Header."""
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    ip = _client_ip()
    # 12/min 刻意留在 ADMIN_PWD_FAIL_MAX(10) 之上：否则配额会先于管理口令锁定
    # 报 429，把"猜口令"的退避窗口挡住看不见（而且那个 429 不带 Retry-After）。
    allowed, _reason = _rate_check(ip, "gallery-delete", per_minute=12, per_day=60)
    if not allowed:
        return jsonify({"error": "删除太频繁，请稍后再试 (限 12 次/分钟) / "
                                 "Too many deletes, please try again later (12/min)"}), 429
    token = request.cookies.get(f"termify_admin_{work_id}", "")
    hdr_token = request.headers.get("X-Termify-Admin", "")
    input_token = hdr_token or token
    token_ok = _secret_equal(input_token, work["admin_token"])
    hdr_state = "absent" if token_ok else _admin_pwd_header_state()
    if hdr_state == "locked":
        return _admin_locked_response(_admin_pwd_wait(ip) or ADMIN_PWD_FAIL_LOCK)
    if not (token_ok or hdr_state == "ok"):
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


@app.after_request
def _security_headers(resp):
    """防御纵深响应头。

    CSP 不含 script-src 'unsafe-inline'：页面可执行脚本全部是 static/js 下的
    外置文件，服务端数据经 <script type="application/json"> 过河（不可执行
    类型不受 script-src 管辖）。style-src 仍留 'unsafe-inline'——模板里有大量
    style="--c:#00ff41" 这类内联样式属性，收紧需先迁移样式属性。
    """
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; media-src 'self' data: blob:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'")
    # HTML 页面禁缓存：HTML 引用带版本号的静态资源，HTML 本身一旦被浏览器
    # 启发式缓存，改版后用户会一直拿到旧页面（无版本号变化的旧 JS/CSS），
    # 表现为"修了没生效"。静态资源带 ?v= 版本号，可长缓存。
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# PF-6 静态资源长缓存：Flask 的 send_static_file 把 max_age 取自
# SEND_FILE_MAX_AGE_DEFAULT（默认 None），werkzeug send_file 见 max_age 为 None
# 就无条件写 Cache-Control: no-cache —— 模板里的 ?v= 对缓存策略零影响。
# 不设 SEND_FILE_MAX_AGE_DEFAULT 是刻意的：它是全局的，设了会让 gallery/file/*
# 这类动态 send_file 响应也吃到同一 max-age（误伤），且会顺带写一个与
# Cache-Control 不一致的 Expires。改在 after_request 按"是否带版本参数"分流更精确。
STATIC_VERSIONED_MAX_AGE = 31536000  # 1 年：URL 自带 ?v= 指纹，内容变了 URL 就变
STATIC_PLAIN_MAX_AGE = 86400  # 1 天：裸 static URL 没有指纹，给短值留失效出口

# robots.txt / sitemap.xml：爬虫重取频率低（同站通常数小时到数天一次），一小时
# 既能让新发布的作品在下一轮抓取里出现，也挡掉每次抓取都全量查一次作品库。
CRAWLER_CACHE_CONTROL = "public, max-age=3600"


@app.after_request
def _static_cache_policy(resp):
    """带 ?v= 的静态资源给一年 + immutable；不带版本参数的给一天且不加 immutable。

    只认 endpoint == "static"：/gallery/file/* 等动态路由（缩略图、原图、音频）
    内容会随作品变更，绝不能被打上 immutable。304 也要复用同一套头，否则
    浏览器拿到 304 后会用回自己缓存的旧策略。
    """
    if request.endpoint != "static" or resp.status_code not in (200, 304):
        return resp
    if request.args.get("v"):
        value = f"public, max-age={STATIC_VERSIONED_MAX_AGE}, immutable"
    else:
        value = f"public, max-age={STATIC_PLAIN_MAX_AGE}"
    # setlist 是"整键替换"语义：把同名的所有既有实例一起删掉，只留这一条。
    # werkzeug 的 send_file 在 SEND_FILE_MAX_AGE_DEFAULT 有值时会先自己下发一条
    # Cache-Control（默认 86400），与上面的策略并存就是两条同名头；RFC 9111
    # §4.2 允许缓存方取第一条，一年 immutable 会输给那条短的，?v= 指纹白加。
    resp.headers.setlist("Cache-Control", [value])
    # Expires 只可能是 werkzeug 按 SEND_FILE_MAX_AGE_DEFAULT 算的，与上面的策略
    # 无关；留着就是自相矛盾的过期时间（老客户端与中间缓存会按它判断）。
    if "Expires" in resp.headers:
        del resp.headers["Expires"]
    return resp


# /api/* 一律 no-store：这些响应里是任务状态、用户作品、管理数据，任何中间
# 缓存留存都是错误答案（旧版浏览器按启发式缓存把一次 5xx/旧列表记住过）。只加在
# /api/ 前缀上，静态资源的 max-age 策略见上面的 _static_cache_policy。
# 用 setdefault：显式给出 Cache-Control 的动态路由（source-frames 的 60s 清单、
# 单帧的 immutable）是刻意设计，不能被这条兜底覆盖。
@app.after_request
def _api_cache_no_store(resp):
    """兜底策略：/api/ 下没有明确缓存头的响应一律 no-store。"""
    path = request.path
    if path == "/api" or path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@gallery_route("/api/gallery/admin", methods=["GET"])
def gallery_admin_list():
    """Admin dashboard: list works + pending reports.

    Requires X-Termify-Admin-Pwd header.
    """
    ok, deny = _admin_check()
    if not ok:
        return deny
    GALLERY_DB.anonymize_old_report_ips(days=90)  # 隐私最小化：过期举报人 IP 置空
    works = GALLERY_DB.admin_list_works()
    reports = GALLERY_DB.admin_list_reports(status="pending")
    return jsonify({
        "works": [_gallery_public_dict(w) for w in works],
        "reports": reports,
    })


@gallery_route("/api/gallery/admin/<work_id>", methods=["DELETE"])
def gallery_admin_delete(work_id):
    """Admin hard delete."""
    ok, deny = _admin_check()
    if not ok:
        return deny
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


@gallery_route("/api/gallery/admin/report/<int:report_id>", methods=["POST"])
def gallery_admin_resolve_report(report_id):
    """Mark a report resolved/dismissed."""
    ok, deny = _admin_check()
    if not ok:
        return deny
    data = _json_body()
    status = data.get("status", "resolved")
    if status not in ("resolved", "dismissed"):
        return jsonify({"error": "Invalid status"}), 400
    GALLERY_DB.admin_update_report(report_id, status)
    return jsonify({"ok": True})


# --- gallery preview + download (derived from stored source) ---

@gallery_route("/api/gallery/source-frames/<work_id>", methods=["GET"])
def gallery_source_frames(work_id):
    """Serve stored source frames of a VIDEO gallery work as base64 JPEGs.

    Lets the viewer page render every charset/size client-side (方案B),
    so switching is instant instead of waiting for a server re-render.
    Shape: {ok, w, h, interval, count, frames: ["<b64>", ...]}
    """
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    auth = _work_raw_file_authorized(work)
    if auth is not True:
        if auth is False:
            return jsonify({"error": "Unauthorized"}), 403
        return auth
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


# --- PF-1 分帧下发：首帧不再等 12.1MB 整包 ---
# 整包端点 /source-frames/<id> 是 514 帧 base64 塞一块 JSON（12,133,260B），
# 首帧被末帧的到达绑死（实测 13.8s 白屏）。这里补一条"清单 + 单帧"链路：
# 清单只描述元数据（几百字节），单帧返回 image/jpeg 原始字节。
# 帧目录在作品生成后不再变化，故单帧响应可长期缓存；版本串由首/末帧的
# size+mtime 与帧数算出，帧被重生成时自动变化，客户端缓存随之失效。
# 整包端点保持原样（既有测试与 embed/导出链路依赖它的响应形状）。

_SRC_FRAME_CACHE: "collections.OrderedDict[tuple, bytes]" = collections.OrderedDict()
_SRC_FRAME_CACHE_BYTES = 0
_SRC_FRAME_CACHE_MAX_BYTES = 24 * 1024 * 1024
# threaded=True 下一个作品的帧可能被多条线程同时请求；只锁缓存读写，
# JPEG 编码留在锁外（几十 KB 的编码没必要串行化）。
_SRC_FRAME_LOCK = threading.Lock()


def _source_frames_context(work_id):
    """(work, (paths, original)) 或 (None, error_response)。"""
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return None, (jsonify({"error": "Work not found"}), 404)
    auth = _work_raw_file_authorized(work)
    if auth is not True:
        if auth is False:
            return None, (jsonify({"error": "Unauthorized"}), 403)
        return None, auth
    original = json.loads(work["params_json"]) if work["params_json"] else {}
    if original.get("kind") != "video":
        return None, (jsonify({"error": "Not a video work"}), 400)
    fd = original.get("frames_dir") or ""
    if not fd or not os.path.isdir(fd):
        return None, (jsonify({"error": "Video frames missing"}), 410)
    from termify.video import frames_dir_to_images
    paths = frames_dir_to_images(fd)
    if not paths:
        return None, (jsonify({"error": "Video frames missing"}), 410)
    return work, (paths, original)


def _source_frames_version(paths):
    """帧集版本串：只取首/末帧的 size+mtime + 帧数（514 次 stat 没必要）。"""
    st0 = os.stat(paths[0])
    stn = os.stat(paths[-1])
    sig = "%s|%d|%d|%d|%s|%d|%d" % (
        paths[0], len(paths), st0.st_size, int(st0.st_mtime),
        paths[-1], stn.st_size, int(stn.st_mtime))
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:12]


def _source_frame_jpeg(work_id, paths, index):
    """单帧 JPEG 字节（q80，与整包端点同一编码参数）；进程内 LRU 缓存。"""
    global _SRC_FRAME_CACHE_BYTES
    key = (work_id, index)
    with _SRC_FRAME_LOCK:
        hit = _SRC_FRAME_CACHE.get(key)
        if hit is not None:
            _SRC_FRAME_CACHE.move_to_end(key)
            return hit
    from PIL import Image
    import io as _io
    with Image.open(paths[index]) as im:
        buf = _io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=80)
    data = buf.getvalue()
    with _SRC_FRAME_LOCK:
        _SRC_FRAME_CACHE[key] = data
        _SRC_FRAME_CACHE_BYTES += len(data)
        while _SRC_FRAME_CACHE_BYTES > _SRC_FRAME_CACHE_MAX_BYTES and _SRC_FRAME_CACHE:
            _old_key, old_data = _SRC_FRAME_CACHE.popitem(last=False)
            _SRC_FRAME_CACHE_BYTES -= len(old_data)
    return data


@gallery_route("/api/gallery/source-frames/<work_id>/manifest", methods=["GET"])
def gallery_source_frames_manifest(work_id):
    """分帧下发的清单：{ok, w, h, interval, count, version}（几百字节）。"""
    work, ctx = _source_frames_context(work_id)
    if work is None:
        return ctx
    paths, original = ctx
    from PIL import Image
    with Image.open(paths[0]) as im:
        w, h = im.size
    resp = jsonify({
        "ok": True,
        "w": w,
        "h": h,
        "interval": original.get("interval") or 0.1,
        "count": len(paths),
        "version": _source_frames_version(paths),
    })
    # 清单只有帧元数据 + 版本串，每次请求都要 stat 首末帧（实测 15.8ms）：
    # 给 60 秒客户端缓存，够挡住重复打开作品页的连续请求；version 里带
    # size/mtime，作品重新生成后版本串会变，客户端最迟 1 分钟内自然换新。
    # 不能用 immutable / 长缓存（那样重生成视频后客户端会一直拿旧清单）。
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp


@gallery_route("/api/gallery/source-frames/<work_id>/frame/<int:index>", methods=["GET"])
def gallery_source_frame(work_id, index):
    """单帧 JPEG 原始字节，可长期缓存（ETag 由帧集版本 + 索引构成）。"""
    work, ctx = _source_frames_context(work_id)
    if work is None:
        return ctx
    paths, _original = ctx
    if index >= len(paths):
        return jsonify({"error": "Frame index out of range"}), 404
    etag = "%s-%d" % (_source_frames_version(paths), index)
    resp = Response(_source_frame_jpeg(work_id, paths, index), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = (
        "private, max-age=3600" if work.get("is_private")
        else "public, max-age=31536000, immutable")
    resp.set_etag(etag)
    return resp.make_conditional(request)


@gallery_route("/api/gallery/preview/<work_id>", methods=["GET"])
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
    charset_ramp = None
    if charset == "custom":
        # 自定义字符梯是每请求参数（不入库）：作品页与主页风格卡对齐的关键一环
        raw_ramp = request.args.get("chars", "")
        if not isinstance(raw_ramp, str) or not raw_ramp.strip():
            return jsonify({"error": "custom charset requires a 'chars' ramp",
                            "too_large": False}), 400
        try:
            charset_ramp = _sanitize_ramp(raw_ramp)
        except ValueError:
            return jsonify({"error": "custom charset ramp is empty after "
                                     "cleaning / 自定义字符为空"}), 400
    elif charset not in CHARSETS:
        return jsonify({"error": f"Invalid charset: {charset}"}), 400
    try:
        width = int(request.args.get("width", original.get("width", 80)))
        height = int(request.args.get("height", original.get("height", 24)))
    except (TypeError, ValueError):
        return jsonify({"error": "width/height must be integers"}), 400
    requested_w, requested_h = width, height
    width = max(1, min(400, width))
    height = max(1, min(400, height))

    color_mode, err = _parse_color_mode(
        request.args.get("color", original.get("color")))
    if err:
        return jsonify({"error": err}), 400
    fg_color = _rgb_or_none(request.args.get("fg", original.get("fg")))
    bg_color = _rgb_or_none(request.args.get("bg", original.get("bg")))

    # PF-2 成本闸门：先把纵向截到终端一屏，再按响应体预算限帧。缓存键用
    # 「截断后」的尺寸，同一 URL 永远命中同一份字节。
    height, clamped_rows = _preview_clamp_height(charset, height)
    clamped = clamped_rows or (width, height) != (requested_w, requested_h)
    cache_key = _preview_cache_key("preview", work, charset, width, height,
                                   color_mode, fg_color, bg_color, charset_ramp)
    hit = _PREVIEW_CACHE.get(cache_key)
    if hit is not None:
        return _preview_cache_response(hit, ("gallery-preview", work_id))

    from termify import convert
    # 渲染**前**算帧数并限帧：体积守卫是渲染之后才跑的，事后 413 已经太晚
    # （400 列 × 数百帧能直接把渲染进程 OOM 掉，本地开发服务实测被杀、
    # 页面全线 Failed to fetch）。这里先按「格 × 帧」封顶，渲染量因此有界。
    rows = height * _preview_row_mult(charset)
    cell_budget = max(1, PREVIEW_CELL_BUDGET // max(1, width * rows))
    frame_cap = _preview_max_frames(width, height, charset, color_mode)
    body_cap = _preview_body_frame_cap(charset, width, height, color_mode)

    def _cap(total):
        return max(1, min(cell_budget, frame_cap, body_cap, total))

    src_total = 0

    if original.get("kind") == "piece":
        from termify import pieces as pieces_mod
        data = pieces_mod.read_piece(original.get("piece") or "")
        if not data:
            return jsonify({"error": "Piece data missing"}), 410
        src_total = len(data["frames"])
        try:
            seq = pieces_mod.sequence(
                data, charset, width, height,
                interval=original.get("interval") or pieces_mod.DEFAULT_INTERVAL,
                color_mode=color_mode,
                fg_color=fg_color, bg_color=bg_color,
                charset_ramp=charset_ramp,
                max_frames=_cap(src_total))
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("piece conversion failed: %s", exc)
            return jsonify({"error": "转换失败 / Conversion failed"}), 400
    elif original.get("kind") == "video":
        from termify.video import frames_dir_to_images, sequence_from_frames_dir
        fd = original.get("frames_dir") or ""
        if not fd or not os.path.isdir(fd):
            return jsonify({"error": "Video frames missing"}), 410
        try:
            src_total = len(frames_dir_to_images(fd))
        except Exception:  # noqa: BLE001 — 目录读不了就让渲染自己报错
            src_total = 0
        try:
            seq = sequence_from_frames_dir(fd, charset, width, height,
                                           interval=original.get("interval") or 0.1,
                                           color_mode=color_mode,
                                           fg_color=fg_color, bg_color=bg_color,
                                           charset_ramp=charset_ramp,
                                           max_frames=_cap(src_total or frame_cap))
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("gallery conversion failed: %s", exc)
            return jsonify({"error": "转换失败 / Conversion failed"}), 400
    else:
        try:
            src_total = _source_frame_count(work["source_path"])
        except Exception:  # noqa: BLE001
            src_total = 0
        try:
            seq = convert(work["source_path"], charset, width, height,
                          fg_color=fg_color, bg_color=bg_color,
                          color_mode=color_mode, charset_ramp=charset_ramp,
                          max_frames=_cap(src_total or frame_cap))
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("gallery conversion failed: %s", exc)
            return jsonify({"error": "转换失败 / Conversion failed"}), 400

    frames, _rendered_total = _subsample_frames(seq, charset, color_mode)
    # total_frames 报**素材**帧数（渲染前限帧已经砍过一轮），否则前端
    # 拿不到"原 120 帧 → 预览 62 帧"的提示
    total_frames = src_total or _rendered_total
    if len(frames) < total_frames and _preview_payload_too_large(
            1, seq.width, seq.height, charset, color_mode):
        return jsonify({
            "error": "单帧数据过大，请调小列数 / Single frame too large, "
                     "try fewer columns",
            "too_large": True,
        }), 413
    payload = {
        "frames": frames,
        "frame_count": len(frames),
        "total_frames": total_frames,
        "subsampled": len(frames) < total_frames,
        "interval": seq.interval,
        "width": seq.width,
        "height": seq.height,
        "charset": charset,
    }
    extra = {}
    if clamped:
        # 静默改变画面比变慢更坏：明确回报被截到多少
        payload["clamped"] = True
        payload["requested"] = {"width": requested_w, "height": requested_h}
        extra["X-Preview-Clamped"] = _preview_clamp_note(
            requested_w, width, requested_h, height)
    return _preview_cache_store(cache_key, jsonify(payload),
                                "public, max-age=%d" % PREVIEW_MAX_AGE,
                                extra, ("gallery-preview", work_id))


def _export_budget_reject(width: int, height: int, frames: int):
    """格×帧预算护栏：超限返回 400 响应（含建议列数），通过返回 None。

    大网格 MP4 导出在小规格 ECS 上渲染 + 编码可达 10 分钟级——
    超时不仅失败还会拖死整机（2026-09-07 事故），必须在编码前拒绝。
    """
    from termify.output import video as video_mod

    cells = width * height * frames
    if cells <= video_mod.EXPORT_CELL_BUDGET:
        return None
    sugg = max(60, int(width * (video_mod.EXPORT_CELL_BUDGET / cells) ** 0.5))
    return jsonify({
        "error": f"该尺寸渲染量过大（{width}×{height} × {frames} 帧），"
                 f"服务器无法在时限内完成。请把列数降到 {sugg} 以内，"
                 f"或截短素材后重试 / Too heavy to render server-side; "
                 f"lower columns to ≤ {sugg} or trim the clip"
    }), 400


@gallery_route("/api/gallery/embed-frames/<work_id>", methods=["GET"])
def gallery_embed_frames(work_id):
    """Frames JSON for the one-line embed runtime (contract B).

    Same charset/size/color query params as /api/gallery/preview; the body is
    termify.output.embed.frames_payload(seq) -- plain text rows, ANSI
    stripped -- for <termify-play src="..."> to fetch. 页面里嵌的作品
    不该每次滚动都回源重渲染，所以带 24h 公共缓存头（URL 完全标识内容）。
    """
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    original = json.loads(work["params_json"]) if work["params_json"] else {}
    if original.get("kind") == "text":
        # 文字作品没有帧序列：艺术字已渲染进 params.frames（单帧静态画面），
        # 本端点的职责是"把作品重渲染成可播放的帧"，对它无意义。此前这里
        # 拿作品的 charset="text" 去校验字符集，回的是 400 "Invalid charset:
        # text" —— 把一个"作品类型不支持"的语义错误地塞进"参数写错了"的通道，
        # 调用方只会去翻参数文档。404 才说得清"这个资源没有帧"。
        return jsonify({
            "error": "文字作品没有帧序列 / Text works have no frame sequence",
            "reason": "no_frame_sequence",
            "kind": "text",
        }), 404
    charset = request.args.get("charset", original.get("charset", "blocks")).strip().lower()
    charset_ramp = None
    if charset == "custom":
        raw_ramp = request.args.get("chars", "")
        if not isinstance(raw_ramp, str) or not raw_ramp.strip():
            return jsonify({"error": "custom charset requires a 'chars' ramp"}), 400
        try:
            charset_ramp = _sanitize_ramp(raw_ramp)
        except ValueError:
            return jsonify({"error": "custom charset ramp is empty after "
                                     "cleaning / 自定义字符为空"}), 400
    elif charset not in CHARSETS:
        return jsonify({"error": f"Invalid charset: {charset}"}), 400
    try:
        width = int(request.args.get("width", original.get("width", 80)))
        height = int(request.args.get("height", original.get("height", 24)))
    except (TypeError, ValueError):
        return jsonify({"error": "width/height must be integers"}), 400
    requested_w, requested_h = width, height
    width = max(1, min(400, width))
    height = max(1, min(400, height))

    color_mode, err = _parse_color_mode(
        request.args.get("color", original.get("color")))
    if err:
        return jsonify({"error": err}), 400
    fg_color = _rgb_or_none(request.args.get("fg", original.get("fg")))
    bg_color = _rgb_or_none(request.args.get("bg", original.get("bg")))

    # PF-5：与预览同款闸门 + 同一个进程内缓存（渲染管线本就完全共用）。
    # 命中缓存不渲染任何东西，不该吃「渲染」限频额度，故排在限频闸之前。
    height, clamped_rows = _preview_clamp_height(charset, height)
    clamped = clamped_rows or (width, height) != (requested_w, requested_h)
    cache_key = _preview_cache_key("embed", work, charset, width, height,
                                   color_mode, fg_color, bg_color, charset_ramp)
    hit = _PREVIEW_CACHE.get(cache_key)
    if hit is not None:
        return _preview_cache_response(hit, ("gallery-embed", work_id))

    # 公开只读端点：与预览同一条限频闸，防止被当成免费渲染 API 刷
    allowed, _reason = _rate_check(_client_ip(), "gallery-embed-frames",
                                   per_minute=30)
    if not allowed:
        return jsonify({"error": "操作太频繁，请稍后再试 / Too many requests, "
                                 "please try again later"}), 429

    from termify import convert
    # 与预览同款渲染前封顶：格 × 帧有界，先算再渲染（事后 413 已太晚）
    rows = height * _preview_row_mult(charset)
    cell_budget = max(1, PREVIEW_CELL_BUDGET // max(1, width * rows))
    frame_cap = _preview_max_frames(width, height, charset, color_mode)
    body_cap = _embed_body_frame_cap(charset, width, height)

    def _cap(total):
        return max(1, min(cell_budget, frame_cap, body_cap, total))

    src_total = 0

    if original.get("kind") == "piece":
        from termify import pieces as pieces_mod
        data = pieces_mod.read_piece(original.get("piece") or "")
        if not data:
            return jsonify({"error": "Piece data missing"}), 410
        src_total = len(data["frames"])
        try:
            seq = pieces_mod.sequence(
                data, charset, width, height,
                interval=original.get("interval") or pieces_mod.DEFAULT_INTERVAL,
                color_mode=color_mode,
                fg_color=fg_color, bg_color=bg_color,
                charset_ramp=charset_ramp,
                max_frames=_cap(src_total))
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("embed piece conversion failed: %s", exc)
            return jsonify({"error": "转换失败 / Conversion failed"}), 400
    elif original.get("kind") == "video":
        from termify.video import frames_dir_to_images, sequence_from_frames_dir
        fd = original.get("frames_dir") or ""
        if not fd or not os.path.isdir(fd):
            return jsonify({"error": "Video frames missing"}), 410
        try:
            src_total = len(frames_dir_to_images(fd))
        except Exception:  # noqa: BLE001
            src_total = 0
        try:
            seq = sequence_from_frames_dir(fd, charset, width, height,
                                           interval=original.get("interval") or 0.1,
                                           color_mode=color_mode,
                                           fg_color=fg_color, bg_color=bg_color,
                                           charset_ramp=charset_ramp,
                                           max_frames=_cap(src_total or frame_cap))
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("embed gallery conversion failed: %s", exc)
            return jsonify({"error": "转换失败 / Conversion failed"}), 400
    else:
        try:
            src_total = _source_frame_count(work["source_path"])
        except Exception:  # noqa: BLE001
            src_total = 0
        try:
            seq = convert(work["source_path"], charset, width, height,
                          fg_color=fg_color, bg_color=bg_color,
                          color_mode=color_mode, charset_ramp=charset_ramp,
                          max_frames=_cap(src_total or frame_cap))
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("embed gallery conversion failed: %s", exc)
            return jsonify({"error": "转换失败 / Conversion failed"}), 400

    # 帧数已被 max_frames 封在体积预算内，只剩"单帧自己就超预算"这一种
    # 情况会超载（与 /api/gallery/preview 的判据一致）
    if _preview_payload_too_large(1, seq.width, seq.height, charset, color_mode):
        return jsonify({
            "error": "单帧数据过大，请调小列数 / Single frame too large, "
                     "try fewer columns",
        }), 413

    from termify.output import embed as embed_mod
    resp = app.response_class(embed_mod.frames_payload(seq),
                              mimetype="application/json")
    extra = {}
    if clamped:
        extra["X-Embed-Clamped"] = _preview_clamp_note(
            requested_w, width, requested_h, height)
    kept, natural = len(seq.lines_per_frame), min(cell_budget, frame_cap,
                                                 src_total or frame_cap)
    if kept < natural:
        # 帧被体积闸门砍掉同样不能静默；embed 载荷是纯帧数组、没有元数据字段，
        # 只能在响应头里交代（预览走 JSON 的 frame_count/total_frames）。
        extra["X-Embed-Frames"] = "kept:%d;total:%d" % (kept, natural)
    # Cache-Control 串与旧值逐字符一致（tests/test_t39_embed_export.py:143 断言）
    return _preview_cache_store(cache_key, resp, "public, max-age=86400",
                                extra, ("gallery-embed", work_id))


@gallery_route("/api/gallery/download/<work_id>", methods=["GET"])
def gallery_download(work_id):
    """Generate + serve a .py, .html or .mp4 download for a gallery work.

    Query params:
      charset = ascii|blocks|braille|geometric|binary|shades (default: work's original)
      width, height = int (default: work's original)
      format  = python|html|mp4|embed (required)
      as      = json (embed only: return {snippet, download_url} instead of the file)
    Increments download_count once per IP per 24h.
    """
    work = GALLERY_DB.get_work(work_id)
    if not work:
        return jsonify({"error": "Work not found"}), 404
    fmt = request.args.get("format", "").lower().strip()
    if fmt not in VALID_FORMATS:
        return jsonify({"error": f"Invalid format: {fmt!r} "
                                  f"(expected python, html, mp4 or embed)"}), 400
    original = json.loads(work["params_json"]) if work["params_json"] else {}
    charset = request.args.get("charset", original.get("charset", "blocks")).strip().lower()
    charset_ramp = None
    if charset == "custom":
        # 与预览端点同策略：自定义字符梯是每请求参数（不入库）
        raw_ramp = request.args.get("chars", "")
        if not isinstance(raw_ramp, str) or not raw_ramp.strip():
            return jsonify({"error": "custom charset requires a 'chars' ramp"}), 400
        try:
            charset_ramp = _sanitize_ramp(raw_ramp)
        except ValueError:
            return jsonify({"error": "custom charset ramp is empty after "
                                     "cleaning / 自定义字符为空"}), 400
    elif charset not in CHARSETS:
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
    if original.get("kind") == "piece":
        from termify import pieces as pieces_mod
        data = pieces_mod.read_piece(original.get("piece") or "")
        if not data:
            return jsonify({"error": "Piece data missing"}), 410
        seq = pieces_mod.sequence(
            data, charset, width, height,
            interval=original.get("interval") or pieces_mod.DEFAULT_INTERVAL,
            color_mode=color_mode,
            fg_color=fg_color, bg_color=bg_color,
            charset_ramp=charset_ramp)
    elif original.get("kind") == "video":
        from termify.video import sequence_from_frames_dir
        fd = original.get("frames_dir") or ""
        if not fd or not os.path.isdir(fd):
            return jsonify({"error": "Video frames missing"}), 410
        seq = sequence_from_frames_dir(fd, charset, width, height,
                                       interval=original.get("interval") or 0.1,
                                       color_mode=color_mode,
                                       fg_color=fg_color, bg_color=bg_color,
                                       charset_ramp=charset_ramp)
    else:
        seq = convert(work["source_path"], charset, width, height,
                      fg_color=fg_color, bg_color=bg_color,
                      color_mode=color_mode, charset_ramp=charset_ramp)
    tmp_dir = paths.tmp_dir()
    os.makedirs(tmp_dir, exist_ok=True)

    if fmt == "embed":
        # 引用形态片段：片段本身只指向上面的 embed-frames 端点 —— README 里
        # 贴 data URI 会撑爆仓库、也会被渲染器吞掉，帧交给宿主页面按需取。
        from urllib.parse import urlencode

        from termify.output import embed as embed_mod

        keep = ("charset", "width", "height", "color", "fg", "bg", "chars")
        src_query = urlencode([(k, v) for k, v in request.args.items(multi=True)
                               if k in keep])
        src_url = f"{request.host_url}api/gallery/embed-frames/{work_id}"
        if src_query:
            src_url += "?" + src_query
        snippet = embed_mod.render_reference(seq, src_url)
        if request.args.get("as", "").strip().lower() == "json":
            # 前端要"同一条附件 URL"：原样去掉 as=json 即可
            dl_query = urlencode([(k, v) for k, v in request.args.items(multi=True)
                                  if k != "as"])
            return jsonify({
                "snippet": snippet,
                "download_url": f"/api/gallery/download/{work_id}?{dl_query}",
            })
        filename = f"gallery_{work_id}_{charset}{color_tag}_embed.html"
        out_path = _tmp_out_path(filename, root=tmp_dir)
        Path(out_path).write_text(snippet, encoding="utf-8")
        _record_download(work_id, _client_ip())
        return send_file(out_path, as_attachment=True, download_name=filename)

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
        reject = _export_budget_reject(width, height, len(seq.lines_per_frame))
        if reject is not None:
            return reject
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
            app.logger.warning("video encode failed: %s", exc, exc_info=True)
            return jsonify({"error": "视频编码失败: "
                                     f"{_public_error_text(exc)}"}), 500
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

def _work_raw_file_authorized(work: dict):
    """私有作品的原始媒体直链（source/audio/source-frames）仅创作者/管理员。

    公开作品一律放行；私有作品按"链接分享即预览"模型仅隐藏于列表，
    但原始文件（源视频/音轨/全量抽帧）要求 ownership cookie、创作者
    token 头或全局管理密码之一。

    返回 True / False；管理口令比对纳入失败计数，该 IP 处于退避窗口时返回
    已生成的 429 响应（调用方直接 return 出去）。
    """
    if not work.get("is_private"):
        return True
    work_id = work["id"]
    token = (request.headers.get("X-Termify-Admin", "")
             or request.cookies.get(f"termify_admin_{work_id}", ""))
    if _secret_equal(token, work["admin_token"]):
        return True
    state = _admin_pwd_header_state()
    if state == "locked":
        return _admin_locked_response(_admin_pwd_wait(_client_ip()) or ADMIN_PWD_FAIL_LOCK)
    return state == "ok"


@gallery_route("/gallery/file/<work_id>/source")
def gallery_source(work_id):
    work = GALLERY_DB.get_work(work_id)
    if not work or not os.path.isfile(work["source_path"]):
        abort(404)
    auth = _work_raw_file_authorized(work)
    if auth is False:
        abort(403)
    if auth is not True:
        return auth
    return send_file(work["source_path"])


@gallery_route("/gallery/file/<work_id>/thumb")
def gallery_thumb(work_id):
    work = GALLERY_DB.get_work(work_id)
    if not work or not os.path.isfile(work["thumbnail_path"]):
        abort(404)
    return send_file(work["thumbnail_path"], mimetype="image/gif")


@gallery_route("/gallery/file/<work_id>/og")
def gallery_og(work_id):
    work = GALLERY_DB.get_work(work_id)
    if not work or not os.path.isfile(work["og_path"]):
        abort(404)
    return send_file(work["og_path"], mimetype="image/png")


@gallery_route("/gallery/file/<work_id>/audio")
def gallery_audio(work_id):
    """Stream a video work's extracted audio track (view-page playback)."""
    work = GALLERY_DB.get_work(work_id)
    if not work:
        abort(404)
    auth = _work_raw_file_authorized(work)
    if auth is False:
        abort(403)
    if auth is not True:
        return auth
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

@gallery_route("/gallery")
def gallery_page():
    return render_template("gallery.html")


@gallery_route("/gallery/")
def gallery_page_trailing_slash():
    """尾斜杠归一（D-3）：301 到 /gallery，理由同 /text-art/。"""
    return redirect("/gallery", 301)


@gallery_route("/v/<work_id>")
def gallery_view(work_id):
    work = GALLERY_DB.get_work(work_id)
    if not work:
        abort(404)
    # params 白名单：模板会整体 tojson 进页面，服务器内部字段（如 frames_dir
    # 绝对路径）绝不外发；仅保留回放渲染所需的渲染参数与音频文件名。
    try:
        params = json.loads(work["params_json"]) if work["params_json"] else {}
    except (json.JSONDecodeError, TypeError):
        params = {}
    view_params = {k: params[k] for k in _VIEW_PARAM_KEYS if k in params}
    work = dict(work)
    work["params_json"] = json.dumps(view_params)
    return render_template("view_work.html", work=work)


@gallery_route("/admin")
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

from werkzeug.serving import WSGIRequestHandler


class _QuietRequestHandler(WSGIRequestHandler):
    """Strip the Server response header fingerprint (Werkzeug/Python 版本).

    生产部署仍应置于反向代理之后由其覆写/移除该头。
    """

    server_version = ""
    sys_version = ""


if __name__ == "__main__":
    os.makedirs(paths.uploads_dir(), exist_ok=True)
    os.makedirs(paths.tmp_dir(), exist_ok=True)
    os.makedirs(GALLERY_DATA_DIR, exist_ok=True)
    os.makedirs("data", exist_ok=True)
    # ponytail: reloader off — the task cache is process-local, so a watchdog
    # restart (e.g. on each /api/generate writing tmp/*.py) would just trigger
    # a cache miss. The metadata is durable in SQLite.
    app.run(debug=False, use_reloader=False, port=5000,
        request_handler=_QuietRequestHandler)
