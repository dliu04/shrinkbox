"""
core/budget.py
--------------
Distribute a total MB target proportionally across a list of FileInfo objects.
"""
from .file_scanner import FileInfo
from utils.size_utils import mb_to_bytes


def distribute_budget(files: list[FileInfo], target_mb: float) -> list[FileInfo]:
    """
    Set *target_size* on each FileInfo proportional to its share of the total
    original size.  Files whose proportional share already exceeds their original
    size are left at original_size (we never upscale).

    Mutates the list in-place and also returns it for convenience.

    Args:
        files:     List of FileInfo (original_size must be populated).
        target_mb: Desired total folder size in megabytes.

    Returns:
        The same list with target_size set on every element.
    """
    if not files:
        return files

    target_bytes = mb_to_bytes(target_mb)
    total_original = sum(f.original_size for f in files)

    if total_original == 0:
        return files

    for f in files:
        proportional = int(f.original_size / total_original * target_bytes)
        # Never inflate a file beyond its original size
        f.target_size = min(proportional, f.original_size)

    return files
