#!/usr/bin/env python3
"""Termify CLI — 图片 / GIF / 视频 → 终端动画（.py / .html）。

纯本地处理，全程不联网、不占服务器资源。

Usage:
    python demo.py <file> [--charset NAME|all] [--width N] [--height N] [--out DIR]
                   [--preview] [--quiet]

支持输入: .gif / .png / .jpg / .mp4 / .webm / .mov / .avi / .mkv
视频不限时长（长视频自动降采样）；--charset all 按核数并行渲染。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

DEFAULT_SAMPLE = Path("sample.gif")

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


def _progress_bar(label: str, done: int, total: int, t0: float, width: int = 22) -> None:
    """单行 stderr 进度条 + ETA（无第三方依赖）。"""
    frac = min(1.0, done / max(1, total))
    elapsed = time.perf_counter() - t0
    eta = (elapsed / done * (total - done)) if done else 0.0
    filled = int(frac * width)
    bar = "█" * filled + "░" * (width - filled)
    sys.stderr.write(
        f"\r  {label} [{bar}] {done}/{total} 帧 · 已用 {elapsed:.0f}s · 预计剩余 {eta:.0f}s "
    )
    if done >= total:
        sys.stderr.write("\n")
    sys.stderr.flush()


def _load_source(src: Path):
    """返回 (帧图片列表, interval, 视频帧临时目录|None)。图片与视频统一入口。"""
    from PIL import Image

    ext = src.suffix.lower()
    if ext in VIDEO_EXTS:
        from termify.video import extract_frames, frames_dir_to_images

        frames_dir, fps = extract_frames(str(src))
        imgs = [Image.open(p).convert("RGB") for p in frames_dir_to_images(frames_dir)]
        interval = 1.0 / fps if fps > 0 else 0.1
        return imgs, interval, frames_dir

    from termify.frames import extract_frames as extract_gif_frames

    pairs = extract_gif_frames(str(src))
    interval = pairs[0][1] if pairs and pairs[0][1] > 0 else 0.1
    return [im for im, _ in pairs], interval, None


def _scale_dims(charset: str, width: int, height: int) -> tuple[int, int]:
    if charset == "blocks":
        return width, height * 2
    if charset == "braille":
        return width * 2, height * 4
    return width, height


def _render_in_parent(src: Path, charset: str, width: int, height: int, quiet: bool):
    """单风格：父进程渲染，带逐帧进度条 + ETA。"""
    from termify.charset import render_frame
    from termify.engine import FrameSequence
    from termify.frames import scale_frame

    frames, interval, video_frames_dir = _load_source(src)
    sw, sh = _scale_dims(charset, width, height)
    lines_per_frame = []
    t0 = time.perf_counter()
    label = f"{charset} {width}x{height}"
    try:
        for i, im in enumerate(frames):
            scaled = scale_frame(im, sw, sh)
            lines_per_frame.append(render_frame(scaled, charset, sw, sh))
            if not quiet:
                _progress_bar(label, i + 1, len(frames), t0)
    finally:
        if video_frames_dir:
            import shutil
            shutil.rmtree(video_frames_dir, ignore_errors=True)
    return FrameSequence(
        lines_per_frame=lines_per_frame,
        interval=interval,
        width=width,
        height=height,
        charset=charset,
    )


def _worker_render(job: dict) -> tuple[str, list, float, str]:
    """--charset all 并行 worker：视频用已抽好的帧目录，图片各自抽取。

    在子进程运行（Windows spawn），不打印进度——父进程按完成数汇报。
    """
    charset = job["charset"]
    src = job["src"]
    width, height = job["width"], job["height"]
    video_frames_dir = job.get("video_frames_dir")
    interval = job.get("interval", 0.1)
    from termify.charset import render_frame
    from termify.engine import FrameSequence
    from termify.frames import scale_frame
    from PIL import Image

    if video_frames_dir:
        from termify.video import frames_dir_to_images
        paths = frames_dir_to_images(video_frames_dir)
        lines_per_frame = []
        for p in paths:
            img = Image.open(p).convert("RGB")
            scaled = scale_frame(img, width, height)
            lines_per_frame.append(render_frame(scaled, charset, width, height))
    else:
        from termify.frames import extract_frames as extract_gif_frames
        pairs = extract_gif_frames(src)
        interval = pairs[0][1] if pairs and pairs[0][1] > 0 else 0.1
        sw, sh = _scale_dims(charset, width, height)
        lines_per_frame = []
        for im, _d in pairs:
            scaled = scale_frame(im, sw, sh)
            lines_per_frame.append(render_frame(scaled, charset, sw, sh))
    return charset, lines_per_frame, interval, ""


def _emit_outputs(out_dir: Path, stem: str, charset: str, lines_per_frame, interval,
                  width: int, height: int) -> list[Path]:
    from termify.engine import FrameSequence
    from termify.output import render

    seq = FrameSequence(
        lines_per_frame=lines_per_frame,
        interval=interval,
        width=width,
        height=height,
        charset=charset,
    )
    py_text = render(seq, "python")
    html_text = render(seq, "html")
    py_path = out_dir / f"{stem}_{charset}.py"
    html_path = out_dir / f"{stem}_{charset}.html"
    py_path.write_text(py_text, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")
    return [py_path, html_path]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Termify CLI — 图片/GIF/视频 → 终端动画（纯本地，不联网）")
    ap.add_argument("image", nargs="?", default=str(DEFAULT_SAMPLE), help="输入文件（图片/GIF/视频）")
    ap.add_argument("--charset", default="ascii", help="charset key 或 'all'")
    ap.add_argument("--width", type=int, default=80)
    ap.add_argument("--height", type=int, default=24)
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--preview", action="store_true", help="打印第一帧（Unicode 字符在旧终端可能乱码）")
    ap.add_argument("--quiet", action="store_true", help="不显示进度条")
    args = ap.parse_args()

    from termify.charset import CHARSETS

    src = Path(args.image)
    if not src.is_file():
        print(f"输入文件不存在: {src}", file=sys.stderr)
        sys.exit(2)

    if args.charset == "custom":
        print("CLI 暂不支持自定义字符集 —— 请用 Web 端创作：", file=sys.stderr)
        print("    python app.py  →  打开 http://127.0.0.1:5000", file=sys.stderr)
        print("    → 风格卡片「自定义字符」→ 弹窗中点选字符库或直接输入", file=sys.stderr)
        sys.exit(2)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    t_all = time.perf_counter()
    written: list[Path] = []

    if args.charset == "all":
        charsets = [cs for cs in CHARSETS if cs != "custom"]
        is_video = src.suffix.lower() in VIDEO_EXTS
        video_frames_dir = None
        interval = 0.1
        n_frames = 0
        if is_video:
            # 视频：父进程抽帧一次，所有 worker 共享帧目录（避免 6× 重复 ffmpeg）
            from termify.video import extract_frames, frames_dir_to_images

            print("视频抽帧中（自适应采样，分辨率已缩放）…", file=sys.stderr)
            frames_dir, fps = extract_frames(str(src))
            video_frames_dir = frames_dir
            interval = 1.0 / fps if fps > 0 else 0.1
            n_frames = len(frames_dir_to_images(frames_dir))
            print(f"  已抽 {n_frames} 帧 @ {fps:.1f}fps", file=sys.stderr)

        jobs = [{
            "charset": cs, "src": str(src), "width": args.width,
            "height": args.height, "video_frames_dir": video_frames_dir,
            "interval": interval,
        } for cs in charsets]

        import multiprocessing as mp
        workers = min(len(jobs), max(1, os.cpu_count() or 1))
        print(f"并行渲染 {len(jobs)} 种字符集（{workers} 进程）…", file=sys.stderr)
        done_count = 0
        t_render = time.perf_counter()
        if workers > 1:
            ctx = mp.get_context("spawn")
            with ctx.Pool(workers) as pool:
                for charset, lines, iv, _ in pool.imap_unordered(_worker_render, jobs):
                    written += _emit_outputs(out_dir, stem, charset, lines, iv,
                                             args.width, args.height)
                    done_count += 1
                    sys.stderr.write(
                        f"\r  [{done_count}/{len(jobs)}] ✓ {charset} ({len(lines)} 帧)   ")
                    sys.stderr.flush()
            sys.stderr.write("\n")
        else:
            for job in jobs:
                charset, lines, iv, _ = _worker_render(job)
                written += _emit_outputs(out_dir, stem, charset, lines, iv,
                                         args.width, args.height)
                done_count += 1
                sys.stderr.write(f"\r  [{done_count}/{len(jobs)}] ✓ {charset}   ")
            sys.stderr.write("\n")
        if video_frames_dir:
            import shutil
            shutil.rmtree(video_frames_dir, ignore_errors=True)
        print(f"渲染完成：{time.perf_counter() - t_render:.1f}s（{workers} 进程并行）",
              file=sys.stderr)
    else:
        if args.charset not in CHARSETS:
            print(f"未知字符集: {args.charset}（可选: {'/'.join(sorted(CHARSETS))} 或 'all'）",
                  file=sys.stderr)
            sys.exit(2)
        seq = _render_in_parent(src, args.charset, args.width, args.height, args.quiet)
        written += _emit_outputs(out_dir, stem, args.charset,
                                 seq.lines_per_frame, seq.interval,
                                 args.width, args.height)
        print(f"帧数={len(seq.lines_per_frame)} 间隔={seq.interval}s 尺寸={seq.width}x{seq.height}")
        if args.preview and seq.lines_per_frame:
            print("第一帧（旧终端可能乱码，建议 Windows Terminal）:")
            for ln in seq.lines_per_frame[0]:
                try:
                    print(ln)
                except UnicodeEncodeError:
                    print(ln.encode("ascii", errors="replace").decode("ascii"))

    # 完成提示：响铃 + 文件清单
    print("\a", end="", file=sys.stderr)
    print("═" * 46)
    print(f"全部完成 ✓ 共 {len(written)} 个文件，用时 {time.perf_counter() - t_all:.1f}s")
    for p in written:
        print(f"  {p}  ({p.stat().st_size // 1024}KB)")
    py_files = [p for p in written if p.suffix == ".py"]
    if py_files:
        print(f"提示: python \"{py_files[0]}\" 直接在终端播放（Ctrl+C 停止）")


if __name__ == "__main__":
    main()
