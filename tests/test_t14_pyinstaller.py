"""T14 PyInstaller 一键独立包 — spec 验证 + launcher 导入测试。

验证 termify.spec 结构正确, launcher 可导入, 打包流程可行。
不需要实际运行 pyinstaller (CI 环境可能无 pyinstaller)。
"""

from __future__ import annotations

import ast
import os

import pytest


def test_spec_file_exists():
    """spec 根存在且可 parse。"""
    assert os.path.isfile("termify.spec")
    with open("termify.spec", "r", encoding="utf-8") as f:
        src = f.read()
    # 验证 key sections present
    assert "Analysis(" in src
    assert "termify_launcher.py" in src
    assert "'templates/', 'templates'" in src
    assert "'static/', 'static'" in src
    assert "EXE(" in src
    assert "COLLECT(" in src


def test_launcher_importable():
    """launcher 可 import 且不立即执行 Flask。"""
    # 防止 launcher 启动 Flask
    import sys
    saved = sys.argv
    # Import should be safe (code guarded by __name__ == '__main__')
    try:
        import termify_launcher
        assert hasattr(termify_launcher, "launch")
        assert hasattr(termify_launcher, "_resource_path")
    finally:
        sys.argv = saved


def test_launcher_resource_path_dev():
    """非打包模式下 _resource_path 返回项目根下的相对路径。"""
    import termify_launcher
    p = termify_launcher._resource_path("templates")
    assert p.endswith(os.path.join("templates")) or p.endswith("templates")


def test_version_info_exists():
    """version_info.txt 提供 Windows 版本信息。"""
    assert os.path.isfile("version_info.txt")
    with open("version_info.txt", "r", encoding="utf-8") as f:
        src = f.read()
    assert "VSVersionInfo" in src
    assert "1, 0, 0, 0" in src


def test_icon_exists():
    """打包用 ico 图标存在。"""
    assert os.path.isfile("static/img/icon.ico")


def test_spec_hidden_imports():
    """spec 包含 Flask 关键 hidden imports。"""
    with open("termify.spec", "r", encoding="utf-8") as f:
        src = f.read()
    assert "jinja2.ext" in src
    assert "flask" in src


def test_launcher_template_folder_logic():
    """验证 frozen 模式下 template_folder 设置正确。"""
    import sys
    import os

    # Mock frozen mode
    saved_frozen = getattr(sys, "frozen", False)
    saved_meipass = getattr(sys, "_MEIPASS", None)
    try:
        sys.frozen = True
        sys._MEIPASS = "/fake/bundle"
        import termify_launcher
        p = termify_launcher._resource_path("templates")
        assert "/fake/bundle" in p
    finally:
        if saved_meipass is not None:
            sys._MEIPASS = saved_meipass
        elif hasattr(sys, "_MEIPASS"):
            delattr(sys, "_MEIPASS")
        if saved_frozen:
            sys.frozen = saved_frozen
        elif hasattr(sys, "frozen"):
            delattr(sys, "frozen")
