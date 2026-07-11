"""
utils/ffmpeg_utils.py
---------------------
Wrappers around ffmpeg and ffprobe subprocess calls.
"""
import functools
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _ffmpeg_bin(name: str) -> str:
    """
    Resolve an ffmpeg/ffprobe binary path.

    When running as a PyInstaller bundle (sys.frozen is True), prefer the
    copy extracted alongside the exe (sys._MEIPASS).  Otherwise fall back
    to whatever is on the system PATH.
    """
    if getattr(sys, "frozen", False):
        candidate = Path(sys._MEIPASS) / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return name

def check_dependencies() -> list[str]:
    """
    Return a list of tool names that are missing from PATH.
    An empty list means all required tools are present.
    """
    missing = []
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(_ffmpeg_bin(tool)) is None:
            missing.append(tool)
    return missing


@functools.lru_cache(maxsize=None)
def get_available_encoders() -> frozenset[str]:
    """
    Return the set of video encoder names compiled into this ffmpeg build.
    Result is cached after the first call.
    """
    try:
        result = subprocess.run(
            [_ffmpeg_bin("ffmpeg"), "-hide_banner", "-encoders"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        encoders: set[str] = set()
        for line in result.stdout.splitlines():
            parts = line.split()
            # Encoder lines look like: " V..... libx264    ..."
            if len(parts) >= 2 and len(parts[0]) >= 2 and parts[0][0] in "VA":
                encoders.add(parts[1])
        return frozenset(encoders)
    except Exception:  # noqa: BLE001
        return frozenset()


def get_video_metadata(path: str | Path) -> dict:
    """
    Return the full ffprobe JSON output for a video file as a dict.

    Raises RuntimeError on failure (ffprobe not found, timeout, bad file).
    """
    cmd = [
        _ffmpeg_bin("ffprobe"),
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
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
    cmd = [_ffmpeg_bin("ffmpeg"), "-hide_banner", "-loglevel", "error"] + args
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg was not found on PATH.")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg process timed out.")

    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr.strip()}")

    return result
