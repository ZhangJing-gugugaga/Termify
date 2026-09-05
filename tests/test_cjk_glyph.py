"""T36 — 汉字活字引擎：字形校验四道关 / 生成重试 / SQLite 缓存。

全部 mock LLM（FakeLLM），绝不真调网络；缓存一律落 pytest tmp_path。
"""

from __future__ import annotations

import threading

import pytest

from termify import cjk_glyph

PIXEL = cjk_glyph.style_by_slug("pixel")
H = PIXEL["height"]
W = PIXEL["width"]

# 手工构造的合规 8×8「中」字形（密度 28%、无贯穿性空列带）。
VALID_ZHONG = [
    "        ",
    "  ----  ",
    "  |  |  ",
    "  |  |  ",
    " ------ ",
    "  |  |  ",
    "  |  |  ",
    "        ",
]
GOOD_REPLY = "\n".join(VALID_ZHONG)
LLM_CFG = {"base_url": "http://x/v1", "model": "fake", "api_key": "k"}


class FakeLLM:
    """termify.llm 的最小替身：按序返回预设回复 / 抛异常。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, cfg, *, temperature=0.4, max_tokens=2000):
        self.calls.append({"temperature": temperature, "messages": messages})
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def is_configured(self, cfg):
        return True


# ── 校验器四道关 ─────────────────────────────────────────────────────────────

def test_styles_registry():
    slugs = [s["slug"] for s in cjk_glyph.GLYPH_STYLES]
    assert slugs == ["pixel", "brush", "outline"]
    dims = {s["slug"]: (s["height"], s["width"]) for s in cjk_glyph.GLYPH_STYLES}
    assert dims == {"pixel": (8, 8), "brush": (10, 10), "outline": (9, 10)}
    for s in cjk_glyph.GLYPH_STYLES:
        assert s["name"] and s["system_prompt"]
        assert s["width"] % 2 == 0, "宽度必须为偶数"


def test_validate_accepts_good_glyph():
    ok, msg = cjk_glyph.validate_glyph(VALID_ZHONG, W, height=H)
    assert ok, msg


def test_validate_pads_and_trims_rows():
    # 去掉尾随空格（不足补 pad）与超宽（trim 后仍等宽）都能过
    stripped = [r.rstrip() for r in VALID_ZHONG]
    ok, msg = cjk_glyph.validate_glyph(stripped, W, height=H)
    assert ok, msg
    overwide = [r + "  " for r in VALID_ZHONG]
    ok, msg = cjk_glyph.validate_glyph(overwide, W, height=H)
    assert ok, msg


def test_validate_rejects_non_ascii_charset():
    rows = list(VALID_ZHONG)
    rows[3] = "  中    "
    ok, _ = cjk_glyph.validate_glyph(rows, W, height=H)
    assert not ok


def test_validate_rejects_wrong_row_count():
    ok, _ = cjk_glyph.validate_glyph(VALID_ZHONG[:7], W, height=H)
    assert not ok
    ok, _ = cjk_glyph.validate_glyph(VALID_ZHONG + ["        "], W, height=H)
    assert not ok


def test_validate_rejects_too_sparse():
    ok, _ = cjk_glyph.validate_glyph(["        "] * H, W, height=H)
    assert not ok


def test_validate_rejects_too_dense():
    ok, _ = cjk_glyph.validate_glyph(["########"] * H, W, height=H)
    assert not ok


def test_validate_rejects_broken_glyph():
    # 中部（第 1 列）为贯穿性全空列带，且左右两侧均有墨迹 → 拒
    rows = ["# #   # "] * H
    ok, _ = cjk_glyph.validate_glyph(rows, W, height=H)
    assert not ok


def test_validate_accepts_edge_whitespace_columns():
    # 首尾留白是合法的（断裂检测不扫首尾列）
    rows = ["      ##"] * H
    ok, msg = cjk_glyph.validate_glyph(rows, W, height=H)
    assert ok, msg  # 密度 2/8 = 25%，无内部断裂


# ── 生成：重试与归一化 ───────────────────────────────────────────────────────

def test_generate_retries_then_succeeds():
    fake = FakeLLM([
        "好的，这是你要的字形：8 行，每行 8 个字符",  # 畸形（行数错）
        "```text\n########\n########\n```",           # 畸形（过密且行数错）
        GOOD_REPLY,                                    # 第三次合规
    ])
    rows = cjk_glyph.generate_glyph("中", PIXEL, LLM_CFG, fake)
    assert rows is not None
    assert len(rows) == H
    assert all(len(r) == W for r in rows)
    assert [c["temperature"] for c in fake.calls] == [0.7, 0.9, 1.0]


def test_generate_returns_none_after_three_failures():
    fake = FakeLLM(["bad", "bad", "bad"])
    assert cjk_glyph.generate_glyph("中", PIXEL, LLM_CFG, fake) is None
    assert len(fake.calls) == 3


def test_generate_swallows_llm_errors():
    fake = FakeLLM([RuntimeError("boom")] * 3)
    assert cjk_glyph.generate_glyph("中", PIXEL, LLM_CFG, fake) is None
    assert len(fake.calls) == 3


def test_generate_normalizes_fenced_reply():
    fenced = "```ascii\n" + GOOD_REPLY + "\n```"
    fake = FakeLLM([fenced])
    rows = cjk_glyph.generate_glyph("中", PIXEL, LLM_CFG, fake)
    assert rows == VALID_ZHONG


# ── SQLite 缓存 ──────────────────────────────────────────────────────────────

def test_cache_roundtrip(tmp_path):
    fake = FakeLLM([GOOD_REPLY])
    d1 = cjk_glyph.get_or_generate("中", "pixel", LLM_CFG, fake,
                                   data_dir=str(tmp_path))
    assert d1 is not None and d1["cached"] is False
    assert d1["source"] == "llm" and d1["rows"] == VALID_ZHONG
    calls_after_first = len(fake.calls)

    d2 = cjk_glyph.get_or_generate("中", "pixel", LLM_CFG, fake,
                                   data_dir=str(tmp_path))
    assert d2["cached"] is True
    assert d2["rows"] == d1["rows"]
    assert len(fake.calls) == calls_after_first  # 缓存命中不触发 LLM


def test_unknown_style_raises(tmp_path):
    with pytest.raises(ValueError):
        cjk_glyph.get_or_generate("中", "nope", LLM_CFG, FakeLLM([]),
                                  data_dir=str(tmp_path))


def test_prompt_version_change_regenerates(tmp_path, monkeypatch):
    fake = FakeLLM([GOOD_REPLY, GOOD_REPLY])
    cjk_glyph.get_or_generate("中", "pixel", LLM_CFG, fake,
                              data_dir=str(tmp_path))
    assert len(fake.calls) == 1
    monkeypatch.setattr(cjk_glyph, "PROMPT_VERSION", "v2-test")
    d = cjk_glyph.get_or_generate("中", "pixel", LLM_CFG, fake,
                                  data_dir=str(tmp_path))
    assert d["cached"] is False          # 版本变了 → 旧缓存视为 miss
    assert len(fake.calls) == 2


def test_inflight_dedup(tmp_path):
    """同 (style, ch) 的并发请求只生成一次。"""
    started = threading.Event()
    release = threading.Event()

    class SlowLLM(FakeLLM):
        def chat(self, *args, **kwargs):
            started.set()
            release.wait(timeout=10)
            return super().chat(*args, **kwargs)

    fake = SlowLLM([GOOD_REPLY])
    results = {}

    def gen():
        results["first"] = cjk_glyph.get_or_generate(
            "中", "pixel", LLM_CFG, fake, data_dir=str(tmp_path))

    t = threading.Thread(target=gen)
    t.start()
    assert started.wait(timeout=10)
    second = cjk_glyph.get_or_generate("中", "pixel", LLM_CFG, fake,
                                       data_dir=str(tmp_path))
    release.set()
    t.join(timeout=10)
    assert len(fake.calls) == 1          # 第二个请求没有再调 LLM
    assert results["first"] is not None
    assert second is not None
    assert second["rows"] == results["first"]["rows"]
    assert second["cached"] is True      # 等待方最终从缓存取到
