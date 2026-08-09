import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets
from log_viewer.main_window import LogViewerWindow
from log_viewer.config import UserConfig

def test_reload_preserves_filter_state(temp_log_file):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path = temp_log_file(num_lines=10)
    tab = window.open_file_in_tab(path)

    tab.file_path = path

    # Symulacja aktywnego filtra
    tab._main.filter_entry.setText("TestFilter")
    assert tab._main.filter_entry.text() == "TestFilter"

    # Wywołanie reload
    with patch.object(tab.file_controller, "open_file") as mock_open:
        tab.cmd_reload()
        # Sprawdzamy czy zostawiło flage na uruchomienie filter
        assert tab._pending_reload_filter is True
        
        # open_file powinno zostać wywołane z preserve_state=True
        mock_open.assert_called_once_with(path, tab._assigned_title, preserve_state=True)

    tab.close()

def test_open_file_cleans_up_threads(temp_log_file):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path = temp_log_file(num_lines=10)
    tab = window.open_file_in_tab(path)
    
    # Symulacja istniejacych pracujących watkow i workerów (podmieniamy żeby sprawdzić jak reaguje)
    mock_thread = MagicMock()
    mock_thread.isRunning.return_value = True
    tab._indexer_thread = mock_thread
    
    mock_worker = MagicMock()
    tab._indexer_worker = mock_worker

    # Ponownie otwieramy ten sam plik
    with patch("PySide6.QtCore.QThread.start"):
        tab.file_controller.open_file(path, preserve_state=True)
    
    # Zabezpieczenie antywyciekowe (Memory/Thread Leak safeguard z AGENTS.md) powinno przerwać stare wątki
    mock_worker.cancel.assert_called_once()
    mock_thread.quit.assert_called_once()
    mock_thread.wait.assert_called_once_with(1500)
    
    tab.close()
