import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.config import UserConfig
from log_viewer.controllers.tab_file_controller import FileController
from log_viewer.main_window import LogViewerWindow
from PySide6 import QtWidgets


def _ensure_app() -> None:
    if QtWidgets.QApplication.instance() is None:
        QtWidgets.QApplication(sys.argv)


def test_reload_preserves_filter_state(temp_log_file, tmp_path: Path):
    _ensure_app()
    cfg = UserConfig(config_path=str(tmp_path / "cfg1.json"))
    window = LogViewerWindow(config=cfg)

    try:
        path = temp_log_file(num_lines=10)
        with patch("PySide6.QtCore.QThread.start"):
            tab = window.open_file_in_tab(path)
        assert tab is not None

        tab.file_path = path

        # Symulacja aktywnego filtra
        tab._main.filter_entry.setText("TestFilter")
        assert tab._main.filter_entry.text() == "TestFilter"

        # Wywołanie reload
        controller: FileController = tab.file_controller
        with patch.object(controller, "open_file") as mock_open:
            tab.cmd_reload()
            # Sprawdzamy czy zostawiło flage na uruchomienie filter
            assert tab._pending_reload_filter is True

            # open_file powinno zostać wywołane z preserve_state=True
            mock_open.assert_called_once_with(path, tab._assigned_title, preserve_state=True)

        tab.close()
    finally:
        window.close()


def test_open_file_cleans_up_threads(temp_log_file, tmp_path: Path):
    _ensure_app()
    cfg = UserConfig(config_path=str(tmp_path / "cfg2.json"))
    window = LogViewerWindow(config=cfg)

    try:
        path = temp_log_file(num_lines=10)
        with patch("PySide6.QtCore.QThread.start"):
            tab = window.open_file_in_tab(path)
            assert tab is not None

            # Symulacja istniejacych pracujących watkow i workerów (podmieniamy żeby sprawdzić jak reaguje)
            mock_thread = MagicMock()
            mock_thread.isRunning.return_value = True
            tab._indexer_thread = mock_thread

            mock_worker = MagicMock()
            tab._indexer_worker = mock_worker

            # Ponownie otwieramy ten sam plik
            controller: FileController = tab.file_controller
            controller.open_file(path, preserve_state=True)

        # Zabezpieczenie antywyciekowe (Memory/Thread Leak safeguard z AGENTS.md) powinno przerwać stare wątki
        mock_worker.cancel.assert_called_once()
        mock_thread.quit.assert_called_once()
        mock_thread.wait.assert_called_once_with(1500)

        tab.close()
    finally:
        window.close()
