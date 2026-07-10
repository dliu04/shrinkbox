"""
ui/preview_panel.py
-------------------
Inline quality-preview panel embedded in the main window via QSplitter.

Images  — side-by-side original vs compressed panels.
           A target-size slider (1–100 % of original) triggers a background
           re-encode on release.

Videos  — QVideoWidget playing a 5-second clip encoded at the target bitrate.
           Same slider; a new clip is generated on release.

Apply
-----
The "Apply" button emits quality_accepted(new_target_bytes, apply_to_all).
"Apply to all files" defaults to checked: the same quality *percentage*
is applied proportionally to every file in the list.

PermissionError fix
-------------------
Windows holds a file lock while QMediaPlayer has a source set.  Before
deleting any temp file we call player.stop() + player.setSource(QUrl()) to
release the lock.  Any remaining PermissionErrors are caught and ignored; the
OS will reclaim the temp files on the next reboot at the latest.
"""
import copy
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.estimator import estimate_image, estimate_video
from core.file_scanner import FileInfo, MediaType
from utils.size_utils import human_readable

_SLIDER_MIN = 1    # 1 % of original
_SLIDER_MAX = 100  # 100 % of original (= no compression)

# Stack page indices
_PAGE_PLACEHOLDER = 0
_PAGE_IMAGE = 1
_PAGE_VIDEO = 2


class PreviewPanel(QWidget):
    """
    Embeddable quality-preview panel.

    Signals
    -------
    quality_accepted(new_target_bytes: int, apply_to_all: bool)
        Emitted when the user clicks "Apply".  MainWindow handles the
        table update.
    """

    quality_accepted = pyqtSignal(int, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fi: FileInfo | None = None
        self._temp_files: list[Path] = []
        self._worker: "_EncodeWorker | None" = None
        self._setup_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 4, 0, 0)
        root.setSpacing(4)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_placeholder())   # 0
        self._stack.addWidget(self._build_image_panels())  # 1
        self._stack.addWidget(self._build_video_panel())   # 2
        root.addWidget(self._stack, stretch=1)

        root.addWidget(self._build_controls())
        self._set_controls_enabled(False)

    def _build_placeholder(self) -> QLabel:
        lbl = QLabel("Select a file in the table above, then click  Preview.")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: gray; font-style: italic;")
        return lbl

    def _build_image_panels(self) -> QWidget:
        container = QWidget()
        hbox = QHBoxLayout(container)
        hbox.setContentsMargins(4, 0, 4, 0)
        hbox.setSpacing(10)

        orig_group = QGroupBox("Original")
        orig_inner = QVBoxLayout(orig_group)
        self._orig_img = _ScaledImageLabel()
        self._orig_size_lbl = QLabel()
        self._orig_size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        orig_inner.addWidget(self._orig_img, stretch=1)
        orig_inner.addWidget(self._orig_size_lbl)

        comp_group = QGroupBox("Compressed")
        comp_inner = QVBoxLayout(comp_group)
        self._comp_img = _ScaledImageLabel()
        self._comp_size_lbl = QLabel("—")
        self._comp_size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        comp_inner.addWidget(self._comp_img, stretch=1)
        comp_inner.addWidget(self._comp_size_lbl)

        hbox.addWidget(orig_group)
        hbox.addWidget(comp_group)
        return container

    def _build_video_panel(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 0, 4, 0)
        vbox.setSpacing(6)

        self._video_info_lbl = QLabel()
        self._video_info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self._video_info_lbl)

        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumHeight(100)
        self._audio_output = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_widget)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        vbox.addWidget(self._video_widget, stretch=1)

        ctrl = QWidget()
        ctrl_layout = QHBoxLayout(ctrl)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        self._play_btn = QPushButton("▶  Play")
        self._play_btn.setEnabled(False)
        self._play_btn.setFixedWidth(90)
        self._play_btn.clicked.connect(self._toggle_play)
        self._video_status_lbl = QLabel()
        self._video_status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self._play_btn)
        ctrl_layout.addSpacing(12)
        ctrl_layout.addWidget(self._video_status_lbl)
        ctrl_layout.addStretch()
        vbox.addWidget(ctrl)
        return container

    def _build_controls(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(6, 4, 6, 6)
        vbox.setSpacing(6)

        # Slider row
        slider_row = QWidget()
        sl = QHBoxLayout(slider_row)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        sl.addWidget(QLabel("Smaller"))
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(_SLIDER_MIN, _SLIDER_MAX)
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setTickInterval(10)
        self._slider.setToolTip("Drag to change target size; release to regenerate preview")
        self._slider.valueChanged.connect(self._on_slider_moved)
        self._slider.sliderReleased.connect(self._on_slider_released)
        sl.addWidget(self._slider, stretch=1)
        sl.addWidget(QLabel("Larger"))
        sl.addSpacing(12)
        self._target_lbl = QLabel()
        self._target_lbl.setMinimumWidth(200)
        self._target_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        sl.addWidget(self._target_lbl)
        vbox.addWidget(slider_row)

        # Apply row: checkbox + generating label + apply button
        apply_row = QWidget()
        ar = QHBoxLayout(apply_row)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.setSpacing(10)
        self._apply_all_check = QCheckBox("Apply this quality to all files")
        self._apply_all_check.setChecked(True)
        self._apply_all_check.setToolTip(
            "When checked, the same quality percentage is applied proportionally "
            "to every file in the list."
        )
        self._apply_all_check.stateChanged.connect(self._on_apply_all_changed)
        ar.addWidget(self._apply_all_check)
        ar.addStretch()
        self._generating_lbl = QLabel()
        self._generating_lbl.setStyleSheet("color: gray;")
        ar.addWidget(self._generating_lbl)
        self._apply_btn = QPushButton("Apply to all")
        self._apply_btn.setFixedWidth(100)
        self._apply_btn.setToolTip("Commit this target size to the file list")
        self._apply_btn.clicked.connect(self._on_apply)
        ar.addWidget(self._apply_btn)
        vbox.addWidget(apply_row)

        self._controls_container = container
        return container

    # ── public API ────────────────────────────────────────────────────────────

    def load_file(self, file_info: FileInfo) -> None:
        """Load a new file and start generating its preview."""
        self._release_player()
        self._cleanup_temp_files()
        self._cancel_worker()

        self._fi = copy.copy(file_info)
        self._set_controls_enabled(True)
        self._slider.setValue(self._pct(self._fi.target_size))
        self._refresh_target_label()

        if file_info.media_type == MediaType.IMAGE:
            self._stack.setCurrentIndex(_PAGE_IMAGE)
            self._orig_size_lbl.setText(human_readable(file_info.original_size))
            px = QPixmap(str(file_info.path))
            self._orig_img.setPixmap(px) if not px.isNull() else self._orig_img.setText("?")
            self._comp_img.clear()
            self._comp_img.setText("Generating…")
            self._comp_size_lbl.setText("—")
        else:
            self._stack.setCurrentIndex(_PAGE_VIDEO)
            self._video_info_lbl.setText(
                f"<b>{file_info.path.name}</b>  ·  "
                f"original: {human_readable(file_info.original_size)}  ·  "
                "showing first 5 seconds"
            )
            self._play_btn.setEnabled(False)
            self._video_status_lbl.setText("Generating preview clip…")

        self._schedule_encode()

    def clear(self) -> None:
        """Reset to placeholder state and release resources."""
        self._release_player()
        self._cleanup_temp_files()
        self._cancel_worker()
        self._fi = None
        self._stack.setCurrentIndex(_PAGE_PLACEHOLDER)
        self._set_controls_enabled(False)

    def cleanup(self) -> None:
        """Called by MainWindow.closeEvent — waits for worker, removes temp files."""
        if self._worker and self._worker.isRunning():
            try:
                self._worker.encode_finished.disconnect()
                self._worker.encode_error.disconnect()
            except TypeError:
                pass
            self._worker.requestInterruption()
            self._worker.wait()
            self._worker = None
        self._release_player()
        self._cleanup_temp_files()

    # ── slots: slider ─────────────────────────────────────────────────────────

    def _on_slider_moved(self, value: int) -> None:
        if self._fi:
            self._fi.target_size = self._slider_to_bytes(value)
            self._refresh_target_label()

    def _on_slider_released(self) -> None:
        self._schedule_encode()

    # ── slots: video ──────────────────────────────────────────────────────────

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

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        """Loop the 5-second preview clip continuously."""
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._player.setPosition(0)
            self._player.play()

    # ── slots: apply ──────────────────────────────────────────────────────────

    def _on_apply_all_changed(self) -> None:
        self._apply_btn.setText(
            "Apply to all" if self._apply_all_check.isChecked() else "Apply"
        )

    def _on_apply(self) -> None:
        if self._fi:
            apply_to_all = self._apply_all_check.isChecked()
            self.quality_accepted.emit(self._fi.target_size, apply_to_all)

            # Brief visual confirmation on the button
            confirm = "✓ Applied to all" if apply_to_all else "✓ Applied"
            self._apply_btn.setText(confirm)
            self._apply_btn.setEnabled(False)
            QTimer.singleShot(1500, self._restore_apply_btn)

    def _restore_apply_btn(self) -> None:
        apply_to_all = self._apply_all_check.isChecked()
        self._apply_btn.setText("Apply to all" if apply_to_all else "Apply")
        self._apply_btn.setEnabled(True)

    # ── encode worker lifecycle ───────────────────────────────────────────────

    def _schedule_encode(self) -> None:
        if not self._fi:
            return
        self._cancel_worker()
        self._set_loading(True)
        worker = _EncodeWorker(copy.copy(self._fi))
        worker.encode_finished.connect(self._on_encode_finished)
        worker.encode_error.connect(self._on_encode_error)
        self._worker = worker
        worker.start()

    def _cancel_worker(self) -> None:
        """Disconnect and abandon any in-progress worker (non-blocking)."""
        if self._worker and self._worker.isRunning():
            try:
                self._worker.encode_finished.disconnect(self._on_encode_finished)
                self._worker.encode_error.disconnect(self._on_encode_error)
            except TypeError:
                pass
            self._worker.requestInterruption()
            self._worker.finished.connect(self._worker.deleteLater)
            self._worker = None

    def _on_encode_finished(self, temp_path: Path) -> None:
        self._temp_files.append(temp_path)
        self._set_loading(False)

        if self._fi and self._fi.media_type == MediaType.IMAGE:
            px = QPixmap(str(temp_path))
            if not px.isNull():
                self._comp_img.setPixmap(px)
            size = temp_path.stat().st_size
            self._comp_size_lbl.setText(
                f"{human_readable(size)}  "
                f"({_savings_pct(size, self._fi.original_size):.0f}% savings)"
            )
        else:
            # Release previous lock before setting new source
            self._release_player()
            self._player.setSource(QUrl.fromLocalFile(str(temp_path)))
            self._play_btn.setEnabled(True)
            self._player.play()
            if self._fi:
                size = temp_path.stat().st_size
                self._video_status_lbl.setText(
                    f"{human_readable(size)}  "
                    f"({_savings_pct(size, self._fi.original_size):.0f}% savings)"
                )

    def _on_encode_error(self, message: str) -> None:
        self._set_loading(False)
        if self._fi and self._fi.media_type == MediaType.IMAGE:
            self._comp_img.setText(f"Error:\n{message}")
            self._comp_size_lbl.setText("—")
        else:
            self._video_status_lbl.setText(f"Error: {message}")

    def _set_loading(self, loading: bool) -> None:
        self._slider.setEnabled(not loading)
        self._apply_btn.setEnabled(not loading)
        self._generating_lbl.setText("Generating…" if loading else "")

    # ── resource management ───────────────────────────────────────────────────

    def _release_player(self) -> None:
        """Stop the media player and clear its source to release Windows file locks."""
        if hasattr(self, "_player"):
            self._player.stop()
            self._player.setSource(QUrl())   # releases the file handle
            self._play_btn.setEnabled(False)

    def _cleanup_temp_files(self) -> None:
        """Delete temp files; skips any still locked by Windows."""
        QApplication.processEvents()  # let Qt flush pending media operations
        remaining = []
        for p in self._temp_files:
            try:
                p.unlink(missing_ok=True)
            except PermissionError:
                remaining.append(p)  # still locked; retry on next cleanup
        self._temp_files = remaining

    # ── helpers ───────────────────────────────────────────────────────────────

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._slider.setEnabled(enabled)
        self._apply_all_check.setEnabled(enabled)
        self._apply_btn.setEnabled(enabled)

    def _pct(self, target_bytes: int) -> int:
        if not self._fi or self._fi.original_size == 0:
            return _SLIDER_MAX
        return max(_SLIDER_MIN, min(_SLIDER_MAX, round(target_bytes / self._fi.original_size * 100)))

    def _slider_to_bytes(self, value: int) -> int:
        if not self._fi:
            return 0
        return max(1, int(value / 100 * self._fi.original_size))

    def _refresh_target_label(self) -> None:
        if not self._fi:
            self._target_lbl.setText("")
            return
        pct_savings = 100 - self._fi.target_size / max(1, self._fi.original_size) * 100
        self._target_lbl.setText(
            f"Target: {human_readable(self._fi.target_size)}  "
            f"({pct_savings:.0f}% savings)"
        )


# ── background encode worker ──────────────────────────────────────────────────

class _EncodeWorker(QThread):
    encode_finished = pyqtSignal(Path)
    encode_error = pyqtSignal(str)

    def __init__(self, file_info: FileInfo) -> None:
        super().__init__()
        self._fi = file_info

    def run(self) -> None:
        try:
            path = (
                estimate_image(self._fi)
                if self._fi.media_type == MediaType.IMAGE
                else estimate_video(self._fi)
            )
            if self.isInterruptionRequested():
                path.unlink(missing_ok=True)
            else:
                self.encode_finished.emit(path)
        except Exception as exc:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.encode_error.emit(str(exc))


# ── helpers ───────────────────────────────────────────────────────────────────

def _savings_pct(compressed: int, original: int) -> float:
    return 0.0 if original == 0 else (1 - compressed / original) * 100


class _ScaledImageLabel(QLabel):
    """QLabel that scales its pixmap to fit while keeping aspect ratio."""

    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(100, 80)
        self._src: QPixmap | None = None

    def setPixmap(self, pixmap: QPixmap) -> None:  # type: ignore[override]
        self._src = pixmap
        self._render()

    def resizeEvent(self, event) -> None:
        self._render()
        super().resizeEvent(event)

    def _render(self) -> None:
        if self._src and not self._src.isNull():
            super().setPixmap(
                self._src.scaled(
                    self.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
