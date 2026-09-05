"""T36 — 汉字活字引擎：混排渲染（拼接 / 间距 / 换行 / 占位 / 过滤）。"""

from __future__ import annotations

import pytest

from termify import cjk_render, textart

GLYPH = [
    "        ",
    "  ----  ",
    "  |  |  ",
    "  |  |  ",
    " ------ ",
    "  |  |  ",
    "  |  |  ",
    "        ",
]
H, W = len(GLYPH), len(GLYPH[0])
PLACEHOLDER = "█" * W


@pytest.fixture
def glyph_map(monkeypatch):
    """monkeypatch cjk_glyph.get_or_generate：按字符查表返回，缺的字返回 None。"""
    table = {}

    def _install(mapping):
        table.clear()
        table.update(mapping)

        def fake_get_or_generate(ch, style_slug, llm_cfg, llm_mod, *,
                                 data_dir=None):
            rows = table.get(ch)
            if rows is None:
                return None
            return {"rows": list(rows), "source": "llm", "cached": True}

        monkeypatch.setattr(cjk_render.cjk_glyph, "get_or_generate",
                            fake_get_or_generate)

    return _install


def test_single_char_dimensions(glyph_map):
    glyph_map({"中": GLYPH})
    r = cjk_render.render_cjk_text("中", "pixel", {}, None)
    assert r["missing"] == [] and r["style"] == "pixel"
    assert r["cols"] == W and r["rows"] == H
    assert r["art"].split("\n") == GLYPH


def test_multi_char_gap_and_equal_width(glyph_map):
    glyph_map({"你": GLYPH, "好": GLYPH})
    r = cjk_render.render_cjk_text("你好", "pixel", {}, None)
    assert r["cols"] == W * 2 + 2  # 字间 2 列空格间距
    lines = r["art"].split("\n")
    assert len(lines) == H
    for ln in lines:
        assert len(ln) == r["cols"]        # 每行严格等宽
        assert ln[W:W + 2] == "  "         # 第 8-9 列为字间距


def test_wrap_when_over_max_cols(glyph_map):
    glyph_map({c: GLYPH for c in "春夏秋冬"})
    r = cjk_render.render_cjk_text("春夏秋冬", "pixel", {}, None, max_cols=30)
    # per_line = (30+2)//(8+2) = 3 → 分组 3 + 1，组间 1 空行
    lines = r["art"].split("\n")
    assert lines[H] == ""                  # 分组之间的空行
    assert r["rows"] == H * 2 + 1
    for i, ln in enumerate(lines):
        if i != H:
            assert len(ln) <= 30
    # 第一组 3 字（8*3+2*2=28 列），第二组 1 字（8 列）
    assert len(lines[0]) == 28 and len(lines[-1]) == W


def test_missing_char_uses_placeholder(glyph_map):
    glyph_map({"你": GLYPH})  # 「好」生成失败
    r = cjk_render.render_cjk_text("你好", "pixel", {}, None)
    assert r["missing"] == ["好"]
    lines = r["art"].split("\n")
    assert lines[3] == GLYPH[3] + "  " + PLACEHOLDER  # 占位为实心块


def test_non_cjk_characters_filtered(glyph_map):
    glyph_map({"你": GLYPH, "好": GLYPH})
    r = cjk_render.render_cjk_text("a你,好!", "pixel", {}, None)
    assert r["cols"] == W * 2 + 2  # 只有 你好 参与渲染


def test_empty_input_raises(glyph_map):
    with pytest.raises(textart.TextArtError):
        cjk_render.render_cjk_text("", "pixel", {}, None)
    with pytest.raises(textart.TextArtError):
        cjk_render.render_cjk_text("abc!!", "pixel", {}, None)


def test_too_many_chars_raises(glyph_map):
    with pytest.raises(textart.TextArtError):
        cjk_render.render_cjk_text("汉" * 13, "pixel", {}, None)


def test_unknown_style_raises(glyph_map):
    with pytest.raises(ValueError):
        cjk_render.render_cjk_text("你", "nope", {}, None)
