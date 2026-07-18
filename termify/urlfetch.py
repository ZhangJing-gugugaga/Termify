"""URL SSRF protection utilities for Termify."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse
from typing import Optional


# Private / reserved IP ranges that must never be fetched (SSF blocking).
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("fc00::/7"),       # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),      # IPv6 link-local
    ipaddress.ip_network("::1/128"),        # IPv6 loopback
]

ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_CONTENT_TYPES = {"image/gif", "image/png", "image/jpeg", "image/jpg", "image/x-png"}
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


class URLFetchError(Exception):
    """Raised when URL fetch fails validation."""


def validate_url(url: str) -> str:
    """Validate URL scheme + host (SSRF). Returns cleaned URL or raises."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise URLFetchError("Only HTTP / HTTPS URLs are allowed")
    if not parsed.hostname:
        raise URLFetchError("URL has no hostname")

    # Resolve hostname and block private IPs
    try:
        for info in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)):
            addr = info[4][0]
            ip = ipaddress.ip_address(addr)
            for net in _BLOCKED_NETWORKS:
                if ip in net:
                    raise URLFetchError("URLs pointing to internal / private networks are blocked")
    except socket.gaierror:
        raise URLFetchError("Could not resolve hostname")

    return url


def fetch_url_to_temp(url: str, tmp_dir: str = "uploads", timeout: int = 15) -> Optional[str]:
    """Download URL to temp file with full validation. Returns path or raises."""
    import os
    import urllib.request
    import urllib.error
    from PIL import Image

    url = validate_url(url)

    req = urllib.request.Request(url, headers={"User-Agent": "Termify/1.0 (image fetch)"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError) as e:
        raise URLFetchError(f"Download failed: {e}")

    # Content-Type check
    ct = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if ct and ct not in ALLOWED_CONTENT_TYPES:
        raise URLFetchError(f"Unsupported Content-Type: {ct}")

    # Content-Length check
    content_length = resp.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_DOWNLOAD_BYTES:
        raise URLFetchError("Remote file exceeds 20MB limit")

    # Stream download with size cap
    os.makedirs(tmp_dir, exist_ok=True)
    import uuid
    import mimetypes
    ext = mimetypes.guess_extension(ct) or ".img"
    if ext == ".jpeg":
        ext = ".jpg"
    tmp_path = os.path.join(tmp_dir, f"url_{uuid.uuid4().hex[:12]}{ext}")

    total = 0
    with open(tmp_path, "wb") as f:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                f.close()
                os.remove(tmp_path)
                raise URLFetchError("Remote file exceeds 20MB limit during download")
            f.write(chunk)

    # Verify the downloaded file is a valid image
    try:
        with Image.open(tmp_path) as im:
            im.verify()
    except Exception:
        os.remove(tmp_path)
        raise URLFetchError("Downloaded file is not a valid image")

    return tmp_path
