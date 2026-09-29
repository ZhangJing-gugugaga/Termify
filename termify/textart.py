"""Text → ASCII art (FIGlet 直转 + 中文 TTF 点阵 + 入库归一化).

直转路径对齐 lddgo/figlet 语义：非 ASCII 字符被忽略（而不是报错），
FIGlet 负责字形与 smushing；入库/外部输入路径只做安全归一化（去代码围栏、
统一缩进、剥离控制字符），不改动艺术内容。
"""

from __future__ import annotations

import os as _os
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

MAX_ART_COLS = 440           # 入库作品 / 字符画的最大列
MAX_ART_ROWS = 120           # ……与最大行
# 中文点阵的宽度预算（列）：1:2 终端比例下 cols = 2 × rows × 字数，
# 留 40 列余量给导出预览。400 → 1~4 字都能拿到 50 行（见 cjk_effective_height）。
CJK_MAX_CELL_W = 400

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


# ── 中文 TTF 点阵路径（纯 PIL 光栅化）──────────────────────────────────────
# 根因结论（2026-09-05 实测）：小尺寸等宽网格装不下复杂汉字笔画；TTF 系统
# 字体 16×16 光栅化 100% 可读（含繁体）。中文路径不走 FIGlet（非 ASCII 会被
# filter_figlet_text 忽略），改为像素阈值 → 字符画。

CJK_MAX_CHARS = 8           # 单次渲染汉字上限（1:2 比例 × 160 列红线推出，
                            # 与 render_cjk_ttf 的行高收缩公式一致）
CJK_DEFAULT_HEIGHT = 26     # 单字占的字符画行数（列数自动 = 2×行数，见下）。
                            # 16 → 26（2026-09-28）：16 行下「完」的儿只剩两根
                            # 竖、「腾」的灬 压成 1-2 格，肉眼看就是"字的下半部分
                            # 没了"（用户连报三轮"被切割/显示不完全"）。26 行时
                            # 撇、竖弯钩、灬 都成形的——这不是裁切 bug，是分辨率。
                            # 1~7 字都拿得到 26 行（宽度预算 400 列）。
CJK_MAX_HEIGHT = 64         # 字符高度上限（过高时按文本长度自动收缩）
# 点阵的"点亮"字符用 █ 实心块而不是 #。
# 中文点阵本质是位图，而 # 的墨迹只占 em box 的 ~43%（实测 13px 字号下
# 墨迹 10px），相邻行之间天然留 36% 空隙 → 整幅字看着被横切成一条条
# （用户报的"被切割"），且**无论行距收到多紧都存在**：# 填不满字符格。
# █ 的墨迹接近整个 em box（18px/13px），上下行自然连成一体，观感与真正的
# 点阵字一致；终端粘贴、.txt/.py 导出、画廊回放也都是实心块。
_CJK_ON = "█"
def _bundled_heiti_path() -> str:
    """仓库内置 Noto Sans CJK SC Light 的绝对路径（static/fonts/）。

    中文点阵"黑体"的统一字体：各端（开发机/ECS/发布包）都指向同一份
    文件，渲染结果逐字节一致。OFL-1.1 允许随仓库再分发，许可证与版权
    声明见 static/fonts/LICENSE.NotoSansCJK。
    """
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    return _os.path.join(root, "static", "fonts", "NotoSansCJKsc-Light.otf")


# (key, 展示名, 字体候选)。候选按序探测，首个存在者生效（Win/Linux/macOS）。
# 黑体首选**仓库内置**的 Noto Sans CJK SC Light（static/fonts/，OFL-1.1，
# 许可证见 static/fonts/LICENSE.NotoSansCJK）：各端共用同一份字体文件，
# 本地/线上逐字节一致。教训（2026-09-29）：此前 heiti 在 Windows 命中
# simhei.ttf、在 ECS 命中 NotoSansCJK-Regular.ttc——同一选项两端字形完全
# 不同，且 Regular 权重笔画粗、在覆盖度采样下低行数粘连成实心块（用户
# 端到端看到的"线上被切割、本地没事"的根因）。Light 权重笔画细，26 行
# 网格下结构与 simhei 相当。系统字体仅作内置文件缺失时的兜底。
CJK_FONTS: list[tuple[str, str, tuple[str, ...]]] = [
    ("songti", "宋体", (
        "simsun.ttc", "SimSun.ttf",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "NotoSerifCJK-Regular.ttc",
    )),
    ("heiti", "黑体", (
        _bundled_heiti_path(),
        "simhei.ttf", "SimHei.ttf",
    )),
    ("kaiti", "楷体", (
        "simkai.ttf", "KaiTi.ttf",
        "/usr/share/fonts/truetype/arphic/ukai.ttc",
    )),
]
CJK_DEFAULT_FONT = "heiti"

_CJK_FONT_DIRS = (
    "",  # 裸名：交给 PIL 的默认搜索路径（Windows 会扫 Fonts 目录）
    "C:/Windows/Fonts/",
    "/usr/share/fonts/truetype/dejavu/",  # 占位无害；主要覆盖常见目录
    "/usr/share/fonts/",
    "/System/Library/Fonts/",
    "/Library/Fonts/",
)


def cjk_has_glyph(text: object) -> bool:
    """输入里是否含 CJK 字符（决定 /api/text/convert 走哪条路）。"""
    if not isinstance(text, str):
        return False
    return any(
        0x4E00 <= ord(ch) <= 0x9FFF or 0x3400 <= ord(ch) <= 0x4DBF
        for ch in text
    )


def _resolve_cjk_font(slug: object) -> str | None:
    """CJK 字体 slug → 绝对字体路径；未知/无效 slug 回落默认字体。

    仅当默认字体也找不到文件时返回 None（调用方报「缺字体」）。
    """
    if not isinstance(slug, str) or slug == "auto":
        slug = CJK_DEFAULT_FONT
    entry = next((e for e in CJK_FONTS if e[0] == slug), None)
    if entry is None:
        entry = next(e for e in CJK_FONTS if e[0] == CJK_DEFAULT_FONT)
    for name in entry[2]:
        for d in _CJK_FONT_DIRS:
            path = d + name
            if _os.path.isfile(path):
                return path
    # 请求的字体没有 → 尝试其余字体兜底（任一可用即渲染）
    for entry2 in CJK_FONTS:
        if entry2[0] == entry[0]:
            continue
        for name in entry2[2]:
            for d in _CJK_FONT_DIRS:
                path = d + name
                if _os.path.isfile(path):
                    return path
    return None


def _cjk_font_file_exists(slug: str) -> bool:
    """该字体的候选文件本身是否存在于搜索路径（不含跨字体兜底）。"""
    entry = next((e for e in CJK_FONTS if e[0] == slug), None)
    if entry is None:
        return False
    return any(_os.path.isfile(d + name)
               for name in entry[2] for d in _CJK_FONT_DIRS)


def cjk_available_fonts() -> list[dict]:
    """前端中文字体下拉的数据源：slug + name + 是否可用。

    available 按「候选文件本身存在」判定——不能用带兜底的
    _resolve_cjk_font（任一字体存在时三项全报可用，选中缺文件项会
    静默渲染成兜底字体，前端置灰提示失效）。
    """
    out = []
    for slug, name, candidates in CJK_FONTS:
        out.append({"slug": slug, "name": name,
                    "available": _cjk_font_file_exists(slug)})
    return out


def filter_cjk_text(text: object) -> str:
    """中文路径输入清洗：保留 CJK/ASCII 可打印，去换行，限长。"""
    if not isinstance(text, str):
        return ""
    kept = []
    for ch in text:
        code = ord(ch)
        if (0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF
                or 0x20 <= code <= 0x7E):
            kept.append(ch)
    return "".join(kept).strip()[:CJK_MAX_CHARS]


def cjk_effective_height(text: object, height: object) -> int:
    """用户要的字符高度 → 实际可渲染高度（宽度红线收缩后的值）。

    1:2 终端比例下 cols = 2 × rows × 字数，宽度封顶 ``CJK_MAX_CELL_W``。
    1~4 字都能拿满 50 行；字数越多收缩越狠（5 字 40 / 6 字 33 / 8 字 25）。
    调用方（API /api/text/convert）必须复用本函数，否则回给前端的
    effective height 与真正渲染出来的不一致，收缩提示永远不触发。
    """
    clean = filter_cjk_text(text)
    try:
        h = int(height)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        h = CJK_DEFAULT_HEIGHT
    h = max(10, min(CJK_MAX_HEIGHT, h))
    if not clean:
        return h
    return max(10, min(h, CJK_MAX_CELL_W // (2 * len(clean))))


def _cjk_glyph_images(clean: str, font_path: str, cell_px: int):
    """逐字光栅化，返回 [(char, PIL.Image)]（半角/空格返回 None 占位）。"""
    from PIL import Image, ImageDraw, ImageFont

    try:
        f = ImageFont.truetype(font_path, cell_px)  # 字号 = 列数 = 方形
    except (OSError, IOError):
        raise TextArtError(
            "中文字体加载失败 / Failed to load Chinese font")
    out = []
    for ch in clean:
        if not (0x4E00 <= ord(ch) <= 0x9FFF or 0x3400 <= ord(ch) <= 0x4DBF):
            out.append(None)  # 半角/空格：格子留白（列对齐由全角格保证）
            continue
        img = Image.new("L", (cell_px, cell_px), 255)
        ImageDraw.Draw(img).text((0, 0), ch, font=f, fill=0)
        out.append(img)
    return out


def _ink_box(glyphs) -> tuple[int, int, int, int]:
    """一组字形的墨迹外接框（em box 坐标系的 y 区间，全字共用）。

    根因（2026-09-27）：旧实现把整个 em box 平均压到 h 行，而 TTF 的
    em box 上下都留白（黑体字号 192 时墨迹只占 y 12~185，约 90%），白边
    又按比例分摊到 h 行里 —— 首行整行空白、字头那一横只剩半个格子，
    观感就是「字被吃了一半」。改按墨迹盒取样：整幅字满幅落在 h 行内，
    既不裁也不浪费。字形间基线不齐的问题也一并消失（全字共用一个盒）。
    """
    boxes = [g.point(lambda v: 255 - v).getbbox() for g in glyphs if g is not None]
    boxes = [b for b in boxes if b]
    if not boxes:
        return None  # type: ignore[return-value]
    return (min(b[1] for b in boxes), min(b[0] for b in boxes),
            max(b[3] for b in boxes), max(b[2] for b in boxes))


def render_cjk_ttf(text: object, font: object = CJK_DEFAULT_FONT,
                   height: object = CJK_DEFAULT_HEIGHT) -> str:
    """中文 → TTF 光栅化点阵字符画（纯本地）。

    每个汉字先在高分辨率画布上逐字光栅化（字与字之间不重叠），再把
    **墨迹盒**（见 _ink_box）压到 ``h`` 行 × ``2h`` 列。压缩用格子的
    二维面积覆盖度采样（见下），而非逐点取值。
    """

    from PIL import Image

    clean = filter_cjk_text(text)
    if not clean:
        raise TextArtError(
            "请输入汉字（1-8 个）/ Please enter 1-8 Chinese characters")
    font_path = _resolve_cjk_font(font)
    if font_path is None:
        raise TextArtError(
            "服务器缺少中文字体 / Server has no Chinese font installed")
    # 宽度红线：1:2 比例下总列数 = 2 × 行数 × 字数，超了就按字数收缩
    # （见 cjk_effective_height）。字数多到 10 行都放不下才报错
    # （语义清晰优于静默截断）。
    h = cjk_effective_height(clean, height)
    if h * 2 * len(clean) > CJK_MAX_CELL_W:
        raise TextArtError(
            f"文字过多（{len(clean)} 字）——请缩短到 "
            f"{CJK_MAX_CELL_W // 20} 字以内 / Too many characters")
    cell_w = h * 2
    scale = 6  # 高分辨率光栅化：源像素越多，面积平均的量化误差越小
    # （scale=3 时 songti 在 10-16 行低网格下笔画碎裂、时断时续，观感
    # 如"字被截断"；scale=6 实测三字体笔画连贯结构完整，h16 不劣化）
    # 画布 = 字形外接正方形（字号 cell_w*scale），字形按方块渲染
    cell_px = cell_w * scale
    glyphs = _cjk_glyph_images(clean, font_path, cell_px)
    box = _ink_box(glyphs)
    if box is None:  # 纯半角输入：全宽留白行
        return "\n".join([" " * cell_w] * h)
    ink_top, _, ink_bot, _ = box
    # 墨迹盒 → h 行：每行 ink_h/h 像素高；x 方向整幅 em box → cell_w 列。
    # 采样用**二维面积覆盖度**（PIL BOX 缩放 = 每个格子取其源像素块的平均
    # 墨量）。此前的两代实现都有结构性缺陷：
    #   ① min-pool「取最暗」：窗口沾到一笔就点亮 → 粗笔画字体的相邻笔画
    #      在低行数下粘连成实心条（「黑体渲染失败」）；
    #   ② 扫描线覆盖度「任一水平切片均值 ≥ 阈值」：纵向取的是 max，等于
    #      对整幅图做形态学膨胀，细横画变粗、贴线的笔画粘连；且细斜笔
    #      （捺、点）在 6px 窗口里均值不足被丢掉 —— 内置 Light 字体下
    #      「海/腾」内笔画画不全。
    # 面积平均对三者同时成立：粗笔画不粘连（空隙有真实面积权重）、细
    # 横画存活（占格面积达阈值即点亮）、斜笔画按面积自然过渡。实测
    # Light/SimHei/宋/楷 在 10/26/64 行下结构与原字形一致。
    COV_THRESHOLD = 110  # 0-255：格子内平均墨量（≈43% 面积）
    grid = [[" "] * (cell_w * len(clean)) for _ in range(h)]
    for ci, img in enumerate(glyphs):
        if img is None:
            continue
        # 纵向按全字共用的墨迹盒裁剪（与旧实现同），横向保持 em box
        small = img.crop((0, ink_top, cell_px, ink_bot)).resize(
            (cell_w, h), Image.BOX)
        px = small.load()
        base = ci * cell_w
        for ty in range(h):
            row = grid[ty]
            for tx in range(cell_w):
                if 255 - px[tx, ty] >= COV_THRESHOLD:
                    row[base + tx] = _CJK_ON
    rows = ["".join(row) for row in grid]
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(rows)


PREVIEW_TEXT_MAX = 10   # 预览用文本截断（长文本只取前 10 个字符渲染）


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
        # 卡片与点选结果同源：art 存完整作品，前端按 cols/rows 等比缩放
        # （transform: scale）铺满卡片。旧实现把 art 砍到 8 行，ANSI Shadow
        # 这类高字体被拦腰截断（正是用户截图里"半个字"的观感）。
        cols, rows = art_dims(art)
        out.append({"slug": f["slug"], "name": f["name"],
                    "art": art, "full": art, "cols": cols, "rows": rows})
    if not out:
        raise TextArtError("没有可用字体 / No font available")
    return out


# 中文字体墙：卡片里只放 2 个字（4 字 × 16 行 = 128 列，卡片里会缩到
# 不可读），点卡片仍作用于用户输入的全文。
CJK_PREVIEW_CHARS = 2
CJK_PREVIEW_HEIGHT = 26   # 与默认高度一致：墙卡和主预览同一份字形


def render_cjk_font_previews(text: object = "字符",
                             height: object = None) -> list[dict]:
    """中文字体墙：每款可用中文字体一张预览卡（点卡片即换）。

    不可用的字体（缺字体文件）不返回——前端字体墙里没必要摆置灰项，
    缺字体由左栏下拉置灰表达。
    """
    sample = filter_cjk_text(text)[:CJK_PREVIEW_CHARS] or "字符"
    h = CJK_PREVIEW_HEIGHT if height is None else height
    out: list[dict] = []
    for f in cjk_available_fonts():
        if not f["available"]:
            continue
        try:
            art = render_cjk_ttf(sample, f["slug"], h)
        except TextArtError:
            continue
        cols, rows = art_dims(art)
        out.append({"slug": f["slug"], "name": f["name"],
                    "art": art, "full": art, "cols": cols, "rows": rows})
    if not out:
        raise TextArtError("没有可用的中文字体 / No Chinese font available")
    return out


def validate_stored_art(art: object, *, keep_ansi: bool = False) -> str:
    """Validate an art string coming from the client before storing it as a
    gallery work (publish path). Dimension-capped, control chars stripped.

    keep_ansi=True（原色作品）：保留 TrueColor SGR 转义（\\x1b[...m），
    其余控制字符照剥；尺寸按"剥转义后的可见字符"计算——ANSI 序列不计入
    列数，否则原色作品永远过不了 MAX_ART_COLS 红线。
    """
    if not isinstance(art, str):
        raise TextArtError("缺少艺术字内容 / Missing art content")
    # keep_ansi 时放行 ESC(0x1b)（SGR 序列的起始字节），其余控制字符照剥
    cleaned = "".join(
        ch for ch in art.replace("\r\n", "\n").replace("\r", "\n")
        if ch == "\n" or (keep_ansi and ch == "\x1b")
        or (not keep_ansi and ord(ch) >= 0x20 and ord(ch) != 0x7F)
        or (keep_ansi and ord(ch) >= 0x20)
    )
    cleaned = cleaned.strip("\n")
    if not cleaned.strip():
        raise TextArtError("艺术字内容为空 / Art content is empty")
    if keep_ansi:
        # 尺寸校验走可见文本（剥 SGR），入库保留原始带色 art
        plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", cleaned)
        plain = plain.strip("\n")
        if not plain.strip():
            raise TextArtError("艺术字内容为空 / Art content is empty")
        _tidy_art(plain)
        return cleaned
    return _tidy_art(cleaned)


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


def render_terminal_command(art: str, theme: object = None) -> str:
    """Art → python -c 单行命令：粘贴到任意终端（含 Windows cmd）即显示。

    zlib + base64 双重编码：base64 免疫引号/换行/反斜杠/控制字符的 shell
    转义差异，zlib 把带 TrueColor 转义的作品压到 1/5~1/10（一个 SGR 序列
    就 19 字节，纯 base64 的大图轻松上 10 万字符，撞 cmd.exe 8191 上限；
    压缩后绝大多数作品压进单条命令行）。平台无关：cmd / PowerShell / sh 一致。

    theme 给定时按主题着色（无色 art）；原色 art 已含转义则原样嵌入。
    theme 缺省保持旧行为（原样）。
    """
    import base64 as _b64
    import zlib as _zlib
    payload = art
    if theme is not None and "\x1b" not in art:
        # 无色 art 按主题整行着色；已含 TrueColor 转义（原色作品）则原样嵌入
        payload = render_ansi_art(art, theme)
    raw = payload.encode("utf-8")
    b64 = _b64.b64encode(_zlib.compress(raw, 9)).decode("ascii")
    return ('python -c "import sys,zlib,base64;'
            f'sys.stdout.write(zlib.decompress(base64.b64decode(\'{b64}\'))'
            '.decode(\'utf-8\'))"')


# 单条命令行超过这个长度就别指望粘进终端了（cmd.exe 8191 字符硬上限，
# PowerShell 32K 但整行读起来没法用）——前端据此改推「下载 .py」。
TERMINAL_CMD_MAX = 7000


def terminal_command_size(art: str, theme: object = None) -> int:
    """render_terminal_command 产出的命令行长度（字符数）。"""
    return len(render_terminal_command(art, theme))


_PY_SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Termify 字符艺术 —— 在任意终端运行即可显示：

    python %(name)s.py

字符画以 zlib+base64 内嵌（免疫一切 shell / 引号 / 编码差异），
依赖仅标准库。%(note)s
"""
import base64
import sys
import zlib

PAYLOAD = (
%(chunks)s
)


def main() -> None:
    sys.stdout.write(zlib.decompress(base64.b64decode(PAYLOAD)).decode("utf-8"))
    sys.stdout.write("\\n")


if __name__ == "__main__":
    main()
'''


def render_python_script(art: str, theme: object = None,
                         name: str = "termify-art") -> str:
    """Art → 可直接 ``python xxx.py`` 运行的单文件脚本。

    终端命令（python -c 一行）撞 cmd.exe 8191 字符上限时，大作品的正解：
    下载这个 .py，双击/命令行运行都出图，还能在脚本里改配色。
    """
    import base64 as _b64
    import re as _re

    payload = art
    if theme is not None and "\x1b" not in art:
        payload = render_ansi_art(art, theme)
    b64 = _b64.b64encode(
        __import__("zlib").compress(payload.encode("utf-8"), 9)).decode("ascii")
    chunks = [b64[i:i + 76] for i in range(0, len(b64), 76)]
    body = "\n".join('    "%s"' % c for c in chunks) or '    ""'
    safe = _re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "termify-art")) or \
        "termify-art"
    return _PY_SCRIPT_TEMPLATE % {"name": safe, "chunks": body, "note":
                                  "内嵌彩色转义，终端需支持 24 位色。" if
                                  "\x1b" in payload else "纯文本字符画。"}


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




def _bundled_mono_font() -> str:
    """仓库内置 DejaVu Sans Mono 的绝对路径（static/fonts/）。

    块元素 █▓▒░ / 盒线 / 盲文点阵 / 几何形状全覆盖——系统字体
    （如 consola）缺这些字形时字符画 PNG 会渲染成豆腐块/噪声，
    画廊缩略图"乱码"的根因。许可证见 static/fonts/LICENSE.DejaVu。
    """
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    return _os.path.join(root, "static", "fonts", "DejaVuSansMono.ttf")


_MONO_FONT_CANDIDATES = (
    _bundled_mono_font(),
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
    if canvas_w < 1200:  # 小作品整数倍 NEAREST 放大到 ≈1200px：
        # 非整数倍会造成颗粒疏密不均（缩略图二次缩放后糊+锯齿），
        # 整数倍每字符像素均匀，缩略图观感整齐锐利
        factor = max(2, int(1200 / canvas_w))
        img = img.resize((canvas_w * factor, canvas_h * factor),
                         Image.NEAREST)
    img.save(dst_path, format="PNG")
    return canvas_w
