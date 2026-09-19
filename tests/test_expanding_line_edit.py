"""Testy jednostkowe dla kontrolki ExpandingLineEdit (wyszukiwanie i filtrowanie)."""

import os
import sys

import pytest
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.widgets import ExpandingLineEdit


@pytest.fixture
def qapp():
    """Tworzy lub zwraca istniejącą instancję QApplication."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    return app


def test_expanding_line_edit_single_line_focus(qapp):
    """Test sprawdza, czy pole jednolinijkowe NIE powiększa się w pionie przy wejściu w focus."""
    window = QtWidgets.QMainWindow()
    entry = ExpandingLineEdit(window)
    window.setCentralWidget(entry)
    window.show()
    qapp.processEvents()

    initial_height = entry.height()
    assert initial_height == entry.base_height

    # Wejście w focus przy pustym tekście
    entry.setFocus()
    qapp.processEvents()
    assert entry.height() == initial_height
    assert not entry._overlay.isVisible()

    # Wpisanie tekstu jednolinijkowego
    entry.setText("prosta fraza")
    qapp.processEvents()
    assert entry.height() == initial_height
    assert not entry._overlay.isVisible()
    assert entry.text() == "prosta fraza"

    window.close()


def test_expanding_line_edit_multiline_overlay(qapp):
    """Test sprawdza, czy pole wieloliniowe rozwija nakładkę bez zmiany rozmiaru w layoucie."""
    window = QtWidgets.QMainWindow()
    container = QtWidgets.QWidget(window)
    layout = QtWidgets.QVBoxLayout(container)
    entry = ExpandingLineEdit(container)
    dummy_btn = QtWidgets.QPushButton("Poniżej", container)
    layout.addWidget(entry)
    layout.addWidget(dummy_btn)
    window.setCentralWidget(container)
    window.resize(400, 300)
    window.show()
    qapp.processEvents()

    initial_entry_height = entry.height()
    initial_btn_y = dummy_btn.y()

    # Ustawienie tekstu wieloliniowego i otwarcie nakładki
    entry.setText("linia 1\nlinia 2\nlinia 3")
    entry.setFocus()
    qapp.processEvents()

    # Nakładka powinna być widoczna i mieć większą wysokość niż pole bazowe
    assert entry._overlay.isVisible()
    assert entry._overlay.height() > initial_entry_height

    # Przycisk poniżej w layoucie NIE powinien zmienić swojej pozycji (brak spychania!)
    assert entry.height() == initial_entry_height
    assert dummy_btn.y() == initial_btn_y

    assert entry.text() == "linia 1\nlinia 2\nlinia 3"

    window.close()


def test_expanding_line_edit_blur_collapse(qapp):
    """Test sprawdza zwijanie nakładki po utracie focusu (collapse / blur)."""
    window = QtWidgets.QMainWindow()
    entry = ExpandingLineEdit(window)
    window.setCentralWidget(entry)
    window.show()
    qapp.processEvents()

    entry.setText("linia 1\nlinia 2")
    entry.open_overlay()
    qapp.processEvents()
    assert entry._overlay.isVisible()

    # Zwinięcie
    entry.collapse()
    qapp.processEvents()
    assert not entry._overlay.isVisible()
    assert entry.text() == "linia 1\nlinia 2"
    # Widok bazowy pokazuje pierwszą linię
    assert entry.base_edit.text() == "linia 1"

    window.close()


def test_expanding_line_edit_key_shortcuts(qapp):
    """Test sprawdza działanie klawiszy Return, Shift+Return oraz Escape."""
    window = QtWidgets.QMainWindow()
    entry = ExpandingLineEdit(window)
    window.setCentralWidget(entry)
    window.show()
    qapp.processEvents()

    return_called = []
    entry.returnPressed.connect(lambda: return_called.append(True))

    entry.setText("szukaj")
    entry.open_overlay()
    qapp.processEvents()
    assert entry._overlay.isVisible()

    # Wciśnięcie Return w nakładce powinno wyemitować returnPressed i zwinąć nakładkę
    event = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    entry._overlay.keyPressEvent(event)
    qapp.processEvents()

    assert len(return_called) == 1
    assert not entry._overlay.isVisible()

    # Otwórz nakładkę i wciśnij Escape -> zwinięcie
    entry.open_overlay()
    qapp.processEvents()
    assert entry._overlay.isVisible()

    esc_event = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    entry._overlay.keyPressEvent(esc_event)
    qapp.processEvents()
    assert not entry._overlay.isVisible()

    window.close()


def test_expanding_line_edit_clear_and_signals(qapp):
    """Test sprawdza poprawność czyszczenia tekstu oraz emisji sygnału textChanged."""
    window = QtWidgets.QMainWindow()
    entry = ExpandingLineEdit(window)
    window.setCentralWidget(entry)
    window.show()
    qapp.processEvents()

    changes = []
    entry.textChanged.connect(lambda: changes.append(entry.text()))

    entry.setText("nowy tekst")
    assert entry.text() == "nowy tekst"
    assert len(changes) == 1

    entry.clear()
    assert entry.text() == ""
    assert not entry._overlay.isVisible()

    window.close()
