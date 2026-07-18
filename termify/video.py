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


MAX_VIDEO_DURATION_SEC = 30
MAX_VIDEO_BYTES = 20 * 1024 * 1024  # same as image upload cap
VALID_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv"}


class VideoError(Exception):
    """Raised when video processing fails."""


def _ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def extract_frames(
    video_path: str,
    max_duration: int = MAX_VIDEO_DURATION_SEC,
) -> tuple[str, float]:
    """Extract frames from a video file into a temp directory.

    Returns (frames_dir, fps). The caller is responsible for cleaning up
    frames_dir when done. Raises VideoError on failure.

    Uses ffmpeg to extract at most max_duration seconds of video at 10 fps.
    """
    if not _ffmpeg_path():
        raise VideoError("ffmpeg is not installed or not on PATH")

    # Validate file size before processing
    file_size = os.path.getsize(video_path)
    if file_size > MAX_VIDEO_BYTES:
        raise VideoError(f"Video file exceeds {MAX_VIDEO_BYTES // (1024 * 1024)}MB limit")

    # Create temp dir for frames
    frames_dir = tempfile.mkdtemp(prefix="termify_frames_")

    # Extract frames with ffmpeg: -t limits duration, -r 10 = 10 fps
    # -an = no audio, -sn = no subtitles, -vsync 0 = exact frame timing
    cmd = [
        _ffmpeg_path(), "-y",
        "-i", video_path,
        "-t", str(max_duration),
        "-r", "10",
        "-an", "-sn",
        "-q:v", "2",
        os.path.join(frames_dir, "frame_%05d.png"),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=max_duration + 30,  # generous timeout
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

    fps = 10.0
    return frames_dir, fps


def frames_dir_to_images(frames_dir: str) -> list[str]:
    """Return sorted list of PNG frame paths from a frames directory."""
    return sorted(
        os.path.join(frames_dir, f)
        for f in os.listdir(frames_dir)
        if f.startswith("frame_") and f.endswith(".png")
    )


def validate_video(path: str) -> None:
    """Validate video by extension + file size + ffprobe duration."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in VALID_VIDEO_EXTS:
        raise VideoError(f"Unsupported video format: {ext}")

    file_size = os.path.getsize(path)
    if file_size > MAX_VIDEO_BYTES:
        raise VideoError(f"Video file exceeds {MAX_VIDEO_BYTES // (1024 * 1024)}MB limit")

    # Use ffprobe for duration check if available
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                duration = float(result.stdout.strip())
                if duration > MAX_VIDEO_DURATION_SEC:
                    raise VideoError(f"Video exceeds {MAX_VIDEO_DURATION_SEC}s limit ({duration:.1f}s)")
        except (ValueError, subprocess.TimeoutExpired):
            pass  # If ffprobe fails, we'll still try ffmpeg
