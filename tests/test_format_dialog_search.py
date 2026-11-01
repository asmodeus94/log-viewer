"""Testy wyszukiwarki, kolorowania składni, Word Wrap, kopiowania i długich linii w FormatDialog."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.config import UserConfig
from log_viewer.indexer import LineIndexer
from log_viewer.main_window import LogViewerWindow
from log_viewer.widgets import FormatDialog, get_full_line_text_at_cursor
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPlainTextEdit


def _ensure_app() -> QtWidgets.QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app  # type: ignore[return-value]


class TestFormatDialogSearchAndFeatures:
    def test_internal_search_navigation_and_case_sensitivity(self):
        _ensure_app()
        raw = '{"alpha": "val1", "beta": "VAL1", "gamma": "val1"}'
        dialog = FormatDialog(None, raw, "JSON")

        dialog.search_input.setText("val1")
        assert len(dialog._search_matches) == 3
        assert dialog._current_match_idx == 0
        assert "1" in dialog.lbl_search_count.text()
        assert "3" in dialog.lbl_search_count.text()

        # Nawigacja w przód i zawijanie
        dialog.find_next()
        assert dialog._current_match_idx == 1
        dialog.find_next()
        assert dialog._current_match_idx == 2
        dialog.find_next()
        assert dialog._current_match_idx == 0

        # Nawigacja wstecz z zawijaniem
        dialog.find_prev()
        assert dialog._current_match_idx == 2

        # Case-sensitive: tylko 2 trafienia dla małych liter "val1"
        dialog.search_case_cb.setChecked(True)
        assert len(dialog._search_matches) == 2

        # Brak trafień
        dialog.search_input.setText("nonexistent_token")
        assert len(dialog._search_matches) == 0
        assert dialog._current_match_idx == -1
        assert len(dialog.text_edit.extraSelections()) == 0
        dialog.close()

    def test_two_stage_escape_and_f3_shortcuts(self):
        _ensure_app()
        dialog = FormatDialog(None, '{"key": "hello", "key2": "hello"}', "JSON")
        dialog.show()

        dialog.search_input.setText("hello")
        assert len(dialog._search_matches) == 2
        assert dialog._current_match_idx == 0

        # Klawisz F3 -> następny wynik
        f3_event = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, Qt.Key.Key_F3, Qt.KeyboardModifier.NoModifier)
        dialog.keyPressEvent(f3_event)
        assert dialog._current_match_idx == 1

        # Shift+F3 -> poprzedni wynik
        shift_f3 = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, Qt.Key.Key_F3, Qt.KeyboardModifier.ShiftModifier)
        dialog.keyPressEvent(shift_f3)
        assert dialog._current_match_idx == 0

        # Pierwszy Escape: czyści wyszukiwanie, ale NIE zamyka dialogu
        esc_event1 = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        dialog.keyPressEvent(esc_event1)
        assert dialog.search_input.text() == ""
        assert len(dialog._search_matches) == 0
        assert dialog.isVisible() is True

        # Drugi Escape (gdy search_input jest pusty i nie ma fokusu): zamyka dialog
        dialog.text_edit.setFocus()
        esc_event2 = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
        dialog.keyPressEvent(esc_event2)
        assert dialog.result() == QtWidgets.QDialog.DialogCode.Rejected
        dialog.close()

    def test_word_wrap_and_copy_button(self):
        _ensure_app()
        dialog = FormatDialog(None, '{"msg": "very long text value"}', "JSON")

        assert dialog.text_edit.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
        dialog.ui.wrap_cb.setChecked(True)
        assert dialog.text_edit.lineWrapMode() == QPlainTextEdit.LineWrapMode.WidgetWidth
        dialog.ui.wrap_cb.setChecked(False)
        assert dialog.text_edit.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap

        QApplication.clipboard().clear()
        dialog.ui.btn_copy.click()
        assert '"msg": "very long text value"' in QApplication.clipboard().text()
        dialog.close()

    def test_syntax_highlighter_json_and_xml_and_long_block_safety(self):
        _ensure_app()
        # JSON + przełączenie na XML z komentarzem i uszkodzonym fragmentem
        dialog = FormatDialog(None, '{"a": 123, "b": true, "c": null, "d": "str"}', "JSON")
        assert dialog.highlighter.mode == "JSON"
        assert dialog.highlighter.enabled is True

        dialog.original_text = '<!-- comment --><root attr="val"><child>1</child></root>'
        dialog.formatter_combo.setCurrentText("XML")
        assert dialog.highlighter.mode == "XML"
        assert "<root" in dialog.text_edit.toPlainText()

        # Bezpiecznik dla bloku > 10 000 znaków (brak zawieszenia na uszkodzonym, długim ciągu)
        huge_broken = "{" + '"a":' * 5000
        dialog.original_text = huge_broken
        dialog._apply_format("JSON")
        assert len(dialog.text_edit.toPlainText()) > 10_000
        dialog.close()

    def test_full_untruncated_line_fetched_from_tab(self, tmp_path: Path):
        _ensure_app()
        cfg = UserConfig(config_path=str(tmp_path / "cfg.json"))
        win = LogViewerWindow(config=cfg)
        win.max_display_line_length = 100  # Mały limit wyświetlania, aby wymusić ucięcie w widoku

        # Tworzymy linię JSON o długości > 500 znaków (w widoku zostanie ucięta do 100 znaków)
        long_payload = "x" * 500
        full_json_line = f'2026-11-01 INFO {{"status": "ok", "payload": "{long_payload}"}}'
        log_file = tmp_path / "long_line.log"
        log_file.write_text(full_json_line + "\n", encoding="utf-8")

        tab = win._new_tab()
        tab.file_path = str(log_file)
        tab._on_index_done(LineIndexer(str(log_file), encoding="utf-8"))
        QApplication.processEvents()

        # W widoku głównym linia jest ucięta:
        displayed = tab.text.toPlainText()
        assert "[truncated" in displayed

        # Ale get_full_line_text_at_cursor pobiera pełną, nieuciętą linię z bufora/pliku:
        fetched = get_full_line_text_at_cursor(tab.text, tab)
        assert fetched == full_json_line
        assert "[truncated" not in fetched

        # FormatDialog poprawnie formatuje cały obiekt JSON:
        dialog = FormatDialog(tab, fetched, "JSON")
        assert f'"payload": "{long_payload}"' in dialog.text_edit.toPlainText()
        dialog.close()
        win.close()
