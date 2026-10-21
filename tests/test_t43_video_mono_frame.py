"""T43 — MP4 单色帧（ramp 字符集）栅格化回归：列不错位、不成散点噪声。

缺陷（本任务修复）：termify/output/video.py 的 frame_to_image() 里「uniform
非盲文行」快速路径把 _glyph_strip() 返回的逐字形整块字节（char_h 行 ×
char_w*3 字节）当成「整行行优先字节」切片（row_w = len(text)*char_w*3），
于是第 2 个字符起的字节被当成同一行的行内容 —— 整行跨格错位，所有单色帧
（ramp 字符集、行内同一 fg/bg）渲染成横向散点噪声。生产与烘焙脚本走
color_mode="source"（len(fgs) > 1）绕开了该路径，所以一直没暴露。

参考实现 _cell_reference() 是逐格绘制：每个字符画进自己的 char_w×char_h
格子再贴回 —— 即字符网格语义（▀ / █ / 盲文三条快速路径同样是严格格内合成）。
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageChops

from termify.output.video import (
    DEFAULT_BG,
    DEFAULT_FG,
    _measure_cell,
    frame_to_image,
    pick_font,
)

RAMP = " .:-=+*#%@"


def _cell_reference(lines, font, char_w, char_h, out_w, out_h,
                    fg=DEFAULT_FG, bg=DEFAULT_BG):
    """逐格绘制参考实现：每字符画进自己的格子里再贴回（纯文本行，无 SGR）。"""
    img = Image.new("RGB", (out_w, out_h), bg)
    max_cells = out_w // char_w
    for y, line in enumerate(lines):
        for i, ch in enumerate(line[:max_cells]):
            cell = Image.new("RGB", (char_w, char_h), bg)
            ImageDraw.Draw(cell).text((0, 0), ch, fill=fg, font=font)
            img.paste(cell, (i * char_w, y * char_h))
    return img


def _ramp_frame(cols, rows, step=3):
    return ["".join(RAMP[(x + y * step) % len(RAMP)] for x in range(cols))
            for y in range(rows)]


def _font():
    font = pick_font(14)
    char_w, char_h = _measure_cell(font)
    return font, char_w, char_h


# --- 单色快速路径：整帧必须与逐格绘制参考实现逐字节一致 ---------------------------

def test_mono_ramp_frame_matches_per_cell_reference():
    """ramp 字符集单色帧：修复前整行错位（散点噪声），与参考实现逐字节不等。"""
    font, cw, ch = _font()
    cols, rows = 13, 5
    lines = _ramp_frame(cols, rows)
    img = frame_to_image(lines, font, cw, ch, cols * cw, rows * ch)
    assert img.size == (cols * cw, rows * ch)
    ref = _cell_reference(lines, font, cw, ch, cols * cw, rows * ch)
    assert img.tobytes() == ref.tobytes()


def test_mono_cells_are_identical_across_columns():
    """同一字符铺满一行：每个格子渲染结果必须逐字节相同（列不错位、不跨格）。"""
    font, cw, ch = _font()
    cols = 10
    img = frame_to_image(["." * cols], font, cw, ch, cols * cw, ch)
    cells = [img.crop((i * cw, 0, (i + 1) * cw, ch)) for i in range(cols)]
    first = cells[0].tobytes()
    assert first != Image.new("RGB", (cw, ch), DEFAULT_BG).tobytes()  # 确有点墨
    for i, cell in enumerate(cells):
        assert cell.tobytes() == first, f"cell {i} 与 cell 0 不一致（跨格错位）"


def test_mono_ink_stays_inside_its_own_cell_columns():
    """每个格子的非背景像素必须落在该格自己的列区间内（i*cw .. (i+1)*cw）。"""
    font, cw, ch = _font()
    cols, rows = 9, 2
    lines = ["#.+-=*%" * 2, "oOxX%$&" * 2]
    img = frame_to_image(lines, font, cw, ch, cols * cw, rows * ch)
    blank = Image.new("RGB", (cw, ch), DEFAULT_BG)
    for y in range(rows):
        for i in range(cols):
            cell = img.crop((i * cw, y * ch, (i + 1) * cw, (y + 1) * ch))
            diff = ImageChops.difference(cell, blank)
            bbox = diff.getbbox()
            if bbox is None:
                continue  # 空格子（空格字符）没有墨迹
            assert bbox[0] >= 0 and bbox[2] <= cw, f"cell ({i},{y}) 墨迹越格: {bbox}"
            # 与参考实现的对应格子一致 —— 墨迹形状不能是从别处串过来的字节
            ref = _cell_reference([lines[y]], font, cw, ch, cols * cw, ch)
            assert cell.tobytes() == ref.crop((i * cw, 0, (i + 1) * cw, ch)).tobytes(), \
                f"cell ({i},{y}) 与逐格参考实现不一致"


# --- 单色快速路径：行不满宽（rstrip）不得污染行尾格 / 不得移位后续行 ---------------

def test_mono_short_line_keeps_trailing_cells_default():
    """短行只占前几个格子，行尾保持默认背景，后续行位置不受影响。"""
    font, cw, ch = _font()
    cols, rows = 12, 3
    lines = ["ab", "abcdef", "cd"]
    img = frame_to_image(lines, font, cw, ch, cols * cw, rows * ch)
    assert img.size == (cols * cw, rows * ch)   # 缓冲区长度不得被切片赋值改变
    ref = _cell_reference(lines, font, cw, ch, cols * cw, rows * ch)
    assert img.tobytes() == ref.tobytes()
    # 第 1 行行尾（第 4 格起）必须是默认背景
    for i in range(2, cols):
        assert img.getpixel((i * cw + cw // 2, ch // 2)) == DEFAULT_BG


def test_mono_uniform_bg_fills_only_occupied_cells():
    """整行同一 SGR 背景色时，只有实际字符格填该背景，行尾仍为默认背景。"""
    font, cw, ch = _font()
    cols, rows = 10, 2
    bg = (0, 0, 64)
    lines = ["\x1b[48;2;0;0;64m" + "abc"] * rows
    img = frame_to_image(lines, font, cw, ch, cols * cw, rows * ch)
    assert img.size == (cols * cw, rows * ch)
    for y in (0, 1):
        assert img.getpixel((0, y * ch + ch - 1)) == bg          # 占用格 = SGR 背景
        assert img.getpixel((2 * cw + cw // 2, y * ch + ch - 1)) == bg
        assert img.getpixel((3 * cw, y * ch)) == DEFAULT_BG       # 行尾 = 默认背景


def test_mono_full_row_fills_whole_width():
    """满宽单色行：整幅宽度都被字形格覆盖（列数与帧宽严格对齐）。"""
    font, cw, ch = _font()
    cols = 8
    lines = ["#" * cols]
    img = frame_to_image(lines, font, cw, ch, cols * cw, ch)
    ref = _cell_reference(lines, font, cw, ch, cols * cw, ch)
    assert img.tobytes() == ref.tobytes()


# --- 零回归：▀ / █ 两条字节合成快速路径的输出（本次一并加固切片边界）--------------

def test_blocks_path_still_exact():
    """▀ 快路径：上半 = fg，下半 = bg，逐字节等于手工合成结果。"""
    font, cw, ch = _font()
    cols = 5
    line = "\x1b[38;2;255;0;0m\x1b[48;2;0;0;255m\u2580" * cols
    img = frame_to_image([line], font, cw, ch, cols * cw, ch)
    half = ch // 2
    expected = bytearray()
    for yy in range(ch):
        rgb = (255, 0, 0) if yy < half else (0, 0, 255)
        expected += bytes(rgb) * (cols * cw)
    assert img.tobytes() == bytes(expected)


def test_binary_path_still_exact():
    """█ 快路径：整个 cell = fg，逐字节等于纯色填充。"""
    font, cw, ch = _font()
    cols = 5
    line = "\x1b[38;2;1;2;3m\u2588" * cols
    img = frame_to_image([line, line], font, cw, ch, cols * cw, 2 * ch)
    assert img.tobytes() == bytes((1, 2, 3)) * (cols * cw * 2 * ch)


def _mean_rgb(img):
    px = img.load()
    total = img.width * img.height
    r = g = b = 0
    for y in range(img.height):
        for x in range(img.width):
            pr, pg, pb = px[x, y]
            r += pr
            g += pg
            b += pb
    return r / total, g / total, b / total


def test_braille_and_colour_rows_untouched():
    """盲文行与逐格异色行仍走慢路径：像素语义（点阵/逐格颜色）不变。"""
    font, cw, ch = _font()
    img = frame_to_image(["\u28ff\u28ff"], font, cw, ch, 2 * cw, ch)
    # 满点盲文 = 2×4 点阵铺满整格 → 大部分像素为前景色
    colors = dict((c, n) for n, c in (img.getcolors(maxcolors=1 << 24) or []))
    assert colors.get(DEFAULT_FG, 0) > (2 * cw * ch) // 2
    # 逐格异色（source 模式）：左格偏红、右格偏绿
    img2 = frame_to_image(["\x1b[38;2;255;0;0ma\x1b[38;2;0;255;0mb"],
                          font, cw, ch, 2 * cw, ch)
    left = _mean_rgb(img2.crop((0, 0, cw, ch)))
    right = _mean_rgb(img2.crop((cw, 0, 2 * cw, ch)))
    assert left[0] > left[1] + 20, left
    assert right[1] > right[0] + 20, right
