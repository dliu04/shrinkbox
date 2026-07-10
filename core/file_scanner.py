"""
core/file_scanner.py
--------------------
Scan a folder for supported image and video files, returning FileInfo records.
"""
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path


class MediaType(Enum):
    IMAGE = auto()
    VIDEO = auto()


IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}
)

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".mov", ".mkv", ".avi", ".wmv", ".m4v", ".webm"}
)


@dataclass
class FileInfo:
    path: Path
    media_type: MediaType
    original_size: int  # bytes
    target_size: int = 0  # bytes; populated by budget.distribute_budget()


def scan_folder(folder: str | Path, recursive: bool = True) -> list[FileInfo]:
    """
    Walk *folder* and return a sorted list of FileInfo for every supported
    image and video file found. Non-media files are silently skipped.

    Args:
        folder:    Root directory to scan.
        recursive: If True (default), descend into sub-directories.

    Returns:
        List of FileInfo sorted by file path.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {folder}")

    pattern = "**/*" if recursive else "*"
    results: list[FileInfo] = []

    for p in folder.glob(pattern):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            media_type = MediaType.IMAGE
        elif ext in VIDEO_EXTENSIONS:
            media_type = MediaType.VIDEO
        else:
            continue
        results.append(
            FileInfo(path=p, media_type=media_type, original_size=p.stat().st_size)
        )

    results.sort(key=lambda f: f.path)
    return results
