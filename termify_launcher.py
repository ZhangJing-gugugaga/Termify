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
    """Start the Termify Flask server (blocking)."""
    # Ensure upload/tmp dirs exist next to the exe
    base_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    for d in ("uploads", "tmp"):
        os.makedirs(os.path.join(base_dir, d), exist_ok=True)

    # Tell Flask where to find templates/static if frozen
    template_dir = _resource_path("templates") if getattr(sys, "frozen", False) else "templates"
    static_dir = _resource_path("static") if getattr(sys, "frozen", False) else "static"

    # Late import so errors surface cleanly
    from app import app
    app.template_folder = template_dir
    app.static_folder = static_dir

    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}/")).start()

    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    launch()
