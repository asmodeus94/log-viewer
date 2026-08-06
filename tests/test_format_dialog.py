"""Testy dla FormatDialog i nawigacji."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Konfiguracja Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets
from PySide6.QtGui import QTextCursor
from log_viewer.widgets import FormatDialog, LogPlainTextEdit

def test_format_dialog_next_prev_line():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    
    # Tworzymy edytor tekstowy
    editor = LogPlainTextEdit()
    editor.setPlainText('{"line": 1}\n{"line": 2}\n{"line": 3}')
    
    # Ustawiamy kursor na pierwszej linii i zaznaczamy ją
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.select(QTextCursor.LineUnderCursor)
    editor.setTextCursor(cursor)
    
    # Pobieramy zaznaczony tekst
    selected_text = cursor.selectedText().replace("\u2029", "\n")
    
    # Otwieramy FormatDialog
    dialog = FormatDialog(editor, selected_text, "JSON")
    
    # Weryfikacja stanu początkowego
    assert dialog.editor == editor
    assert "line\": 1" in dialog.original_text
    
    # Krok 1: Przechodzimy do następnej linii
    dialog.ui.btn_next.click()
    
    # Oczekujemy, że original_text i kursor w editor zaktualizują się
    assert "line\": 2" in dialog.original_text
    new_cursor = editor.textCursor()
    assert new_cursor.blockNumber() == 1
    
    # Krok 2: Przechodzimy znowu do następnej linii
    dialog.ui.btn_next.click()
    assert "line\": 3" in dialog.original_text
    assert editor.textCursor().blockNumber() == 2
    
    # Krok 3: Wracamy do poprzedniej linii
    dialog.ui.btn_prev.click()
    assert "line\": 2" in dialog.original_text
    assert editor.textCursor().blockNumber() == 1
    
    # Krok 4: Wracamy na sam początek (linia 1) i sprawdzamy zawijanie (wrap-around do ostatniej linii)
    dialog.ui.btn_prev.click()
    assert "line\": 1" in dialog.original_text
    assert editor.textCursor().blockNumber() == 0
    dialog.ui.btn_prev.click()  # kolejna próba kliknięcia wstecz na pierwszej linii
    assert "line\": 3" in dialog.original_text
    assert editor.textCursor().blockNumber() == 2

    # Krok 5: Przechodzimy na sam koniec i sprawdzamy zapętlenie w drugą stronę (do pierwszej linii)
    dialog.ui.btn_next.click()  # wrap around to line 1
    assert "line\": 1" in dialog.original_text
    assert editor.textCursor().blockNumber() == 0
    
    # Zamknięcie dialogu
    dialog.close()
