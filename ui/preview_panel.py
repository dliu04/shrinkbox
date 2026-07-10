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

from PyQt6.QtCore import QEvent, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
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
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 0, 4, 0)
        vbox.setSpacing(4)

        panels = QWidget()
        hbox = QHBoxLayout(panels)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(10)

        orig_group = QGroupBox("Original")
        orig_inner = QVBoxLayout(orig_group)
        self._orig_img = _ZoomableImageView()
        self._orig_size_lbl = QLabel()
        self._orig_size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        orig_inner.addWidget(self._orig_img, stretch=1)
        orig_inner.addWidget(self._orig_size_lbl)

        comp_group = QGroupBox("Compressed")
        comp_inner = QVBoxLayout(comp_group)
        self._comp_img = _ZoomableImageView()
        self._comp_size_lbl = QLabel("\u2014")
        self._comp_size_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        comp_inner.addWidget(self._comp_img, stretch=1)
        comp_inner.addWidget(self._comp_size_lbl)

        hbox.addWidget(orig_group)
        hbox.addWidget(comp_group)
        vbox.addWidget(panels, stretch=1)
        vbox.addWidget(self._build_zoom_bar("image"))
        return container

    def _build_video_panel(self) -> QWidget:
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(4, 0, 4, 0)
        vbox.setSpacing(6)

        self._video_info_lbl = QLabel()
        self._video_info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self._video_info_lbl)

        # Scroll area wrapper — resizing the inner container zooms the video
        self._video_scroll = QScrollArea()
        self._video_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._video_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video_scroll.setWidgetResizable(True)  # zoom=1: fill viewport
        self._video_scroll.viewport().installEventFilter(self)
        self._video_zoom = 1.0

        self._video_container = QWidget()
        _cl = QVBoxLayout(self._video_container)
        _cl.setContentsMargins(0, 0, 0, 0)
        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumHeight(100)
        _cl.addWidget(self._video_widget)
        self._video_scroll.setWidget(self._video_container)

        self._audio_output = QAudioOutput()
        self._player = QMediaPlayer()
        self._player.setAudioOutput(self._audio_output)
        self._player.setVideoOutput(self._video_widget)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        vbox.addWidget(self._video_scroll, stretch=1)

        ctrl = QWidget()
        ctrl_layout = QHBoxLayout(ctrl)
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        _style = QApplication.style()
        self._play_btn = QPushButton("Play")
        self._play_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
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
        ctrl_layout.addWidget(self._build_zoom_bar("video"))
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

    def _build_zoom_bar(self, kind: str) -> QWidget:
        """Return a small [\u2212] [Fit] [+] row wired to image or video zoom."""
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(2)
        layout.addStretch()

        btn_out = QPushButton("\u2212")
        btn_out.setFixedSize(26, 22)
        btn_out.setToolTip("Zoom out  (scroll wheel)")
        btn_fit = QPushButton("Fit")
        btn_fit.setFixedSize(36, 22)
        btn_fit.setToolTip("Reset to fit view")
        btn_in = QPushButton("+")
        btn_in.setFixedSize(26, 22)
        btn_in.setToolTip("Zoom in  (scroll wheel)")

        if kind == "image":
            def _zo():
                self._orig_img.zoom_out()
                self._comp_img.zoom_out()
            def _zf():
                self._orig_img.zoom_fit()
                self._comp_img.zoom_fit()
            def _zi():
                self._orig_img.zoom_in()
                self._comp_img.zoom_in()
            btn_out.clicked.connect(_zo)
            btn_fit.clicked.connect(_zf)
            btn_in.clicked.connect(_zi)
        else:
            btn_out.clicked.connect(self._video_zoom_out)
            btn_fit.clicked.connect(self._video_zoom_fit)
            btn_in.clicked.connect(self._video_zoom_in)

        layout.addWidget(btn_out)
        layout.addWidget(btn_fit)
        layout.addWidget(btn_in)
        return bar

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
            self._video_zoom_set(1.0)
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

    def pause_playback(self) -> None:
        """Pause video if currently playing \u2014 called before compression starts."""
        if (hasattr(self, "_player")
                and self._player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState):
            self._player.pause()

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
        _style = QApplication.style()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._play_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_MediaPause))
            self._play_btn.setText("Pause")
        else:
            self._play_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            self._play_btn.setText("Play")

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        """Loop the 5-second preview clip continuously."""
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._player.setPosition(0)
            self._player.play()

    # ── event filter (video scroll-wheel zoom) ────────────────────────────────

    def eventFilter(self, obj, event) -> bool:
        if (hasattr(self, "_video_scroll")
                and obj is self._video_scroll.viewport()
                and event.type() == QEvent.Type.Wheel):
            if event.angleDelta().y() > 0:
                self._video_zoom_in()
            else:
                self._video_zoom_out()
            return True
        return super().eventFilter(obj, event)

    # ── video zoom ────────────────────────────────────────────────────────────

    def _video_zoom_in(self) -> None:
        self._video_zoom_set(self._video_zoom * 1.25)

    def _video_zoom_out(self) -> None:
        self._video_zoom_set(self._video_zoom / 1.25)

    def _video_zoom_fit(self) -> None:
        self._video_zoom_set(1.0)

    def _video_zoom_set(self, z: float) -> None:
        self._video_zoom = max(0.1, min(z, 8.0))
        if abs(self._video_zoom - 1.0) < 0.02:
            self._video_zoom = 1.0
            self._video_container.setMinimumSize(0, 0)
            self._video_container.setMaximumSize(16_777_215, 16_777_215)
            self._video_scroll.setWidgetResizable(True)
        else:
            self._video_scroll.setWidgetResizable(False)
            vw = self._video_scroll.viewport().width()
            vh = self._video_scroll.viewport().height()
            self._video_container.setFixedSize(
                max(1, int(vw * self._video_zoom)),
                max(1, int(vh * self._video_zoom)),
            )

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


class _ZoomableImageView(QScrollArea):
    """QScrollArea displaying a QPixmap with scroll-wheel / button zoom.

    _zoom = 0.0  \u2192  fit to viewport (default; resets on each new image).
    _zoom > 0.0  \u2192  fixed scale; scrollbars appear when larger than view.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(100, 80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._inner = QLabel()
        self._inner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._inner.setMinimumSize(1, 1)
        self.setWidget(self._inner)
        self._src: QPixmap | None = None
        self._zoom: float = 0.0   # 0 = fit

    # ── public ────────────────────────────────────────────────────────────────

    def setPixmap(self, pixmap: QPixmap) -> None:   # type: ignore[override]
        self._src = pixmap
        self._zoom = 0.0
        self._render()

    def clear(self) -> None:
        self._src = None
        self._inner.clear()
        self._inner.resize(
            max(1, self.viewport().width()), max(1, self.viewport().height())
        )

    def setText(self, text: str) -> None:   # type: ignore[override]
        self._src = None
        self._inner.setText(text)
        self._inner.resize(
            max(1, self.viewport().width()), max(1, self.viewport().height())
        )

    def zoom_in(self) -> None:
        self._zoom = self._effective_zoom() * 1.25
        self._render()

    def zoom_out(self) -> None:
        self._zoom = max(0.05, self._effective_zoom() / 1.25)
        self._render()

    def zoom_fit(self) -> None:
        self._zoom = 0.0
        self._render()

    # ── events ────────────────────────────────────────────────────────────────

    def wheelEvent(self, event) -> None:
        if event.angleDelta().y() > 0:
            self.zoom_in()
        else:
            self.zoom_out()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._zoom == 0.0:
            self._render()

    # ── internals ─────────────────────────────────────────────────────────────

    def _effective_zoom(self) -> float:
        if self._zoom > 0.0:
            return self._zoom
        if not self._src or self._src.isNull():
            return 1.0
        vw, vh = self.viewport().width(), self.viewport().height()
        sw, sh = self._src.width(), self._src.height()
        if sw == 0 or sh == 0:
            return 1.0
        return min(vw / sw, vh / sh)

    def _render(self) -> None:
        if not self._src or self._src.isNull():
            return
        z = self._effective_zoom()
        w = max(1, int(self._src.width() * z))
        h = max(1, int(self._src.height() * z))
        px = self._src.scaled(
            w, h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._inner.setPixmap(px)
        self._inner.resize(px.width(), px.height())
