"""F5 — /v/ 详情页的播放按钮：无素材态点暂停是死控件，且没有 aria-pressed。

现场（F2/F3 只修了 app.js，详情页走的是另一份实现）：
    static/js/view_work_page.js:281  function startPlayer() {
                                         if (playing || frames.length < 2) return;   ← 有守卫
    static/js/view_work_page.js:301  function pausePlayer() {
                                         playing = false;                            ← 没守卫
                                         playBtn.classList.remove("active");          ← 照样翻 class
                                         pauseBtn.classList.add("active");
无素材时点暂停 → 两个按钮换了脸，实际什么都没暂停；全文件 grep aria-pressed = 0 命中。

修法（与 app.js 同一形状，不另创一套）：canPlay() 作为唯一前置条件，
syncPlayerControls() 把 class 与 aria-pressed 一起从 playing 派生。

本文件钉住结构（守卫在第一条语句、class 不再由别处翻、aria 唯一写入点、
首屏对齐、模板按钮接线），再跑 tests/js_view_work_player_state.mjs 的真行为。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VIEW_JS = REPO_ROOT / "static" / "js" / "view_work_page.js"
VIEW_HTML = REPO_ROOT / "templates" / "view_work.html"
NODE_HARNESS = REPO_ROOT / "tests" / "js_view_work_player_state.mjs"

INDENT = " " * 12


def read_view_js() -> str:
    return VIEW_JS.read_text(encoding="utf-8")


def function_body(src: str, name: str) -> str:
    lines = src.split("\n")
    pattern = re.compile("^" + INDENT + "function " + re.escape(name) + r"\s*\(")
    start = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
    assert start is not None, "view_work_page.js 里找不到 function %s" % name
    for i in range(start + 1, len(lines)):
        if lines[i] == INDENT + "}":
            return "\n".join(lines[start:i + 1])
    raise AssertionError("function %s 没有闭合" % name)


def first_code_line(body: str) -> str:
    for line in body.split("\n")[1:]:
        text = line.strip()
        if not text or text.startswith("//") or text == "}":
            continue
        return text
    raise AssertionError("函数体里没有可执行语句")


def test_pause_and_play_share_one_precondition():
    """两个入口同一把尺：既是"对称"，也是"启用但不空转"的唯一保证。"""
    src = read_view_js()
    assert first_code_line(function_body(src, "startPlayer")) == "if (playing || !canPlay()) return;"
    assert first_code_line(function_body(src, "pausePlayer")) == "if (!playing || !canPlay()) return;"
    assert 'function canPlay() {'.replace(" {", "") in function_body(src, "canPlay")


def test_can_play_threshold_is_two_frames():
    body = function_body(read_view_js(), "canPlay")
    assert "frames.length >= 2" in body, body


def test_class_is_never_flipped_outside_the_sync_helper():
    """历史 bug 的形态就是"别处还在一句句 add/remove('active')"。"""
    src = read_view_js()
    for btn in ("playBtn", "pauseBtn"):
        assert btn + '.classList.add(' not in src, btn
        assert btn + '.classList.remove(' not in src, btn


def test_aria_pressed_has_exactly_one_writer():
    src = read_view_js()
    sync_body = function_body(src, "syncPlayerControls")
    for btn in ("playBtn", "pauseBtn"):
        hits = [m.start() for m in re.finditer(btn + r'\.setAttribute\("aria-pressed"', src)]
        assert len(hits) == 1, (btn, len(hits))
        body_start = src.index(INDENT + "function syncPlayerControls(")
        body_end = body_start + len(sync_body)
        assert body_start <= hits[0] <= body_end, btn
    truthy = re.findall(r'aria-pressed",\s*([^;]*?)\);', sync_body)
    assert len(truthy) == 2 and truthy[0] != truthy[1], sync_body
    assert all("playing" in expr for expr in truthy), truthy


def test_first_paint_syncs_to_the_real_state():
    """模板出厂把 Play 标成 active，而真正开播要等第一帧；首屏必须按实际状态对齐。"""
    src = read_view_js()
    anchor = src.index('if (pauseBtn) pauseBtn.addEventListener("click", pausePlayer);')
    window = src[anchor:anchor + 400]
    assert "syncPlayerControls();" in window, window


def test_fallback_leaves_the_buttons_in_pause_state():
    src = read_view_js()
    body = function_body(src, "showPreviewFallback")
    assert re.search(r"playing = false;\s*\n\s*syncPlayerControls\(\);", body), body


def test_template_buttons_are_the_ones_the_script_binds():
    html = VIEW_HTML.read_text(encoding="utf-8")
    assert re.search(r'<button[^>]*id="playBtn"[^>]*title="Play"', html), "详情页没有 #playBtn"
    assert re.search(r'<button[^>]*id="pauseBtn"[^>]*title="Pause"', html), "详情页没有 #pauseBtn"
    src = read_view_js()
    assert 'document.getElementById("playBtn")' in src
    assert 'document.getElementById("pauseBtn")' in src


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 跑行为断言")
def test_no_material_pause_is_inert_on_the_view_page():
    proc = subprocess.run(["node", str(NODE_HARNESS)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(proc.stdout)
    assert report["failed"] == 0, [c for c in report["checks"] if not c["ok"]]
    by_name = {c["name"]: c for c in report["checks"]}
    for name in (
        "无素材首屏：Play 不亮、Pause 亮",
        "无素材首屏：aria-pressed 说暂停",
        "无素材点暂停：playing 仍是 false",
        "无素材点暂停：class 一字不动",
        "无素材点暂停：aria-pressed 一字不动",
        "无素材点暂停：不安排也不取消 rAF",
        "2 帧点播放：aria-pressed 说在播",
        "2 帧点暂停：aria-pressed 说暂停",
    ):
        assert name in by_name, name
        assert by_name[name]["ok"], by_name[name]
