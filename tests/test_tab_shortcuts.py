"""Testy skrótów klawiszowych dla zarządzania kartami."""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Konfiguracja Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets
from log_reader.app import LogViewerWindow

def test_tab_shortcuts():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = LogViewerWindow()

    next_shortcuts = [s.toString() for s in win._action_next_tab.shortcuts()]
    prev_shortcuts = [s.toString() for s in win._action_prev_tab.shortcuts()]

    # Sprawdzenie, czy obecne są tylko i wyłącznie poprawne skróty
    # oraz weryfikacja braku kłopotliwych na macOS znaków '{' i '}'
    assert 'Ctrl+Tab' in next_shortcuts
    assert 'Ctrl+]' in next_shortcuts
    assert len(next_shortcuts) == 2
    for s in next_shortcuts:
        assert '{' not in s and '}' not in s

    assert 'Ctrl+Shift+Tab' in prev_shortcuts
    assert 'Ctrl+[' in prev_shortcuts
    assert len(prev_shortcuts) == 2
    for s in prev_shortcuts:
        assert '{' not in s and '}' not in s
