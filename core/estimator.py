"""
core/estimator.py
-----------------
Fast preview encodes used by the UI before the user commits to full compression.

Images  — encoded to a NamedTemporaryFile at the estimated quality level.
Videos  — the first PREVIEW_CLIP_SECONDS encoded to a NamedTemporaryFile at
          the estimated bitrate; QMediaPlayer can play these directly.

In both cases the caller owns the temp file and must delete it when done
(e.g. using pathlib.Path.unlink(missing_ok=True)).

estimate_compressed_size_image() performs the quality search in-memory and
returns just the byte count — useful for updating the UI size readout while
the slider is being dragged, without writing any file.
"""
import io
import tempfile
from pathlib import Path

from PIL import Image

from .file_scanner import FileInfo
from .image_compressor import find_jpeg_quality, find_webp_quality
from .video_compressor import (
    AUDIO_BITRATE_KBPS,
    MIN_VIDEO_BITRATE_KBPS,
    compute_video_bitrate,
)
from utils.ffmpeg_utils import get_duration_seconds, get_video_metadata, run_ffmpeg

PREVIEW_CLIP_SECONDS: int = 5


# ── image ────────────────────────────────────────────────────────────────────

def estimate_image(file_info: FileInfo) -> Path:
    """
    Compress the image described by *file_info* to its target_size and save
    the result to a temporary file.

    Returns:
        Path to the temporary file (caller must delete when done).
    """
    source = file_info.path
    ext = source.suffix.lower()

    tmp = tempfile.NamedTemporaryFile(
        suffix=ext, delete=False, prefix="shrinkbox_preview_"
    )
    tmp_path = Path(tmp.name)
    tmp.close()

    with Image.open(source) as img:
        if ext in (".jpg", ".jpeg"):
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            quality = find_jpeg_quality(img, file_info.target_size)
            img.save(tmp_path, format="JPEG", quality=quality, optimize=True)
        elif ext == ".webp":
            quality = find_webp_quality(img, file_info.target_size)
            img.save(tmp_path, format="WEBP", quality=quality)
        else:
            # PNG / BMP / TIFF — best-effort lossless optimize
            img.save(tmp_path, optimize=True)

    return tmp_path


def estimate_compressed_size_image(file_info: FileInfo) -> int:
    """
    Estimate the compressed image size in bytes *without* writing any file.
    Useful for live slider feedback in the preview dialog.

    Returns:
        Estimated compressed size in bytes.
    """
    source = file_info.path
    ext = source.suffix.lower()

    with Image.open(source) as img:
        if ext in (".jpg", ".jpeg"):
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            quality = find_jpeg_quality(img, file_info.target_size)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            return buf.tell()

        if ext == ".webp":
            quality = find_webp_quality(img, file_info.target_size)
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=quality)
            return buf.tell()

        # PNG / others — in-memory optimize
        buf = io.BytesIO()
        img.save(buf, optimize=True)
        return buf.tell()


# ── video ────────────────────────────────────────────────────────────────────

def estimate_video(file_info: FileInfo) -> Path:
    """
    Encode the first PREVIEW_CLIP_SECONDS of the video described by *file_info*
    at its target bitrate and save the result to a temporary .mp4 file.

    The bitrate is floored at MIN_VIDEO_BITRATE_KBPS so the preview always
    produces a playable file even when the target size is very small.

    Returns:
        Path to the temporary .mp4 file (caller must delete when done).
    """
    source = file_info.path

    tmp = tempfile.NamedTemporaryFile(
        suffix=".mp4", delete=False, prefix="shrinkbox_preview_"
    )
    tmp_path = Path(tmp.name)
    tmp.close()

    metadata = get_video_metadata(source)
    duration = get_duration_seconds(metadata)
    clip_duration = min(PREVIEW_CLIP_SECONDS, duration)

    video_kbps = max(
        compute_video_bitrate(file_info.target_size, duration),
        MIN_VIDEO_BITRATE_KBPS,
    )

    run_ffmpeg([
        "-y", "-i", str(source),
        "-t", str(clip_duration),
        "-c:v", "libx264",
        "-b:v", f"{video_kbps}k",
        "-c:a", "aac",
        "-b:a", f"{AUDIO_BITRATE_KBPS}k",
        str(tmp_path),
    ])

    return tmp_path
