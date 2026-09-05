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
        fonts = textart.cjk_available_fonts()
        slugs = {f["slug"] for f in fonts}
        assert {"songti", "heiti", "kaiti"} <= slugs
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


@needs_font
class TestRenderCjkTtf:
    def test_basic_render(self):
        art = textart.render_cjk_ttf("你好")
        cols, rows = textart.art_dims(art)
        assert cols > 0 and rows > 0
        assert "#" in art  # 有实像素
        # 无制表/控制字符
        assert all(ord(ch) >= 0x20 or ch == "\n" for ch in art)

    def test_all_three_fonts(self):
        for slug in ("songti", "heiti", "kaiti"):
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
        assert "#" in art

    def test_dimension_capped(self):
        art = textart.render_cjk_ttf("一二三四五六七八九十百千")
        cols, rows = textart.art_dims(art)
        assert cols <= textart.MAX_ART_COLS
        assert rows <= textart.MAX_ART_ROWS

    def test_no_blank_row_collapse(self):
        # 行数应接近 CJK_DEFAULT_HEIGHT（首尾全空白行会被裁掉，ascender
        # 边距导致 1-2 行浮动，可接受；但不应塌成一半）
        art1 = textart.render_cjk_ttf("一二三四五六七八九十")
        _, rows1 = textart.art_dims(art1)
        assert textart.CJK_DEFAULT_HEIGHT - 2 <= rows1 <= textart.CJK_DEFAULT_HEIGHT
