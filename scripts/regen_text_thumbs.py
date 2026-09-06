#!/usr/bin/env python3
"""画廊文字作品缩略图重生成（维护脚本，站主手动运行）。

背景：2026-09-06 起服务端 PNG 渲染改用仓库内置 DejaVu Sans Mono
（块字符/盲文/盒线全覆盖）。此前上传的文字作品缩略图由缺字形字体
渲染，观感为"乱码"；本脚本从 params_json.frames 里的原始 art 出发，
重跑 source PNG → 缩略图 → OG 图管线，已有作品无需重新上传。

用法（项目根目录）：
    .venv/Scripts/python.exe scripts/regen_text_thumbs.py
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from termify import gallery as gallery_mod  # noqa: E402
from termify import textart  # noqa: E402

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def main() -> int:
    db = gallery_mod.GalleryDB(os.path.join(DATA_DIR, "termify.db"))
    works, _total = db.list_works(limit=10000, include_private=True)
    regen = 0
    for w in works:
        params = json.loads(w["params_json"])
        if params.get("kind") != "text":
            continue
        frames = params.get("frames") or []
        art = frames[0] if frames else ""
        if not art:
            continue
        work_id = w["id"]
        base = os.path.join(DATA_DIR, "gallery")
        source_path = os.path.join(base, f"{work_id}.png")
        thumb_path = os.path.join(base, f"{work_id}_thumb.gif")
        og_path = os.path.join(base, f"{work_id}_og.png")
        # 原色作品的 SGR 转义不能画进 PNG（与上传路径同规则）
        art_plain = _ANSI_RE.sub("", art) if params.get("color") == "source" \
            else art
        fg = params.get("fg") if isinstance(params.get("fg"), list) else None
        fg = tuple(fg) if fg and len(fg) == 3 else textart.ART_FG_DEFAULT
        try:
            textart.render_art_png(art_plain, source_path, fg=fg)
            gallery_mod.make_thumbnail(source_path, thumb_path, fit=True)
            gallery_mod.make_og_image(source_path, og_path,
                                      w.get("title") or "字符艺术",
                                      w.get("author") or "")
        except Exception as exc:  # noqa: BLE001 — 单作品失败不中断
            print(f"  ✗ {work_id}: {exc}")
            continue
        regen += 1
        print(f"  ✓ {work_id} {w.get('title', '')}")
    print(f"完成：重生成 {regen} 个文字作品缩略图")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
