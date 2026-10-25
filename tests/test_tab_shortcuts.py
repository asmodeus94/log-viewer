"""Testy skrótów klawiszowych dla zarządzania kartami."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Konfiguracja Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.main_window import LogViewerWindow
from PySide6 import QtWidgets


def test_tab_shortcuts():
    if QtWidgets.QApplication.instance() is None:
        QtWidgets.QApplication(sys.argv)
    win = LogViewerWindow()

    next_shortcuts = [s.toString() for s in win._action_next_tab.shortcuts()]
    prev_shortcuts = [s.toString() for s in win._action_prev_tab.shortcuts()]

    # Sprawdzenie, czy obecne są tylko i wyłącznie poprawne skróty
    # oraz weryfikacja braku kłopotliwych na macOS znaków '{' i '}'
    assert "Ctrl+Tab" in next_shortcuts
    assert "Ctrl+]" in next_shortcuts
    assert len(next_shortcuts) == 2
    for s in next_shortcuts:
        assert "{" not in s and "}" not in s

    assert "Ctrl+Shift+Tab" in prev_shortcuts
    assert "Ctrl+[" in prev_shortcuts
    assert len(prev_shortcuts) == 2
    for s in prev_shortcuts:
        assert "{" not in s and "}" not in s
