import sys

from PySide6.QtWidgets import QApplication

from . import profiles
from .app import MainWindow, load_stylesheet


def main() -> int:
    profiles.migrate_old_config()
    app = QApplication(sys.argv)
    app.setApplicationName("fanatec-pitbox")
    app.setApplicationDisplayName("Fanatec Pitbox")
    app.setDesktopFileName("fanatec-pitbox")
    app.setStyle("Fusion")
    app.setStyleSheet(load_stylesheet())
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
