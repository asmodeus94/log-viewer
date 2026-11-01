"""Testy akcji kontekstowych zależnych od otwartych kart."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Konfiguracja Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.main_window import LogViewerWindow
from PySide6 import QtWidgets


def test_context_actions_disabled_no_tabs(tmp_path):
    if QtWidgets.QApplication.instance() is None:
        QtWidgets.QApplication(sys.argv)
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


def test_context_menu_reload_refresh_translations(tmp_path):
    if QtWidgets.QApplication.instance() is None:
        QtWidgets.QApplication(sys.argv)
    win = LogViewerWindow()
    win.set_language("pl")

    test_file = tmp_path / "test_ctx.log"
    test_file.write_text("Line 1\nLine 2")
    tab = win.open_file_in_tab(str(test_file))
    assert tab is not None

    # Sprawdzenie początkowych polskich nazw w menu kontekstowym tekstu
    custom_actions = tab.ui.text.custom_context_actions
    action_texts = [a.text() for a in custom_actions]
    assert "Odśwież (F5)" in action_texts
    assert "Przeładuj (Ctrl+R)" in action_texts

    # Zmiana języka na angielski
    win.set_language("en")
    action_texts_en = [a.text() for a in tab.ui.text.custom_context_actions]
    assert "Refresh (F5)" in action_texts_en
    assert "Reload (Ctrl+R)" in action_texts_en

    # Powrót do języka polskiego
    win.set_language("pl")
    action_texts_pl = [a.text() for a in tab.ui.text.custom_context_actions]
    assert "Odśwież (F5)" in action_texts_pl
    assert "Przeładuj (Ctrl+R)" in action_texts_pl

    win.close()
