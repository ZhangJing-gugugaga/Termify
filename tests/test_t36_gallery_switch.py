"""T36 画廊开关双形态 — TERMIFY_ENABLE_GALLERY 开/关。

依据 docs/DECISION-LOCAL-FIRST-2026-09-22.md（v3.1）§1 + Phase 1「去画廊」：
  - 桌面包 launcher 注入 TERMIFY_ENABLE_GALLERY=0 → 启动时不注册任何画廊
    路由/页面，模板也不渲染画廊入口（避免点了报 404 的死按钮）；
  - 本地 Web / 线上缺省（未设或 =1）行为完全不变；
  - 开关只包路由注册，转化链路（上传/预览/导出）两种形态都必须可用。

GALLERY_ENABLED 在 app.py import 时求值，因此这里用**子进程**探针跑真实双形态，
而不是 monkeypatch 环境变量——后者测不到"注册期"行为。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 子进程探针：真实 import app → 打画廊页/转化链路的实际状态码 + 模板是否含画廊 UI
_PROBE = r'''
import io, json, sys
sys.path.insert(0, sys.argv[1])
from PIL import Image
from app import app, GALLERY_ENABLED

client = app.test_client()
pages = {p: client.get(p).status_code for p in ("/", "/text-art", "/gallery", "/admin")}

index = client.get("/").get_data(as_text=True)
text_art = client.get("/text-art").get_data(as_text=True)

# 转化链路：真实上传一张 PNG → 预览（与画廊无关的核心路径）
buf = io.BytesIO()
Image.new("RGB", (16, 8), (120, 180, 240)).save(buf, format="PNG")
buf.seek(0)
up = client.post("/api/upload", data={"file": (buf, "t36.png")},
                 content_type="multipart/form-data")
task_id = (up.get_json() or {}).get("task_id")
preview_status = client.get("/api/preview/" + task_id).status_code if task_id else None

print("<<<T36>>>" + json.dumps({
    "gallery_enabled": GALLERY_ENABLED,
    "pages": pages,
    "upload_status": up.status_code,
    "preview_status": preview_status,
    "index_gallery_ui": ('href="/gallery"' in index
                         or "shareToGalleryBtn" in index
                         or "galleryModal" in index),
    "text_art_gallery_ui": ('href="/gallery"' in text_art
                            or "taShareBtn" in text_art
                            or "galleryModal" in text_art),
}))
'''


def _base_env(enable_gallery: str | None) -> dict:
    """隔离的产物基准目录 + 指定开关形态的环境变量。

    uploads/ 与 tmp/ 必须预先建好：app.py 只在 ``__main__`` 里 makedirs，
    全新目录下直接 import 会让上传落盘失败（既有已知项）。
    """
    base = tempfile.mkdtemp(prefix="t36_")
    for sub in ("uploads", "tmp"):
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    env = dict(os.environ)
    env["TERMIFY_BASE_DIR"] = base
    env.pop("TERMIFY_ENABLE_GALLERY", None)
    if enable_gallery is not None:
        env["TERMIFY_ENABLE_GALLERY"] = enable_gallery
    return env


def _probe(enable_gallery: str | None) -> dict:
    """在子进程中以指定开关形态启动 app，返回探针结果。"""
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, REPO_ROOT],
        capture_output=True, text=True, env=_base_env(enable_gallery),
        cwd=REPO_ROOT, timeout=180,
    )
    marker = "<<<T36>>>"
    if marker not in proc.stdout:
        pytest.fail(f"探针未输出结果\nstdout={proc.stdout[-500:]}\nstderr={proc.stderr[-1500:]}")
    return json.loads(proc.stdout.split(marker, 1)[1].strip())


@pytest.fixture(scope="module")
def default_form():
    """缺省形态（环境变量未设）——线上/本地 Web 的既有行为。"""
    return _probe(None)


@pytest.fixture(scope="module")
def disabled_form():
    """桌面包形态（TERMIFY_ENABLE_GALLERY=0）。"""
    return _probe("0")


def test_default_form_keeps_gallery(default_form):
    """缺省形态：画廊全功能在，转化链路正常。"""
    assert default_form["gallery_enabled"] is True
    assert default_form["pages"]["/gallery"] == 200
    assert default_form["pages"]["/admin"] == 200
    assert default_form["pages"]["/"] == 200
    assert default_form["pages"]["/text-art"] == 200
    assert default_form["index_gallery_ui"] is True
    assert default_form["text_art_gallery_ui"] is True
    assert default_form["upload_status"] == 200
    assert default_form["preview_status"] == 200


def test_disabled_form_drops_gallery_but_keeps_conversion(disabled_form):
    """桌面包形态：画廊路由 404 + 零画廊 UI，转化链路照常 200。"""
    assert disabled_form["gallery_enabled"] is False
    # 画廊路由/页面在启动期就没注册 → 一律 404
    assert disabled_form["pages"]["/gallery"] == 404
    assert disabled_form["pages"]["/admin"] == 404
    # 非画廊页面与转化链路不受影响
    assert disabled_form["pages"]["/"] == 200
    assert disabled_form["pages"]["/text-art"] == 200
    assert disabled_form["upload_status"] == 200
    assert disabled_form["preview_status"] == 200
    # 模板不再渲染画廊入口/发布按钮/发布弹窗（无死按钮）
    assert disabled_form["index_gallery_ui"] is False
    assert disabled_form["text_art_gallery_ui"] is False


def test_explicit_one_matches_default(default_form):
    """显式 =1 与缺省等价（开关只认关闭语义，其余一律视为开）。"""
    assert _probe("1") == default_form


def test_gallery_api_routes_absent_when_disabled(disabled_form):
    """画廊 API 在关闭形态下同样不可达（不是只隐藏页面）。"""
    assert disabled_form["gallery_enabled"] is False
    probe = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from app import app
client = app.test_client()
out = {}
for path in ("/api/gallery/list", "/api/gallery/custom-tags", "/api/gallery/work/xyz"):
    out[path] = client.get(path).status_code
print("<<<T36>>>" + json.dumps(out))
'''
    env = _base_env("0")
    proc = subprocess.run([sys.executable, "-c", probe, REPO_ROOT],
                          capture_output=True, text=True, env=env, cwd=REPO_ROOT, timeout=120)
    assert "<<<T36>>>" in proc.stdout, proc.stderr[-800:]
    statuses = json.loads(proc.stdout.split("<<<T36>>>", 1)[1].strip())
    assert set(statuses.values()) == {404}, statuses


def test_launcher_injects_switch_before_app_import():
    """桌面包 launcher 必须在 `from app import app` 之前注入开关。

    顺序错了开关就失效（GALLERY_ENABLED 在 import app 时求值）——这里用源码
    位置断言把该约束钉死，防止后续重构把注入挪到 import 之后。
    """
    with open(os.path.join(REPO_ROOT, "termify_launcher.py"), encoding="utf-8") as f:
        src = f.read()
    inject_at = src.find('setdefault("TERMIFY_ENABLE_GALLERY", "0")')
    import_at = src.find("from app import app")
    assert inject_at != -1, "launcher 未注入 TERMIFY_ENABLE_GALLERY"
    assert import_at != -1, "launcher 缺少 `from app import app`"
    assert inject_at < import_at, "开关注入必须在 import app 之前"
