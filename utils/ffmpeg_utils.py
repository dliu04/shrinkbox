"""
utils/ffmpeg_utils.py
---------------------
Wrappers around ffmpeg and ffprobe subprocess calls.
"""
import json
import shutil
import subprocess
from pathlib import Path


def check_dependencies() -> list[str]:
    """
    Return a list of tool names that are missing from PATH.
    An empty list means all required tools are present.
    """
    missing = []
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            missing.append(tool)
    return missing


def get_video_metadata(path: str | Path) -> dict:
    """
    Return the full ffprobe JSON output for a video file as a dict.

    Raises RuntimeError on failure (ffprobe not found, timeout, bad file).
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError("ffprobe was not found on PATH.")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffprobe timed out reading: {path}")

    if result.returncode != 0:
        raise RuntimeError(f"ffprobe error for {path}:\n{result.stderr.strip()}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Could not parse ffprobe output: {exc}") from exc


def get_duration_seconds(metadata: dict) -> float:
    """
    Extract duration in seconds from a ffprobe metadata dict.

    Raises ValueError if duration cannot be determined.
    """
    duration = metadata.get("format", {}).get("duration")
    if duration is not None:
        return float(duration)

    # Fallback: check individual streams
    for stream in metadata.get("streams", []):
        if stream.get("codec_type") == "video" and "duration" in stream:
            return float(stream["duration"])

    raise ValueError("Could not determine video duration from ffprobe metadata.")


def run_ffmpeg(
    args: list[str],
    timeout: int | None = None,
) -> subprocess.CompletedProcess:
    """
    Run ffmpeg with the given argument list (do not include 'ffmpeg' itself).

    Raises RuntimeError on non-zero exit or if ffmpeg is not found.
    Returns the CompletedProcess for inspection if needed.
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg was not found on PATH.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg process timed out.")

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr.strip()}")

    return result
