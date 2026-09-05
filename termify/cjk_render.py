"""汉字活字引擎 · 混排渲染：多个汉字字形 → 水平拼接 / 超宽换行的文本块。

逐字走 cjk_glyph.get_or_generate（SQLite 缓存 + LLM 生成）；生成失败的
字用实心块 '█' 占位（记入 missing，前端可提示）。拼接规则：字间 2 列
空格间距；总宽超 max_cols 时按可容纳的字数分组换行，行间 1 空行分隔。
最终成品过 textart._tidy_art 的尺寸上限语义检查（超限报 TextArtError）。
"""

from __future__ import annotations

import re

from termify import cjk_glyph
from termify.textart import TextArtError, _tidy_art, art_dims

# 合法汉字范围（MVP 只覆盖基本区）。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

CJK_MIN_CHARS = 1
CJK_MAX_CHARS = 12

# 字间水平间距（列）。
CHAR_GAP = 2
# 换行分组之间的空行数。
GROUP_GAP_ROWS = 1


def filter_cjk(text: object) -> list[str]:
    """从输入中按顺序提取 CJK 汉字，其余字符类型一律剔除。"""
    if not isinstance(text, str):
        return []
    return _CJK_RE.findall(text)


def _placeholder_rows(height: int, width: int) -> list[str]:
    """生成失败时的实心块占位字形（'█' 填充）。"""
    return ["█" * width for _ in range(height)]


def _hjoin(blocks: list[list[str]], gap: int) -> list[str]:
    """等高字形块水平拼接，块间插入 ``gap`` 列空格；每行严格等宽。"""
    out: list[str] = []
    for j in range(len(blocks[0])):
        parts: list[str] = []
        for i, block in enumerate(blocks):
            if i:
                parts.append(" " * gap)
            parts.append(block[j])
        out.append("".join(parts))
    return out


def render_cjk_text(text: str, style_slug: str, llm_cfg: dict, llm_mod, *,
                    max_cols: int = 160) -> dict:
    """把 1-12 个汉字渲染为一幅等宽字符画文本块。

    返回 {art, missing, style, cols, rows}；非法输入 / 尺寸超限抛
    TextArtError（用户可读、双语）。生成失败的字用实心块占位并记入
    missing。未知风格抛 ValueError。
    """
    style = cjk_glyph.style_by_slug(style_slug)
    if style is None:
        raise ValueError(f"unknown glyph style: {style_slug!r}")
    chars = filter_cjk(text)
    if len(chars) < CJK_MIN_CHARS:
        raise TextArtError(
            "请输入至少 1 个汉字 / Enter at least one Chinese character")
    if len(chars) > CJK_MAX_CHARS:
        raise TextArtError(
            f"汉字过多，最多 {CJK_MAX_CHARS} 个 / Too many characters, "
            f"max {CJK_MAX_CHARS}")

    height, width = style["height"], style["width"]
    blocks: list[list[str]] = []
    missing: list[str] = []
    for ch in chars:
        glyph = cjk_glyph.get_or_generate(ch, style_slug, llm_cfg, llm_mod)
        if glyph is None:
            blocks.append(_placeholder_rows(height, width))
            missing.append(ch)
        else:
            blocks.append(glyph["rows"])

    # 超宽换行：每行可容纳 per_line 个字（字间 CHAR_GAP 列间距）。
    per_line = max(1, (max_cols + CHAR_GAP) // (width + CHAR_GAP))
    lines: list[str] = []
    for gi in range(0, len(blocks), per_line):
        group = blocks[gi:gi + per_line]
        if lines:
            lines.extend([""] * GROUP_GAP_ROWS)
        lines.extend(_hjoin(group, CHAR_GAP))
    art = "\n".join(lines)

    # 复用 textart 的尺寸上限语义检查（超宽/过高 → TextArtError）。
    # 只校验不采纳其返回值：_tidy_art 会 rstrip 每行，破坏字形网格的
    # 「每行严格等宽」约束；成品的补空格由上方拼接逻辑保证。
    _tidy_art(art, max_cols=max_cols)
    cols, rows = art_dims(art)
    return {"art": art, "missing": missing, "style": style_slug,
            "cols": cols, "rows": rows}
