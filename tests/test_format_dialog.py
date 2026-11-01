"""Testy dla FormatDialog, nawigacji, maksymalizacji okna oraz przewijania poziomego (Shift + Scroll)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Konfiguracja Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.widgets import FormatDialog, LogPlainTextEdit
from PySide6 import QtWidgets
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QTextCursor, QWheelEvent


def _ensure_app() -> QtWidgets.QApplication:
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    return app  # type: ignore[return-value]


def _make_wheel_event(
    dx: int,
    dy: int,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> QWheelEvent:
    return QWheelEvent(
        QPointF(50.0, 50.0),
        QPointF(50.0, 50.0),
        QPoint(0, 0),
        QPoint(dx, dy),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def test_format_dialog_next_prev_line() -> None:
    _ensure_app()

    # Tworzymy edytor tekstowy
    editor = LogPlainTextEdit()
    editor.setPlainText('{"line": 1}\n{"line": 2}\n{"line": 3}')

    # Ustawiamy kursor na pierwszej linii i zaznaczamy ją
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.select(QTextCursor.SelectionType.LineUnderCursor)
    editor.setTextCursor(cursor)

    # Pobieramy zaznaczony tekst
    selected_text = cursor.selectedText().replace("\u2029", "\n")

    # Otwieramy FormatDialog
    dialog = FormatDialog(editor, selected_text, "JSON")

    # Weryfikacja stanu początkowego
    assert dialog.editor == editor
    assert 'line": 1' in dialog.original_text

    # Krok 1: Przechodzimy do następnej linii
    dialog.ui.btn_next.click()

    # Oczekujemy, że original_text i kursor w editor zaktualizują się
    assert 'line": 2' in dialog.original_text
    new_cursor = editor.textCursor()
    assert new_cursor.blockNumber() == 1

    # Krok 2: Przechodzimy znowu do następnej linii
    dialog.ui.btn_next.click()
    assert 'line": 3' in dialog.original_text
    assert editor.textCursor().blockNumber() == 2

    # Krok 3: Wracamy do poprzedniej linii
    dialog.ui.btn_prev.click()
    assert 'line": 2' in dialog.original_text
    assert editor.textCursor().blockNumber() == 1

    # Krok 4: Wracamy na sam początek (linia 1) i sprawdzamy zawijanie (wrap-around do ostatniej linii)
    dialog.ui.btn_prev.click()
    assert 'line": 1' in dialog.original_text
    assert editor.textCursor().blockNumber() == 0
    dialog.ui.btn_prev.click()  # kolejna próba kliknięcia wstecz na pierwszej linii
    assert 'line": 3' in dialog.original_text
    assert editor.textCursor().blockNumber() == 2

    # Krok 5: Przechodzimy na sam koniec i sprawdzamy zapętlenie w drugą stronę (do pierwszej linii)
    dialog.ui.btn_next.click()  # wrap around to line 1
    assert 'line": 1' in dialog.original_text
    assert editor.textCursor().blockNumber() == 0

    # Zamknięcie dialogu
    dialog.close()
    editor.close()


def test_format_dialog_can_be_maximized() -> None:
    """Weryfikuje, że okno FormatDialog posiada flagi maksymalizacji i minimalizacji oraz uchwyt zmiany rozmiaru."""
    _ensure_app()
    dialog = FormatDialog(None, '{"a": 1}', "JSON")
    try:
        flags = dialog.windowFlags()
        assert bool(flags & Qt.WindowType.WindowMaximizeButtonHint)
        assert bool(flags & Qt.WindowType.WindowMinimizeButtonHint)
        assert dialog.isSizeGripEnabled() is True
    finally:
        dialog.close()


def test_shift_wheel_scrolls_horizontally_in_log_editor_and_format_dialog() -> None:
    """Sprawdza przewijanie poziome przy przytrzymanym Shift + Scroll w głównym edytorze i w FormatDialog."""
    app = _ensure_app()

    editor = LogPlainTextEdit()
    editor.resize(220, 180)
    long_lines = "\n".join(f"Line {i}: " + ("X" * 400) for i in range(80))
    editor.setPlainText(long_lines)
    editor.show()
    app.processEvents()

    try:
        hbar = editor.horizontalScrollBar()
        vbar = editor.verticalScrollBar()
        assert hbar.maximum() > 0
        assert vbar.maximum() > 0
        assert hbar.value() == 0
        assert vbar.value() == 0

        scrolled_signals: list[bool] = []
        editor.user_scrolled.connect(lambda: scrolled_signals.append(True))

        # 1. Windows/Linux: Shift + pionowy ruch rolką w dół (dy < 0, dx == 0) -> przewija w prawo
        ev_down_win = _make_wheel_event(0, -120, Qt.KeyboardModifier.ShiftModifier)
        editor.wheelEvent(ev_down_win)
        assert hbar.value() > 0
        assert vbar.value() == 0
        assert len(scrolled_signals) == 0

        pos_after_first = hbar.value()

        # 2. macOS: Shift + zamieniona przez system oś X (dx < 0, dy == 0) -> przewija dalej w prawo
        ev_down_mac = _make_wheel_event(-120, 0, Qt.KeyboardModifier.ShiftModifier)
        editor.wheelEvent(ev_down_mac)
        assert hbar.value() > pos_after_first
        assert vbar.value() == 0

        # 3. Shift + ruch rolką w górę (dy > 0) -> przewija z powrotem w lewo
        ev_up = _make_wheel_event(0, 120, Qt.KeyboardModifier.ShiftModifier)
        editor.wheelEvent(ev_up)
        assert hbar.value() == pos_after_first

        # 4. Zwykły ruch rolką bez Shift -> przewija pionowo i emituje user_scrolled
        ev_vert = _make_wheel_event(0, -120, Qt.KeyboardModifier.NoModifier)
        editor.wheelEvent(ev_vert)
        assert vbar.value() > 0
        assert len(scrolled_signals) == 1
    finally:
        editor.close()

    # 5. Sprawdzenie przewijania Shift + Scroll wewnątrz FormatDialog
    long_json = '{"key": "' + ("Y" * 500) + '", "items": [' + ",".join(str(i) for i in range(60)) + "]}"
    dialog = FormatDialog(None, long_json, "JSON")
    dialog.resize(300, 240)
    dialog.show()
    app.processEvents()

    try:
        d_hbar = dialog.text_edit.horizontalScrollBar()
        d_vbar = dialog.text_edit.verticalScrollBar()
        assert d_hbar.maximum() > 0
        assert d_hbar.value() == 0
        assert d_vbar.value() == 0

        ev_dlg_shift = _make_wheel_event(0, -120, Qt.KeyboardModifier.ShiftModifier)
        QtWidgets.QApplication.sendEvent(dialog.text_edit.viewport(), ev_dlg_shift)
        assert d_hbar.value() > 0
        assert d_vbar.value() == 0
    finally:
        dialog.close()
