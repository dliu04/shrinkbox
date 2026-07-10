# Stub — fully implemented in Phase 3
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QMainWindow


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Shrinkbox")
        self.resize(960, 640)

        placeholder = QLabel("Phase 1 scaffold\nUI arrives in Phase 3")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCentralWidget(placeholder)
