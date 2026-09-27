"""画廊开关（TERMIFY_ENABLE_GALLERY）相关的测试标记。

依据 docs/DECISION-LOCAL-FIRST-2026-09-22.md（v3.1）§1：开关关闭（桌面包
形态）时画廊路由/页面根本不注册，针对画廊功能的用例在此形态下无意义——
标 ``@requires_gallery`` 让它们在关闭形态下跳过，从而两种形态都能全绿
（验收标准 §5「pytest 双形态（开关开/关）全绿」）。
"""

from __future__ import annotations

import os

import pytest

_OFF_VALUES = ("0", "false", "no", "off")

GALLERY_DISABLED = os.environ.get("TERMIFY_ENABLE_GALLERY", "1").strip().lower() in _OFF_VALUES

requires_gallery = pytest.mark.skipif(
    GALLERY_DISABLED,
    reason="TERMIFY_ENABLE_GALLERY=0：画廊未注册（桌面包形态），跳过画廊相关用例",
)
