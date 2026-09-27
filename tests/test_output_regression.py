"""输出产物端到端回归测试（任务清单第 4 项）。

覆盖：
  1. 3 帧小序列生成的 .py 实跑：优雅退出（Ctrl+C）后 stdout 含
     "Thanks for using Termify!"、无 Traceback；
  2. 生成的 .html：var FRAMES = ...; 可整段 json.loads、无模板占位符残留；
  3. ascii 与 blocks 两种 charset 都验证；
  4. blocks 模式 JS 已适配整帧字符串（split 后绘制）。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from termify.engine import FrameSequence
from termify.output import render

PY = sys.executable


def _make_seq(charset: str, n: int = 3) -> FrameSequence:
    """n 帧小序列，每帧两行，含 TrueColor ANSI。"""
    lines = [
        "\x1b[38;2;200;40;40mHello\x1b[0m Termify",
        "\x1b[48;2;20;60;120m\x1b[38;2;240;240;240mframe-{0}\x1b[0m".format(0),
    ]
    return FrameSequence(
        lines_per_frame=[list(lines) for _ in range(n)],
        interval=0.05,
        width=16,
        height=2,
        charset=charset,
    )


# ── 1. 生成的 .py 实跑：优雅退出 + 告别语 ─────────────────────


def _run_and_interrupt(script_path, limit=30.0, ready_timeout=25.0):
    """启动播放器，等它真正开始输出后再发 Ctrl+C，返回 (rc, out, err)。

    旧实现是"固定 sleep 1s 后立刻中断"，存在竞态：播放器进程的 Python 启动
    + import 在负载高的机器上（全量长跑、并发子进程）可能超过 1s，此时
    CTRL_C_EVENT 会命中尚未安装 KeyboardInterrupt 处理器的进程——进程直接
    被终止，拿不到告别语、退出码也非 0。这正是 2026-09-22 评估里记录的
    偶发 flaky 根因。改为等待首个 stdout 输出（渲染循环已启动 ⇒ 处理器
    已就绪）再中断，把时序竞态换成确定性信号。
    """
    kwargs = {}
    if os.name == "nt":
        # 新进程组：让 CTRL_C_EVENT 只命中子进程，不波及测试进程本身
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        [PY, str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        **kwargs,
    )

    chunks: list[bytes] = []

    def _drain_stdout() -> None:
        # 必须按字节读：播放器用 ANSI 光标定位逐帧刷新，整帧不含换行
        # （'\\x1b[{{n}};1H{{line}}'），按行迭代会一直阻塞到进程退出。
        try:
            fd = proc.stdout.fileno()  # type: ignore[union-attr]
            while True:
                data = os.read(fd, 4096)
                if not data:
                    break
                chunks.append(data)
        except (ValueError, OSError):  # pragma: no cover — 进程被杀时的收尾
            pass

    reader = threading.Thread(target=_drain_stdout, daemon=True)
    reader.start()

    # 就绪信号：播放器每帧后都 flush stdout，见到输出即说明已在渲染循环里
    deadline = time.monotonic() + ready_timeout
    while not chunks and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)

    if proc.poll() is None:
        if os.name == "nt":
            os.kill(proc.pid, signal.CTRL_C_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        reader.join(timeout=2)
        pytest.fail("播放器未在时限内优雅退出（Ctrl+C 未生效）")
    reader.join(timeout=5)
    out = b"".join(chunks).decode("utf-8", errors="replace")
    err = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
    return proc.returncode, out, err


@pytest.mark.parametrize("charset", ["ascii", "blocks"])
def test_generated_py_runs_and_farewells_on_interrupt(tmp_path, charset):
    """3 帧小序列 py 实跑：Ctrl+C 优雅退出，stdout 含告别语、无 Traceback。"""
    path = tmp_path / f"player_{charset}.py"
    path.write_text(render(_make_seq(charset), "python"), encoding="utf-8")

    rc, out, err = _run_and_interrupt(path)

    assert "Traceback" not in err, f"stderr 含 Traceback: {err[:300]}"
    assert "Thanks for using Termify!" in out, "stdout 缺少告别语（未优雅退出）"
    assert rc == 0, f"退出码非 0: {rc}"
    # 播放期间确实渲染过帧内容
    assert len(out) > len("Thanks for using Termify!")


# ── 2. 生成的 .html：FRAMES 可整段 JSON 解析 + 无占位符残留 ────

_TEMPLATE_PLACEHOLDERS = [
    "{charset}", "{w}", "{h}", "{n}", "{frames}", "{interval}",
    "{is_blocks}", "{audio_uri}", "{audio_mime}", "{pre_style}",
    "{canvas_style}",
]


@pytest.mark.parametrize("charset", ["ascii", "blocks"])
def test_html_frames_json_extractable_and_no_placeholder_residue(charset):
    """var FRAMES = ...; 整段可 json.loads，且无任何模板占位符残留。"""
    src = render(_make_seq(charset), "html")

    start = src.index("var FRAMES = ") + len("var FRAMES = ")
    end = src.index("];", start) + 1
    frames = json.loads(src[start:end])

    assert isinstance(frames, list) and len(frames) == 3
    # 新数据形状：每帧一个字符串，行以 \n 相连；split 后行数与原序列一致
    for frame, expected in zip(frames, _make_seq(charset).lines_per_frame):
        assert isinstance(frame, str)
        if charset == "blocks":
            # blocks 模式存原始 ANSI：split('\n') 后应与原行数组完全一致
            assert frame.split("\n") == expected
        else:
            # 非 blocks 模式存 ansi_to_html 预转换结果
            assert "<span" in frame or "<" in frame

    for ph in _TEMPLATE_PLACEHOLDERS:
        assert ph not in src, f"模板占位符 {ph} 残留"


# ── 3. blocks 模式 JS 适配整帧字符串（静态回归） ───────────────


def test_html_blocks_js_handles_joined_frame_strings():
    """blocks 模式 JS 从整帧字符串 split('\\n') 起步，绘制入口不再 .join。

    注：pytest 无 JS 引擎，这里对生成的 JS 做静态断言锁定适配契约；
    解码正确性（split 后与原行数组一致）已在 JSON 提取测试中验证。
    """
    src = render(_make_seq("blocks"), "html")
    assert "parseBlocksFrame(frameStr)" in src
    assert "frameStr.split('\\n')" in src
    assert "el.innerHTML = FRAMES[frameIdx];" in src
    assert ".join('\\n')" not in src, "JS 仍在对整帧字符串做 .join('\\n')"
