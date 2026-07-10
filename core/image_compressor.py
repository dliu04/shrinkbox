"""
core/image_compressor.py
------------------------
Compress images to a target byte size using Pillow.

Strategy per format
-------------------
JPEG / WebP : Binary search on the quality parameter (1-95).
PNG         : Lossless optimize first; falls back to palette quantization.
BMP / TIFF  : Re-encoded as JPEG at the output path (these formats do not
              support meaningful lossy compression in Pillow).

If the source file is already at or below target_bytes it is copied unchanged.
"""
import io
import shutil
from pathlib import Path

from PIL import Image

# ── quality search bounds ────────────────────────────────────────────────────
_QUALITY_MIN = 1
_QUALITY_MAX = 95


# ── public API ───────────────────────────────────────────────────────────────

def compress_image(
    source: str | Path,
    output: str | Path,
    target_bytes: int,
) -> int:
    """
    Compress *source* image to approximately *target_bytes* and save to *output*.

    Args:
        source:       Path to the original image file.
        output:       Destination path (extension determines output format).
        target_bytes: Maximum desired output size in bytes.

    Returns:
        Actual output file size in bytes.
    """
    source = Path(source)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if source.stat().st_size <= target_bytes:
        shutil.copy2(source, output)
        return output.stat().st_size

    ext = output.suffix.lower()

    with Image.open(source) as img:
        if ext in (".jpg", ".jpeg"):
            _save_jpeg(img, output, target_bytes)
        elif ext == ".webp":
            _save_webp(img, output, target_bytes)
        elif ext == ".png":
            _save_png(img, output, target_bytes)
        else:
            # BMP, TIFF, etc. — fall back to JPEG
            _save_jpeg(img, output, target_bytes)

    return output.stat().st_size


def find_jpeg_quality(img: Image.Image, target_bytes: int) -> int:
    """
    Return the highest JPEG quality (1-95) whose encoded size fits within
    *target_bytes*.  The image is not saved to disk.
    """
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return _binary_search_quality(
        lambda q, buf: img.save(buf, format="JPEG", quality=q, optimize=True),
        target_bytes,
    )


def find_webp_quality(img: Image.Image, target_bytes: int) -> int:
    """
    Return the highest WebP quality (1-95) whose encoded size fits within
    *target_bytes*.  The image is not saved to disk.
    """
    return _binary_search_quality(
        lambda q, buf: img.save(buf, format="WEBP", quality=q),
        target_bytes,
    )


# ── private helpers ───────────────────────────────────────────────────────────

def _binary_search_quality(save_fn, target_bytes: int) -> int:
    """
    Binary search for the highest quality in [_QUALITY_MIN, _QUALITY_MAX] such
    that save_fn(quality, BytesIO_buffer) produces output ≤ target_bytes.

    *save_fn* is called as save_fn(quality: int, buf: BytesIO).
    Returns _QUALITY_MIN if even the lowest quality exceeds target_bytes.
    """
    lo, hi, best = _QUALITY_MIN, _QUALITY_MAX, _QUALITY_MIN
    while lo <= hi:
        mid = (lo + hi) // 2
        buf = io.BytesIO()
        save_fn(mid, buf)
        if buf.tell() <= target_bytes:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _save_jpeg(img: Image.Image, output: Path, target_bytes: int) -> None:
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    quality = find_jpeg_quality(img, target_bytes)
    img.save(output, format="JPEG", quality=quality, optimize=True)


def _save_webp(img: Image.Image, output: Path, target_bytes: int) -> None:
    quality = find_webp_quality(img, target_bytes)
    img.save(output, format="WEBP", quality=quality)


def _save_png(img: Image.Image, output: Path, target_bytes: int) -> None:
    # Pass 1: lossless optimize
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    if buf.tell() <= target_bytes:
        output.write_bytes(buf.getvalue())
        return

    # Pass 2: palette quantization (lossy, but still a valid PNG)
    if img.mode == "RGBA":
        quantized = img.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
    else:
        quantized = img.convert("RGBA").quantize(colors=256, method=Image.Quantize.FASTOCTREE)

    buf = io.BytesIO()
    quantized.save(buf, format="PNG", optimize=True)
    output.write_bytes(buf.getvalue())
