"""仓库根锚定的磁盘产物路径 — 全项目唯一的 uploads/tmp 基准。

历史遗留：uploads/ 与 tmp/ 此前按"调用时 CWD"解析（app.py 与 termify/
各自 os.path.join 相对路径），写入与读取只有在 CWD 恒等于仓库根时才自洽；
systemd WorkingDirectory 漂移、PyInstaller 启动器、隔离部署都会断裂
（曾实测 /api/download 500）。本模块把基准统一到仓库根，并提供
``TERMIFY_BASE_DIR`` 环境变量覆盖（测试隔离 / 自定义部署用）。

每次调用都重新读取环境变量——测试可在 import 之后随时 monkeypatch，
无 import 顺序陷阱。
"""

from __future__ import annotations

import os


def base_dir() -> str:
    """产物基准目录：``TERMIFY_BASE_DIR`` 优先，默认仓库根（termify/ 上一级）。"""
    env = os.environ.get("TERMIFY_BASE_DIR")
    if env:
        return os.path.abspath(env)
    # paths.py 位于 <仓库根>/termify/ 下，上一级的上一级即仓库根。
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def uploads_dir() -> str:
    """上传源文件 / 视频帧目录 / 音频产物的基准目录（<base>/uploads）。"""
    return os.path.join(base_dir(), "uploads")


def tmp_dir() -> str:
    """转换产物（.py/.html/.mp4）的基准目录（<base>/tmp）。"""
    return os.path.join(base_dir(), "tmp")
