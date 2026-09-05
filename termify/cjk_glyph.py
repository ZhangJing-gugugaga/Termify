"""汉字活字引擎 MVP —— 用 LLM 为单个汉字生成等宽字符画字形（含 SQLite 缓存）。

流程：风格提示词 → LLM 输出 N 行 × M 列的 0x20-0x7E 字符画 → 四道关校验 →
写缓存（data/cjk_glyphs.db）。风格提示词改版时递增 PROMPT_VERSION 即可
让全量缓存自然失效重生成。

设计要点：
- generate_glyph 全程吞异常（logging.warning），3 次重试全败返回 None，
  调用方（cjk_render）用实心块占位，永不把上游错误抛给用户。
- 缓存 miss 时进程内做 generating 去重：同一 (style, char) 的并发请求
  只触发一次 LLM 生成，其余等待后直接读缓存。
- data 目录定位与本模块内常量为准（与 app.py 的 GALLERY_DATA_DIR 语义
  一致：项目根/data），避免脚本/测试循环 import app。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

# 缓存 key 组成部分之一：风格提示词改版时递增（v1 → v2 → ...）。
PROMPT_VERSION = "v1"

# data 目录（项目根/data，与 app.py 的 GALLERY_DATA_DIR 同语义）。
DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_FILENAME = "cjk_glyphs.db"

# 单次生成的 chat 上限：字形极小（≤10 行 × ≤10 列），给闲聊模型留余量即可。
_GLYPH_MAX_TOKENS = 600
# 重试温度阶梯（低→高：先要稳定，再换随机性）。
_RETRY_TEMPERATURES = (0.7, 0.9, 1.0)
# 同 (style, char) 并发生成等待上限（秒）。
_INFLIGHT_TIMEOUT_S = 60.0


def _glyph_system_prompt(height: int, width: int, style_hint: str) -> str:
    """Build the shared system prompt for one glyph style."""
    return (
        "You design monospaced ASCII-art glyphs for single Chinese "
        "characters (hanzi). The user gives you ONE character; draw its "
        "shape as printable ASCII art on a fixed grid. Rules: output "
        f"EXACTLY {height} lines; every line is EXACTLY {width} characters "
        "from the printable ASCII range 0x20-0x7E (spaces count toward the "
        "width); all lines must align on the same monospaced grid to form "
        "one instantly recognisable glyph of that character; do NOT leave "
        "trailing spaces at line ends; do NOT use code fences, markdown, "
        "quotes or any explanation — output ONLY the "
        f"{height} lines of the glyph. Style: {style_hint}."
    )


# ── MVP 三种风格（width 必须为偶数：与中文方块字形的视觉对称性对齐）─────────
GLYPH_STYLES: list[dict] = [
    {
        "slug": "pixel",
        "name": "像素体",
        "height": 8,
        "width": 8,
        "system_prompt": _glyph_system_prompt(
            8, 8,
            "blocky pixel style using dense ink characters such as "
            "# @ % 8 & for strokes"),
    },
    {
        "slug": "brush",
        "name": "笔刷体",
        "height": 10,
        "width": 10,
        "system_prompt": _glyph_system_prompt(
            10, 10,
            "expressive brush-stroke style using strokes like "
            "/ \\ | _ - = ( ) with a hand-drawn feel"),
    },
    {
        "slug": "outline",
        "name": "轮廓体",
        "height": 9,
        "width": 10,
        "system_prompt": _glyph_system_prompt(
            9, 10,
            "thin outline style tracing only the contour with light "
            "characters like . : ; ' ` | / \\ _"),
    },
]

_STYLE_BY_SLUG: dict[str, dict] = {s["slug"]: s for s in GLYPH_STYLES}


def style_by_slug(slug: object) -> dict | None:
    """Return the style dict for ``slug``; None when unknown."""
    if not isinstance(slug, str):
        return None
    return _STYLE_BY_SLUG.get(slug)


# ── 校验：四道关 ─────────────────────────────────────────────────────────────

_PRINTABLE_MIN = 0x20
_PRINTABLE_MAX = 0x7E
_DENSITY_MIN = 0.05   # 非空字符占比下限（低于即近乎空白）
_DENSITY_MAX = 0.40   # 非空字符占比上限（高于即糊成一片）
_MIN_ROWS = 2         # 行数合理下限（单行画不出汉字）
_MAX_ROWS = 64        # 行数合理上限（防失控输出）


def _normalize_rows(rows: list[str], width: int) -> list[str]:
    """Per-row: expand tabs, drop trailing spaces, pad short / trim overwide."""
    out = []
    for row in rows:
        row = row.expandtabs(4).rstrip()
        if len(row) < width:
            row = row + " " * (width - len(row))
        elif len(row) > width:
            row = row[:width]
        out.append(row)
    return out


def validate_glyph(rows: list[str], width: int, *,
                   height: int | None = None) -> tuple[bool, str]:
    """四道关校验一个字形；返回 (ok, 原因)。

    ① 行数与等宽校验：不足 width 的行补空格、超宽 trim，之后所有行必须
       等宽；给出 height 时行数必须严格相等（不给则只做合理范围检查）。
    ② 字符集校验：全部字符落在 0x20-0x7E。
    ③ 密度启发式：非空字符占比 5%-40%，出界即拒。
    ④ 断裂检测：字形中部出现贯穿性的全空列带（该列左右两侧都有非空
       字符）即拒。
    """
    if not isinstance(rows, list) or not rows:
        return False, "字形为空 / glyph is empty"
    if not all(isinstance(r, str) for r in rows):
        return False, "字形行必须是字符串 / glyph rows must be strings"
    if not isinstance(width, int) or width < 2:
        return False, "宽度非法 / invalid width"
    # ① 行数与等宽
    norm = _normalize_rows(rows, width)
    if height is not None:
        if len(norm) != height:
            return False, (f"行数不符（{len(norm)} != {height}）/ wrong row "
                           f"count ({len(norm)} != {height})")
    elif not (_MIN_ROWS <= len(norm) <= _MAX_ROWS):
        return False, (f"行数越界（{len(norm)}）/ row count out of range "
                       f"({len(norm)})")
    if any(len(r) != width for r in norm):  # pad/trim 后防御性复查
        return False, "各行不等宽 / rows are not equal width"
    # ② 字符集
    for r in norm:
        for ch in r:
            if not (_PRINTABLE_MIN <= ord(ch) <= _PRINTABLE_MAX):
                return False, ("含非可打印 ASCII 字符 / non-printable-ASCII "
                               "character found")
    # ③ 密度
    total = len(norm) * width
    ink = sum(1 for r in norm for ch in r if ch != " ")
    density = ink / total if total else 0.0
    if density < _DENSITY_MIN:
        return False, (f"字形过疏（{density:.0%}）/ glyph too sparse "
                       f"({density:.0%})")
    if density > _DENSITY_MAX:
        return False, (f"字形过密（{density:.0%}）/ glyph too dense "
                       f"({density:.0%})")
    # ④ 断裂检测：内部全空列 + 左右两侧均有墨迹 → 贯穿性断裂
    cols_with_ink = [any(r[c] != " " for r in norm) for c in range(width)]
    for c in range(1, width - 1):  # 首尾各留 1 列不扫（边缘留白是合法的）
        if cols_with_ink[c]:
            continue
        left = any(cols_with_ink[:c])
        right = any(cols_with_ink[c + 1:])
        if left and right:
            return False, ("字形中部存在贯穿性断裂 / glyph is broken by a "
                           "full empty column band")
    return True, "ok"


# ── 生成：调 LLM + 重试 ──────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    """Strip markdown code fences (closed block wins, else lone fence lines)."""
    import re

    block = re.compile(r"```[^\n]*\n(.*?)\n?```", re.DOTALL)
    m = block.search(text)
    if m and m.group(1).strip():
        return m.group(1)
    fence = re.compile(r"^[ \t]*(?:```+|~~~+)(.*)$", re.MULTILINE)
    return "\n".join(ln for ln in text.split("\n") if not fence.match(ln))


def _normalize_output(raw: str, width: int) -> list[str]:
    """LLM 原文 → 候选行列表：去围栏、expandtabs、去尾空格、pad/trim 到
    width。字形边缘的全空行是网格的一部分（影响行数校验），只剥纯换行的
    首尾空行（strip("\\n")），不丢空格行；行数不符时由 validate_glyph 拒绝。"""
    if not isinstance(raw, str):
        return []
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    inner = _strip_fences(text).strip("\n")
    if not inner.strip():
        return []
    lines = [ln.rstrip() for ln in inner.expandtabs(4).split("\n")]
    return _normalize_rows(lines, width)


def generate_glyph(ch: str, style: dict, llm_cfg: dict,
                   llm_mod) -> list[str] | None:
    """为一个汉字生成字形：最多重试 3 次（温度 0.7→0.9→1.0）。

    任何异常（网络/格式/校验失败）都吞掉并 logging.warning，3 次全败
    返回 None，绝不向上抛 —— 调用方负责占位降级。
    """
    height, width = style["height"], style["width"]
    messages = [
        {"role": "system", "content": style["system_prompt"]},
        {"role": "user", "content": f"Character: {ch}"},
    ]
    for temp in _RETRY_TEMPERATURES:
        try:
            reply = llm_mod.chat(messages, llm_cfg, temperature=temp,
                                 max_tokens=_GLYPH_MAX_TOKENS)
        except Exception as exc:  # noqa: BLE001 — 网络等异常全部吞掉
            logger.warning("glyph %r temp=%.1f chat failed: %s", ch, temp, exc)
            continue
        if not isinstance(reply, str) or not reply.strip():
            logger.warning("glyph %r temp=%.1f empty reply", ch, temp)
            continue
        rows = _normalize_output(reply, width)
        ok, why = validate_glyph(rows, width, height=height)
        if ok:
            return rows
        logger.warning("glyph %r temp=%.1f rejected: %s", ch, temp, why)
    return None


# ── SQLite 缓存 ──────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS glyphs (
    style_slug     TEXT    NOT NULL,
    codepoint      INTEGER NOT NULL,
    prompt_version TEXT    NOT NULL,
    height         INTEGER NOT NULL,
    width          INTEGER NOT NULL,
    rows           TEXT    NOT NULL,
    quality        REAL    NOT NULL DEFAULT 1.0,
    source         TEXT    NOT NULL DEFAULT 'llm',
    created_at     TEXT    NOT NULL,
    PRIMARY KEY (style_slug, codepoint, prompt_version)
);
"""


def db_path(data_dir: str | None = None) -> str:
    """Cache DB location for a data directory (default: project data/)."""
    return os.path.join(data_dir or DEFAULT_DATA_DIR, DB_FILENAME)


def _connect(path: str) -> sqlite3.Connection:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(_SCHEMA)
    return conn


def _cache_get(ch: str, style_slug: str, path: str) -> dict | None:
    codepoint = ord(ch)
    with _connect(path) as conn:
        cur = conn.execute(
            "SELECT rows, height, width, source FROM glyphs "
            "WHERE style_slug = ? AND codepoint = ? AND prompt_version = ?",
            (style_slug, codepoint, PROMPT_VERSION))
        row = cur.fetchone()
    if row is None:
        return None
    rows, height, width, source = row
    style = style_by_slug(style_slug) or {}
    # 风格尺寸已变 → 旧缓存作废，视为 miss
    if height != style.get("height") or width != style.get("width"):
        return None
    return {"rows": rows.split("\n"), "source": source, "cached": True}


def _cache_put(ch: str, style_slug: str, rows: list[str],
               path: str, quality: float = 1.0) -> None:
    style = style_by_slug(style_slug) or {}
    with _connect(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO glyphs (style_slug, codepoint, "
            "prompt_version, height, width, rows, quality, source, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (style_slug, ord(ch), PROMPT_VERSION, style.get("height", 0),
             style.get("width", 0), "\n".join(rows), quality, "llm",
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))


def is_cached(ch: str, style_slug: str, *,
              data_dir: str | None = None) -> bool:
    """True when a valid cached glyph exists (预热脚本断点续跑用)。"""
    path = db_path(data_dir)
    try:
        return _cache_get(ch, style_slug, path) is not None
    except sqlite3.Error as exc:
        logger.warning("cache read failed for %r/%s: %s", ch, style_slug, exc)
        return False


# 进程内 generating 去重：{(style_slug, codepoint): threading.Event}
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict[tuple[str, int], threading.Event] = {}


def get_or_generate(ch: str, style_slug: str, llm_cfg: dict, llm_mod, *,
                    data_dir: str | None = None) -> dict | None:
    """取字形：缓存命中直接回；miss 则生成并写缓存。

    同 (style, ch) 的并发请求只生成一次：后来者等待首个生成者完成后
    直接读缓存。未知风格抛 ValueError；生成失败返回 None（调用方占位）。
    """
    style = style_by_slug(style_slug)
    if style is None:
        raise ValueError(f"unknown glyph style: {style_slug!r}")
    path = db_path(data_dir)
    try:
        hit = _cache_get(ch, style_slug, path)
    except sqlite3.Error as exc:
        logger.warning("cache read failed for %r/%s: %s", ch, style_slug, exc)
        hit = None
    if hit is not None:
        return hit

    key = (style_slug, ord(ch))
    with _INFLIGHT_LOCK:
        event = _INFLIGHT.get(key)
        is_waiter = event is not None
        if not is_waiter:
            event = threading.Event()
            _INFLIGHT[key] = event
    if is_waiter:
        # 已有同 key 生成在进行：等它落地后读缓存（超时则放弃 → None）
        event.wait(timeout=_INFLIGHT_TIMEOUT_S)
        try:
            return _cache_get(ch, style_slug, path)
        except sqlite3.Error:
            return None

    try:
        rows = generate_glyph(ch, style, llm_cfg, llm_mod)
        if rows is None:
            return None
        try:
            _cache_put(ch, style_slug, rows, path)
        except sqlite3.Error as exc:
            logger.warning("cache write failed for %r/%s: %s",
                           ch, style_slug, exc)
        return {"rows": rows, "source": "llm", "cached": False}
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(key, None)
        event.set()
