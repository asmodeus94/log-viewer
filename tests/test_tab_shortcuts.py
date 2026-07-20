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

    assert 'Ctrl+Tab' in next_shortcuts
    assert 'Ctrl+]' in next_shortcuts

    assert 'Ctrl+Shift+Tab' in prev_shortcuts
    assert 'Ctrl+[' in prev_shortcuts
