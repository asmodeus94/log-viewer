import os
import pytest
from PySide6.QtCore import Qt, QUrl, QMimeData
from PySide6.QtGui import QDropEvent
from log_viewer.main_window import LogViewerWindow

def test_dnd_multiple_files(qtbot, tmp_path):
    # Utwórz kilka plików tymczasowych
    file1 = tmp_path / "file1.log"
    file2 = tmp_path / "file2.log"
    file3 = tmp_path / "file3.log"

    file1.write_text("line1\nline2")
    file2.write_text("line1\nline2")
    file3.write_text("line1\nline2")

    window = LogViewerWindow()
    qtbot.addWidget(window)
    window.show()
    with qtbot.waitExposed(window):
        pass

    # Początkowo nie ma żadnych zakładek (lub jest jedna pusta)
    initial_tabs = window.tabs.count()

    paths = [str(file1), str(file2), str(file3)]

    # Symulacja przeciągnięcia
    window._on_files_dropped(paths)

    # Poczekaj aż wszystkie pliki zostaną załadowane (kolejka pusta, brak aktywnej karty dnd)
    def check_finished():
        assert len(window._dnd_queue) == 0
        assert window._dnd_current_tab is None
        assert window._dnd_progress_dialog is None
        assert window.tabs.count() == initial_tabs + 3

    qtbot.waitUntil(check_finished, timeout=5000)
