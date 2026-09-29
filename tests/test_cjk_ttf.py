"""T37 中文 TTF 点阵渲染测试（无 LLM）。

字体依赖系统环境：Windows 自带 simhei/simsun/simkai；CI 无中文字体时
相关断言自动 skip（无截图环境跳过渲染，逻辑断言照常跑）。
"""

import os

import pytest

from termify import textart


HAS_CJK_FONT = textart._resolve_cjk_font(textart.CJK_DEFAULT_FONT) is not None

needs_font = pytest.mark.skipif(
    not HAS_CJK_FONT, reason="系统无中文字体（CI/裸容器）")


class TestCjkDetection:
    def test_has_glyph_chinese(self):
        assert textart.cjk_has_glyph("你好") is True
        assert textart.cjk_has_glyph("hello 世界") is True

    def test_has_glyph_ascii(self):
        assert textart.cjk_has_glyph("hello") is False
        assert textart.cjk_has_glyph("") is False
        assert textart.cjk_has_glyph(None) is False

    def test_ext_e_latin(self):
        # 扩展 B 区（生僻字）不算常用 CJK，避免误路由
        assert textart.cjk_has_glyph("\U00020000") is False


class TestFilterCjkText:
    def test_keeps_cjk_and_ascii(self):
        assert textart.filter_cjk_text("你好T2!") == "你好T2!"

    def test_strips_newlines_emoji(self):
        assert textart.filter_cjk_text("你\n好 😀") == "你好"

    def test_truncates_to_max(self):
        out = textart.filter_cjk_text("一二三四五六七八九十百千万亿")
        assert len(out) == textart.CJK_MAX_CHARS


class TestCjkFonts:
    def test_available_fonts_structure(self):
        # 黑体已按用户决策下线（2026-09-29）：各端系统黑体字形不一致，
        # 四轮修复过不了视觉关——字形维度只保留宋体/楷体
        fonts = textart.cjk_available_fonts()
        slugs = {f["slug"] for f in fonts}
        assert slugs == {"songti", "kaiti"}
        for f in fonts:
            assert isinstance(f["available"], bool)

    def test_resolve_unknown_slug_falls_back(self):
        # 未知 slug 回落默认字体（不是硬报错），默认字体缺失才返回 None
        path = textart._resolve_cjk_font("nonexistent-slug")
        if HAS_CJK_FONT:
            assert path is not None
        else:
            assert path is None

    def test_resolve_auto_falls_default(self):
        if not HAS_CJK_FONT:
            pytest.skip("系统无中文字体")
        assert textart._resolve_cjk_font("auto") is not None


class TestCjkCharsets:
    def test_table_shape(self):
        assert textart.CJK_CHARSETS[0][0] == textart.CJK_DEFAULT_CHARSET
        slugs = [c[0] for c in textart.CJK_CHARSETS]
        assert len(slugs) == len(set(slugs))
        for _slug, name, on in textart.CJK_CHARSETS:
            assert name and on and len(on) == 1

    def test_known_and_on_char(self):
        assert textart.known_cjk_charset("braille") is True
        assert textart.known_cjk_charset("nope") is False
        assert textart.cjk_on_char("ascii") == "#"
        assert textart.cjk_on_char(None) == textart._CJK_ON
        assert textart.cjk_on_char("nope") == textart._CJK_ON

    @needs_font
    def test_charset_changes_on_char_not_grid(self):
        base = textart.render_cjk_ttf("完成", "songti", 26)
        block = textart.render_cjk_ttf("完成", "songti", 26, "block")
        braille = textart.render_cjk_ttf("完成", "songti", 26, "braille")
        assert block == base                       # 默认即字块
        assert block.replace(textart._CJK_ON, " ") == \
            braille.replace("⣿", " ")              # 点阵相同，仅字符不同
        assert "⣿" in braille and textart._CJK_ON not in braille

    @needs_font
    def test_charset_wall_previews(self):
        cards = textart.render_cjk_charset_previews("完成")
        assert [c["slug"] for c in cards] == \
            [c[0] for c in textart.CJK_CHARSETS]
        arts = {c["slug"]: c["art"].replace(" ", "") for c in cards}
        # 同一点阵：去掉点亮字符后骨架完全一致
        skeletons = {s: a.replace(textart.cjk_on_char(s), "")
                     for s, a in arts.items()}
        assert len(set(skeletons.values())) == 1


@needs_font
class TestRenderCjkTtf:
    def test_basic_render(self):
        art = textart.render_cjk_ttf("你好")
        cols, rows = textart.art_dims(art)
        assert cols > 0 and rows > 0
        # 点阵的"点亮"字符是 █ 实心块（# 填不满字符格，整幅字会被切成
        # 横向条带——见 textart._CJK_ON 的说明）
        assert textart._CJK_ON in art
        # 无制表/控制字符
        assert all(ord(ch) >= 0x20 or ch == "\n" for ch in art)

    def test_all_fonts(self):
        for slug in ("songti", "kaiti"):
            if textart._resolve_cjk_font(slug) is None:
                continue
            art = textart.render_cjk_ttf("测试", slug)
            cols, rows = textart.art_dims(art)
            assert cols > 0 and rows > 0

    def test_empty_input_raises(self):
        with pytest.raises(textart.TextArtError):
            textart.render_cjk_ttf("   ")

    def test_bad_font_falls_back_to_default(self):
        # 传一个不存在的 slug → 回落默认字体，不该抛错
        art = textart.render_cjk_ttf("测", "definitely-not-a-font")
        assert textart._CJK_ON in art

    def test_dimension_capped(self):
        art = textart.render_cjk_ttf("一二三四五六七八九十百千")
        cols, rows = textart.art_dims(art)
        assert cols <= textart.MAX_ART_COLS
        assert rows <= textart.MAX_ART_ROWS

    def test_no_blank_row_collapse(self):
        # 短文本（≤5 字）：行数应接近 CJK_DEFAULT_HEIGHT（首尾全空白行会被
        # 裁掉，ascender 边距导致 1-2 行浮动，可接受；但不应塌成一半）。
        # 宽文本行高按 1:2 比例自动收缩（MAX_ART_COLS 红线），不在此断言。
        art1 = textart.render_cjk_ttf("你好世界大")
        _, rows1 = textart.art_dims(art1)
        assert textart.CJK_DEFAULT_HEIGHT - 2 <= rows1 <= textart.CJK_DEFAULT_HEIGHT
