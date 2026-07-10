"""
core/video_compressor.py
------------------------
Compress videos to a target byte size using ffmpeg two-pass libx264 encoding.

Bitrate formula
---------------
  total_kbps    = (target_bytes × 8) / duration_seconds / 1000
  video_kbps    = total_kbps − AUDIO_BITRATE_KBPS

If the computed video bitrate is below MIN_VIDEO_BITRATE_KBPS a ValueError is
raised so the caller (or UI) can warn the user before proceeding.

If the source file is already at or below target_bytes it is copied unchanged.
"""
import os
import shutil
import tempfile
from pathlib import Path

from utils.ffmpeg_utils import get_duration_seconds, get_video_metadata, run_ffmpeg

AUDIO_BITRATE_KBPS: int = 128
MIN_VIDEO_BITRATE_KBPS: int = 100


def compute_video_bitrate(target_bytes: int, duration_seconds: float) -> int:
    """
    Return the video bitrate in kbps required to fit *target_bytes* given
    *duration_seconds*, accounting for a fixed audio track at AUDIO_BITRATE_KBPS.

    Returns 0 if the arithmetic yields a non-positive value (caller should
    check against MIN_VIDEO_BITRATE_KBPS before proceeding).
    """
    if duration_seconds <= 0:
        return 0
    total_kbps = (target_bytes * 8) / duration_seconds / 1000
    return max(0, int(total_kbps) - AUDIO_BITRATE_KBPS)


def compress_video(
    source: str | Path,
    output: str | Path,
    target_bytes: int,
) -> int:
    """
    Two-pass libx264 encode of *source* to approximately *target_bytes*,
    writing the result to *output*.

    Args:
        source:       Path to the original video file.
        output:       Destination path (must end in a container ffmpeg supports,
                      e.g. .mp4).
        target_bytes: Maximum desired output size in bytes.

    Returns:
        Actual output file size in bytes.

    Raises:
        ValueError:   If the required video bitrate is below MIN_VIDEO_BITRATE_KBPS.
        RuntimeError: If ffmpeg reports an error during encoding.
    """
    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if source.stat().st_size <= target_bytes:
        shutil.copy2(source, output)
        return output.stat().st_size

    metadata = get_video_metadata(source)
    duration = get_duration_seconds(metadata)
    video_kbps = compute_video_bitrate(target_bytes, duration)

    if video_kbps < MIN_VIDEO_BITRATE_KBPS:
        raise ValueError(
            f"Required video bitrate ({video_kbps} kbps) is below the minimum "
            f"({MIN_VIDEO_BITRATE_KBPS} kbps). "
            f"The target size is too small for this video "
            f"({duration:.1f}s duration)."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        passlogfile = os.path.join(tmpdir, "ffmpeg2pass")

        # Pass 1 — analysis; no output written
        run_ffmpeg([
            "-y", "-i", str(source),
            "-c:v", "libx264",
            "-b:v", f"{video_kbps}k",
            "-pass", "1",
            "-passlogfile", passlogfile,
            "-an",
            "-f", "null",
            "NUL",          # Windows null device
        ])

        # Pass 2 — actual encode
        run_ffmpeg([
            "-y", "-i", str(source),
            "-c:v", "libx264",
            "-b:v", f"{video_kbps}k",
            "-pass", "2",
            "-passlogfile", passlogfile,
            "-c:a", "aac",
            "-b:a", f"{AUDIO_BITRATE_KBPS}k",
            str(output),
        ])

    return output.stat().st_size
