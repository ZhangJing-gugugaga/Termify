"""py 播放器音频自启（Windows PowerShell 分支）回归测试。

历史 bug：生成代码里 ps 命令用 ``"...{}...".format(audio)`` 构建，而命令含
PowerShell 字面脚本块 ``{ Start-Sleep ... }``，str.format() 命中未转义花括号
抛 KeyError/ValueError，被 ``except Exception: return None`` 吞掉 —— Windows
配乐 100% 静默失效。

修复后生成的播放器用字符串拼接构建 ps 命令。本文件把生成的
``_start_audio`` 函数体提取出来独立执行（stub 掉 Popen / 播放器探测），
验证：
  1. 无音频文件时不启动任何进程、返回 None；
  2. 有 music.mp3 时不抛异常、Popen 收到的 ps 含正确注入的 Open 路径，
     且 PowerShell 脚本块花括号原样保留；
  3. 生成的 ps 命令能被真实 PowerShell 解析为合法语法（有 powershell 时）。
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import types

import pytest

from termify.engine import FrameSequence
from termify.output import render

PY = sys.executable


def _make_seq(charset: str = "ascii", n: int = 3) -> FrameSequence:
    """n 帧小序列（含 TrueColor ANSI，模拟真实产物）。"""
    lines = [
        "\x1b[38;2;255;0;0mABC\x1b[0m",
        "\x1b[48;2;0;0;255mdef\x1b[0m",
    ]
    return FrameSequence(
        lines_per_frame=[list(lines) for _ in range(n)],
        interval=0.05,
        width=3,
        height=2,
        charset=charset,
    )


def _extract_function(gen_src: str, name: str) -> str:
    """从生成的播放器源码中提取指定函数的源码文本（独立执行用）。"""
    tree = ast.parse(gen_src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            seg = ast.get_source_segment(gen_src, node)
            assert seg, f"无法提取 {name} 源码段"
            return seg
    raise AssertionError(f"生成的播放器中找不到函数 {name}")


class _FakePopen:
    """记录调用参数的 Popen 替身（不真正启动进程）。"""

    calls: list = []

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        _FakePopen.calls.append(cmd)

    def terminate(self):
        pass

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


def _make_fake_sp():
    _FakePopen.calls = []
    return types.SimpleNamespace(Popen=_FakePopen, DEVNULL=-3)


def _load_start_audio(tmp_path, charset: str, player="powershell", script_dir=None):
    """生成播放器源码，提取 _start_audio 并在受控命名空间中 exec。

    返回 (ns, fake_sp)。_find_audio_player 被 stub 成 powershell 分支，
    平台无关地覆盖出 bug 的路径。script_dir 覆盖播放器所在目录
    （通过 __file__），默认 tmp_path。
    """
    gen_src = render(_make_seq(charset), "python")
    fn_src = _extract_function(gen_src, "_start_audio")
    fake_sp = _make_fake_sp()
    base = tmp_path if script_dir is None else script_dir
    ns = {
        "os": os,
        "__file__": str(base / "player.py"),
        "_audio_proc": None,
        "_sp": fake_sp,
        "_find_audio_player": lambda player=player: player,
    }
    exec(compile(fn_src, "<generated _start_audio>", "exec"), ns)
    return ns, fake_sp


# ── 1. 无音频文件 ─────────────────────────────────────────────


@pytest.mark.parametrize("charset", ["ascii", "blocks"])
def test_start_audio_without_music_file_is_silent_noop(tmp_path, charset):
    """同目录无任何音频文件：不抛异常、不启动进程、返回 None。"""
    ns, fake_sp = _load_start_audio(tmp_path, charset)
    assert ns["_start_audio"]() is None
    assert _FakePopen.calls == []
    assert ns["_audio_proc"] is None


# ── 2. 有 music.mp3：ps 构建成功 + 路径注入正确 ───────────────


@pytest.mark.parametrize("charset", ["ascii", "blocks"])
def test_start_audio_builds_powershell_command_with_injected_path(tmp_path, charset):
    """有 music.mp3：ps 构建不抛异常，Open 路径正确注入，脚本块花括号保留。"""
    music = tmp_path / "music.mp3"
    music.write_bytes(b"\x00\x01")  # 内容无关紧要，只看路径注入

    ns, fake_sp = _load_start_audio(tmp_path, charset)
    proc = ns["_start_audio"]()

    # 修复前：.format() 命中 { Start-Sleep } 字面花括号抛 KeyError 被吞，
    # 返回 None 且 Popen 不会被调用。修复后必须成功走到 Popen。
    assert proc is not None, "ps 命令构建失败（音频静默失效）"
    assert len(_FakePopen.calls) == 1
    cmd = _FakePopen.calls[0]
    assert cmd[0] == "powershell"
    ps = cmd[-1]

    expected_open = "$p.Open('" + str(music).replace("'", "''") + "')"
    assert expected_open in ps, f"Open 路径未正确注入: {ps[:200]}"
    # PowerShell 脚本块花括号必须原样保留（修复前会被 format 吃掉/报错）
    assert "{ Start-Sleep -Milliseconds 100 }" in ps
    # try / catch / while 三处脚本块，花括号成对且无 format 占位符残留
    assert ps.count("{") == 3 and ps.count("}") == 3, "残留未转义的 format 占位符"
    # MediaPlayer 在 PresentationCore 中，Windows PowerShell 默认不加载：
    # 命令需自带按需加载（缺失时 New-Object 直接 TypeNotFound）
    assert "Add-Type -AssemblyName PresentationCore" in ps
    assert "[void][System.Windows.Media.MediaPlayer]" in ps
    # MediaPlayer 播放与等待时长逻辑仍在
    assert "System.Windows.Media.MediaPlayer" in ps
    assert "$p.Play()" in ps
    assert "Start-Sleep -Seconds $dur" in ps
    # _audio_proc 全局被记录，供 _stop_audio 终止
    assert ns["_audio_proc"] is proc


def test_start_audio_escapes_single_quotes_in_path(tmp_path):
    """路径含单引号（如目录名）时应按 PowerShell 规则转义为两个单引号。"""
    quoted_dir = tmp_path / "it's dir"
    quoted_dir.mkdir()
    music = quoted_dir / "music.mp3"
    music.write_bytes(b"\x00")

    ns, fake_sp = _load_start_audio(tmp_path, "ascii", script_dir=quoted_dir)
    assert ns["_start_audio"]() is not None

    ps = _FakePopen.calls[0][-1]
    escaped = str(music).replace("'", "''")
    assert "$p.Open('" + escaped + "')" in ps


# ── 3. 音频候选扩展：mp3 → m4a → wav → ogg ────────────────────


@pytest.mark.parametrize("charset", ["ascii", "blocks"])
def test_start_audio_prefers_first_existing_candidate_in_order(tmp_path, charset):
    """四个候选都在时按 music.mp3 → m4a → wav → ogg 顺序取第一个存在者。"""
    for ext in ("mp3", "m4a", "wav", "ogg"):
        (tmp_path / f"music.{ext}").write_bytes(b"\x00")

    ns, _ = _load_start_audio(tmp_path, charset)
    assert ns["_start_audio"]() is not None
    ps = _FakePopen.calls[0][-1]
    assert "$p.Open('" + str(tmp_path / "music.mp3") + "')" in ps


@pytest.mark.parametrize("charset", ["ascii", "blocks"])
@pytest.mark.parametrize("ext", ["m4a", "wav", "ogg"])
def test_start_audio_falls_back_to_next_candidate(tmp_path, charset, ext):
    """只有某个候选存在时也能生效（mp3 缺失 → 依次回退）。"""
    (tmp_path / f"music.{ext}").write_bytes(b"\x00")

    ns, _ = _load_start_audio(tmp_path, charset)
    assert ns["_start_audio"]() is not None
    ps = _FakePopen.calls[0][-1]
    assert "$p.Open('" + str(tmp_path / f"music.{ext}") + "')" in ps


def test_start_audio_non_powershell_branch_receives_audio_path(tmp_path):
    """非 PowerShell 分支（ffplay 等）也把选中的音频路径传给播放器。"""
    (tmp_path / "music.ogg").write_bytes(b"\x00")

    ns, _ = _load_start_audio(tmp_path, "ascii", player=["ffplay"])
    assert ns["_start_audio"]() is not None
    cmd = _FakePopen.calls[0]
    assert cmd[0] == "ffplay"
    assert cmd[-1] == str(tmp_path / "music.ogg")


# ── 4. 生成的 ps 命令是合法 PowerShell 语法 ───────────────────

powershell = shutil.which("powershell")


@pytest.mark.skipif(powershell is None, reason="powershell 不可用")
def test_generated_powershell_command_parses(tmp_path):
    """把生成的 ps 命令交给真实 PowerShell Parser 做语法校验。"""
    music = tmp_path / "music.mp3"
    music.write_bytes(b"\x00\x01")

    ns, _ = _load_start_audio(tmp_path, "ascii")
    assert ns["_start_audio"]() is not None
    ps = _FakePopen.calls[0][-1]

    # 单引号 here-string 原样承载 ps 命令（ps 中不含行首 '@）
    checker = (
        "$ps = @'\n" + ps + "\n'@\n"
        "$t = $null; $e = $null\n"
        "$null = [System.Management.Automation.Language.Parser]::ParseInput("
        "$ps, [ref]$t, [ref]$e)\n"
        "if ($e.Count -gt 0) { Write-Output ('PARSE_ERR: ' + $e[0].Message); exit 1 }\n"
        "Write-Output 'SYNTAX_OK'\n"
        "exit 0\n"
    )
    ps1 = tmp_path / "_ps_syntax_check.ps1"
    ps1.write_text(checker, encoding="utf-8-sig")

    r = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"PowerShell 语法校验失败: {r.stdout!r} {r.stderr!r}"
    assert "SYNTAX_OK" in r.stdout
