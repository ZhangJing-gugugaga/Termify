"""TaskStore — SQLite-backed task metadata + per-worker conversion cache.

L1 production-bug fix: under gunicorn (4 workers), the legacy module-level
``TASKS`` dict in ``app.py`` lived in each worker's private memory, so a
task created in worker A was invisible to workers B/C/D. This module splits
task state into two layers:

1. **Metadata** (filepath / size / frames_count / interval / created_at)
   persisted to a SQLite table ``tasks`` so every worker sees the same
   truth source. SQLite's own file locking keeps concurrent workers safe.

2. **Cache** (FrameSequence objects keyed by
   ``"<task_id>:<charset>:<width>x<height>:<fg>:<bg>"``) kept in a
   process-local dict. Cache misses are non-fatal: we re-compute from
   ``metadata.filepath`` via ``termify.convert()``. Repeated computation
   is the cost we pay for staying simple and avoiding a network cache.
   The cache is capped at ``CACHE_MAX_ENTRIES`` (128) with oldest-insertion
   eviction, so long-running workers cannot grow it without bound.

A background thread sweeps expired rows every 5 minutes; on app boot the
store also pre-cleans anything past its TTL.

Public API
----------
- ``TaskStore(db_path, ttl_seconds=3600)`` — main class.
- ``task_store`` — module-level singleton, initialised in :func:`get_store`.
- ``put(task_id, **fields)`` — INSERT OR REPLACE.
- ``get(task_id)`` — SELECT, returns dict or None.
- ``delete(task_id)`` — DELETE.
- ``get_or_404(task_id)`` — tuple ``(task_dict | None, (json, 404))``.
- ``cache_get(task_id, key)``, ``cache_put(task_id, key, seq)``,
  ``cache_key(...)`` — per-worker cache helpers.
- ``sweep_expired()`` — DELETE rows whose ``ttl_until`` is in the past,
  and best-effort remove their on-disk artifacts (uploads/ source files /
  frame dirs + tmp/ ``<task_id>_*`` downloads).
- ``init()`` — idempotent: creates tables, sweeps expired rows, starts
  the background sweep thread.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from typing import Any, Iterable

# Default TTL for an uploaded task. After this many seconds, the row is
# eligible for sweep. Default 1 hour matches the existing user-visible
# expectation that conversions are short-lived.
DEFAULT_TTL_SECONDS = 3600

# How often the background sweeper runs.
SWEEP_INTERVAL_SECONDS = 5 * 60

# 产物根目录统一走 termify.paths（仓库根锚定，TERMIFY_BASE_DIR 可覆盖）。
# 此前这里的 UPLOADS_DIR="uploads" / TMP_DIR="tmp" CWD 相对常量已移除——
# CWD 漂移（systemd / PyInstaller / 隔离部署）时写入与读取会断裂。
from termify.paths import base_dir
from termify.paths import tmp_dir as _artifact_tmp_dir
from termify.paths import uploads_dir as _artifact_uploads_dir


# Module-level cache. Each gunicorn worker has its own copy of this dict;
# that's intentional — see module docstring. 用 OrderedDict 承载，最多保留
# CACHE_MAX_ENTRIES 条，超出时按插入序淘汰最旧条目——长驻进程里
# FrameSequence 不再无界增长（内存上限保护）。
CACHE: "OrderedDict[str, Any]" = OrderedDict()
CACHE_MAX_ENTRIES = 128
_CACHE_LOCK = threading.Lock()


class TaskStore:
    """SQLite-backed task metadata store with TTL and process-local cache.

    Thread-safe per call (uses an internal lock for write paths). SQLite
    itself coordinates cross-process writes via the file lock + WAL.
    """

    def __init__(self, db_path: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.db_path = db_path
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._sweep_thread: threading.Thread | None = None
        self._sweep_stop = threading.Event()
        self._sweep_hook = None
        self._initialised = False
        # Ensure parent dir exists
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def set_sweep_hook(self, hook) -> None:
        """Register a zero-arg callable run alongside every background sweep.

        app.py 用它把 ``_sweep_stale_frame_dirs``（uploads/ 帧目录 +
        tmp/gallery_* 下载产物的 24h TTL 清理）挂进 sweep 循环——那些产物
        不归 TaskStore 管，但寿命管理是同一件事。
        """
        self._sweep_hook = hook

    # --- connection -----------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a new SQLite connection with sane pragmas."""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # --- schema ---------------------------------------------------------

    def init_db(self) -> None:
        """Create the ``tasks`` table + TTL index if absent. Idempotent.

        Uses ``DROP TABLE IF EXISTS`` + ``CREATE TABLE`` (not
        ``CREATE TABLE IF NOT EXISTS``) so that schema corrections
        (e.g. making ``filepath`` nullable) are applied even when the
        DB file already contains an older version of the table.  The
        ``_initialised`` flag ensures this runs at most once per process,
        so data loss between gunicorn workers on start-up is not a
        practical concern (no requests are served until *all* workers
        are ready).  Once deployed, ``_initialised`` prevents future
        startup scans from touching the table.
        """
        with self._lock:
            if self._initialised:
                return
            with self._connect() as conn:
                conn.execute("DROP TABLE IF EXISTS tasks")
                conn.executescript(
                    """
                    CREATE TABLE tasks (
                        task_id TEXT PRIMARY KEY,
                        filepath TEXT,
                        original_size_w INTEGER,
                        original_size_h INTEGER,
                        target_size_w INTEGER,
                        target_size_h INTEGER,
                        frames_count INTEGER,
                        interval REAL,
                        created_at REAL,
                        ttl_until REAL
                    );
                    CREATE INDEX IF NOT EXISTS idx_tasks_ttl
                        ON tasks(ttl_until);
                    """
                )
            self._initialised = True

    # --- write ----------------------------------------------------------

    def put(
        self,
        task_id: str,
        *,
        filepath: str,
        original_size: dict | tuple | list | None = None,
        target_size: dict | tuple | list | None = None,
        frames_count: int | None = None,
        interval: float | None = None,
        ttl_seconds: int | None = None,
        now: float | None = None,
    ) -> None:
        """INSERT OR REPLACE a task row. ``original_size`` / ``target_size``
        accept either a ``{"width": w, "height": h}`` dict or a ``(w, h)``
        2-tuple. Other fields are stored verbatim.
        """
        if not task_id:
            raise ValueError("task_id is required")
        if not filepath and filepath is not None:
            # Note: filepath can legitimately be None for video tasks
            # (the file has been cleaned up after frame extraction).
            raise ValueError("filepath must be a string or None")

        ow, oh = _size_to_w_h(original_size)
        tw, th = _size_to_w_h(target_size)
        ts = time.time() if now is None else now
        ttl = ts + (ttl_seconds if ttl_seconds is not None else self.ttl_seconds)

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks
                  (task_id, filepath,
                   original_size_w, original_size_h,
                   target_size_w, target_size_h,
                   frames_count, interval,
                   created_at, ttl_until)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task_id,
                    filepath,
                    ow, oh, tw, th,
                    frames_count, interval,
                    ts, ttl,
                ),
            )

    # --- read -----------------------------------------------------------

    def get(self, task_id: str) -> dict | None:
        """Return metadata dict for ``task_id`` or ``None`` if absent.

        The returned dict contains:

        - ``task_id``
        - ``filepath`` (str | None)
        - ``original_size`` = ``{"width": w, "height": h}`` (None if either
          dimension is missing — for video tasks we use a sentinel of
          both None).
        - ``target_size`` = same shape as above.
        - ``frames_count`` (int | None)
        - ``interval`` (float | None)
        - ``created_at`` (float, unix seconds)
        - ``ttl_until`` (float, unix seconds)
        """
        if not task_id:
            return None
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return _row_to_task(row)

    def exists(self, task_id: str) -> bool:
        """Cheap existence check (no row materialisation)."""
        if not task_id:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM tasks WHERE task_id = ? LIMIT 1", (task_id,)
            ).fetchone()
        return row is not None

    # --- delete ---------------------------------------------------------

    def delete(self, task_id: str) -> bool:
        """Delete ``task_id`` row. Returns True iff a row was removed."""
        if not task_id:
            return False
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            return cur.rowcount > 0

    def sweep_expired(self, *, now: float | None = None) -> int:
        """DELETE rows whose ``ttl_until < now``. Returns row count removed.

        被删行的磁盘产物一并做 best-effort 清理：

        - ``filepath`` 落在 uploads/ 内（直接子级，包含检查后）的源文件
          或视频帧目录；
        - tmp/ 下以 ``<task_id>_`` 为前缀的下载产物（.py/.html/.mp4）。

        所有删除都包在 try/except OSError 里——清理失败只放弃该文件，
        绝不影响 sweep 本身。
        """
        ts = time.time() if now is None else now
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT task_id, filepath FROM tasks WHERE ttl_until < ?",
                (ts,),
            ).fetchall()
            cur = conn.execute("DELETE FROM tasks WHERE ttl_until < ?", (ts,))
            removed = cur.rowcount
        for task_id, filepath in rows:
            self._cleanup_task_artifacts(task_id, filepath)
        return removed

    def _cleanup_task_artifacts(self, task_id: str, filepath: str | None) -> None:
        """Best-effort delete one expired task's on-disk artifacts.

        任何 OSError（文件已消失/被占用/权限不足）都被吞掉——清理失败
        绝不能影响 sweep 本身。
        """
        if filepath:
            _remove_within_dir(_artifact_uploads_dir(), filepath)
        if task_id:
            tmp_root = _artifact_tmp_dir()
            try:
                names = os.listdir(tmp_root)
            except OSError:
                names = []
            marker = f"{task_id}_"
            for name in names:
                if not name.startswith(marker):
                    continue
                _remove_within_dir(tmp_root, os.path.join(tmp_root, name))

    # --- background sweeper --------------------------------------------

    def start_sweeper(self) -> None:
        """Start the periodic TTL sweep in a daemon thread.

        Safe to call multiple times — subsequent calls are no-ops.
        """
        if self._sweep_thread is not None and self._sweep_thread.is_alive():
            return
        self._sweep_stop.clear()

        def _loop() -> None:
            while not self._sweep_stop.wait(SWEEP_INTERVAL_SECONDS):
                try:
                    self.sweep_expired()
                except Exception:  # noqa: BLE001 — keep thread alive
                    # Logging would be nice but we want zero import cost;
                    # failing silently is OK because the next sweep will
                    # try again. We deliberately don't propagate to avoid
                    # noisy stderr under flaky disk conditions.
                    pass
                hook = self._sweep_hook
                if hook is not None:
                    try:
                        hook()
                    except Exception:  # noqa: BLE001 — keep thread alive
                        pass

        t = threading.Thread(target=_loop, name="TaskStoreSweeper", daemon=True)
        t.start()
        self._sweep_thread = t

    def stop_sweeper(self) -> None:
        """Stop the periodic sweeper. Mostly useful in tests."""
        self._sweep_stop.set()

    # --- context manager for lifecycle ---------------------------------

    def close(self) -> None:
        """Stop background thread (does NOT close the DB file)."""
        self.stop_sweeper()


# ---------------------------------------------------------------------------
# Public helpers exposed to app.py
# ---------------------------------------------------------------------------

_STORE: TaskStore | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> TaskStore:
    """Return the process-wide ``TaskStore`` singleton.

    Lazily initialised using ``TERMIFY_TASK_DB`` env var (defaults to
    ``<data>/tasks.db``). Initialisation creates the schema, sweeps
    expired rows once, and starts the background sweeper.
    """
    global _STORE
    if _STORE is not None:
        return _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            return _STORE
        db_path = os.environ.get(
            "TERMIFY_TASK_DB",
            os.path.join(base_dir(), "data", "tasks.db"),
        )
        store = TaskStore(db_path)
        store.init_db()
        # Pre-clean any rows that expired while the service was down.
        store.sweep_expired()
        store.start_sweeper()
        _STORE = store
        return _STORE


def reset_store_for_tests() -> None:
    """Drop the cached singleton so a test can re-initialise with a
    different ``TERMIFY_TASK_DB``. Stops the sweeper if running.
    """
    global _STORE
    with _STORE_LOCK:
        if _STORE is not None:
            _STORE.close()
        _STORE = None


# --- per-task helpers -------------------------------------------------------

def cache_key(task_id: str, charset: str, width: int, height: int,
              fg: Any = None, bg: Any = None, charset_ramp: Any = None) -> str:
    """Build the cache key for a converted sequence.

    ``fg`` / ``bg`` are either an ``(r, g, b)`` 3-tuple or ``None``.
    ``charset_ramp`` (custom charset) is hashed so different ramps of the
    same task never collide.
    """
    fg_part = _color_part(fg)
    bg_part = _color_part(bg)
    ramp_part = "none"
    if charset_ramp:
        ramp_part = hashlib.sha256(str(charset_ramp).encode("utf-8")).hexdigest()[:12]
    return f"{task_id}:{charset}:{width}x{height}:{fg_part}:{bg_part}:{ramp_part}"


def _color_part(color: Any) -> str:
    if color is None:
        return "none"
    if isinstance(color, (list, tuple)) and len(color) == 3:
        try:
            r, g, b = (int(color[0]), int(color[1]), int(color[2]))
        except (TypeError, ValueError):
            return "none"
        if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
            return "none"
        return f"rgb({r},{g},{b})"
    return "none"


def cache_get(task_id: str, key: str) -> Any:
    """Return cached FrameSequence for ``(task_id, key)`` or None."""
    full = f"{task_id}:{key}"
    with _CACHE_LOCK:
        return CACHE.get(full)


def cache_put(task_id: str, key: str, seq: Any) -> None:
    """Cache ``seq`` under ``(task_id, key)``. No-op if ``seq`` is None.

    超过 ``CACHE_MAX_ENTRIES`` 时按插入序淘汰最旧条目；对已存在 key 的
    重新 put 视为最新插入（移到队尾）。
    """
    if seq is None:
        return
    full = f"{task_id}:{key}"
    with _CACHE_LOCK:
        if full in CACHE:
            CACHE.move_to_end(full)
        CACHE[full] = seq
        while len(CACHE) > CACHE_MAX_ENTRIES:
            CACHE.popitem(last=False)


def cache_clear_task(task_id: str) -> None:
    """Drop all cache entries for a task (used by tests / on delete)."""
    prefix = f"{task_id}:"
    with _CACHE_LOCK:
        for k in list(CACHE.keys()):
            if k.startswith(prefix):
                CACHE.pop(k, None)


def cache_clear_all() -> None:
    """Drop the entire process-local cache (test helper)."""
    with _CACHE_LOCK:
        CACHE.clear()


# --- internal helpers -------------------------------------------------------

def _remove_within_dir(base_dir: str, path: str) -> None:
    """Delete a file/dir only when it provably sits directly inside base_dir.

    参照 app.py ``_safe_remove_upload`` 的加固模式：解析为绝对路径后要求
    父目录恰为 base（不递归子目录）、无 ``..`` 段，然后才删除；目录用
    rmtree，文件/链接用 os.remove。任何 OSError 一律吞掉。
    """
    import shutil

    base = os.path.abspath(base_dir)
    resolved = os.path.abspath(path)
    if os.path.dirname(resolved) != base:
        return
    if not resolved.startswith(base + os.sep):
        return
    if ".." in resolved:
        return
    try:
        if os.path.isdir(resolved):
            shutil.rmtree(resolved, ignore_errors=True)
        elif os.path.isfile(resolved) or os.path.islink(resolved):
            os.remove(resolved)
    except OSError:
        pass


def _size_to_w_h(value: Any) -> tuple[int | None, int | None]:
    """Normalise a size input to ``(width, height)`` 2-tuple of ints.

    Accepts ``None``, ``(w, h)`` tuple/list, ``{"width": w, "height": h}``.
    """
    if value is None:
        return (None, None)
    if isinstance(value, dict):
        w = value.get("width")
        h = value.get("height")
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        w, h = value[0], value[1]
    else:
        return (None, None)
    try:
        return (int(w) if w is not None else None,
                int(h) if h is not None else None)
    except (TypeError, ValueError):
        return (None, None)


def _row_to_task(row: sqlite3.Row) -> dict:
    """Map a raw SQLite row to the canonical task dict shape."""
    ow, oh = row["original_size_w"], row["original_size_h"]
    tw, th = row["target_size_w"], row["target_size_h"]
    return {
        "task_id": row["task_id"],
        "filepath": row["filepath"],
        "original_size": (
            {"width": ow, "height": oh} if (ow is not None and oh is not None)
            else None
        ),
        "target_size": (
            {"width": tw, "height": th} if (tw is not None and th is not None)
            else None
        ),
        "frames_count": row["frames_count"],
        "interval": row["interval"],
        "created_at": row["created_at"],
        "ttl_until": row["ttl_until"],
    }


# Public re-export for convenience in app.py
__all__ = [
    "TaskStore",
    "CACHE",
    "CACHE_MAX_ENTRIES",
    "DEFAULT_TTL_SECONDS",
    "SWEEP_INTERVAL_SECONDS",
    "get_store",
    "reset_store_for_tests",
    "cache_key",
    "cache_get",
    "cache_put",
    "cache_clear_task",
    "cache_clear_all",
]
