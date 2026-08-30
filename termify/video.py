"""Video frame extraction via ffmpeg (backend only, NOT ffmpeg.wasm).

Complies with the iron rule: video processing goes through backend ffmpeg,
never frontend ffmpeg.wasm. Extracted frames are fed into the existing
termify.convert() pipeline for charset rendering.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid

# Size cap is configurable so self-hosters can raise it; the public demo
# keeps a disk-guard. There is NO duration cap: long videos are accepted and
# sampled at an adaptive fps (see adaptive_fps) so conversion stays bounded.
MAX_VIDEO_BYTES = int(os.environ.get("TERMIFY_MAX_VIDEO_MB", "200")) * 1024 * 1024
VALID_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}

# Adaptive sampling targets: keep the extracted frame count bounded so a
# sync conversion request finishes regardless of source length.
TARGET_MAX_FRAMES = 900
BASE_FPS = 10.0
MIN_FPS = 0.5


class VideoError(Exception):
    """Raised when video processing fails."""


def _ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


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
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
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


def extract_frames(
    video_path: str,
    max_duration: int | None = None,
) -> tuple[str, float]:
    """Extract frames from a video file into a temp directory.

    Returns (frames_dir, fps). The caller is responsible for cleaning up
    frames_dir when done. Raises VideoError on failure.

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

    # Create temp dir for frames
    frames_dir = tempfile.mkdtemp(prefix="termify_frames_")

    # Extract frames with ffmpeg: optional -t window, adaptive -r sampling
    # -an = no audio, -sn = no subtitles
    cmd = [
        _ffmpeg_path(), "-y",
        "-i", video_path,
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
        raise VideoError("ffmpeg timed out")
    except OSError as exc:
        raise VideoError(f"ffmpeg execution failed: {exc}")

    if result.returncode != 0:
        # Clean up and report
        stderr = result.stderr.decode("utf-8", errors="replace")[-500:]
        shutil.rmtree(frames_dir, ignore_errors=True)
        raise VideoError(f"ffmpeg failed (rc={result.returncode}): {stderr}")

    # Count extracted frames
    frames = sorted(
        f for f in os.listdir(frames_dir) if f.startswith("frame_") and f.endswith(".png")
    )
    if not frames:
        shutil.rmtree(frames_dir, ignore_errors=True)
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
                       delete_source: bool = False):
    """Extract frames + render every frame + clean up, in one call.

    Returns a FrameSequence. When ``delete_source`` is set the temp video
    file is removed along with the extracted frames, whatever the outcome.
    Raises VideoError for extraction problems; other exceptions propagate.
    """
    from termify.engine import FrameSequence, render_frame, scale_frame
    from PIL import Image

    frames_dir, fps = extract_frames(video_path)
    try:
        lines_per_frame = []
        for fpath in frames_dir_to_images(frames_dir):
            img = Image.open(fpath).convert("RGB")
            scaled = scale_frame(img, width, height)
            lines_per_frame.append(render_frame(scaled, charset, width, height))
    finally:
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
