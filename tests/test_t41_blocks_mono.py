"""T41 blocks 单色（mono）主题色回归测试。

背景：作品页配色除「原作(saved)」外必须强制单色——丢原色、整幅染成该主题色，
预览 / 下载 / 嵌入三处一致。此前 blocks 在服务端与前端都硬编码短路成「像素色
优先」，点主题色画面一个字都不变（库里 20+ 个 params.charset=blocks 的作品）。

覆盖：source/source256 逐字零回归（钉改动前 sha256）、mono 只出 bg→fg 渐变上的
色、结构仍是「▀ + 上像素前景 + 下像素背景」、同色 SGR 去重、缺 fg 退回像素色。
一致性：前端 static/js/termify-render.js 的 renderBlocks 同公式同舍入（pyRound
银行家舍入），逐格比对见 tmp 手工脚本（不进仓库）。
"""

from __future__ import annotations

import hashlib
import re

from PIL import Image

from termify.charset import render_frame

FG = (51, 255, 51)
BG = (10, 14, 20)
SGR_RE = re.compile(r"\x1b\[(?:38|48);2;(\d+);(\d+);(\d+)m")

PALETTE = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255),
           (0, 0, 0), (128, 64, 32), (10, 200, 90), (240, 30, 120)]

# 改动前（blocks 忽略 fg/bg 时）8x8 合成图的输出哈希：source 分支必须逐字不变。
PRE_CHANGE_SHA = "80fe7e1fa9be2436279001611bac3d1a26e4e6a0b9b2d9f3e93d17f6dd34fa52"


def _img():
    img = Image.new("RGB", (8, 8))
    for y in range(8):
        for x in range(8):
            img.putpixel((x, y), PALETTE[(x * 3 + y * 5) % len(PALETTE)])
    return img


def _sha(lines):
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _colors(lines):
    return {(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            for ln in lines for m in SGR_RE.finditer(ln)}


def _ramp(fg=FG, bg=BG):
    return {(round(bg[0] + (fg[0] - bg[0]) * (i / 255)),
             round(bg[1] + (fg[1] - bg[1]) * (i / 255)),
             round(bg[2] + (fg[2] - bg[2]) * (i / 255))) for i in range(256)}


# ── 1. source 分支零回归 ────────────────────────────────────────


def test_source_branch_byte_identical_to_pre_change():
    img = _img()
    for mode in ("source", "source256"):
        assert _sha(render_frame(img, "blocks", 8, 4, color_mode=mode)) == PRE_CHANGE_SHA


def test_source_ignores_theme_colours():
    img = _img()
    plain = render_frame(img, "blocks", 8, 4, color_mode="source")
    tinted = render_frame(img, "blocks", 8, 4, fg_color=FG, bg_color=BG,
                          color_mode="source")
    assert tinted == plain
    assert _sha(tinted) == PRE_CHANGE_SHA


def test_mono_without_fg_keeps_pixel_colour():
    img = _img()
    assert _sha(render_frame(img, "blocks", 8, 4)) == PRE_CHANGE_SHA
    # 只给 bg、没有 fg：无渐变可依，退回像素色（不能整幅糊成一个色）
    assert render_frame(img, "blocks", 8, 4, bg_color=BG) == render_frame(img, "blocks", 8, 4)


# ── 2. mono 分支：只剩主题色系 ──────────────────────────────────


def test_mono_only_emits_theme_ramp_colours():
    lines = render_frame(_img(), "blocks", 8, 4, fg_color=FG, bg_color=BG,
                         color_mode="mono")
    cols = _colors(lines)
    assert cols, "mono blocks 仍应发 TrueColor"
    assert cols <= _ramp(), f"出现渐变之外的色：{sorted(cols - _ramp())}"
    assert not (cols & set(PALETTE)), "原色残留"
    # 合成图里有纯黑与纯白，渐变两端都必须出现
    assert BG in cols and FG in cols


def test_mono_tint_changes_output_from_source():
    img = _img()
    mono = render_frame(img, "blocks", 8, 4, fg_color=FG, bg_color=BG,
                        color_mode="mono")
    assert mono != render_frame(img, "blocks", 8, 4)


def test_mono_order_follows_luminance():
    black = Image.new("RGB", (2, 2), (0, 0, 0))
    white = Image.new("RGB", (2, 2), (255, 255, 255))
    assert _colors(render_frame(black, "blocks", 2, 1, fg_color=FG, bg_color=BG,
                                color_mode="mono")) == {BG}
    assert _colors(render_frame(white, "blocks", 2, 1, fg_color=FG, bg_color=BG,
                                color_mode="mono")) == {FG}


# ── 3. 结构：▀ 数量 / 行数 / 行末不 reset / SGR 去重 ─────────────


def test_mono_keeps_blocks_structure():
    lines = render_frame(_img(), "blocks", 8, 4, fg_color=FG, bg_color=BG,
                         color_mode="mono")
    assert len(lines) == 4
    assert [ln.count("\u2580") for ln in lines] == [8, 8, 8, 8]
    # 行末不发 reset（reset 会清状态，后续行变黑）
    assert not any(ln.endswith("\x1b[0m") for ln in lines)
    assert all(ln.endswith("\u2580") for ln in lines)


def test_mono_dedupes_repeated_sgr():
    flat = Image.new("RGB", (8, 8), (128, 128, 128))
    for ln in render_frame(flat, "blocks", 8, 4, fg_color=FG, bg_color=BG,
                           color_mode="mono"):
        assert ln.count("\x1b[38;2;") == 1, "同色前景只应发一次 SGR"
        assert ln.count("\x1b[48;2;") == 1, "同色背景只应发一次 SGR"
