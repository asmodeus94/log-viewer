"""Entry point — uruchamia aplikację log-viewer (PySide6)."""

from __future__ import annotations

import sys
import multiprocessing

from PySide6 import QtWidgets, QtGui
from PySide6.QtCore import Qt

import os
from .app import LogViewerWindow
from .config import UserConfig
from .helpers import get_resource_path


def main() -> None:
    # Wymagane dla multiprocessing na Windows (PyInstaller, frozen exe).
    # Bez tego aplikacja może wejść w nieskończoną pętlę tworzenia procesów.
    multiprocessing.freeze_support()

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("log-viewer")
    app.setApplicationVersion("1.0")

    # Ustawianie ikony okna aplikacji
    icon_path = get_resource_path(os.path.join("assets", "icon.png"))
    if os.path.exists(icon_path):
        app.setWindowIcon(QtGui.QIcon(icon_path))

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
