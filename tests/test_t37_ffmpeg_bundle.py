"""T37 ffmpeg 捆绑与三级查找 — 桌面包完全体的资源定位。

依据 docs/DECISION-LOCAL-FIRST-2026-09-22.md Phase 1：
  - `_ffmpeg_path()` 三级查找：PyInstaller 解包目录（sys._MEIPASS）→ 打包
    目录（Termify.exe 同级）→ PATH；
  - termify.spec 捆绑 ffmpeg.exe（构建机找不到时直接报错中止）；
  - yt-dlp 懒加载 import 显式进 hiddenimports；
  - 桌面包只捆 ffmpeg.exe（不捆 ffprobe.exe，省 ~100MB），因此时长/音轨
    探测必须有 ffmpeg 回退路径，否则无 ffprobe 的机器上音轨静默丢失。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from termify import paths as paths_mod
from termify import video as video_mod
from termify.output.video import ffmpeg_available

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAS_FFMPEG = shutil.which("ffmpeg") is not None


# --- 三级查找 -------------------------------------------------------------------

def test_meipass_bundle_wins(tmp_path, monkeypatch):
    """第一级：PyInstaller 解包目录里的 ffmpeg 优先于 PATH。"""
    bundled = tmp_path / "_internal"
    bundled.mkdir()
    fake = bundled / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    fake.write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundled), raising=False)
    monkeypatch.setattr(paths_mod.shutil, "which", lambda _n: "/from/path/ffmpeg")
    assert paths_mod.ffmpeg_path() == str(fake)


def test_exe_dir_bundle_wins_over_path(tmp_path, monkeypatch):
    """第二级：Termify.exe 同级目录（sys.frozen）优先于 PATH。"""
    exe_dir = tmp_path / "Termify"
    exe_dir.mkdir()
    fake = exe_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    fake.write_bytes(b"")
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "Termify.exe"))
    monkeypatch.setattr(paths_mod.shutil, "which", lambda _n: "/from/path/ffmpeg")
    assert paths_mod.ffmpeg_path() == str(fake)


def test_falls_back_to_path(tmp_path, monkeypatch):
    """前两级都没有 → 退回 PATH（开发机 / 系统已装 ffmpeg）。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(empty), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(empty / "Termify.exe"))
    monkeypatch.setattr(paths_mod.shutil, "which", lambda _n: "/from/path/ffmpeg")
    assert paths_mod.ffmpeg_path() == "/from/path/ffmpeg"


def test_returns_none_when_absent(tmp_path, monkeypatch):
    """三级全落空 → None（调用方负责给出友好报错，不得崩溃）。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(empty), raising=False)
    monkeypatch.setattr(paths_mod.shutil, "which", lambda _n: None)
    assert paths_mod.ffmpeg_path() is None


def test_ffprobe_lookup_is_independent(tmp_path, monkeypatch):
    """ffmpeg 与 ffprobe 各自独立查找，互不牵连。"""
    bundled = tmp_path / "_internal"
    bundled.mkdir()
    probe = bundled / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    probe.write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(bundled), raising=False)
    monkeypatch.setattr(paths_mod.shutil, "which", lambda _n: None)
    assert paths_mod.ffmpeg_path() is None, "该目录里没有 ffmpeg，不应命中"
    assert paths_mod.ffprobe_path() == str(probe)


def test_ffmpeg_available_follows_lookup(monkeypatch):
    """ffmpeg_available 走三级查找结果，而不是硬编码 PATH。"""
    monkeypatch.setattr("termify.output.video.ffmpeg_path", lambda: None)
    assert ffmpeg_available() is False
    monkeypatch.setattr("termify.output.video.ffmpeg_path", lambda: "C:/bundle/ffmpeg.exe")
    assert ffmpeg_available() is True


# --- ffprobe 缺失时的 ffmpeg 回退 --------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("  Duration: 00:00:51.10, start: 0.000000, bitrate: 826 kb/s", 51.10),
    ("  Duration: 01:02:03.50, start: 0.0", 3723.5),
    ("  Duration: 00:00:02.00, start: 0.000000", 2.0),
    ("no duration here", None),
    ("Duration: N/A, start: 0.0", None),
])
def test_parse_duration(text, expected):
    """回退路径的 Duration 解析（纯函数，不依赖二进制）。"""
    assert video_mod._parse_duration(text) == expected


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg 未安装")
def test_probe_fallback_matches_ffprobe(tmp_path):
    """无 ffprobe 时，时长与音轨探测结果必须与 ffprobe 一致。"""
    with_audio = tmp_path / "a.mp4"
    silent = tmp_path / "s.mp4"
    for out, extra in ((with_audio, True), (silent, False)):
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-f", "lavfi", "-i", "testsrc=duration=2:size=128x64:rate=10"]
        if extra:
            cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    "-c:a", "aac", "-shortest"]
        cmd += ["-pix_fmt", "yuv420p", str(out)]
        subprocess.run(cmd, check=True, capture_output=True)

    # 先记录 ffprobe 可用时的基线
    baseline = [
        (video_mod.probe_duration(with_audio), video_mod.has_audio_stream(with_audio)),
        (video_mod.probe_duration(silent), video_mod.has_audio_stream(silent)),
    ]
    assert baseline[0][1] is True and baseline[1][1] is False

    # 模拟桌面包：只有 ffmpeg.exe，没有 ffprobe.exe
    original = video_mod._ffprobe_path
    video_mod._ffprobe_path = lambda: None
    try:
        fallback = [
            (video_mod.probe_duration(with_audio), video_mod.has_audio_stream(with_audio)),
            (video_mod.probe_duration(silent), video_mod.has_audio_stream(silent)),
        ]
    finally:
        video_mod._ffprobe_path = original

    assert fallback == baseline, f"回退结果与 ffprobe 不一致: {fallback} != {baseline}"


# --- spec 内容约束 ---------------------------------------------------------------

def test_spec_bundles_ffmpeg_and_ytdlp():
    """spec 必须捆绑 ffmpeg、显式收集 yt_dlp、且不对 ffmpeg 做 UPX。"""
    with open(os.path.join(REPO_ROOT, "termify.spec"), encoding="utf-8") as f:
        src = f.read()
    assert "binaries=" in src
    assert "_find_ffmpeg" in src, "spec 缺少构建机 ffmpeg 定位"
    assert "yt_dlp" in src, "spec 缺少 yt_dlp hiddenimport（懒加载 import 需显式声明）"
    assert "upx_exclude" in src and "ffmpeg.exe" in src, "ffmpeg.exe 应排除 UPX"


def test_launcher_keeps_gallery_off_and_dirs():
    """launcher 仍负责建 uploads/tmp 并在 import app 前注入画廊开关。"""
    with open(os.path.join(REPO_ROOT, "termify_launcher.py"), encoding="utf-8") as f:
        src = f.read()
    assert 'for d in ("uploads", "tmp")' in src
    assert src.find('setdefault("TERMIFY_ENABLE_GALLERY", "0")') < src.find("from app import app")
