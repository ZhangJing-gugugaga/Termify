# -*- mode: python ; coding: utf-8 -*-
"""
Termify PyInstaller spec file — build a single-folder Windows .exe.

Usage:
    pyinstaller termify.spec --clean --noconfirm

Output: dist/Termify/Termify.exe (single folder)

ffmpeg 捆绑（本地化决策 Phase 1）：桌面包自带 ffmpeg.exe，用户机器无需预装，
视频上传 / MP4 导出开箱可用。构建机上的 ffmpeg 按 TERMIFY_FFMPEG_BIN →
PATH → 常见安装目录查找；找不到直接报错中止，避免产出一个"看起来正常、
视频功能全废"的包。运行期由 termify.paths.ffmpeg_path() 三级查找定位
（_MEIPASS → 打包目录 → PATH）。
"""

import os
import shutil

from PyInstaller.utils.hooks import collect_data_files


def _find_ffmpeg() -> str | None:
    """定位构建机上的 ffmpeg 可执行文件。"""
    env = os.environ.get("TERMIFY_FFMPEG_BIN")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in (
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"D:\ffmpeg\bin\ffmpeg.exe",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


_ffmpeg = _find_ffmpeg()
if not _ffmpeg:
    raise SystemExit(
        "未找到 ffmpeg：桌面包必须捆绑 ffmpeg.exe（docs/DECISION-LOCAL-FIRST-2026-09-22.md Phase 1）。\n"
        "请安装 ffmpeg 并加入 PATH，或用 TERMIFY_FFMPEG_BIN 指定其绝对路径。"
    )

block_cipher = None

# Collect Flask/Werkzeug internal data files (debug icons, etc.)
flask_datas = collect_data_files('flask', include_py_files=False)

a = Analysis(
    ['termify_launcher.py'],
    pathex=[os.path.abspath('.')],
    # ffmpeg.exe 落到 _internal/（PyInstaller 6.x one-folder），即 sys._MEIPASS 下
    binaries=[(_ffmpeg, '.')],
    datas=[
        ('templates/', 'templates'),
        ('static/', 'static'),
    ] + flask_datas,
    hiddenimports=[
        'jinja2.ext',
        'flask',
        'werkzeug',
        # videofetch.py 在函数内 import yt_dlp（懒加载，缺失时优雅报错），
        # 显式声明以保证收集齐全（yt_dlp 自带 PyInstaller hook 会被触发）。
        'yt_dlp',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas', 'scipy', 'PILtk', 'cryptography'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Termify',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='static/img/icon.ico' if os.path.exists('static/img/icon.ico') else None,
    version='version_info.txt' if os.path.exists('version_info.txt') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    # ffmpeg.exe 是 100MB 级已压缩二进制：UPX 压它既慢又可能损坏文件，
    # 还会触发杀软误报（桌面包要的是双击即用，不是极限体积）。
    upx_exclude=['ffmpeg.exe'],
    name='Termify',
)
