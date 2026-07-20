"""Testy akcji kontekstowych zależnych od otwartych kart."""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Konfiguracja Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets
from log_reader.app import LogViewerWindow

def test_context_actions_disabled_no_tabs(tmp_path):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = LogViewerWindow()

    # Sprawdzenie czy nie ma otwartych kart
    assert win.tabs.count() == 0

    # Akcje goto_start i goto_end powinny być zablokowane gdy nie ma kart
    assert not win._action_goto_start.isEnabled()
    assert not win._action_goto_end.isEnabled()

    # Otwieramy kartę
    test_file = tmp_path / "test.log"
    test_file.write_text("Hello\nWorld")
    win.open_file_in_tab(str(test_file))

    # Teraz powinny być włączone
    assert win._action_goto_start.isEnabled()
    assert win._action_goto_end.isEnabled()

    # Zamykamy okno
    win.close()
