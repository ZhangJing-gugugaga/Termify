"""T14 PyInstaller 桌面独立包 — 真实 build 验证 + launcher 测试。

验证 termify.spec 能真实构建出 dist/Termify/Termify.exe。
CI 环境无 pyinstaller 时跳过构建测试。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys

import pytest

HAVE_PYINSTALLER = importlib.util.find_spec("PyInstaller") is not None


def test_spec_file_exists():
    """spec 文件存在且结构正确。"""
    assert os.path.isfile("termify.spec")
    with open("termify.spec", "r", encoding="utf-8") as f:
        src = f.read()
    assert "Analysis(" in src
    assert "termify_launcher.py" in src
    assert "'templates/', 'templates'" in src
    assert "'static/', 'static'" in src
    assert "EXE(" in src
    assert "COLLECT(" in src


def test_version_info_valid():
    """version_info.txt 可被 PyInstaller 正确解析。"""
    assert os.path.isfile("version_info.txt")
    with open("version_info.txt", "r", encoding="utf-8") as f:
        src = f.read()
    # 正确格式: VSVersionInfo(ffi=..., kids=[...])
    assert src.strip().startswith("VSVersionInfo(")
    assert "ffi=FixedFileInfo(" in src
    assert "kids=[" in src


def test_launcher_importable():
    """launcher 可 import 且不立即执行 Flask。"""
    import termify_launcher
    assert hasattr(termify_launcher, "launch")
    assert hasattr(termify_launcher, "_resource_path")


def test_launcher_resource_path_dev():
    """非打包模式下 _resource_path 返回有效路径。"""
    import termify_launcher
    p = termify_launcher._resource_path("templates")
    assert p.endswith("templates")


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


@pytest.mark.skipif(not HAVE_PYINSTALLER, reason="pyinstaller 未安装")
def test_pyinstaller_build_produces_exe(tmp_path):
    """真实运行 pyinstaller build，验证 dist/Termify/Termify.exe 产出。"""
    import PyInstaller.__main__
    build_dir = tmp_path / "build"
    dist_dir = tmp_path / "dist"
    PyInstaller.__main__.run([
        "termify.spec",
        "--workpath", str(build_dir),
        "--distpath", str(dist_dir),
        "--noconfirm",
        "--clean",
    ])
    exe = dist_dir / "Termify" / "Termify.exe"
    assert exe.is_file(), f"exe 不存在: {exe}"
    assert exe.stat().st_size > 1024 * 1024, "exe 过小 (<1MB)，可能构建不完整"
