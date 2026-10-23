"""T47 — /v/ 视频作品换尺寸时渲染缓存必须保持 {map, used} 形状。

契约（static/js/view_work_page.js ↔ static/js/termify-render.js）：
- termify-render.js 的 cachePut() 直接写 cache.map[key]，cacheGet() 读
  cache.map；调用方传入的缓存对象必须有 map（used/budget 可缺省，首写时补）。
- view_work_page.js 换尺寸（尺寸滑杆 change）时整体作废缓存，必须以对象
  字面量重建完整形状；写成裸 `renderCache = {}` 会让 cachePut 抛
  "Cannot set properties of undefined (setting 'l:WxH:0')"，预览停在旧尺寸
  （2026-10-23 实测：拖 200→208 时抛错且不重渲）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEWER = ROOT / "static" / "js" / "view_work_page.js"
RENDER = ROOT / "static" / "js" / "termify-render.js"


def _viewer() -> str:
    return VIEWER.read_text(encoding="utf-8")


def test_no_shape_breaking_reset():
    """任何 renderCache 重置都不得退化成裸空对象。"""
    src = _viewer()
    bad = re.findall(r"renderCache\s*=\s*\{\s*\}", src)
    assert bad == [], "renderCache 重置丢了 map/used 形状：%r" % bad


def test_every_literal_keeps_contract_shape():
    """所有 renderCache 对象字面量都带 map 与 used。"""
    src = _viewer()
    heads = [src[m.end():m.end() + 80]
             for m in re.finditer(r"renderCache\s*=\s*", src)]
    assert len(heads) >= 2, "未找到 renderCache 声明与失效点"  # 声明 + 换尺寸各一处
    for head in heads:
        assert head.lstrip().startswith("{"), head
        assert "map:" in head and "used:" in head, head


def test_render_side_still_dereferences_map():
    """缓存契约的服务端（渲染器）一侧仍是 cache.map[key]。"""
    src = RENDER.read_text(encoding="utf-8")
    assert "cache.map[key] = value;" in src
