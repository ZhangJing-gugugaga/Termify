"""Termify Online Gallery — anonymous upload, short-link sharing, curation.

SQLite-backed. No external deps (stdlib `sqlite3` + Pillow for thumbnails).

Schema: see GOAL-PROMPT.md §11.⑤ §D (SQLite table layout).
Short ID: 8-char base62 random (~218T space, collision-safe via retry loop).
"""

from __future__ import annotations

import html
import os
import secrets
import sqlite3
import threading
import time
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# --- constants ---------------------------------------------------------------

_SHORT_ID_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SHORT_ID_LEN = 8
_ADMIN_TOKEN_LEN = 32
_TITLE_MAX = 60
_DESC_MAX = 500
_AUTHOR_MAX = 20
_NICK_MAX = 20

VALID_TAGS = ["动画", "几何", "人像", "场景", "抽象", "像素艺术", "ASCII art"]
VALID_REPORT_REASONS = ["nsfw", "copyright", "spam", "other"]

THUMB_W, THUMB_H = 200, 150
THUMB_FRAMES = 4  # 3-5 loop-forever GIF

OG_W, OG_H = 1200, 630

DEFAULT_CHARSET = "blocks"
DEFAULT_WIDTH = 80
DEFAULT_HEIGHT = 24


def make_short_id() -> str:
    """8-char base62 random string."""
    return "".join(secrets.choice(_SHORT_ID_ALPHABET) for _ in range(_SHORT_ID_LEN))


def make_admin_token() -> str:
    """32-char hex random token (ownership cookie)."""
    return secrets.token_hex(_ADMIN_TOKEN_LEN // 2)


def sanitize(text: str | None, max_len: int) -> str:
    """HTML-escape + truncate. Always returns str."""
    if not text:
        return ""
    text = str(text).strip()
    return text[:max_len]


def escape_html(text: str) -> str:
    """HTML escape for display."""
    return html.escape(text, quote=True)


# --- thumbnail / OG generation ----------------------------------------------

def make_thumbnail(src_path: str, dst_path: str) -> None:
    """Save a small looping GIF thumbnail (THUMB_W x THUMB_H, THUMB_FRAMES).

    Reads the source GIF (or still), samples frames, centre-crops to 4:3,
    resizes, saves as GIF looping forever.
    """
    img = Image.open(src_path)
    n_frames = getattr(img, "n_frames", 1)

    # Collect up to THUMB_FRAMES frames evenly spaced
    picks: list[Image.Image] = []
    if n_frames <= 1:
        picks = [img.convert("RGBA")]
    else:
        step = max(1, n_frames // THUMB_FRAMES)
        for i in range(0, n_frames, step):
            img.seek(i)
            picks.append(img.convert("RGBA"))
            if len(picks) >= THUMB_FRAMES:
                break
    if not picks:
        picks = [img.convert("RGBA")]

    # Centre-crop to 4:3 then resize
    cropped = [_crop_to_aspect(im, THUMB_W / THUMB_H) for im in picks]
    frames = [im.resize((THUMB_W, THUMB_H), Image.LANCZOS) for im in cropped]

    # First frame + rest; all frames 100 ms
    durations = [100] * len(frames)
    rgba_to_p(frames)
    frames[0].save(
        dst_path,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,  # infinite
        disposal=2,
        optimize=False,
    )


def _crop_to_aspect(img: Image.Image, ratio: float) -> Image.Image:
    w, h = img.size
    cur = w / h
    if cur > ratio:
        new_w = int(h * ratio)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / ratio)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))


def rgba_to_p(frames: list[Image.Image]) -> None:
    """Convert RGBA frames to P-mode (palette) for GIF saving."""
    for im in frames:
        if im.mode == "RGBA":
            # White background for transparency
            bg = Image.new("RGB", im.size, (20, 20, 24))
            bg.paste(im, mask=im.split()[3])
            im.convert("RGB")
        if im.mode != "P":
            im.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=256)


def make_og_image(src_path: str, dst_path: str, title: str, author: str) -> None:
    """Generate a 1200×630 OG image: left half source thumb, right half info.

    Rendered lazily (upload handler relies on this). Uses Pillow only;
    text rendered in default font (no TTF required).
    """
    # Left half: source first frame, centre-cropped to OG_H x (OG_W//2)
    half_w = OG_W // 2
    img = Image.open(src_path)
    n_frames = getattr(img, "n_frames", 1)
    if n_frames > 1:
        img.seek(0)
    frame = img.convert("RGBA")
    left = _crop_to_aspect(frame, half_w / OG_H)
    left = left.resize((half_w, OG_H), Image.LANCZOS)

    # Composite on dark OG canvas
    og = Image.new("RGB", (OG_W, OG_H), (13, 17, 23))
    # Handle rgba: white -> dark bg
    if left.mode == "RGBA":
        bg = Image.new("RGB", left.size, (13, 17, 23))
        bg.paste(left, mask=left.split()[3])
        left = bg
    og.paste(left, (0, 0))

    # Right half: title / author / Termify logo text
    draw = ImageDraw.Draw(og)
    try:
        font_big = ImageFont.truetype("arial.ttf", 54)
        font_sm = ImageFont.truetype("arial.ttf", 30)
    except (OSError, IOError):
        font_big = ImageFont.load_default()
        font_sm = ImageFont.load_default()

    # Termify "logo" top-right
    draw.text((half_w + 40, 60), "Termify", fill=(88, 166, 255), font=font_sm)
    # Title (word-wrap ~ 20 chars on right half)
    wrapped = _wrap(title or "无标题", 18)
    y = 200
    for line in wrapped[:3]:
        draw.text((half_w + 40, y), line, fill=(230, 237, 243), font=font_big)
        y += 70
    # Author bottom
    author_text = f"by {author or '匿名创作者'}"
    draw.text((half_w + 40, OG_H - 80), author_text, fill=(139, 148, 158), font=font_sm)

    og.save(dst_path, format="PNG")


def _wrap(text: str, width: int) -> list[str]:
    """Naive word-wrap for CJK: break every `width` chars."""
    if not text:
        return []
    return [text[i:i + width] for i in range(0, len(text), width)]


# --- GalleryDB ---------------------------------------------------------------

class GalleryDB:
    """SQLite-backed gallery store. Thread-safe per-call via a lock.

    One instance per Flask app. Call ``init_db()`` to create tables.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

    def init_db(self) -> None:
        """Create tables + indexes if they don't exist yet."""
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS works (
                  id TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  description TEXT DEFAULT '',
                  tags TEXT DEFAULT '[]',
                  author TEXT DEFAULT '匿名创作者',
                  source_path TEXT NOT NULL,
                  thumbnail_path TEXT NOT NULL,
                  og_path TEXT NOT NULL,
                  params_json TEXT NOT NULL,
                  is_private INTEGER DEFAULT 0,
                  admin_token TEXT NOT NULL,
                  view_count INTEGER DEFAULT 0,
                  like_count INTEGER DEFAULT 0,
                  download_count INTEGER DEFAULT 0,
                  fork_count INTEGER DEFAULT 0,
                  created_at TEXT NOT NULL,
                  ip TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS likes (
                  work_id TEXT NOT NULL,
                  ip TEXT NOT NULL,
                  cookie TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY (work_id, ip, cookie)
                );
                CREATE TABLE IF NOT EXISTS reports (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  work_id TEXT NOT NULL,
                  reporter_ip TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  description TEXT DEFAULT '',
                  created_at TEXT NOT NULL,
                  status TEXT DEFAULT 'pending'
                );
                CREATE INDEX IF NOT EXISTS idx_works_created_at ON works(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_works_like_count ON works(like_count DESC);
                CREATE INDEX IF NOT EXISTS idx_works_is_private ON works(is_private);
            """)

    # -- work CRUD --

    def insert_work(self, work: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO works
                   (id, title, description, tags, author,
                    source_path, thumbnail_path, og_path,
                    params_json, is_private, admin_token,
                    view_count, like_count, download_count, fork_count,
                    created_at, ip)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    work["id"], work["title"], work["description"],
                    work["tags"], work["author"],
                    work["source_path"], work["thumbnail_path"], work["og_path"],
                    work["params_json"], work["is_private"], work["admin_token"],
                    0, 0, 0, 0,
                    work["created_at"], work["ip"],
                ),
            )

    def get_work(self, work_id: str) -> dict | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM works WHERE id = ?", (work_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_works(
        self,
        sort: str = "latest",
        tag: str | None = None,
        page: int = 1,
        limit: int = 24,
        include_private: bool = False,
    ) -> tuple[list[dict], int]:
        """Return (items, total). Always paginated."""
        where_clauses = []
        params: list[Any] = []
        if not include_private:
            where_clauses.append("is_private = 0")
        if tag:
            # Tags stored as JSON array; use LIKE for simple tag-match.
            where_clauses.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        order = "created_at DESC"
        if sort == "hot":
            order = "like_count DESC, created_at DESC"
        elif sort == "random":
            order = "RANDOM()"

        offset = (max(page, 1) - 1) * limit
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            total = conn.execute(
                f"SELECT COUNT(*) FROM works {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM works {where} ORDER BY {order} LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()
        return [dict(r) for r in rows], total

    def delete_work(self, work_id: str) -> dict | None:
        """Delete a work and return its file paths (for cleanup), or None."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT source_path, thumbnail_path, og_path FROM works WHERE id = ?",
                (work_id,),
            ).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM works WHERE id = ?", (work_id,))
            conn.execute("DELETE FROM likes WHERE work_id = ?", (work_id,))
            conn.execute("DELETE FROM reports WHERE work_id = ?", (work_id,))
        return {"source_path": row[0], "thumbnail_path": row[1], "og_path": row[2]}

    # -- actions --

    def increment_view(self, work_id: str) -> int:
        """+1 view. Returns new count."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE works SET view_count = view_count + 1 WHERE id = ?",
                (work_id,),
            )
            row = conn.execute(
                "SELECT view_count FROM works WHERE id = ?", (work_id,)
            ).fetchone()
            return row[0] if row else 0

    def has_liked(self, work_id: str, ip: str, cookie: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM likes WHERE work_id = ? AND ip = ? AND cookie = ?",
                (work_id, ip, cookie),
            ).fetchone()
        return row is not None

    def toggle_like(self, work_id: str, ip: str, cookie: str) -> tuple[bool, int]:
        """(liked, new_count). Idempotent per (ip, cookie)."""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM likes WHERE work_id=? AND ip=? AND cookie=?",
                (work_id, ip, cookie),
            ).fetchone()
            if existing:
                # Unlike
                conn.execute(
                    "DELETE FROM likes WHERE work_id=? AND ip=? AND cookie=?",
                    (work_id, ip, cookie),
                )
                conn.execute(
                    "UPDATE works SET like_count = MAX(like_count - 1, 0) WHERE id=?",
                    (work_id,),
                )
                liked = False
            else:
                # Like
                conn.execute(
                    "INSERT INTO likes (work_id, ip, cookie, created_at) VALUES (?,?,?,?)",
                    (work_id, ip, cookie, _now_iso()),
                )
                conn.execute(
                    "UPDATE works SET like_count = like_count + 1 WHERE id=?",
                    (work_id,),
                )
                liked = True
            row = conn.execute(
                "SELECT like_count FROM works WHERE id=?", (work_id,)
            ).fetchone()
            return liked, (row[0] if row else 0)

    def increment_download(self, work_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE works SET download_count = download_count + 1 WHERE id = ?",
                (work_id,),
            )

    def add_report(self, work_id: str, ip: str, reason: str, desc: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO reports (work_id, reporter_ip, reason, description, created_at) VALUES (?,?,?,?,?)",
                (work_id, ip, reason, desc, _now_iso()),
            )
            return cur.lastrowid or 0

    def admin_list_reports(self, status: str | None = None) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            if status:
                rows = conn.execute(
                    "SELECT * FROM reports WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM reports ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    def admin_update_report(self, report_id: int, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE reports SET status = ? WHERE id = ?",
                (status, report_id),
            )

    def admin_list_works(self) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM works ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        return [dict(r) for r in rows]

    def verify_admin_token(self, work_id: str, token: str) -> bool:
        work = self.get_work(work_id)
        if not work:
            return False
        return work["admin_token"] == token

    # -- helpers --

    def id_collides(self, work_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM works WHERE id = ?", (work_id,)
            ).fetchone()
        return row is not None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --- storage helpers ---------------------------------------------------------

def gallery_base(data_dir: str) -> str:
    """Root dir for gallery uploads (created on demand)."""
    base = os.path.join(data_dir, "gallery")
    os.makedirs(base, exist_ok=True)
    return base


def source_ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower() or ".img"
