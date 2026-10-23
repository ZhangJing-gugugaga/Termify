"""F2 — 无素材态点暂停是死控件：class 翻了个儿，其实什么都没暂停。

现场：`startPlayer` 首行 `if (playing || S.frames.length < 2) return;` 先跑了，
`pausePlayer` 却没有对称守卫，于是没有素材时点暂停只会 remove/add 一次 active，
`playing` 一直是 false —— 按钮说"暂停了"，实际从来没播过。

修法：两个入口共用同一个前置条件 `canPlay()`（`S.frames.length >= 2`），条件不成立
就早返回；按钮的 `.active` 只从 `playing` 这一个真相派生，别处不再各写一份。

本文件两层断言：
1. 源码契约（便宜、每次都跑）：守卫对称、active 只在 syncPlayerControls 里改。
2. 真行为（需要 node）：tests/js_player_state_machine.mjs 把 app.js 里那四个函数
   原文切出来真的跑一遍，点无素材的播放/暂停，读 class 与 playing。
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
NODE_HARNESS = REPO_ROOT / "tests" / "js_player_state_machine.mjs"


def read_app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def function_body(src: str, name: str) -> str:
    """按 app.js 的两空格缩进风格切出一个函数的原文（含签名行到闭合行）。"""
    lines = src.split("\n")
    pattern = re.compile(r"^  function " + re.escape(name) + r"\s*\(")
    start = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
    assert start is not None, "app.js 里找不到 function %s" % name
    for i in range(start + 1, len(lines)):
        if lines[i] == "  }":
            return "\n".join(lines[start:i + 1])
    raise AssertionError("function %s 没有闭合" % name)


def first_code_line(body: str) -> str:
    """函数体里第一条真正的代码（跳过签名、空行、注释）。"""
    for line in body.split("\n")[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*")):
            continue
        return stripped
    raise AssertionError("函数体是空的")


def test_can_play_is_the_two_frame_threshold():
    body = function_body(read_app_js(), "canPlay")
    assert "S.frames.length >= 2" in body, body


def test_both_entries_guard_on_the_same_precondition():
    src = read_app_js()
    start = function_body(src, "startPlayer")
    pause = function_body(src, "pausePlayer")
    assert "!canPlay()" in first_code_line(start), start
    assert "!canPlay()" in first_code_line(pause), pause
    assert "return;" in first_code_line(start) and "return;" in first_code_line(pause)


def test_pause_does_not_flip_state_before_its_guard():
    """守卫必须是第一条语句——先 playing=false 再 return 就还是空翻。"""
    pause = function_body(read_app_js(), "pausePlayer")
    guard = first_code_line(pause)
    assert guard.startswith("if ("), guard
    assert guard.index("return;") > guard.index("!playing")


def test_active_class_is_only_touched_by_the_sync_helper():
    """播放/暂停按钮的 active 只许由 syncPlayerControls 统一翻——别再各写一份。"""
    src = read_app_js()
    for btn in ("playBtn", "pauseBtn"):
        assert btn + '.classList.add(' not in src, btn
        assert btn + '.classList.remove(' not in src, btn
        assert btn + '.classList.toggle(' in function_body(src, "syncPlayerControls"), btn
    sync = function_body(src, "syncPlayerControls")
    assert 'playBtn.classList.toggle("active", playing)' in sync
    assert 'pauseBtn.classList.toggle("active", !playing)' in sync


def test_every_playing_mutation_resyncs_the_buttons():
    """playing 有三处会被置 false（暂停、拖进度条、重渲染前），都要跟着刷按钮。"""
    src = read_app_js()
    tails = [src[m.end():m.end() + 300] for m in re.finditer(r"playing = false;", src)]
    assert len(tails) >= 3, len(tails)
    for tail in tails:
        assert "syncPlayerControls()" in tail, "playing=false 之后没刷按钮：%r" % tail[:120]


def test_init_syncs_buttons_once_after_binding():
    src = read_app_js()
    bind = src.index('pauseBtn.addEventListener("click", pausePlayer)')
    after = src[bind:bind + 400]
    assert "syncPlayerControls();" in after, after


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 跑行为断言")
def test_real_behaviour_without_material():
    proc = subprocess.run(["node", str(NODE_HARNESS)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["failed"] == 0, [c for c in report["checks"] if not c["ok"]]
    names = {c["name"] for c in report["checks"]}
    assert "no-material 点暂停不翻 class" in names
    assert "no-material 点暂停不安排/取消 rAF" in names
    assert "2 帧点暂停：playing=false" in names
