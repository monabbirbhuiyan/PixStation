import sys

from PySide6.QtWidgets import QApplication

from . import theme
from .window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PixStation")
    theme.apply(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
