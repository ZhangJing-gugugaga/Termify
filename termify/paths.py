"""仓库根锚定的磁盘产物路径 + 外部二进制查找 — 全项目唯一的资源定位基准。

历史遗留：uploads/ 与 tmp/ 此前按"调用时 CWD"解析（app.py 与 termify/
各自 os.path.join 相对路径），写入与读取只有在 CWD 恒等于仓库根时才自洽；
systemd WorkingDirectory 漂移、PyInstaller 启动器、隔离部署都会断裂
（曾实测 /api/download 500）。本模块把基准统一到仓库根，并提供
``TERMIFY_BASE_DIR`` 环境变量覆盖（测试隔离 / 自定义部署用）。

外部二进制（ffmpeg/ffprobe）走同一模块的三级查找：桌面包把它们捆进
PyInstaller 解包目录后，用户机器上无需预装。

每次调用都重新读取环境变量——测试可在 import 之后随时 monkeypatch，
无 import 顺序陷阱。
"""

from __future__ import annotations

import os
import shutil
import sys


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


def _bundled_binary(name: str) -> str | None:
    """三级查找外部二进制：PyInstaller 解包目录 → 打包目录 → PATH。

    PyInstaller 6.x one-folder 模式下 ``sys._MEIPASS`` 指向 ``_internal/``，
    也就是 ``binaries=[("ffmpeg.exe", ".")]`` 的落点；第二级覆盖"用户手工把
    ffmpeg.exe 丢到 Termify.exe 同级"的情形。前两级都未命中才退回 PATH。
    """
    names = [name]
    if os.name == "nt" and not name.lower().endswith(".exe"):
        names.append(name + ".exe")
    roots: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(meipass)
    if getattr(sys, "frozen", False):
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    for root in roots:
        for candidate in names:
            path = os.path.join(root, candidate)
            if os.path.isfile(path):
                return path
    for candidate in names:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def ffmpeg_path() -> str | None:
    """ffmpeg 可执行文件路径（捆绑优先 → PATH），找不到返回 None。"""
    return _bundled_binary("ffmpeg")


def ffprobe_path() -> str | None:
    """ffprobe 可执行文件路径（捆绑优先 → PATH），找不到返回 None。"""
    return _bundled_binary("ffprobe")
