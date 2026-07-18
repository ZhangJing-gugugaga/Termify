"""T13b 视频路由 — handleFiles 按扩展名分流到正确端点。"""

from __future__ import annotations

import importlib.util

import pytest


pytestmark = pytest.mark.skipif(
    not importlib.util.find_spec("flask"),
    reason="flask 未安装",
)


def test_js_file_routing_logic():
    """验证 app.js 中 isVideo/isImage 分流 + 端点调用。"""
    with open("static/js/app.js", "r", encoding="utf-8") as f:
        src = f.read()

    # 视频扩展名表
    assert ".mp4" in src and ".webm" in src and ".mov" in src
    # 图片扩展名表
    assert ".png" in src and ".gif" in src and ".jpg" in src
    # 分流函数存在
    assert "function isVideo" in src
    assert "function isImage" in src
    assert "function uploadVideo" in src
    assert "function uploadImages" in src
    # 各调各的端点
    assert '"/api/upload-video"' in src
    assert '"/api/upload-batch"' in src


def test_api_upload_video_route():
    """验证路由注册。"""
    from app import app
    app.config["TESTING"] = True
    rules = {r.endpoint: r.rule for r in app.url_map.iter_rules()}
    assert "upload_video" in rules
    assert rules["upload_video"] == "/api/upload-video"


def test_api_upload_batch_route():
    """验证路由注册。"""
    from app import app
    app.config["TESTING"] = True
    rules = {r.endpoint: r.rule for r in app.url_map.iter_rules()}
    assert "upload_batch" in rules
    assert rules["upload_batch"] == "/api/upload-batch"


def test_js_unsupported_format_toast():
    """不支持的格式应触发 toast。"""
    with open("static/js/app.js", "r", encoding="utf-8") as f:
        src = f.read()
    assert "不支持的格式" in src
