"""Entry point — uruchamia aplikację Czytnik Logów (PySide6)."""

from __future__ import annotations

import sys
import multiprocessing

from PySide6 import QtWidgets, QtGui
from PySide6.QtCore import Qt

from .app import LogViewerWindow
from .config import UserConfig


def main() -> None:
    # Wymagane dla multiprocessing na Windows (PyInstaller, frozen exe).
    # Bez tego aplikacja może wejść w nieskończoną pętlę tworzenia procesów.
    multiprocessing.freeze_support()

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Czytnik Logów")
    app.setApplicationVersion("1.0")
    config = UserConfig()
    window = LogViewerWindow(config=config)

    # Nasłuchuj zmiany motywu systemowego (dark/light mode toggle)
    def on_color_scheme_changed():
        window._apply_theme()

    try:
        app.styleHints().colorSchemeChanged.connect(on_color_scheme_changed)
    except Exception:
        pass  # Qt < 6.5 nie ma colorSchemeChanged

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
