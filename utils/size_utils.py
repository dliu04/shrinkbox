"""
utils/size_utils.py
-------------------
Helpers for converting and formatting file sizes.
"""


def bytes_to_mb(b: int) -> float:
    return b / (1024 * 1024)


def mb_to_bytes(mb: float) -> int:
    return int(mb * 1024 * 1024)


def human_readable(b: int) -> str:
    """Return a human-friendly size string (B / KB / MB / GB)."""
    if b < 1024:
        return f"{b} B"
    if b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    if b < 1024 ** 3:
        return f"{b / 1024 ** 2:.1f} MB"
    return f"{b / 1024 ** 3:.2f} GB"
