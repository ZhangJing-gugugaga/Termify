"""Termify desktop launcher — opens browser + runs Flask server.

This is the entry point for the PyInstaller-bundled .exe. It starts the
Flask backend on a local port and opens the browser to the player UI.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser


def _resource_path(relative: str) -> str:
    """Get absolute path to a resource (works for dev + PyInstaller bundle)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def launch(host: str = "127.0.0.1", port: int = 5000, open_browser: bool = True) -> None:
    """Start the Termify Flask server (blocking).

    环境变量覆盖（缺省行为不变）：
      - ``TERMIFY_HOST`` / ``TERMIFY_PORT``：换监听地址/端口（端口被占用时用）；
      - ``TERMIFY_NO_BROWSER=1``：不自动打开浏览器——自动化冒烟测试用。
    """
    host = os.environ.get("TERMIFY_HOST", host)
    try:
        port = int(os.environ.get("TERMIFY_PORT", port))
    except ValueError:
        pass  # 非法端口值 → 保持缺省，不让启动直接崩掉
    if os.environ.get("TERMIFY_NO_BROWSER", "").strip().lower() in ("1", "true", "yes", "on"):
        open_browser = False

    # Ensure upload/tmp dirs exist next to the exe
    base_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    for d in ("uploads", "tmp"):
        os.makedirs(os.path.join(base_dir, d), exist_ok=True)

    # Tell Flask where to find templates/static if frozen
    template_dir = _resource_path("templates") if getattr(sys, "frozen", False) else "templates"
    static_dir = _resource_path("static") if getattr(sys, "frozen", False) else "static"

    # 桌面包与云端 0 依赖：关闭画廊（不注册画廊路由/页面、不渲染画廊入口），
    # 产物 .py/.html/.mp4 完全本地自持。
    # docs/DECISION-LOCAL-FIRST-2026-09-22.md §1 —— setdefault 让显式
    # 环境变量覆盖仍然生效（想临时开画廊的用户可自行设 1）。
    os.environ.setdefault("TERMIFY_ENABLE_GALLERY", "0")

    # Late import so errors surface cleanly
    from app import app
    app.template_folder = template_dir
    app.static_folder = static_dir

    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}/")).start()

    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    launch()
