"""
ui/preview_dialog.py
--------------------
Per-file quality preview dialog.

Images  — side-by-side original vs compressed QLabel panels.
           A target-size slider (1–100 % of original) triggers a background
           re-encode on release; both panels update when the encode finishes.

Videos  — QVideoWidget playing a 5-second clip encoded at the target bitrate.
           Same slider; a new clip is generated on release.

Accepting
---------
Clicking "Accept Settings" emits settings_accepted(new_target_bytes: int) so
the caller (MainWindow) can update the file's target_size in the table.
Clicking "Cancel" (or closing) discards all changes.

Thread safety
-------------
Encodes run in _EncodeWorker (QThread).  When a new encode is requested while
one is still running, the old worker's signals are disconnected so its result
is silently discarded; the old thread finishes naturally and cleans up its own
temp file.  We only call wait() on the active worker at dialog close.
"""
import copy
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.estimator import estimate_image, estimate_video
from core.file_scanner import FileInfo, MediaType
from utils.size_utils import human_readable

# Slider range: percentage of original file size (1 % … 100 %)
_SLIDER_MIN = 1
_SLIDER_MAX = 100


class PreviewDialog(QDialog):
    """
    Show a compression-quality preview for one file and optionally accept a
    custom per-file target size.

    Parameters
    ----------
    file_info:
        The FileInfo whose target_size drives the initial slider position.
        The object is *not* mutated until the user clicks "Accept Settings".
    parent:
        Optional Qt parent widget.

    Signals
    -------
    settings_accepted(int):
        Emitted with the new target_size in bytes when the user accepts.
    """

    settings_accepted = pyqtSignal(int)

    def __init__(self, file_info: FileInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Work on a copy so we never mutate the real FileInfo until accepted
        self._fi = copy.copy(file_info)
        self._temp_files: list[Path] = []
        self._worker: _EncodeWorker | None = None

        self.setWindowTitle(f"Preview — {file_info.path.name}")
        self.setMinimumSize(720, 520)
        self.resize(940, 640)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        if self._fi.media_type == MediaType.IMAGE:
            root.addWidget(self._build_image_panels(), stretch=1)
        else:
            root.addWidget(self._build_video_panel(), stretch=1)

        root.addWidget(self._build_slider_row())
        root.addWidget(self._build_button_box())

        # Kick off the initial preview encode
        self._schedule_encode()

    # ── image panels ──────────────────────────────────────────────────────────

    def _build_image_panels(self) -> QWidget:
        container = QWidget()
        hbox = QHBoxLayout(container)
        hbox.setSpacing(12)
        hbox.setContentsMargins(0, 0, 0, 0)

        # Original panel
        orig_group = QGroupBox("Original")
        orig_inner = QVBoxLayout(orig_group)
        self._orig_img = _ScaledImageLabel()
        self._orig_size_lbl = QLabel(human_readable(self._fi.original_size))
        self._orig_size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        orig_inner.addWidget(self._orig_img, stretch=1)
        orig_inner.addWidget(self._orig_size_lbl)

        # Compressed panel
        comp_group = QGroupBox("Compressed")
        comp_inner = QVBoxLayout(comp_group)
        self._comp_img = _ScaledImageLabel()
        self._comp_img.setText("Generating…")
        self._comp_size_lbl = QLabel("—")
        self._comp_size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        comp_inner.addWidget(self._comp_img, stretch=1)
        comp_inner.addWidget(self._comp_size_lbl)

        hbox.addWidget(orig_group)
        hbox.addWidget(comp_group)

        # Load the original image immediately (no encoding needed)
        px = QPixmap(str(self._fi.path))
        if not px.isNull():
            self._orig_img.setPixmap(px)

        return container

    def _display_image_result(self, temp_path: Path) -> None:
        px = QPixmap(str(temp_path))
        if not px.isNull():
            self._comp_img.setPixmap(px)
        size = temp_path.stat().st_size
        self._comp_size_lbl.setText(
            f"{human_readable(size)}  "
            f"({_savings_pct(size, self._fi.original_size):.0f}% savings)"
        )

    # ── video panel ───────────────────────────────────────────────────────────

    def _build_video_panel(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setSpacing(8)
        vbox.setContentsMargins(0, 0, 0, 0)

        info = QLabel(
            f"<b>{self._fi.path.name}</b>  ·  "
            f"original: {human_readable(self._fi.original_size)}  ·  "
            f"showing first 5 seconds at estimated quality"
        )
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(info)

        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumHeight(240)
        vbox.addWidget(self._video_widget, stretch=1)

        self._audio_output = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_widget)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)

        # Playback controls
        ctrl = QWidget()
        ctrl_layout = QHBoxLayout(ctrl)
        ctrl_layout.setContentsMargins(0, 4, 0, 0)

        self._play_btn = QPushButton("▶  Play")
        self._play_btn.setEnabled(False)
        self._play_btn.setFixedWidth(90)
        self._play_btn.clicked.connect(self._toggle_play)

        self._video_status_lbl = QLabel("Generating preview clip…")
        self._video_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self._play_btn)
        ctrl_layout.addSpacing(16)
        ctrl_layout.addWidget(self._video_status_lbl)
        ctrl_layout.addStretch()
        vbox.addWidget(ctrl)

        return container

    def _display_video_result(self, temp_path: Path) -> None:
        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(str(temp_path)))
        self._play_btn.setEnabled(True)
        self._player.play()
        size = temp_path.stat().st_size
        self._video_status_lbl.setText(
            f"{human_readable(size)}  "
            f"({_savings_pct(size, self._fi.original_size):.0f}% savings)"
        )

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self._play_btn.setText(
            "⏸  Pause"
            if state == QMediaPlayer.PlaybackState.PlayingState
            else "▶  Play"
        )

    # ── slider ────────────────────────────────────────────────────────────────

    def _build_slider_row(self) -> QWidget:
        row = QWidget()
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(0, 4, 0, 0)
        hbox.setSpacing(10)

        hbox.addWidget(QLabel("Smaller"))

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(_SLIDER_MIN, _SLIDER_MAX)
        self._slider.setValue(self._pct_of_original(self._fi.target_size))
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setTickInterval(10)
        self._slider.setToolTip("Drag to adjust target size, then release to regenerate preview")
        self._slider.valueChanged.connect(self._on_slider_moved)
        self._slider.sliderReleased.connect(self._on_slider_released)
        hbox.addWidget(self._slider, stretch=1)

        hbox.addWidget(QLabel("Larger"))
        hbox.addSpacing(16)

        self._target_lbl = QLabel()
        self._target_lbl.setMinimumWidth(220)
        self._target_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hbox.addWidget(self._target_lbl)

        self._refresh_target_label()
        return row

    def _pct_of_original(self, target_bytes: int) -> int:
        """Convert target_bytes → slider value (1–100)."""
        if self._fi.original_size == 0:
            return _SLIDER_MAX
        pct = target_bytes / self._fi.original_size * 100
        return max(_SLIDER_MIN, min(_SLIDER_MAX, round(pct)))

    def _slider_to_bytes(self, value: int) -> int:
        """Convert slider value (1–100) → target_bytes."""
        return max(1, int(value / 100 * self._fi.original_size))

    def _on_slider_moved(self, value: int) -> None:
        """Update the label live while dragging; don't re-encode yet."""
        self._fi.target_size = self._slider_to_bytes(value)
        self._refresh_target_label()

    def _on_slider_released(self) -> None:
        """Slider released — trigger a new preview encode."""
        self._schedule_encode()

    def _refresh_target_label(self) -> None:
        pct_savings = 100 - self._fi.target_size / max(1, self._fi.original_size) * 100
        self._target_lbl.setText(
            f"Target: {human_readable(self._fi.target_size)}  "
            f"({pct_savings:.0f}% savings)"
        )

    # ── encode worker lifecycle ───────────────────────────────────────────────

    def _schedule_encode(self) -> None:
        """Start a fresh encode, silently abandoning any in-progress one."""
        if self._worker and self._worker.isRunning():
            # Disconnect signals so we ignore any late-arriving result
            try:
                self._worker.encode_finished.disconnect(self._on_encode_finished)
                self._worker.encode_error.disconnect(self._on_encode_error)
            except TypeError:
                pass
            self._worker.requestInterruption()
            # Let the thread finish naturally; it cleans up its own temp file
            self._worker.finished.connect(self._worker.deleteLater)
            self._worker = None

        self._set_loading(True)

        worker = _EncodeWorker(copy.copy(self._fi))
        worker.encode_finished.connect(self._on_encode_finished)
        worker.encode_error.connect(self._on_encode_error)
        self._worker = worker
        worker.start()

    def _on_encode_finished(self, temp_path: Path) -> None:
        self._temp_files.append(temp_path)
        self._set_loading(False)
        if self._fi.media_type == MediaType.IMAGE:
            self._display_image_result(temp_path)
        else:
            self._display_video_result(temp_path)

    def _on_encode_error(self, message: str) -> None:
        self._set_loading(False)
        if self._fi.media_type == MediaType.IMAGE:
            self._comp_img.setText(f"Error:\n{message}")
            self._comp_size_lbl.setText("—")
        else:
            self._video_status_lbl.setText(f"Error: {message}")

    def _set_loading(self, loading: bool) -> None:
        self._slider.setEnabled(not loading)
        if loading:
            if self._fi.media_type == MediaType.IMAGE:
                self._comp_img.setText("Generating…")
                self._comp_size_lbl.setText("—")
            else:
                self._video_status_lbl.setText("Generating preview clip…")
                self._play_btn.setEnabled(False)

    # ── buttons ───────────────────────────────────────────────────────────────

    def _build_button_box(self) -> QDialogButtonBox:
        box = QDialogButtonBox()
        cancel_btn = box.addButton(QDialogButtonBox.StandardButton.Cancel)
        accept_btn = box.addButton(
            "Accept Settings", QDialogButtonBox.ButtonRole.AcceptRole
        )
        accept_btn.setDefault(True)
        cancel_btn.clicked.connect(self.reject)
        accept_btn.clicked.connect(self._on_accept)
        return box

    def _on_accept(self) -> None:
        self.settings_accepted.emit(self._fi.target_size)
        self.accept()

    # ── cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        # Stop and wait for the active worker before cleaning up temp files
        if self._worker and self._worker.isRunning():
            try:
                self._worker.encode_finished.disconnect()
                self._worker.encode_error.disconnect()
            except TypeError:
                pass
            self._worker.requestInterruption()
            self._worker.wait()

        if hasattr(self, "_player"):
            self._player.stop()

        for p in self._temp_files:
            p.unlink(missing_ok=True)

        super().closeEvent(event)


# ── background encode worker ──────────────────────────────────────────────────

class _EncodeWorker(QThread):
    """
    Run an estimator encode off the main thread.

    Signals
    -------
    encode_finished(Path):  Emitted with the temp file path on success.
    encode_error(str):      Emitted with the error message on failure.

    If interrupted (via requestInterruption()) before the encode completes,
    neither signal is emitted and the temp file (if any) is deleted.
    """

    encode_finished = pyqtSignal(Path)
    encode_error = pyqtSignal(str)

    def __init__(self, file_info: FileInfo) -> None:
        super().__init__()
        self._fi = file_info

    def run(self) -> None:
        try:
            if self._fi.media_type == MediaType.IMAGE:
                path = estimate_image(self._fi)
            else:
                path = estimate_video(self._fi)

            if self.isInterruptionRequested():
                path.unlink(missing_ok=True)
            else:
                self.encode_finished.emit(path)

        except Exception as exc:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.encode_error.emit(str(exc))


# ── helpers ───────────────────────────────────────────────────────────────────

def _savings_pct(compressed: int, original: int) -> float:
    if original == 0:
        return 0.0
    return (1 - compressed / original) * 100


class _ScaledImageLabel(QLabel):
    """
    QLabel that stores the full-resolution pixmap and scales it to fit
    the widget on every resize, maintaining aspect ratio.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(180, 180)
        self._source_pixmap: QPixmap | None = None

    def setPixmap(self, pixmap: QPixmap) -> None:  # type: ignore[override]
        self._source_pixmap = pixmap
        self._render()

    def resizeEvent(self, event) -> None:
        self._render()
        super().resizeEvent(event)

    def _render(self) -> None:
        if self._source_pixmap and not self._source_pixmap.isNull():
            scaled = self._source_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            super().setPixmap(scaled)
