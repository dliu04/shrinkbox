"""
core/worker.py
--------------
Background compression worker.

Images are processed in parallel (one thread per logical CPU core) because
Pillow's quality binary-search is CPU-bound and benefits from concurrency.

Videos are processed sequentially — each ffmpeg subprocess already spawns
threads for all available cores, so running multiple encodes in parallel
would only split resources and slow things down.

Cancellation
------------
Call requestInterruption().  The worker checks between files; a running
ffmpeg encode is NOT killed mid-process — it finishes naturally before the
worker stops.
"""
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from core.compression_settings import CompressionSettings, ImageFormat
from core.file_scanner import FileInfo, MediaType
from core.image_compressor import compress_image
from core.video_compressor import compress_video
from utils.size_utils import human_readable


class CompressionWorker(QThread):
    """
    Signals
    -------
    file_started(index)               File has begun processing.
    file_done(index, final_size)      File finished successfully.
    file_error(index, message)        File failed; original untouched.
    log_message(text)                 Human-readable status line.
    all_done(total_original, total_final)  Emitted after every file (or cancel).
    """

    file_started  = pyqtSignal(int)
    file_done     = pyqtSignal(int, int)   # index, final_size_bytes
    file_error    = pyqtSignal(int, str)   # index, error_message
    file_progress = pyqtSignal(int, int)   # index, percent 0-100 (video only)
    log_message   = pyqtSignal(str)
    all_done      = pyqtSignal(int, int)   # total_original, total_final

    def __init__(
        self,
        files: list[FileInfo],
        source_folder: Path,
        output_folder: Path,
        settings: CompressionSettings | None = None,
    ) -> None:
        super().__init__()
        self.files         = files
        self.source_folder = source_folder
        self.output_folder = output_folder
        self.settings      = settings or CompressionSettings()

    # ── QThread entry point ───────────────────────────────────────────────────

    def run(self) -> None:
        total_original = sum(f.original_size for f in self.files)
        total_final    = 0
        total_lock     = threading.Lock()

        # Pre-compute output paths for every file
        tasks: list[tuple[int, FileInfo, Path]] = []
        for index, f in enumerate(self.files):
            try:
                relative = f.path.relative_to(self.source_folder)
            except ValueError:
                relative = Path(f.path.name)
            output_path = self.output_folder / relative

            # Remap output extension when a format conversion is requested
            if f.media_type == MediaType.IMAGE:
                _fmt = self.settings.image_format
                if _fmt == ImageFormat.JPEG:
                    output_path = output_path.with_suffix(".jpg")
                elif _fmt == ImageFormat.WEBP:
                    output_path = output_path.with_suffix(".webp")
                elif _fmt == ImageFormat.AVIF:
                    output_path = output_path.with_suffix(".avif")

            tasks.append((index, f, output_path))

        image_tasks = [(i, f, p) for i, f, p in tasks if f.media_type == MediaType.IMAGE]
        video_tasks = [(i, f, p) for i, f, p in tasks if f.media_type == MediaType.VIDEO]

        def _process(index: int, f: FileInfo, output_path: Path) -> None:
            nonlocal total_final
            if self.isInterruptionRequested():
                return

            output_path.parent.mkdir(parents=True, exist_ok=True)
            self.file_started.emit(index)

            # ── already small enough: copy unchanged ─────────────────────────
            if f.target_size >= f.original_size:
                try:
                    shutil.copy2(f.path, output_path)
                    size = output_path.stat().st_size
                    with total_lock:
                        total_final += size
                    self.file_done.emit(index, size)
                    self.log_message.emit(
                        f"  [copied]    {f.path.name}  ({human_readable(size)})"
                    )
                except OSError as exc:
                    with total_lock:
                        total_final += f.original_size
                    self.file_error.emit(index, str(exc))
                    self.log_message.emit(f"  [error]     {f.path.name}: {exc}")
                return

            # ── compress ─────────────────────────────────────────────────────
            self.log_message.emit(f"  [encoding]  {f.path.name}…")
            try:
                if f.media_type == MediaType.IMAGE:
                    final_size = compress_image(f.path, output_path, f.target_size)
                else:
                    def _on_progress(pct: int, _idx: int = index) -> None:
                        self.file_progress.emit(_idx, pct)
                    final_size = compress_video(
                        f.path, output_path, f.target_size,
                        self.settings.video_codec,
                        on_progress=_on_progress,
                    )

                with total_lock:
                    total_final += final_size
                self.file_done.emit(index, final_size)
                savings = (1 - final_size / f.original_size) * 100
                over_budget = (
                    f.media_type == MediaType.VIDEO and final_size > f.target_size
                )
                note = "  ⚠ min bitrate applied" if over_budget else ""
                self.log_message.emit(
                    f"  [done]      {f.path.name}  "
                    f"{human_readable(f.original_size)} → {human_readable(final_size)}"
                    f"  ({savings:.0f}% savings){note}"
                )

            except Exception as exc:  # noqa: BLE001
                with total_lock:
                    total_final += f.original_size
                self.file_error.emit(index, str(exc))
                self.log_message.emit(f"  [error]     {f.path.name}: {exc}")

        # ── images: parallel across all logical cores ─────────────────────────
        if image_tasks and not self.isInterruptionRequested():
            n_workers = max(1, os.cpu_count() or 4)
            with ThreadPoolExecutor(max_workers=n_workers) as pool:
                futures = {
                    pool.submit(_process, i, f, p): (i, f)
                    for i, f, p in image_tasks
                }
                for future in as_completed(futures):
                    if self.isInterruptionRequested():
                        break
                    try:
                        future.result()
                    except Exception:  # noqa: BLE001
                        pass  # errors already handled inside _process

        # ── videos: sequential (each ffmpeg already uses all cores) ──────────
        for i, f, p in video_tasks:
            if self.isInterruptionRequested():
                break
            _process(i, f, p)

        if self.isInterruptionRequested():
            self.log_message.emit("\n— Cancelled —")

        self.all_done.emit(total_original, total_final)

