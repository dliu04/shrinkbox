"""
ui/progress_dialog.py
---------------------
Compression progress dialog.

While running
-   Overall QProgressBar  (0 → N files)
-   "Encoding: <filename>" label
-   Indeterminate per-file QProgressBar
-   Scrolling plain-text log (Consolas font)
-   Cancel button  →  requestInterruption(); waits for current file to finish

After completion / cancellation
-   Summary: total original → total final, savings %
-   "Open output folder" button  (opens Windows Explorer)
-   "Close" button
"""
import subprocess

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.worker import CompressionWorker
from utils.size_utils import human_readable


class ProgressDialog(QDialog):
    """
    Pass a CompressionWorker (not yet started), then call worker.start()
    immediately before exec()-ing the dialog.
    """

    def __init__(
        self,
        worker: CompressionWorker,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker   = worker
        self._total    = max(len(worker.files), 1)
        self._done     = 0
        self._errors   = 0

        self.setWindowTitle("Compressing…")
        self.setMinimumWidth(580)
        self.resize(660, 460)

        self._setup_ui()
        self._connect_signals()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(14, 14, 14, 14)

        # Overall progress row
        overall_row = QWidget()
        or_layout = QHBoxLayout(overall_row)
        or_layout.setContentsMargins(0, 0, 0, 0)
        or_layout.addWidget(QLabel("Overall:"))
        self._overall_bar = QProgressBar()
        self._overall_bar.setRange(0, self._total)
        self._overall_bar.setValue(0)
        self._overall_bar.setFormat(f"%v / {self._total} files")
        or_layout.addWidget(self._overall_bar, stretch=1)
        root.addWidget(overall_row)

        # Current file
        self._current_lbl = QLabel("Starting…")
        self._current_lbl.setWordWrap(True)
        root.addWidget(self._current_lbl)

        self._file_bar = QProgressBar()
        self._file_bar.setRange(0, 0)  # indeterminate
        root.addWidget(self._file_bar)

        # Log
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(4000)
        f = self._log.font()
        f.setFamily("Consolas")
        f.setPointSize(9)
        self._log.setFont(f)
        root.addWidget(self._log, stretch=1)

        # Button row
        btn_row = QWidget()
        bl = QHBoxLayout(btn_row)
        bl.setContentsMargins(0, 4, 0, 0)
        bl.addStretch()

        self._open_btn = QPushButton("Open output folder")
        self._open_btn.setVisible(False)
        self._open_btn.clicked.connect(self._open_output_folder)
        bl.addWidget(self._open_btn)

        self._close_btn = QPushButton("Close")
        self._close_btn.setVisible(False)
        self._close_btn.clicked.connect(self.accept)
        bl.addWidget(self._close_btn)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedWidth(90)
        self._cancel_btn.clicked.connect(self._on_cancel)
        bl.addWidget(self._cancel_btn)

        root.addWidget(btn_row)

    # ── signal handlers ───────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.file_error.connect(self._on_file_error)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.log_message.connect(self._on_log)
        self._worker.all_done.connect(self._on_all_done)

    def _on_file_started(self, index: int) -> None:
        name = self._worker.files[index].path.name
        self._current_lbl.setText(f"Encoding:  {name}")
        # Reset to indeterminate; switches to 0-100 on first file_progress tick
        self._file_bar.setRange(0, 0)
        self._file_bar.setValue(0)

    def _on_file_progress(self, _index: int, pct: int) -> None:
        if self._file_bar.maximum() == 0:
            self._file_bar.setRange(0, 100)
        self._file_bar.setValue(pct)

    def _on_file_done(self, _index: int, _final_size: int) -> None:
        self._done += 1
        self._overall_bar.setValue(self._done)

    def _on_file_error(self, _index: int, _message: str) -> None:
        self._done += 1
        self._errors += 1
        self._overall_bar.setValue(self._done)

    def _on_log(self, text: str) -> None:
        self._log.appendPlainText(text)
        # Auto-scroll to bottom
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_all_done(self, total_original: int, total_final: int) -> None:
        cancelled = self._worker.isInterruptionRequested()
        savings   = (1 - total_final / total_original) * 100 if total_original else 0
        label     = "Cancelled" if cancelled else "Done"

        summary = (
            f"{label}  —  "
            f"{human_readable(total_original)} → {human_readable(total_final)}"
            f"  ({savings:.0f}% savings)"
        )
        if self._errors:
            summary += f"  ·  ⚠ {self._errors} file(s) had errors (see log)"

        self._current_lbl.setText(summary)
        self._file_bar.setRange(0, 1)
        self._file_bar.setValue(1)
        self._overall_bar.setValue(self._done)
        self.setWindowTitle("Compression complete" if not cancelled else "Compression cancelled")

        self._cancel_btn.setVisible(False)
        self._open_btn.setVisible(True)
        self._close_btn.setVisible(True)

    # ── button actions ────────────────────────────────────────────────────────

    def _on_cancel(self) -> None:
        self._worker.requestInterruption()
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.setText("Cancelling…")
        self._current_lbl.setText(
            "Cancelling…  (finishing the current file, then stopping)"
        )

    def _open_output_folder(self) -> None:
        subprocess.Popen(["explorer", str(self._worker.output_folder)])

    # ── prevent accidental close while running ────────────────────────────────

    def reject(self) -> None:
        if self._worker.isRunning():
            self._on_cancel()
        else:
            super().reject()

    def closeEvent(self, event) -> None:
        if self._worker.isRunning():
            self._on_cancel()
            event.ignore()
        else:
            if not self._worker.isFinished():
                self._worker.wait()
            super().closeEvent(event)
