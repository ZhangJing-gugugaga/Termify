"""Video frame extraction via ffmpeg (backend only, NOT ffmpeg.wasm).

Complies with the iron rule: video processing goes through backend ffmpeg,
never frontend ffmpeg.wasm. Extracted frames are fed into the existing
termify.convert() pipeline for charset rendering.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import uuid

from termify.paths import ffmpeg_path as _ffmpeg_path
from termify.paths import ffprobe_path as _ffprobe_path

# Size cap is configurable so self-hosters can raise it; the public demo
# keeps a disk-guard. There is NO duration cap: long videos are accepted and
# sampled at an adaptive fps (see adaptive_fps) so conversion stays bounded.
# 公网 demo 口径统一为 20MB（与 Flask MAX_CONTENT_LENGTH / 前端文案一致）；
# 自部署可用 TERMIFY_MAX_VIDEO_MB 调高。导出（encode）侧不受此限制。
MAX_VIDEO_BYTES = int(os.environ.get("TERMIFY_MAX_VIDEO_MB", "20")) * 1024 * 1024
VALID_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}

# Adaptive sampling targets: keep the extracted frame count bounded so a
# sync conversion request finishes regardless of source length.
TARGET_MAX_FRAMES = 900
BASE_FPS = 10.0
MIN_FPS = 0.5


class VideoError(Exception):
    """Raised when video processing fails."""


def _probe_stderr(video_path: str) -> str:
    """ffprobe 缺失时的容器信息回退：读 ``ffmpeg -i`` 的 stderr。

    不指定输出文件时 ffmpeg 会以 rc=1 退出并把容器元数据（Duration /
    Stream 行）打到 stderr——只解析这段文本，不做任何转码。桌面包只捆绑
    ffmpeg.exe（不捆绑 ffprobe.exe，省 ~100MB），时长探测与音轨探测因此
    需要这条回退路径，否则无 ffprobe 的机器上抽帧固定 10fps、音轨静默丢失。
    """
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        return ""
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", video_path],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stderr or ""


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
_AUDIO_STREAM_RE = re.compile(r"Stream #\d+:\d+.*?: Audio:")


def _parse_duration(text: str) -> float | None:
    """Parse the ffmpeg ``Duration: HH:MM:SS.ss`` field, or None."""
    match = _DURATION_RE.search(text)
    if not match:
        return None
    try:
        return (int(match.group(1)) * 3600 + int(match.group(2)) * 60
                + float(match.group(3)))
    except ValueError:
        return None


def adaptive_fps(duration_sec: float | None) -> float:
    """Sample rate that keeps ~TARGET_MAX_FRAMES for the given duration.

    Short videos get the full 10 fps; long ones are spread out so the whole
    timeline is represented at reduced fps instead of being cut off.
    """
    if not duration_sec or duration_sec <= 0:
        return BASE_FPS
    return max(MIN_FPS, min(BASE_FPS, TARGET_MAX_FRAMES / duration_sec))


def probe_duration(video_path: str) -> float | None:
    """Return video duration in seconds via ffprobe, or None if unknown."""
    ffprobe = _ffprobe_path()
    if not ffprobe:
        return _parse_duration(_probe_stderr(video_path))
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (ValueError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def has_audio_stream(video_path: str) -> bool:
    """True iff the container has at least one audio stream (ffprobe)."""
    ffprobe = _ffprobe_path()
    if not ffprobe:
        return bool(_AUDIO_STREAM_RE.search(_probe_stderr(video_path)))
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "audio" in result.stdout


def extract_audio(video_path: str, out_path: str) -> str | None:
    """Extract the audio track to AAC ``out_path``; None when no audio.

    Best-effort by design: a failure to extract audio must never fail the
    video import, so every error path returns None instead of raising.
    Callers must run this BEFORE the source video is deleted.
    """
    if not _ffmpeg_path() or not has_audio_stream(video_path):
        return None
    try:
        result = subprocess.run(
            [_ffmpeg_path(), "-y", "-loglevel", "error",
             "-i", video_path,
             "-vn", "-acodec", "aac", "-b:a", "128k", "-ac", "2",
             out_path],
            capture_output=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not os.path.isfile(out_path) \
            or os.path.getsize(out_path) == 0:
        return None
    return out_path


def mux_audio_file(video_path: str, audio_path: str, out_path: str) -> str:
    """Merge an audio track into a finished MP4 (video stream copied).

    ``-shortest`` trims the audio when the video ends first. Raises
    VideoError on failure; ``out_path`` is only replaced on success.
    """
    if not _ffmpeg_path():
        raise VideoError("ffmpeg is not installed or not on PATH")
    fd, tmp_out = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    os.remove(tmp_out)  # ffmpeg creates its own output file
    cmd = [
        _ffmpeg_path(), "-y", "-loglevel", "error",
        "-i", video_path, "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
        "-shortest", "-movflags", "+faststart",
        tmp_out,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300,
                                shell=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoError(f"audio mux failed: {exc}")
    if result.returncode != 0 or not os.path.isfile(tmp_out) \
            or os.path.getsize(tmp_out) == 0:
        stderr = result.stderr.decode("utf-8", errors="replace")[-300:]
        raise VideoError(f"audio mux failed: {stderr}")
    shutil.move(tmp_out, out_path)
    return out_path


def extract_frames(
    video_path: str,
    max_duration: int | None = None,
    out_dir: str | None = None,
) -> tuple[str, float]:
    """Extract frames from a video file into a directory.

    Returns (frames_dir, fps). When ``out_dir`` is given, frames are written
    there (created if missing) and it is NOT removed on failure — the caller
    owns that directory. Otherwise a fresh temp dir is used and removed on
    failure. Raises VideoError on failure.

    Duration is unbounded; the sampling fps adapts so long videos yield
    ~TARGET_MAX_FRAMES spread across the whole timeline. ``max_duration``
    is kept as an optional parameter for callers that want a trim window.
    """
    if not _ffmpeg_path():
        raise VideoError("ffmpeg is not installed or not on PATH")

    # Validate file size before processing
    file_size = os.path.getsize(video_path)
    if file_size > MAX_VIDEO_BYTES:
        raise VideoError(
            f"Video file exceeds {MAX_VIDEO_BYTES // (1024 * 1024)}MB limit "
            f"(自部署可用 TERMIFY_MAX_VIDEO_MB 调整)"
        )

    duration = probe_duration(video_path)
    fps = adaptive_fps(duration)

    caller_owned = out_dir is not None
    if caller_owned:
        frames_dir = out_dir
        os.makedirs(frames_dir, exist_ok=True)
    else:
        frames_dir = tempfile.mkdtemp(prefix="termify_frames_")

    def _cleanup_frames_dir() -> None:
        if not caller_owned:
            shutil.rmtree(frames_dir, ignore_errors=True)

    # Extract frames with ffmpeg: optional -t window, adaptive -r sampling
    # -an = no audio, -sn = no subtitles
    # Frames are downscaled to fit 400x240 (the largest source any terminal
    # rendering needs: 200x60 braille = 2x4 px/cell). Full-resolution frames
    # would make every later style/size switch pay a huge PNG-decode cost.
    cmd = [
        _ffmpeg_path(), "-y",
        "-i", video_path,
        "-vf", "scale=w='min(iw,400)':h='min(ih,240)':force_original_aspect_ratio=decrease",
    ]
    if max_duration:
        cmd += ["-t", str(max_duration)]
    cmd += [
        "-r", str(fps),
        "-an", "-sn",
        "-q:v", "2",
        os.path.join(frames_dir, "frame_%05d.png"),
    ]

    timeout = (max_duration + 30) if max_duration else 600
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _cleanup_frames_dir()
        raise VideoError("ffmpeg timed out")
    except OSError as exc:
        _cleanup_frames_dir()
        raise VideoError(f"ffmpeg execution failed: {exc}")

    if result.returncode != 0:
        # Clean up and report
        stderr = result.stderr.decode("utf-8", errors="replace")[-500:]
        _cleanup_frames_dir()
        raise VideoError(f"ffmpeg failed (rc={result.returncode}): {stderr}")

    # Count extracted frames
    frames = sorted(
        f for f in os.listdir(frames_dir) if f.startswith("frame_") and f.endswith(".png")
    )
    if not frames:
        _cleanup_frames_dir()
        raise VideoError("ffmpeg produced no frames (corrupt or empty video)")

    return frames_dir, fps


def frames_dir_to_images(frames_dir: str) -> list[str]:
    """Return sorted list of PNG frame paths from a frames directory."""
    return sorted(
        os.path.join(frames_dir, f)
        for f in os.listdir(frames_dir)
        if f.startswith("frame_") and f.endswith(".png")
    )


def convert_video_file(video_path: str, charset: str = "ascii",
                       width: int = 80, height: int = 24,
                       delete_source: bool = False,
                       frames_out_dir: str | None = None):
    """Extract frames + render every frame, in one call.

    Returns a FrameSequence. When ``delete_source`` is set the temp video
    file is removed, whatever the outcome. When ``frames_out_dir`` is given,
    frames are extracted there and KEPT after return (so a video task can
    re-render any charset/size later); otherwise a temp dir is used and
    removed. Raises VideoError for extraction problems; other exceptions
    propagate.
    """
    from termify.engine import FrameSequence, render_frame, scale_frame
    from PIL import Image

    caller_owned_frames = frames_out_dir is not None
    frames_dir, fps = extract_frames(video_path, out_dir=frames_out_dir)
    try:
        lines_per_frame = []
        for fpath in frames_dir_to_images(frames_dir):
            img = Image.open(fpath).convert("RGB")
            scaled = scale_frame(img, width, height)
            lines_per_frame.append(render_frame(scaled, charset, width, height))
    finally:
        if not caller_owned_frames:
            shutil.rmtree(frames_dir, ignore_errors=True)
        if delete_source and os.path.isfile(video_path):
            os.remove(video_path)

    interval = 1.0 / fps
    return FrameSequence(
        lines_per_frame=lines_per_frame,
        interval=interval,
        width=width,
        height=height,
        charset=charset,
    )


def _scale_dims(charset: str, width: int, height: int) -> tuple[int, int]:
    """Per-charset source scaling (mirrors engine.convert's rules)."""
    if charset == "blocks":
        return width, height * 2
    if charset == "braille":
        return width * 2, height * 4
    return width, height


def _pad_frame_lines(lines: list[str], width: int, height: int) -> list[str]:
    """字符空间补齐（与 engine._pad_frame 同规则）：贴合画比网格小的部分
    用空格/空行居中补齐，绝不在像素空间垫黑（会被字符化成实心色块）。"""
    row_pad = max(0, height - len(lines))
    top = row_pad // 2
    out = [" " * width] * top
    for ln in lines:
        lead = max(0, (width - len(ln)) // 2)
        out.append(" " * lead + ln + " " * max(0, width - len(ln) - lead))
    out.extend([" " * width] * (height - top - len(lines)))
    return out


def _src_size(frames_dir: str) -> tuple[int, int]:
    """源帧尺寸（读第一帧的文件头，不解码全部）。"""
    from PIL import Image

    with Image.open(frames_dir_to_images(frames_dir)[0]) as im:
        return im.size


def _stride_pick(items, max_frames: int | None):
    """均匀抽稀到 max_frames（保留首尾）；不足则原样返回。"""
    if not max_frames or len(items) <= max_frames or max_frames < 2:
        return items
    last = len(items) - 1
    step = max_frames - 1
    return [items[int(round(i * last / step))] for i in range(max_frames)]


def sequence_from_frames_dir(frames_dir: str, charset: str, width: int, height: int,
                             interval: float, charset_ramp=None,
                             color_mode="mono", fg_color=None, bg_color=None,
                             max_frames: int | None = None):
    """Rebuild a FrameSequence from a persisted per-task frames directory.

    Used for video tasks: no ffmpeg re-extraction, just PIL re-rendering, so
    switching charset / size after upload stays fast. fg/bg_color feed the
    same single-colour override path as convert() so exported video-task
    products match the preview palette.
    max_frames: 渲染**前**对源帧均匀抽稀。画廊作品页的大网格预览靠它把
    「格 × 帧」摁在预算内——体积守卫是渲染之后才跑的，400 列 × 514 帧
    在 1~2 vCPU 的机器上能直接把进程 OOM 掉（2026-09-28 实测本地开发
    服务就是这样被系统杀掉、页面全线 Failed to fetch）。
    """
    from termify.engine import FrameSequence, render_frame
    from termify.frames import fit_cells, scale_frame
    from PIL import Image

    fit_w, fit_h = fit_cells(*_src_size(frames_dir), width, height)
    if charset == "blocks":
        sw, sh = fit_w, fit_h * 2
    elif charset == "braille":
        sw, sh = fit_w * 2, fit_h * 4
    else:
        sw, sh = fit_w, fit_h
    lines_per_frame = []
    for fpath in _stride_pick(frames_dir_to_images(frames_dir), max_frames):
        img = Image.open(fpath).convert("RGB")
        # 比例已在字符空间贴合，像素画布直接拉到贴合尺寸
        scaled = scale_frame(img, sw, sh, keep_aspect=False)
        lines_per_frame.append(_pad_frame_lines(
            render_frame(scaled, charset, sw, sh,
                         fg_color=fg_color,
                         bg_color=bg_color,
                         charset_ramp=charset_ramp,
                         color_mode=color_mode),
            width, height))
    return FrameSequence(
        lines_per_frame=lines_per_frame,
        interval=interval if interval and interval > 0 else 0.1,
        width=width,
        height=height,
        charset=charset,
    )


def validate_video(path: str) -> None:
    """Validate video by extension + file size. Duration is unbounded."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in VALID_VIDEO_EXTS:
        raise VideoError(f"Unsupported video format: {ext}")

    file_size = os.path.getsize(path)
    if file_size > MAX_VIDEO_BYTES:
        raise VideoError(
            f"Video file exceeds {MAX_VIDEO_BYTES // (1024 * 1024)}MB limit "
            f"(自部署可用 TERMIFY_MAX_VIDEO_MB 调整)"
        )
