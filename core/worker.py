"""
core/worker.py
--------------
Background compression worker.

For each FileInfo the worker:
  1. Mirrors the source folder structure inside the output folder.
  2. Copies the file unchanged if target_size >= original_size.
  3. Otherwise calls image_compressor or video_compressor.
  4. On bitrate-too-low ValueError, copies the original with a warning.

Cancellation
------------
Call requestInterruption().  The worker checks between files; a running
ffmpeg encode is NOT killed mid-process — it finishes naturally before the
worker stops.  The UI should display "Cancelling… (waiting for current
file to finish)" to set the right expectation.
"""
import shutil
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

    file_started = pyqtSignal(int)
    file_done    = pyqtSignal(int, int)   # index, final_size_bytes
    file_error   = pyqtSignal(int, str)   # index, error_message
    log_message  = pyqtSignal(str)
    all_done     = pyqtSignal(int, int)   # total_original, total_final

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
        total_original = 0
        total_final    = 0

        for index, f in enumerate(self.files):
            if self.isInterruptionRequested():
                self.log_message.emit("\n— Cancelled —")
                break

            total_original += f.original_size

            # Build output path, mirroring the source folder tree
            try:
                relative = f.path.relative_to(self.source_folder)
            except ValueError:
                relative = Path(f.path.name)

            output_path = self.output_folder / relative
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # When converting images to AVIF, change the output extension
            if (f.media_type == MediaType.IMAGE
                    and self.settings.image_format == ImageFormat.AVIF):
                output_path = output_path.with_suffix(".avif")

            self.file_started.emit(index)

            # ── already small enough: copy unchanged ─────────────────────────
            if f.target_size >= f.original_size:
                try:
                    shutil.copy2(f.path, output_path)
                    size = output_path.stat().st_size
                    total_final += size
                    self.file_done.emit(index, size)
                    self.log_message.emit(
                        f"  [copied]    {f.path.name}  ({human_readable(size)})"
                    )
                except OSError as exc:
                    total_final += f.original_size
                    self.file_error.emit(index, str(exc))
                    self.log_message.emit(f"  [error]     {f.path.name}: {exc}")
                continue

            # ── compress ─────────────────────────────────────────────────────
            self.log_message.emit(f"  [encoding]  {f.path.name}…")
            try:
                if f.media_type == MediaType.IMAGE:
                    final_size = compress_image(f.path, output_path, f.target_size)
                else:
                    final_size = compress_video(
                        f.path, output_path, f.target_size, self.settings.video_codec
                    )

                total_final += final_size
                self.file_done.emit(index, final_size)
                savings = (1 - final_size / f.original_size) * 100
                self.log_message.emit(
                    f"  [done]      {f.path.name}  "
                    f"{human_readable(f.original_size)} → {human_readable(final_size)}"
                    f"  ({savings:.0f}% savings)"
                )

            except ValueError as exc:
                # Bitrate too low / target unreachable — fall back to copy
                self.log_message.emit(
                    f"  [warning]   {f.path.name}: {exc} — copying original"
                )
                try:
                    shutil.copy2(f.path, output_path)
                    size = output_path.stat().st_size
                    total_final += size
                    self.file_done.emit(index, size)
                except OSError as copy_exc:
                    total_final += f.original_size
                    self.file_error.emit(index, str(copy_exc))
                    self.log_message.emit(f"  [error]     {f.path.name}: {copy_exc}")

            except Exception as exc:  # noqa: BLE001
                total_final += f.original_size
                self.file_error.emit(index, str(exc))
                self.log_message.emit(f"  [error]     {f.path.name}: {exc}")

        self.all_done.emit(total_original, total_final)
