"""F3 — 播放按钮缺 aria-pressed：读屏器听不出到底在播还是暂停。

现场：#playBtn / #pauseBtn 都只有 .active 这一个视觉信号（class），没有
aria-pressed，屏幕阅读器把两个按钮都读成一个无状态的"按钮"，播放状态完全不可知。

修法：aria-pressed 与 .active 一起从 playing 派生，集中在 syncPlayerControls()
里写（它就是 F2 里那个"唯一真相"出口），首屏、暂停、拖进度条、重渲染前都会经过
它 —— 不会出现"属性说在播、实际没播"。两个按钮构成一组切换：任意时刻恰好一个
aria-pressed="true"。

本文件钉：属性只在 syncPlayerControls 里写、值与 playing 同步、两个按钮互补、
选择器找得到模板里那两颗按钮，再跑一遍 tests/js_player_state_machine.mjs 的真行为。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_JS = REPO_ROOT / "static" / "js" / "app.js"
INDEX_HTML = REPO_ROOT / "templates" / "index.html"
NODE_HARNESS = REPO_ROOT / "tests" / "js_player_state_machine.mjs"


def read_app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def function_body(src: str, name: str) -> str:
    lines = src.split("\n")
    pattern = re.compile(r"^  function " + re.escape(name) + r"\s*\(")
    start = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
    assert start is not None, "app.js 里找不到 function %s" % name
    for i in range(start + 1, len(lines)):
        if lines[i] == "  }":
            return "\n".join(lines[start:i + 1])
    raise AssertionError("function %s 没有闭合" % name)


def test_aria_pressed_is_derived_from_playing():
    body = function_body(read_app_js(), "syncPlayerControls")
    assert 'playBtn.setAttribute("aria-pressed", playing ? "true" : "false")' in body, body
    assert 'pauseBtn.setAttribute("aria-pressed", playing ? "false" : "true")' in body, body


def test_only_the_sync_helper_writes_aria_pressed_on_the_players():
    """唯一真相原则：别处再写一份就会出现"属性说在播、实际没播"。"""
    src = read_app_js()
    for btn in ("playBtn", "pauseBtn"):
        hits = [m.start() for m in re.finditer(btn + r'\.setAttribute\("aria-pressed"', src)]
        assert len(hits) == 1, (btn, len(hits))
        # 那一处必须落在 syncPlayerControls 的函数体里
        body_start = src.index("function syncPlayerControls(")
        body_end = src.index("\n  }\n", body_start)
        assert body_start <= hits[0] <= body_end, btn


def test_both_buttons_form_one_toggle_group():
    """同一时刻恰好一个按下：aria-pressed 的取值互补，不是各写各的。"""
    body = function_body(read_app_js(), "syncPlayerControls")
    truthy = re.findall(r'aria-pressed",\s*([^;]*?)\);', body)
    assert len(truthy) == 2, body
    assert truthy[0] != truthy[1], body
    assert all("playing" in expr for expr in truthy), body


def test_selectors_still_find_the_real_buttons():
    """aria 只写在 JS 里，选择器一漂就整段失效。"""
    src = read_app_js()
    assert '.control-btn[title="Play"]' in src
    assert '.control-btn[title="Pause"]' in src
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert re.search(r'<button[^>]*class="control-btn[^"]*"[^>]*title="Play"', html), "首页没有 Play 按钮"
    assert re.search(r'<button[^>]*class="control-btn[^"]*"[^>]*title="Pause"', html), "首页没有 Pause 按钮"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 跑行为断言")
def test_aria_pressed_follows_real_play_pause():
    proc = subprocess.run(["node", str(NODE_HARNESS)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["failed"] == 0, [c for c in report["checks"] if not c["ok"]]
    by_name = {c["name"]: c for c in report["checks"]}
    for name in (
        "no-material init aria-pressed 说暂停",
        "no-material 点暂停 aria-pressed 一字不动",
        "2 帧点播放：aria-pressed 说在播",
        "2 帧点暂停：aria-pressed 说暂停",
        "2 帧点播放：class 与 aria-pressed 一致",
        "2 帧点暂停：class 与 aria-pressed 一致",
    ):
        assert name in by_name, name
        assert by_name[name]["ok"], by_name[name]
