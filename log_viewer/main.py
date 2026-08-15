"""Entry point — uruchamia aplikację log-viewer (PySide6)."""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

from PySide6 import QtGui, QtWidgets

from .config import UserConfig
from .helpers import get_resource_path
from .main_window import LogViewerWindow


def main() -> None:
    # Wymagane dla multiprocessing na Windows (PyInstaller, frozen exe).
    # Bez tego aplikacja może wejść w nieskończoną pętlę tworzenia procesów.
    multiprocessing.freeze_support()

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("log-viewer")
    app.setApplicationDisplayName("log-viewer")
    app.setApplicationVersion("1.0")

    # Ustawianie ikony okna aplikacji
    icon_path = get_resource_path("assets/icon.png")
    if Path(icon_path).exists():
        app.setWindowIcon(QtGui.QIcon(icon_path))

    config = UserConfig()
    window = LogViewerWindow(config=config)

    # Nasłuchuj zmiany motywu systemowego (dark/light mode toggle)
    def on_color_scheme_changed() -> None:
        window.apply_theme()

    try:
        app.styleHints().colorSchemeChanged.connect(on_color_scheme_changed)
    except (AttributeError, RuntimeError):
        pass  # Qt < 6.5 nie ma colorSchemeChanged

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
