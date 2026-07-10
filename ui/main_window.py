"""
ui/main_window.py
-----------------
Main application window.

Layout
------
  [Input folder  ________________] [Browse]
  [Output folder ________________] [Browse]
  Target: [___] MB  [☐ Subfolders]          [Scan Folder]
  ┌──────────────────────────────────────────────────┐ ↕ splitter
  │ Filename │ Type │ Original │ Target │ Savings │ … │
  ├──────────────────────────────────────────────────┤
  │ Preview panel (image side-by-side OR video)      │
  │ Slider · [☑ Apply to all] · [Apply]              │
  └──────────────────────────────────────────────────┘
  [summary label]                   [Preview] [Compress All]
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.budget import distribute_budget
from core.file_scanner import FileInfo, MediaType, scan_folder
from utils.size_utils import human_readable

_SETTINGS_ORG = "shrinkbox"
_SETTINGS_APP = "shrinkbox"

# Table column indices
_COL_NAME = 0
_COL_TYPE = 1
_COL_ORIGINAL = 2
_COL_TARGET = 3
_COL_SAVINGS = 4
_COL_STATUS = 5

_STATUS_READY = "Ready"
_STATUS_SKIPPED = "Already small"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._files: list[FileInfo] = []
        self._output_manually_set: bool = False
        self._current_preview_row: int = -1
        self._saved_splitter_sizes: list[int] = []
        self._setup_ui()
        self._restore_settings()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self.setWindowTitle("Shrinkbox")
        self.setMinimumSize(800, 500)
        self.resize(1040, 680)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

        root.addWidget(self._build_controls())
        root.addWidget(self._build_files_header())

        from ui.preview_panel import PreviewPanel
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.setCollapsible(0, True)
        self._splitter.addWidget(self._build_table())
        self._preview_panel = PreviewPanel()
        self._preview_panel.quality_accepted.connect(self._on_quality_accepted)
        self._splitter.addWidget(self._preview_panel)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setCollapsible(1, True)

        root.addWidget(self._splitter, stretch=1)
        root.addWidget(self._build_bottom_bar())

        # Debounce timer — auto-preview fires 150 ms after selection settles
        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.setInterval(150)
        self._preview_debounce.timeout.connect(self._trigger_auto_preview)

        # Debounce timer — auto-scan fires 800 ms after folder path settles
        self._scan_debounce = QTimer(self)
        self._scan_debounce.setSingleShot(True)
        self._scan_debounce.setInterval(800)
        self._scan_debounce.timeout.connect(self._auto_scan)

    def _build_controls(self) -> QWidget:
        box = QWidget()
        form = QFormLayout(box)
        form.setContentsMargins(0, 0, 0, 4)
        form.setSpacing(6)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Input folder
        self._input_edit, input_row = self._folder_row()
        self._input_edit.setPlaceholderText("Select a folder of images / videos…")
        self._input_edit.textChanged.connect(self._on_input_changed)
        input_row.findChildren(QPushButton)[0].clicked.connect(self._browse_input)
        form.addRow("Input folder:", input_row)

        # Output folder
        self._output_edit, output_row = self._folder_row()
        self._output_edit.setPlaceholderText("Defaults to <input>_shrinkbox")
        self._output_edit.textEdited.connect(lambda: setattr(self, "_output_manually_set", True))
        output_row.findChildren(QPushButton)[0].clicked.connect(self._browse_output)
        form.addRow("Output folder:", output_row)

        # Options row: target MB + recursive + scan
        opts = QWidget()
        opts_layout = QHBoxLayout(opts)
        opts_layout.setContentsMargins(0, 2, 0, 0)
        opts_layout.setSpacing(10)

        self._target_spin = QSpinBox()
        self._target_spin.setRange(1, 999_999)
        self._target_spin.setValue(100)
        self._target_spin.setSuffix(" MB")
        self._target_spin.setFixedWidth(120)
        self._target_spin.setToolTip("Desired total size of the output folder")
        self._target_spin.valueChanged.connect(self._on_target_changed)

        self._recursive_check = QCheckBox("Include subfolders")
        self._recursive_check.setChecked(True)

        self._scan_btn = QPushButton("Scan Folder")
        self._scan_btn.clicked.connect(self._on_scan)

        opts_layout.addWidget(QLabel("Target size:"))
        opts_layout.addWidget(self._target_spin)
        opts_layout.addSpacing(12)
        opts_layout.addWidget(self._recursive_check)
        opts_layout.addStretch()
        opts_layout.addWidget(self._scan_btn)
        form.addRow("", opts)

        return box

    @staticmethod
    def _folder_row() -> tuple["QLineEdit", QWidget]:
        """Return (QLineEdit, container_widget) for a folder-picker row."""
        from PyQt6.QtWidgets import QLineEdit
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        btn = QPushButton("Browse…")
        btn.setFixedWidth(80)
        layout.addWidget(edit)
        layout.addWidget(btn)
        return edit, row

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["Filename", "Type", "Original", "Target", "Savings", "Status"]
        )
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col in (_COL_TYPE, _COL_ORIGINAL, _COL_TARGET, _COL_SAVINGS, _COL_STATUS):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        return self._table

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)

        self._summary_label = QLabel("No files scanned yet.")
        self._summary_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

        self._compress_btn = QPushButton("Compress All")
        self._compress_btn.setEnabled(False)
        self._compress_btn.setFixedWidth(120)
        self._compress_btn.setToolTip("Compress all files and save to the output folder")
        self._compress_btn.clicked.connect(self._on_compress_all)

        layout.addWidget(self._summary_label)
        layout.addWidget(self._compress_btn)
        return bar

    def _build_files_header(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(8)
        self._files_header_lbl = QLabel("Files")
        layout.addWidget(self._files_header_lbl)
        layout.addStretch()
        self._collapse_btn = QPushButton("▲  Hide table")
        self._collapse_btn.setFlat(True)
        self._collapse_btn.setFixedWidth(110)
        self._collapse_btn.setToolTip("Collapse or expand the file list")
        self._collapse_btn.clicked.connect(self._toggle_collapse_files)
        layout.addWidget(self._collapse_btn)
        return bar

    # ── slots ─────────────────────────────────────────────────────────────────

    def _browse_input(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select input folder", self._input_edit.text() or ""
        )
        if folder:
            self._input_edit.setText(folder)

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select output folder", self._output_edit.text() or ""
        )
        if folder:
            self._output_edit.setText(folder)
            self._output_manually_set = True

    def _on_input_changed(self, text: str) -> None:
        """Auto-suggest output folder and kick off auto-scan debounce."""
        stripped = text.strip()
        if not self._output_manually_set:
            if stripped:
                p = Path(stripped)
                suggested = p.parent / (p.name + "_shrinkbox")
                self._output_edit.setText(str(suggested))
            else:
                self._output_edit.clear()
        if stripped:
            self._scan_debounce.start()

    def _on_target_changed(self) -> None:
        if self._files:
            distribute_budget(self._files, self._target_spin.value())
            self._refresh_targets()
            self._update_summary()

    def _on_scan(self) -> None:
        folder_str = self._input_edit.text().strip()
        if not folder_str:
            QMessageBox.warning(self, "No folder selected",
                                "Please choose an input folder before scanning.")
            return
        self._do_scan(folder_str, show_dialogs=True)

    def _auto_scan(self) -> None:
        """Silent auto-scan triggered when the input folder path settles."""
        folder_str = self._input_edit.text().strip()
        if folder_str and Path(folder_str).is_dir():
            self._do_scan(folder_str, show_dialogs=False)

    def _do_scan(self, folder_str: str, show_dialogs: bool = True) -> None:
        folder = Path(folder_str)
        if not folder.is_dir():
            if show_dialogs:
                QMessageBox.warning(self, "Invalid folder",
                                    f"The path is not a valid directory:\n{folder}")
            return

        try:
            files = scan_folder(folder, recursive=self._recursive_check.isChecked())
        except Exception as exc:
            if show_dialogs:
                QMessageBox.critical(self, "Scan error", str(exc))
            return

        self._files = files

        if not files:
            if show_dialogs:
                QMessageBox.information(
                    self, "No media found",
                    "No supported image or video files were found in that folder.\n\n"
                    "Supported images: JPG, PNG, WebP, BMP, TIFF\n"
                    "Supported videos: MP4, MOV, MKV, AVI, WMV, M4V, WebM",
                )
            self._populate_table()
            self._compress_btn.setEnabled(False)
            self._files_header_lbl.setText("Files  (0)")
            self._update_summary()
            return

        distribute_budget(files, self._target_spin.value())
        self._populate_table()
        self._update_summary()
        self._files_header_lbl.setText(f"Files  ({len(files)})")
        self._compress_btn.setEnabled(True)

    def _on_selection_changed(self) -> None:
        row = self._table.currentRow()
        if row >= 0 and self._files:
            self._current_preview_row = row
            self._preview_debounce.start()

    def _on_quality_accepted(self, new_target: int, apply_to_all: bool) -> None:
        row = self._current_preview_row
        if row < 0 or row >= len(self._files):
            return
        file_info = self._files[row]
        if apply_to_all:
            # Apply the same quality % proportionally to every file
            quality_pct = new_target / max(1, file_info.original_size)
            for f in self._files:
                f.target_size = min(int(quality_pct * f.original_size), f.original_size)
            self._refresh_targets()
        else:
            file_info.target_size = new_target
            self._set_row(row, file_info)
        self._update_summary()

    def _trigger_auto_preview(self) -> None:
        """Called by the debounce timer; loads the selected row into the preview panel."""
        row = self._current_preview_row
        if 0 <= row < len(self._files):
            self._preview_panel.load_file(self._files[row])

    def _toggle_collapse_files(self) -> None:
        """Toggle the file-table splitter pane between collapsed and expanded."""
        sizes = self._splitter.sizes()
        if sizes[0] > 0:
            self._saved_splitter_sizes = sizes[:]
            self._splitter.setSizes([0, sum(sizes)])
            self._collapse_btn.setText("▼  Show table")
        else:
            saved = self._saved_splitter_sizes
            if saved and sum(saved) > 0:
                self._splitter.setSizes(saved)
            else:
                total = sum(sizes)
                self._splitter.setSizes([total // 2, total // 2])
            self._collapse_btn.setText("▲  Hide table")

    def _on_compress_all(self) -> None:
        output_str = self._output_edit.text().strip()
        if not output_str:
            QMessageBox.warning(self, "No output folder",
                                "Please specify an output folder before compressing.")
            return

        input_str = self._input_edit.text().strip()
        source_folder = Path(input_str) if input_str else None
        if not source_folder or not source_folder.is_dir():
            QMessageBox.warning(self, "Invalid source folder",
                                "The source folder is no longer valid. Please re-scan.")
            return

        output_folder = Path(output_str)

        # Warn if the output folder already has content
        if output_folder.exists() and output_folder.is_dir():
            try:
                has_content = any(output_folder.iterdir())
            except OSError:
                has_content = False
            if has_content:
                reply = QMessageBox.question(
                    self, "Output folder not empty",
                    f"The output folder already contains files:\n{output_folder}\n\n"
                    "Files with the same names will be overwritten.  Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Cancel,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

        from core.worker import CompressionWorker
        from ui.progress_dialog import ProgressDialog

        worker = CompressionWorker(
            files=self._files,
            source_folder=source_folder,
            output_folder=output_folder,
        )
        dlg = ProgressDialog(worker, parent=self)
        worker.start()
        dlg.exec()
        # Ensure worker is fully done before the dialog goes out of scope
        if not worker.isFinished():
            worker.wait()

    # ── table helpers ─────────────────────────────────────────────────────────

    def _populate_table(self) -> None:
        self._table.setRowCount(0)
        self._table.setRowCount(len(self._files))
        for row, f in enumerate(self._files):
            self._set_row(row, f)

    def _set_row(self, row: int, f: FileInfo) -> None:
        skipped = f.target_size >= f.original_size

        name_item = QTableWidgetItem(f.path.name)
        name_item.setToolTip(str(f.path))

        type_item = QTableWidgetItem(
            "Image" if f.media_type == MediaType.IMAGE else "Video"
        )

        orig_item = _right_item(human_readable(f.original_size))

        if skipped:
            target_item = _right_item("—")
            savings_item = _right_item("—")
            status_item = _center_item(_STATUS_SKIPPED)
        else:
            target_item = _right_item(human_readable(f.target_size))
            pct = (1 - f.target_size / f.original_size) * 100
            savings_item = _right_item(f"{pct:.0f}%")
            status_item = _center_item(_STATUS_READY)

        for col, item in enumerate(
            [name_item, type_item, orig_item, target_item, savings_item, status_item]
        ):
            item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, col, item)

    def _refresh_targets(self) -> None:
        """Update only the Target / Savings / Status columns after a budget change."""
        for row, f in enumerate(self._files):
            skipped = f.target_size >= f.original_size
            if skipped:
                self._table.item(row, _COL_TARGET).setText("—")
                self._table.item(row, _COL_SAVINGS).setText("—")
                self._table.item(row, _COL_STATUS).setText(_STATUS_SKIPPED)
            else:
                self._table.item(row, _COL_TARGET).setText(human_readable(f.target_size))
                pct = (1 - f.target_size / f.original_size) * 100
                self._table.item(row, _COL_SAVINGS).setText(f"{pct:.0f}%")
                self._table.item(row, _COL_STATUS).setText(_STATUS_READY)

    def _update_summary(self) -> None:
        if not self._files:
            self._summary_label.setText("No files scanned yet.")
            return
        total_orig = sum(f.original_size for f in self._files)
        total_target = sum(f.target_size for f in self._files)
        n_skipped = sum(1 for f in self._files if f.target_size >= f.original_size)
        savings_pct = (1 - total_target / total_orig) * 100 if total_orig else 0

        text = (
            f"{len(self._files)} file(s)  ·  "
            f"{human_readable(total_orig)} → {human_readable(total_target)}  "
            f"({savings_pct:.0f}% savings)"
        )
        if n_skipped:
            text += f"  ·  {n_skipped} already small enough"
        self._summary_label.setText(text)

    # ── settings persistence ──────────────────────────────────────────────────

    def _save_settings(self) -> None:
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        s.setValue("input_folder", self._input_edit.text())
        s.setValue("output_folder", self._output_edit.text())
        s.setValue("target_mb", self._target_spin.value())
        s.setValue("recursive", self._recursive_check.isChecked())
        s.setValue("output_manually_set", self._output_manually_set)

    def _restore_settings(self) -> None:
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        # Block textChanged signal while restoring to avoid spurious output updates
        self._output_manually_set = s.value("output_manually_set", False, type=bool)
        self._input_edit.setText(s.value("input_folder", "", type=str))
        self._output_edit.setText(s.value("output_folder", "", type=str))
        self._target_spin.setValue(int(s.value("target_mb", 100)))
        self._recursive_check.setChecked(s.value("recursive", True, type=bool))

    def closeEvent(self, event) -> None:
        self._preview_panel.cleanup()
        self._save_settings()
        super().closeEvent(event)


# ── item factories ─────────────────────────────────────────────────────────────

def _right_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


def _center_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item
