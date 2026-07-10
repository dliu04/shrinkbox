"""
main.py — Shrinkbox entry point
"""
import sys

from PyQt6.QtWidgets import QApplication, QMessageBox


def _show_missing_deps_error(missing: list[str]) -> None:
    msg = QMessageBox()
    msg.setIcon(QMessageBox.Icon.Critical)
    msg.setWindowTitle("Shrinkbox — Missing Dependencies")
    msg.setText(
        "The following required tools were not found on your PATH:\n\n"
        + "\n".join(f"  \u2022 {tool}" for tool in missing)
    )
    msg.setInformativeText(
        "Download ffmpeg (which includes ffprobe) from "
        "https://ffmpeg.org/download.html, "
        "add the bin\\ folder to your system PATH, "
        "then restart Shrinkbox."
    )
    msg.exec()


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Shrinkbox")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("shrinkbox")

    # Gate on ffmpeg/ffprobe before touching anything else
    from utils.ffmpeg_utils import check_dependencies

    missing = check_dependencies()
    if missing:
        _show_missing_deps_error(missing)
        sys.exit(1)

    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
