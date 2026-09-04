"""Text → ASCII art (FIGlet 直转 + LLM 直接创作的归一化).

直转路径对齐 lddgo/figlet 语义：非 ASCII 字符被忽略（而不是报错），
FIGlet 负责字形与 smushing；LLM 直接创作路径只做安全归一化（去代码围栏、
统一缩进、剥离控制字符），不改动艺术内容。
"""

from __future__ import annotations

import re

import pyfiglet


class TextArtError(ValueError):
    """ Raised with a bilingual, user-safe message (no internal details)."""


# 精选字体：(展示名, pyfiglet slug)。slug 不存在时 curated_fonts() 会过滤。
CURATED_FONTS: list[tuple[str, str]] = [
    ("ANSI Shadow", "ansi_shadow"),
    ("Standard", "standard"),
    ("Big", "big"),
    ("Colossal", "colossal"),
    ("Slant", "slant"),
    ("Small", "small"),
    ("Doom", "doom"),
    ("Block", "block"),
    ("Banner3", "banner3"),
    ("Ghost", "ghost"),
    ("Graffiti", "graffiti"),
    ("Bloody", "bloody"),
    ("Ogre", "ogre"),
    ("Poison", "poison"),
    ("Star Wars", "starwars"),
    ("Fire Font-s", "fire_font-s"),
    ("Larry 3D", "larry3d"),
    ("Nancyj", "nancyj"),
    ("Impossible", "impossible"),
    ("Isometric1", "isometric1"),
    ("Sub-Zero", "sub-zero"),
    ("Calvin S", "calvin_s"),
    ("Delta Corps Priest 1", "delta_corps_priest_1"),
    ("Js Stick Letters", "js_stick_letters"),
]

DEFAULT_FONT = "standard"

TEXT_MAX_CHARS = 64          # FIGlet 输入字符上限（过滤非 ASCII 之后）
DEFAULT_LINE_WIDTH = 120     # FIGlet 自动换行宽度（列）
MIN_LINE_WIDTH = 40
MAX_LINE_WIDTH = 300

MAX_ART_COLS = 200           # LLM 直接创作 / 入库作品的最大列
MAX_ART_ROWS = 120           # ……与最大行
AI_DIRECT_MAX_COLS = 120     # 提示词要求的创作宽度（归一化硬上限仍是上面值）
AI_DIRECT_MAX_ROWS = 60

_FENCE_RE = re.compile(r"^[ \t]*(?:```+|~~~+)(.*)$")
_FIGLET_FONT_SLUGS: set[str] | None = None


def curated_fonts() -> list[dict]:
    """Curated font list, filtered to fonts actually installed."""
    global _FIGLET_FONT_SLUGS
    if _FIGLET_FONT_SLUGS is None:
        try:
            _FIGLET_FONT_SLUGS = set(pyfiglet.FigletFont.getFonts())
        except Exception:  # noqa: BLE001 — pyfiglet 资源异常时降级为空
            _FIGLET_FONT_SLUGS = set()
    return [{"name": name, "slug": slug}
            for name, slug in CURATED_FONTS if slug in _FIGLET_FONT_SLUGS]


def known_font(slug: object) -> bool:
    return isinstance(slug, str) and any(f["slug"] == slug for f in curated_fonts())


def _figlet_available(slug: str) -> bool:
    global _FIGLET_FONT_SLUGS
    if _FIGLET_FONT_SLUGS is None:
        curated_fonts()
    return slug in (_FIGLET_FONT_SLUGS or set())


def filter_figlet_text(text: object) -> str:
    """lddgo 语义：非 ASCII 字符直接忽略（中文不会报错，只会消失）。"""
    if not isinstance(text, str):
        return ""
    kept = []
    for ch in text:
        code = ord(ch)
        if 0x20 <= code <= 0x7E or ch in "\r\n\t":
            kept.append(ch)
    # 换行/制表对 FIGlet 无意义（renderText 会逐行渲染），压成空格
    return re.sub(r"[\r\n\t]+", " ", "".join(kept)).strip()


def render_figlet(text: object, font: object = DEFAULT_FONT,
                  line_width: object = DEFAULT_LINE_WIDTH) -> str:
    """Render ``text`` with a curated FIGlet font; returns the art (no
    trailing blank lines, trailing spaces stripped per line).

    Raises TextArtError with a bilingual message on invalid input.
    """
    clean = filter_figlet_text(text)
    if not clean:
        raise TextArtError(
            "请输入英文/数字内容（中文及符号会被忽略）"
            " / Please enter English letters or digits (non-ASCII ignored)")
    if len(clean) > TEXT_MAX_CHARS:
        raise TextArtError(
            f"文字过长，最多 {TEXT_MAX_CHARS} 个字符 / Text too long, "
            f"max {TEXT_MAX_CHARS} characters")
    slug = font if known_font(font) else DEFAULT_FONT
    try:
        width = int(line_width)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        width = DEFAULT_LINE_WIDTH
    width = max(MIN_LINE_WIDTH, min(MAX_LINE_WIDTH, width))
    if not _figlet_available(slug):
        raise TextArtError("字体不可用 / Font unavailable")
    try:
        art = pyfiglet.Figlet(font=slug, width=width).renderText(clean)
    except Exception as exc:  # noqa: BLE001 — 字体渲染异常不外泄细节
        raise TextArtError(
            "生成失败，请换一段文字或字体 / Failed to render, try other "
            "text or font") from exc
    return _tidy_art(art)


def _tidy_art(art: str, *, max_cols: int = MAX_ART_COLS,
              max_rows: int = MAX_ART_ROWS) -> str:
    """Strip trailing blanks/space padding, enforce dimension caps."""
    lines = [ln.rstrip() for ln in art.replace("\r\n", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        raise TextArtError("生成结果为空 / Empty result")
    cols = max(len(ln) for ln in lines)
    if cols > max_cols:
        raise TextArtError(
            f"结果过宽（{cols} 列 > {max_cols}），请增大行宽或缩短文字"
            f" / Result too wide ({cols} > {max_cols} columns)")
    if len(lines) > max_rows:
        raise TextArtError(
            f"结果过高（{len(lines)} 行 > {max_rows}）/ Result too tall "
            f"({len(lines)} > {max_rows} rows)")
    return "\n".join(lines)


def art_dims(art: str) -> tuple[int, int]:
    """(cols, rows) of a tidied art string."""
    lines = art.split("\n")
    return (max((len(ln) for ln in lines), default=0), len(lines))


# ── 字体墙预览（一次请求渲染全部精选字体）───────────────────────────────────

PREVIEW_TEXT_MAX = 10   # 预览用文本截断（长文本只取前 10 个字符渲染）
PREVIEW_MAX_ROWS = 8    # 预览卡片最大行数（超高字体截断，保持卡片整齐）


def render_font_previews(text: object) -> list[dict]:
    """Render ``text`` in every curated font (small, for the font wall).

    Raises TextArtError when nothing renderable (same semantics as
    render_figlet). Individual font failures are skipped, not fatal.
    """
    clean = filter_figlet_text(text)
    if not clean:
        raise TextArtError(
            "请输入英文/数字内容 / Enter English letters or digits")
    clean = clean[:PREVIEW_TEXT_MAX]
    out: list[dict] = []
    for f in curated_fonts():
        try:
            art = render_figlet(clean, f["slug"], 100)
        except TextArtError:
            continue  # 个别字体对截断文本渲染失败 → 跳过不致命
        lines = art.split("\n")
        # 卡片缩略图截断；full 字段带完整作品——点击卡片前端本地切换，零请求
        preview = lines[:PREVIEW_MAX_ROWS]
        cols, rows = art_dims(art)
        out.append({"slug": f["slug"], "name": f["name"],
                    "art": "\n".join(preview),
                    "full": art, "cols": cols, "rows": rows})
    if not out:
        raise TextArtError("没有可用字体 / No font available")
    return out


def validate_stored_art(art: object) -> str:
    """Validate an art string coming from the client before storing it as a
    gallery work (publish path). Dimension-capped, control chars stripped."""
    if not isinstance(art, str):
        raise TextArtError("缺少艺术字内容 / Missing art content")
    cleaned = "".join(
        ch for ch in art.replace("\r\n", "\n").replace("\r", "\n")
        if ch == "\n" or (ord(ch) >= 0x20 and ord(ch) != 0x7F)
    )
    cleaned = cleaned.strip("\n")
    if not cleaned.strip():
        raise TextArtError("艺术字内容为空 / Art content is empty")
    return _tidy_art(cleaned)


_FENCE_BLOCK_RE = re.compile(
    r"```[^\n]*\n(.*?)\n?```", re.DOTALL)


def normalize_direct_art(raw: object) -> str:
    """Normalise LLM-produced ASCII art: strip code fences / markdown
    indentation, expand tabs, drop control characters, enforce caps."""
    art = _normalize_direct_art_strict(raw)
    if art is None:
        raise TextArtError(
            "AI 没有返回有效内容，请重试 / AI returned nothing useful, retry")
    return art


def _normalize_direct_art_strict(raw: object) -> str | None:
    """Normalize without size enforcement; None when nothing valid."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    m = _FENCE_BLOCK_RE.search(text)
    if m and m.group(1).strip():
        text = m.group(1)
    else:
        # 无闭合围栏时剥掉孤立的起始/结束围栏行
        lines = [ln for ln in text.split("\n") if not _FENCE_RE.match(ln)]
        text = "\n".join(lines).strip()
    if not text:
        return None
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 0x20)
    text = text.expandtabs(4)
    # 统一去掉非空行共有的前导缩进（LLM 常把作品整体缩进 4 空格），
    # 等量去缩进不破坏字符画对齐
    lines = text.split("\n")
    indents = [len(ln) - len(ln.lstrip(" ")) for ln in lines if ln.strip()]
    if indents:
        pad = min(indents)
        if pad > 0:
            lines = [ln[pad:] if ln.strip() else "" for ln in lines]
    lines = [ln.rstrip() for ln in lines]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    art = "\n".join(lines)
    if not art.strip():
        return None
    return art


def auto_fit_art(art: str, *, max_cols: int = MAX_ART_COLS,
                 max_rows: int = MAX_ART_ROWS) -> tuple[str, bool]:
    """Compact fallback（ascii-skills）：超尺寸自动等比缩小，永不拒绝。

    返回 (art, fitted)。缩行方式：行数超限→隔行抽稀；列数超限→
    无法安全缩列（等宽字符画删列会破坏字形），列超限时整体缩行数
    按比例匹配，最后仍超则截断到上限并标注。
    """
    cols, rows = art_dims(art)
    if cols <= max_cols and rows <= max_rows:
        return art, False
    # 行数超限 → 隔行抽稀（保留轮廓）
    if rows > max_rows:
        lines = art.split("\n")
        step = rows / max_rows
        picked = [lines[int(i * step)] for i in range(max_rows)]
        art = "\n".join(picked)
    cols, rows = art_dims(art)
    # 列数仍超限 → 按比例再抽稀行数（视觉宽度近似匹配），最终截断
    if cols > max_cols:
        target_rows = max(4, int(rows * max_cols / cols))
        lines = art.split("\n")
        step = rows / target_rows if target_rows < rows else 0
        if step > 0:
            picked = [lines[min(len(lines) - 1, int(i * step))]
                      for i in range(target_rows)]
            art = "\n".join(picked)
        # 单行仍超宽 → 截断（极限场景，标注 fitted）
        lines = [ln[:max_cols] for ln in art.split("\n")]
        art = "\n".join(lines)
    return art, True


def split_variants(raw: object) -> list[str]:
    """Split multi-variant LLM output into normalised artworks (1..2)."""
    if not isinstance(raw, str) or not raw.strip():
        return []
    parts = raw.split(VARIANT_SEPARATOR)
    out = []
    for part in parts[:2]:
        art = _normalize_direct_art_strict(part)
        if art:
            out.append(art)
    return out


# ── LLM prompts ──────────────────────────────────────────────────────────────

PARAM_SYSTEM_PROMPT = (
    "You map a user's idea to parameters of a FIGlet ASCII-art generator. "
    "Reply with STRICT JSON only, no markdown, no explanations: "
    '{"text": "<letters/digits/short phrase>", "font": "<slug>"}. '
    "`text`: 1-40 chars, English letters, digits, spaces and .,-!? only; "
    "keep the user's intended wording (translate non-English ideas into a "
    "short English word/phrase). "
    "`font`: exactly one of the slugs: ansi_shadow, standard, big, colossal, "
    "slant, small, doom, block, banner3, ghost, graffiti, bloody, ogre, "
    "poison, starwars, fire_font-s, larry3d, nancyj, impossible, isometric1, "
    "sub-zero, calvin_s, delta_corps_priest_1, js_stick_letters. "
    "Pick the font matching the mood (fire -> fire_font-s, scary -> bloody, "
    "sci-fi -> starwars or sub-zero, cyber -> ansi_shadow, elegant -> slant "
    "or isometric1, cartoon -> larry3d)."
)

DIRECT_SYSTEM_PROMPT_TEMPLATE = (
    "You are a world-class ASCII artist. Draw the user's idea as monospaced "
    "ASCII art. Rules: reply with ONLY the artwork — no code fences, no "
    "explanations, no line numbers. Use printable ASCII characters (letters, "
    "# @ % * + = - : . ^ ~ etc). Keep it under {cols} columns and {rows} "
    "lines. Make the subject instantly recognisable."
)

# ── AI 迭代回路（ascii-skills 方法论：结构化输入 + 变体输出 + 无条件降级）──
#
# 方法论来源 full-stack-skills/ascii-skills：
#   1. 结构化输入：把用户意图翻译为明确参数（尺寸/风格/主体），
#      而非裸描述 —— 对应 ITERATE 的「当前作品 + 修改意见」结构。
#   2. 变体输出：banner 技能的 short/long variants 模式 —— DIRECT
#      多候选一次产出 2 版，用户挑选而非盲盒。
#   3. 紧凑降级：宽度不足时的 compact fallback —— 超尺寸不再报错，
#      服务端自动缩行，永不拒绝用户。
#   4. 对齐安全：颜色不破坏布局（空格不着色）—— ANSI 导出同规则。

ITERATE_SYSTEM_PROMPT_TEMPLATE = (
    "You are a world-class ASCII artist refining an existing artwork. "
    "The user will give you the CURRENT artwork and a MODIFICATION "
    "request. Apply the modification while keeping everything else "
    "recognisably the same. Rules: reply with ONLY the modified artwork "
    "— no code fences, no explanations. Monospaced, printable ASCII "
    "only. Keep it under {cols} columns and {rows} lines."
)

# 多候选分隔标记（banner 技能的 variants 模式）：一次生成两版供挑选。
VARIANT_SEPARATOR = "===VARIANT==="


DIRECT_MULTI_SYSTEM_PROMPT_TEMPLATE = (
    "You are a world-class ASCII artist. Draw the user's idea as "
    "monospaced ASCII art in TWO distinct variants (different style or "
    "composition — not pixel-identical twins). Rules: output exactly two "
    "artworks separated by a line containing only: ===VARIANT=== "
    "No code fences, no explanations, no line numbers. Printable ASCII "
    "characters only. Each artwork under {cols} columns and {rows} lines. "
    "Make the subject instantly recognisable."
)


# ── AI 作品示例（未配置 LLM 时的价值预览）────────────────────────────────────
# 手工精选 24 幅示范作品，按 6 个主题批次排列（动物 / 食物 / 物件 / 自然 /
# 符号 / 终端文化），每批 4 幅 —— 前端「换一批」最多刷新 5 次，
# 初始 + 5 次刷新恰好完整展示 24 幅、零重复。
# 风格对齐 ascii-skills（banner/图形/对齐安全）：等宽可辨识、宽度克制（≤44 列）。

AI_SHOWCASE: list[dict] = [
    # 批次 1 · 动物
    {
        "title": "猫",
        "prompt": "画一只猫",
        "art": (
            " |\\      _,,,---,,_\n"
            " /,`.-'`'    -.  ;-;;,_\n"
            " |,4-  ) )-,_..;\\ (  `'-'\n"
            " '---''(_/--'  `-\\_)"
        ),
    },
    {
        "title": "狗",
        "prompt": "画一只吐舌头的狗",
        "art": (
            "   / \\__\n"
            "  (    @\\___\n"
            "  /         O\n"
            " /   (_____/\n"
            "/_____/   U"
        ),
    },
    {
        "title": "猫头鹰",
        "prompt": "画一只猫头鹰",
        "art": (
            "  ,___,\n"
            "  (O,O)\n"
            "  (   )\n"
            "  /)_)\n"
            "   \"\""
        ),
    },
    {
        "title": "鲸鱼",
        "prompt": "画一头喷水的鲸鱼",
        "art": (
            "       .\n"
            "      \":\"\n"
            "    ___:____     |\"\\/\"|\n"
            "  ,'        `.    \\  /\n"
            "  |  O        \\___/  |\n"
            "~^~^~^~^~^~^~^~^~^~^~^~^~"
        ),
    },
    # 批次 2 · 食物
    {
        "title": "咖啡",
        "prompt": "一杯冒着热气的咖啡",
        "art": (
            "       ) )\n"
            "      ( (\n"
            "    ._______.\n"
            "    |       |]\n"
            "    \\       /\n"
            "     `-----'"
        ),
    },
    {
        "title": "披萨",
        "prompt": "画一块披萨",
        "art": (
            "  ___________\n"
            "  \\  o   o  /\n"
            "   \\   o   /\n"
            "    \\  o  /\n"
            "     \\ o /\n"
            "      \\o/\n"
            "       V"
        ),
    },
    {
        "title": "冰激凌",
        "prompt": "画一个甜筒冰激凌",
        "art": (
            "   ( o o o )\n"
            "    \\     /\n"
            "     \\   /\n"
            "      \\ /\n"
            "       V"
        ),
    },
    {
        "title": "蘑菇",
        "prompt": "画一朵蘑菇",
        "art": (
            "     ________\n"
            "    /        \\\n"
            "   /  o    o  \\\n"
            "  (____________)\n"
            "      |    |\n"
            "      |____|"
        ),
    },
    # 批次 3 · 物件
    {
        "title": "火箭",
        "prompt": "一枚正在升空的火箭",
        "art": (
            "        /\\\n"
            "       /  \\\n"
            "      | () |\n"
            "      |    |\n"
            "     /|    |\\\n"
            "    / |    | \\\n"
            "      |____|\n"
            "       |  |\n"
            "      (____)"
        ),
    },
    {
        "title": "相机",
        "prompt": "画一台老式相机",
        "art": (
            "    ___________\n"
            "   |  _______  |\n"
            "   | |       | |\n"
            "   | |  (o)  | |\n"
            "   | |_______| |\n"
            "   |___________|"
        ),
    },
    {
        "title": "灯泡",
        "prompt": "画一个亮着的灯泡",
        "art": (
            "       .-\"\"-.\n"
            "      /      \\\n"
            "     |  \\  /  |\n"
            "     |   \\/   |\n"
            "      \\      /\n"
            "       '-..-'\n"
            "       .-__-."
        ),
    },
    {
        "title": "时钟",
        "prompt": "画一个指针时钟",
        "art": (
            "    \\\\     //\n"
            "       _____\n"
            "     .'     '.\n"
            "    /    |    \\\n"
            "   |     |     |\n"
            "   |  9--o--3  |\n"
            "    \\         /\n"
            "     '._____.'"
        ),
    },
    # 批次 4 · 自然
    {
        "title": "山脉",
        "prompt": "画连绵的雪山",
        "art": (
            "        /\\\n"
            "       /  \\        /\\\n"
            "      /    \\      /  \\\n"
            "     /      \\    /    \\\n"
            "    /        \\  /      \\\n"
            " __/          \\/        \\__"
        ),
    },
    {
        "title": "松树",
        "prompt": "画一棵松树",
        "art": (
            "       ###\n"
            "      #####\n"
            "     #######\n"
            "    #########\n"
            "        ##\n"
            "        ##\n"
            "       ####"
        ),
    },
    {
        "title": "月亮",
        "prompt": "画一弯新月",
        "art": (
            "       _..._\n"
            "     .::::. `.\n"
            "    :::::::.  :\n"
            "    ::::::::  :\n"
            "    `::::::' .'\n"
            "      `'::'-'"
        ),
    },
    {
        "title": "帆船",
        "prompt": "画一艘帆船",
        "art": (
            "        |\\\n"
            "        | \\\n"
            "        |  \\\n"
            "        |___\\\n"
            "    ____|____\n"
            "    \\       /\n"
            " ~~~~`-----'~~~~"
        ),
    },
    # 批次 5 · 符号
    {
        "title": "爱心",
        "prompt": "画一颗像素风的心",
        "art": (
            "  ,d88b.d88b,\n"
            "  88888888888\n"
            "  `Y8888888Y'\n"
            "    `Y888Y'\n"
            "      `Y'"
        ),
    },
    {
        "title": "星星",
        "prompt": "画一颗闪亮的星",
        "art": (
            "        .\n"
            "       ,O,\n"
            "      ,OOO,\n"
            " \"OOOOOOOOOOOOO\"\n"
            "  'OOOOOOOOOOO'\n"
            "    'OOOOOOO'\n"
            "     'OOOOO'\n"
            "      OOO\n"
            "       O"
        ),
    },
    {
        "title": "宝石",
        "prompt": "画一颗钻石",
        "art": (
            "    *   /\\   *\n"
            "       /  \\\n"
            "      /    \\\n"
            "     <      >\n"
            "      \\    /\n"
            "       \\  /\n"
            "    *   \\/   *"
        ),
    },
    {
        "title": "笑脸",
        "prompt": "画一个笑脸",
        "art": (
            "    _______\n"
            "   /       \\\n"
            "  |  o   o  |\n"
            "  |    ^    |\n"
            "  |  \\___/  |\n"
            "   \\_______/"
        ),
    },
    # 批次 6 · 终端文化
    {
        "title": "终端",
        "prompt": "画一个终端窗口",
        "art": (
            " .-------------------------.\n"
            " | [~]$ whoami             |\n"
            " | terminal_artist         |\n"
            " | [~]$ _                  |\n"
            " '-------------------------'"
        ),
    },
    {
        "title": "横幅",
        "prompt": "画一个 TERMIFY 横幅",
        "art": (
            " _____ _____ ____  __  __ ___ _______   __\n"
            "|_   _| ____|  _ \\|  \\/  |_ _|  ___\\ \\ / /\n"
            "  | | |  _| | |_) | |\\/| || || |_   \\ V /\n"
            "  | | | |___|  _ <| |  | || ||  _|   | |\n"
            "  |_| |_____|_| \\_\\_|  |_|___|_|     |_|"
        ),
    },
    {
        "title": "软盘",
        "prompt": "画一张软盘",
        "art": (
            "  .-----------.\n"
            "  | .-------. |\n"
            "  | | [=]   | |\n"
            "  | '-------' |\n"
            "  |  _______  |\n"
            "  | |_______| |\n"
            "  '-----------'"
        ),
    },
    {
        "title": "幽灵",
        "prompt": "画一只小幽灵",
        "art": (
            "     .----.\n"
            "    /      \\\n"
            "   |  o  o  |\n"
            "   |   __   |\n"
            "    \\______/\n"
            "    | |  | |"
        ),
    },
]


# ── 导出矩阵：ANSI 彩色 / HTML 单文件 / 终端命令 ─────────────────────────────

# 配色主题（预览 + ANSI/HTML/PNG 导出共用）
ART_THEMES: dict[str, tuple[str, tuple[int, int, int]]] = {
    #        (主题色css, (fg r,g,b))      背景统一终端深色
    "green":  ("#00ff41", (51, 255, 51)),
    "cyan":   ("#00d4ff", (0, 212, 255)),
    "amber":  ("#ffb000", (255, 176, 0)),
    "magenta": ("#ff4fd8", (255, 79, 216)),
    "red":    ("#ff3b30", (255, 59, 48)),
    "white":  ("#e0e6ed", (224, 230, 237)),
}
DEFAULT_THEME = "green"


def _theme_fg(theme: object) -> tuple[int, int, int]:
    """Theme key → (r,g,b)；未知主题回落默认绿。"""
    t = ART_THEMES.get(theme) if isinstance(theme, str) else None
    return t[1] if t else ART_THEMES[DEFAULT_THEME][1]


def _theme_css(theme: object) -> str:
    t = ART_THEMES.get(theme) if isinstance(theme, str) else None
    return t[0] if t else ART_THEMES[DEFAULT_THEME][0]


def render_ansi_art(art: str, theme: object = DEFAULT_THEME) -> str:
    """Art → ANSI truecolor 文本（对齐安全：逐行整段着色，一次 reset）。"""
    r, g, b = _theme_fg(theme)
    on = f"\x1b[38;2;{r};{g};{b}m"
    off = "\x1b[0m"
    return "\n".join(on + ln + off for ln in art.split("\n"))


def render_terminal_command(art: str) -> str:
    """Art → python -c 单行命令：粘贴到任意终端（含 Windows cmd）即显示。

    base64 编码完全免疫引号/换行/反斜杠/控制字符的 shell 转义差异，
    任意平台（cmd / PowerShell / POSIX sh）行为一致。
    """
    import base64 as _b64
    b64 = _b64.b64encode(art.encode("utf-8")).decode("ascii")
    return ('python -c "import sys,base64;'
            f'sys.stdout.write(base64.b64decode(\'{b64}\').decode(\'utf-8\'))"')


_STANDALONE_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ASCII Art · Termify</title>
<style>
  body {{
    background: #0a0e14; margin: 0; min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
  }}
  pre {{
    color: {css_color};
    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
    font-size: 12px; line-height: 1.2; white-space: pre;
    text-shadow: 0 0 8px {css_color}33;
  }}
</style>
</head>
<body><pre>{art_html}</pre></body>
</html>
"""


def render_standalone_html(art: str, theme: object = DEFAULT_THEME) -> str:
    """Art → 自包含 HTML 单文件（内联样式，可直接发送）。"""
    import html as _html
    return _STANDALONE_HTML_TEMPLATE.format(
        css_color=_theme_css(theme), art_html=_html.escape(art))




_MONO_FONT_CANDIDATES = (
    "consola.ttf", "cour.ttf", "DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Menlo.ttc",
)
ART_BG = (10, 14, 20)      # 与前端终端底色一致
ART_FG_DEFAULT = (51, 255, 51)  # 默认绿（与应用默认配色一致）
ART_PAD = 24


def render_art_png(art: str, dst_path: str, *,
                   fg: tuple[int, int, int] = ART_FG_DEFAULT,
                   bg: tuple[int, int, int] = ART_BG) -> int:
    """Render art text onto a dark terminal-style PNG; returns canvas width.

    Uses a monospace TTF when available, else PIL's embedded bitmap font
    (also monospaced). Caller ensures ``art`` passed validate_stored_art().
    """
    import os as _os

    from PIL import Image, ImageDraw, ImageFont

    font = None
    env_font = _os.environ.get("TERMIFY_MONO_FONT")
    candidates = ([env_font] if env_font else []) + list(_MONO_FONT_CANDIDATES)
    for cand in candidates:
        if cand and _os.path.isfile(cand):
            try:
                font = ImageFont.truetype(cand, 16)
                break
            except (OSError, IOError):
                continue
    if font is None:
        font = ImageFont.load_default()

    lines = art.split("\n")
    cols = max(len(ln) for ln in lines)
    probe = font.getbbox("M")
    w0 = max(1, probe[2] - probe[0])
    # 目标字符宽：让画布宽约 1200px（钳制 8-24px/格），等比缩放字号
    target = max(8.0, min(24.0, 1200.0 / max(1, cols)))
    if w0 != target and hasattr(font, "path"):
        size = max(6, int(round(16 * target / w0)))
        try:
            font = ImageFont.truetype(font.path, size)
        except (OSError, IOError, AttributeError):
            pass

    ascent, descent = font.getmetrics() if hasattr(font, "getmetrics") \
        else (16, 4)
    char_w = max(1.0, font.getlength("M")) if hasattr(font, "getlength") \
        else float(max(1, probe[2] - probe[0]))
    line_h = max(1, int(round(ascent + descent)))
    if char_w <= 0:
        char_w = 6.0

    canvas_w = int(ART_PAD * 2 + cols * char_w) + 1
    canvas_h = ART_PAD * 2 + len(lines) * line_h
    img = Image.new("RGB", (canvas_w, canvas_h), bg)
    draw = ImageDraw.Draw(img)
    y = ART_PAD
    for ln in lines:
        if ln:
            draw.text((ART_PAD, y), ln, font=font, fill=fg)
        y += line_h
    if canvas_w < 600:  # 小作品 NEAREST 放大，缩略图/OG 不至于模糊
        factor = min(4.0, 600.0 / canvas_w)
        img = img.resize((int(canvas_w * factor), int(canvas_h * factor)),
                         Image.NEAREST)
    img.save(dst_path, format="PNG")
    return canvas_w
