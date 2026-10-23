"""T40 — /v/ 作品页配色切换对服务端预览 / 下载 / 嵌入生效（强制单色）。

前端契约（static/js/view_work_page.js，纯静态断言）：
- viewerQueryColors()：curTheme 非 "saved" 时改发 color=mono + 主题 fg/bg，
  不再沿用作品保存的 params.color/fg/bg；"saved" 分支逐字保持原行为
- exportColorsQuery()：主题分支补上 color=mono（缺它时服务端
  _parse_color_mode 回退到作品保存的 source，fg/bg 被忽略 → 下载/嵌入不变色）
- 预览 / 下载 / 嵌入三条出口共用这两个函数，不各拼一套颜色参数
"""

from __future__ import annotations

from pathlib import Path

# S-3：viewer 脚本已外置到 static/js/view_work_page.js
TEMPLATE = (Path(__file__).resolve().parent.parent
            / "static" / "js" / "view_work_page.js")


def _source() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _js_function(name: str) -> str:
    """Return the verbatim source of one top-level JS function."""
    src = _source()
    start = src.index("function %s(" % name)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("unbalanced JS function: %s" % name)


# ── 预览：服务端渲染颜色参数 ─────────────────────────────────

def test_viewer_query_colors_forces_mono_for_theme():
    body = _js_function("viewerQueryColors")
    assert ('var themed = (curTheme !== "saved" && THEME_COLORS[curTheme])'
            ' ? THEME_COLORS[curTheme] : null;') in body
    assert 'q += "&color=mono";' in body
    assert "var qfg = themed ? themed.fg : params.fg;" in body
    assert "var qbg = themed ? themed.bg : params.bg;" in body
    # mono 必须先于 fg/bg 拼进 query（断言拼装顺序）
    assert body.index('q += "&color=mono"') < body.index("var qfg")


def test_viewer_query_colors_saved_branch_unchanged():
    body = _js_function("viewerQueryColors")
    # 选「原作」时仍沿用作品保存的 color/fg/bg（旧行为逐字保留）
    assert 'else if (params.color) {' in body
    assert 'q += "&color=" + encodeURIComponent(String(params.color));' in body
    assert 'if (isRgbArr(qfg)) q += "&fg=rgb("' in body
    assert 'if (isRgbArr(qbg)) q += "&bg=rgb("' in body


# ── 下载 / 嵌入：导出颜色参数 ───────────────────────────────

def test_export_query_colors_adds_mono():
    body = _js_function("exportColorsQuery")
    assert ('return "&color=mono&fg=rgb(" + t.fg.join(",")'
            ' + ")&bg=rgb(" + t.bg.join(",") + ")" + rampQ;') in body
    # saved 分支复用同一份实现，不做两套
    assert "return viewerQueryColors();" in body


# ── 三条出口一致性 ──────────────────────────────────────────

def test_preview_download_embed_share_one_color_query():
    src = _source()
    preview_builders = [ln for ln in src.splitlines()
                        if '"/api/gallery/preview/" + WORK_ID' in ln]
    assert len(preview_builders) == 1, preview_builders
    assert "viewerQueryColors();" in preview_builders[0]

    download_builders = [ln for ln in src.splitlines()
                         if '"/api/gallery/download/" + WORK_ID' in ln]
    assert len(download_builders) == 1, download_builders
    assert "exportColorsQuery();" in download_builders[0]
    # 嵌入（format=embed）复用同一个 url 变量，不另拼颜色参数
    assert 'if (fmt === "embed") {' in src
