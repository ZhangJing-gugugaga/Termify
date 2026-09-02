"""Video URL fetch — download clips from mainstream video platforms via
yt-dlp, restricted to an explicit domain allowlist (SSRF hardening).

Allowlist covers Bilibili / Douyin / YouTube and their short-link hosts.
Anything else (and every private / loopback address) is rejected before any
network traffic happens.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

# Public video platforms users are allowed to pull from.
ALLOWED_HOSTS = {
    # Bilibili
    "bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv",
    # Douyin
    "douyin.com", "www.douyin.com", "v.douyin.com", "iesdouyin.com",
    "www.iesdouyin.com",
    # YouTube
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "music.youtube.com",
}

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024  # mirrors video import guard


class VideoFetchError(Exception):
    """Raised when a video URL cannot be fetched."""


def validate_video_url(url: str) -> str:
    """Validate scheme + allowlisted host. Returns the URL or raises.

    Only http/https, only hosts on ALLOWED_HOSTS. The allowlist inherently
    excludes localhost / private / reserved addresses.
    """
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise VideoFetchError("仅支持 http/https 链接")
    host = (parsed.hostname or "").lower()
    if not host:
        raise VideoFetchError("链接缺少主机名")
    if host not in ALLOWED_HOSTS:
        raise VideoFetchError(
            f"不支持的平台: {host}（目前支持 Bilibili / 抖音 / YouTube 链接）"
        )
    return url


def is_video_platform_url(url: str) -> bool:
    """Loose check used by the frontend router and the shared URL endpoint."""
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def download_video(url: str, dest_dir: str | None = None) -> str:
    """``dest_dir`` 缺省走 termify.paths.uploads_dir()（仓库根锚定）。"""
    """Download a video from an allowlisted platform URL via yt-dlp.

    Returns the server-generated bare file name (never derived from remote
    data): yt-dlp output is merged to an mp4 container and renamed. Raises
    VideoFetchError on failure.
    """
    url = validate_video_url(url)
    try:
        import yt_dlp
    except ImportError:
        raise VideoFetchError("服务器未安装 yt-dlp，无法解析视频链接")

    if dest_dir is None:
        from termify.paths import uploads_dir
        dest_dir = uploads_dir()
    if ".." in dest_dir or os.sep in dest_dir:
        raise VideoFetchError("unsafe destination directory")

    token = uuid.uuid4().hex[:12]
    out_tmpl = f"urlvid_{token}.%(ext)s"
    opts = {
        "outtmpl": out_tmpl,
        "format": "best[ext=mp4][filesize<"
                  f"{MAX_DOWNLOAD_BYTES}]/best[filesize<{MAX_DOWNLOAD_BYTES}]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "max_filesize": MAX_DOWNLOAD_BYTES,
        "socket_timeout": 20,
        "retries": 2,
        # Work inside dest_dir only; no remote-controlled path components.
        "paths": {"home": dest_dir},
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise VideoFetchError("无法解析该链接")
            prepared = ydl.prepare_filename(info)
    except VideoFetchError:
        raise
    except Exception as exc:  # yt_dlp raises a zoo of exception types
        raise VideoFetchError(f"视频下载失败: {exc}") from exc

    final_name = f"urlvid_{token}.mp4"
    final_path = os.path.join(dest_dir, final_name)
    if prepared and os.path.isfile(prepared):
        if os.path.abspath(prepared) != os.path.abspath(final_path):
            os.replace(prepared, final_path)
    if not os.path.isfile(final_path):
        raise VideoFetchError("视频下载失败: 输出文件不存在")
    if os.path.getsize(final_path) > MAX_DOWNLOAD_BYTES:
        os.remove(final_path)
        raise VideoFetchError("视频超过 200MB 限制")
    return final_name
