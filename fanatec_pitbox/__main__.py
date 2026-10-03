import sys

from PySide6.QtWidgets import QApplication

from . import profiles
from .app import MainWindow, app_icon, load_fonts, load_stylesheet


def main() -> int:
    profiles.migrate_old_config()
    if len(sys.argv) > 1:  # command line use (e.g. Steam launch options); no window
        from .cli import main as cli_main
        return cli_main(sys.argv[1:])
    app = QApplication(sys.argv)
    app.setApplicationName("fanatec-pitbox")
    app.setApplicationDisplayName("Fanatec Pitbox")
    app.setDesktopFileName("fanatec-pitbox")
    app.setWindowIcon(app_icon())
    load_fonts()
    app.setStyle("Fusion")
    app.setStyleSheet(load_stylesheet())
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
