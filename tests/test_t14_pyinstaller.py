"""T14 PyInstaller 桌面独立包 — 真实 build 验证 + launcher 测试。

验证 termify.spec 能真实构建出 dist/Termify/Termify.exe。
CI 环境无 pyinstaller 时跳过构建测试。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

# 直接 import 探测，理由同 test_t7 等文件：find_spec 在长跑进程里会被
# import 状态污染而误报缺失，导致构建冒烟被静默跳过。
try:
    import PyInstaller  # noqa: F401
    HAVE_PYINSTALLER = True
except ImportError:  # pragma: no cover — 未装 pyinstaller 的环境
    HAVE_PYINSTALLER = False


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
    """version_info.txt 提供 Windows 版本信息（结构与版本一致性，
    不钉死具体版本号——发版升级不再误伤）。"""
    assert os.path.isfile("version_info.txt")
    with open("version_info.txt", "r", encoding="utf-8") as f:
        src = f.read()
    assert "VSVersionInfo" in src
    # filevers 元组与 FileVersion 字符串应一致（如 (1, 1, 0, 0) ↔ '1.1.0'，
    # 末段 0 在版本串中可省略）
    import re
    m = re.search(r"filevers=\((\d+), (\d+), (\d+), (\d+)\)", src)
    assert m, "filevers tuple missing"
    parts = list(m.groups())
    if len(parts) >= 3 and parts[-1] == "0":
        parts.pop()
    v = ".".join(parts)
    assert f"u'{v}'" in src, f"FileVersion string {v!r} not in version_info"


def test_icon_exists():
    """打包用 ico 图标存在。"""
    assert os.path.isfile("static/img/icon.ico")


@pytest.fixture(scope="module")
def built_dist(tmp_path_factory):
    """构建一次桌面包，供本模块所有产物级断言复用。

    构建要把 100MB 级 ffmpeg 打进包，很贵——不能每个用例各建一次。
    """
    if not HAVE_PYINSTALLER:
        pytest.skip("pyinstaller 未安装")
    import PyInstaller.__main__
    root = tmp_path_factory.mktemp("pyinstaller")
    dist_dir = root / "dist"
    PyInstaller.__main__.run([
        "termify.spec",
        "--workpath", str(root / "build"),
        "--distpath", str(dist_dir),
        "--noconfirm",
        "--clean",
    ])
    return dist_dir


@pytest.mark.skipif(not HAVE_PYINSTALLER, reason="pyinstaller 未安装")
def test_pyinstaller_build_produces_exe(built_dist):
    """真实运行 pyinstaller build，验证 dist/Termify/Termify.exe 产出。"""
    exe = built_dist / "Termify" / "Termify.exe"
    assert exe.is_file(), f"exe 不存在: {exe}"
    assert exe.stat().st_size > 1024 * 1024, "exe 过小 (<1MB)，可能构建不完整"


@pytest.mark.skipif(not HAVE_PYINSTALLER, reason="pyinstaller 未安装")
def test_pyinstaller_build_bundles_ffmpeg_and_ytdlp(built_dist):
    """构建产物必须自带 ffmpeg.exe 与 yt_dlp（Phase 1「桌面包完全体」）。

    这两项都是"缺了不报错、但功能静默失效"的类型（ffmpeg 缺失 → 视频导入/
    MP4 导出不可用；yt_dlp 缺失 → 视频链接解析报未安装），必须断言在包里。
    纯 Python 模块（含 yt_dlp 的 1000+ 子模块）在 PyInstaller 6 的 one-folder
    模式下位于 exe 内嵌的 PYZ 归档里，不是 _internal/ 下的目录。
    """
    internal = built_dist / "Termify" / "_internal"
    assert (internal / "ffmpeg.exe").is_file(), "产物未捆绑 ffmpeg.exe"

    modules = _pyz_modules(built_dist / "Termify" / "Termify.exe")
    assert "yt_dlp" in modules, "产物未收集 yt_dlp"
    assert "yt_dlp.extractor.youtube" in modules, "yt_dlp 提取器未收集齐全"
    # 核心链路模块也得在（防止 analysis 漏收 termify 包）
    assert {"app", "termify.video", "termify.output.video", "termify.paths"} <= modules


def _pyz_modules(exe_path) -> set[str]:
    """读取 exe 内嵌 PYZ 归档里的模块清单。"""
    from PyInstaller.archive.readers import CArchiveReader
    reader = CArchiveReader(str(exe_path))
    return set(reader.open_embedded_archive("PYZ.pyz").toc)


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http_ok(url: str, timeout: float = 90.0) -> int | None:
    """轮询直到拿到 HTTP 响应，返回状态码；超时返回 None。"""
    import urllib.error
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except Exception:
            time.sleep(0.5)
    return None


@pytest.mark.skipif(not HAVE_PYINSTALLER, reason="pyinstaller 未安装")
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="构建机无 ffmpeg，无法产出捆绑包")
def test_packaged_exe_smoke_without_system_ffmpeg(built_dist, tmp_path):
    """真·双击 exe 冒烟：干净环境（无系统 ffmpeg）下视频 → MP4 全链路（Phase 1 验收）。

    这是 Phase 1 验收标准的自动化版本：
      - PATH 剥掉 ffmpeg/ffprobe，唯一可用的 ffmpeg 是包里捆绑的那个；
      - 启动真实 exe → 首页 200；
      - 桌面包形态：画廊路由 404；
      - 上传视频 → 导出 MP4 → 产物是合法 MP4（ftyp 魔数）。

    `TERMIFY_NO_BROWSER=1` 避免自动弹浏览器，`TERMIFY_PORT` 用空闲端口避免冲突。
    """
    exe = built_dist / "Termify" / "Termify.exe"
    base = tmp_path / "products"
    for sub in ("uploads", "tmp"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    port = _free_port()

    env = dict(os.environ)
    env["PATH"] = r"C:\Windows\System32;C:\Windows"   # 干净环境：无 ffmpeg
    env.pop("TERMIFY_FFMPEG_BIN", None)
    env["TERMIFY_BASE_DIR"] = str(base)
    env["TERMIFY_NO_BROWSER"] = "1"
    env["TERMIFY_PORT"] = str(port)

    proc = subprocess.Popen([str(exe)], env=env, cwd=str(built_dist / "Termify"),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        status = _wait_http_ok(f"http://127.0.0.1:{port}/")
        assert status == 200, f"exe 冒烟未返回 200（拿到 {status}）"

        # 桌面包形态：画廊整体下线
        assert _wait_http_ok(f"http://127.0.0.1:{port}/gallery", timeout=10) == 404

        host_ffmpeg = shutil.which("ffmpeg")
        video = tmp_path / "clip.mp4"
        subprocess.run(
            [host_ffmpeg, "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
             "-pix_fmt", "yuv420p", str(video)],
            check=True, capture_output=True,
        )
        result = _http_roundtrip(port, video)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    assert result["upload_status"] == 200, result
    assert result["generate_status"] == 200, result
    assert result["download_status"] == 200, result
    assert "ftyp" in result["mp4_magic"], f"产物不是合法 MP4: {result['mp4_magic']!r}"


def _http_roundtrip(port: int, video) -> dict:
    """对运行中的实例做 上传视频 → 导出 MP4 → 下载 的 HTTP 往返。"""
    import urllib.request

    boundary = "----termifyT14Boundary"
    payload = video.read_bytes()
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="clip.mp4"\r\n'
        "Content-Type: video/mp4\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/upload-video", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        up_status, up_json = resp.status, json.loads(resp.read())

    gen_body = json.dumps({
        "task_id": up_json["task_id"], "charset": "ascii", "format": "mp4",
        "width": 40, "height": 12}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/generate", data=gen_body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        gen_status, gen_json = resp.status, json.loads(resp.read())

    with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{gen_json['download_url']}", timeout=60) as resp:
        dl_status, head = resp.status, resp.read(12)

    return {"upload_status": up_status, "generate_status": gen_status,
            "download_status": dl_status, "mp4_magic": head.decode("latin-1")}
