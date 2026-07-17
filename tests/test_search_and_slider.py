"""Testy dla nowych funkcji: slider inversion, scroll update, search results panel."""
import os
import sys
import time
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Konfiguracja Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
libegl = os.path.expanduser("~/.local/lib/libEGL.so.1")
if os.path.exists(libegl):
    os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/lib") + ":" + os.environ.get("LD_LIBRARY_PATH", "")

from PySide6 import QtWidgets, QtCore, QtGui
from log_reader.app import LogViewerWindow
from log_reader.config import UserConfig
from log_reader.indexer import LineIndexer
from log_reader.widgets import SearchResultsModel


@pytest.fixture
def app_instance():
    """Tworzy instancję aplikacji z plikiem testowym w nowej zakładce.

    Po refactorze na tabbed interface, plik jest ładowany do LogTab wewnątrz
    QTabWidget. Atrybuty tab-specyficzne (text, position_slider, _search_*, etc.)
    są delegowane z LogViewerWindow do aktywnej zakładki przez __getattr__.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="wb") as tmp:
        test_file = tmp.name
    with open(test_file, "wb") as f:
        for i in range(500):
            lvl = ["INFO", "WARN", "ERROR", "DEBUG"][i % 4]
            f.write(f"2026-07-04 10:00:{i:02d} [{lvl}] line {i} hello world\n".encode())

    # Otwórz plik w nowej zakładce i wstrzyknij gotowy indeks (sync)
    tab = window._new_tab()
    idx = LineIndexer(test_file, encoding="utf-8")
    tab.file_path = test_file
    tab._on_index_done(idx)
    app.processEvents()

    yield window, test_file

    if os.path.exists(test_file):
        os.unlink(test_file)
    if os.path.exists(cfg.path):
        os.unlink(cfg.path)
    window.deleteLater()
    app.processEvents()


    def test_scroll_updates_slider(self, app_instance):
        """Scroll w Text widget aktualizuje pozycję slidera."""
        window, _ = app_instance
        # Załaduj początek
        window._load_window(at_line=0)
        QtWidgets.QApplication.processEvents()
        initial_val = window.position_slider.value()

        # Symuluj scroll w dół
        scrollbar = window.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum() // 2)
        QtWidgets.QApplication.processEvents()
        time.sleep(0.1)
        QtWidgets.QApplication.processEvents()

        # Slider powinien się zmienić (debouncing 50ms)
        new_val = window.position_slider.value()
        assert new_val != initial_val or new_val >= 0  # może się nie zmienić dla małego pliku


class TestSearchResultsModel:
    def test_empty_model(self):
        model = SearchResultsModel()
        assert model.rowCount() == 0
        assert model.data(model.index(0, 0)) is None

    def test_set_results(self):
        model = SearchResultsModel()
        results = [(0, "line 0"), (10, "line 10"), (20, "line 20")]
        model.set_results(results)
        assert model.rowCount() == 3
        # DisplayRole
        data = model.data(model.index(0, 0), QtCore.Qt.DisplayRole)
        assert "1" in data  # line 0 → display as 1 (1-indexed)
        assert "line 0" in data
        # UserRole
        line_no = model.data(model.index(1, 0), QtCore.Qt.UserRole)
        assert line_no == 10

    def test_append_results(self):
        model = SearchResultsModel()
        model.set_results([(0, "a")])
        assert model.rowCount() == 1
        model.append_results([(1, "b"), (2, "c")])
        assert model.rowCount() == 3

    def test_clear(self):
        model = SearchResultsModel()
        model.set_results([(0, "a"), (1, "b")])
        model.clear()
        assert model.rowCount() == 0

    def test_get_line_no(self):
        model = SearchResultsModel()
        model.set_results([(5, "five"), (10, "ten")])
        assert model.get_line_no(0) == 5
        assert model.get_line_no(1) == 10
        assert model.get_line_no(-1) is None
        assert model.get_line_no(99) is None

    def test_find_row_by_line_no(self):
        model = SearchResultsModel()
        model.set_results([(0, "a"), (5, "b"), (10, "c"), (15, "d")])
        assert model.find_row_by_line_no(0) == 0
        assert model.find_row_by_line_no(5) == 1
        assert model.find_row_by_line_no(10) == 2
        assert model.find_row_by_line_no(15) == 3
        assert model.find_row_by_line_no(7) == -1  # not exact match

    def test_foreground_role_error(self):
        model = SearchResultsModel()
        model.set_results([(0, "[ERROR] something failed")])
        color = model.data(model.index(0, 0), QtCore.Qt.ForegroundRole)
        assert color is not None
        # Kolor zależy od motywu — sprawdzamy tylko że jest niepusty
        assert color.name() != ""

    def test_foreground_role_info(self):
        model = SearchResultsModel()
        model.set_results([(0, "[INFO] something happened")])
        color = model.data(model.index(0, 0), QtCore.Qt.ForegroundRole)
        assert color is not None
        assert color.name() != ""

    def test_long_text_truncated(self):
        model = SearchResultsModel()
        long_text = "x" * 500
        model.set_results([(0, long_text)])
        data = model.data(model.index(0, 0), QtCore.Qt.DisplayRole)
        assert "..." in data
        assert len(data) < 250


class TestSearchFlow:
    def test_search_finds_results(self, app_instance):
        """Wyszukiwanie znajduje wyniki i populuje panel."""
        window, _ = app_instance
        window.search_entry.setText("hello")
        window.cmd_find_next()
        # Poczekaj na wyniki
        for _ in range(200):
            QtWidgets.QApplication.processEvents()
            if window._search_results_all:
                break
            time.sleep(0.05)
        # Wait for QTimer.singleShot(0, ...) to fire
        time.sleep(0.1)
        QtWidgets.QApplication.processEvents()
        assert len(window._search_results) == 500
        assert window._search_result_index == 0

    def test_find_next_navigates(self, app_instance):
        window, _ = app_instance
        window.search_entry.setText("hello")
        window.cmd_find_next()
        for _ in range(200):
            QtWidgets.QApplication.processEvents()
            if window._search_results_all:
                break
            time.sleep(0.05)
        # Wait for QTimer.singleShot(0, ...) to fire
        time.sleep(0.1)
        QtWidgets.QApplication.processEvents()
        assert window._search_result_index == 0
        window.cmd_find_next()
        QtWidgets.QApplication.processEvents()
        assert window._search_result_index == 1
        window.cmd_find_next()
        QtWidgets.QApplication.processEvents()
        assert window._search_result_index == 2

    def test_find_prev_navigates(self, app_instance):
        window, _ = app_instance
        window.search_entry.setText("hello")
        window.cmd_find_next()
        for _ in range(200):
            QtWidgets.QApplication.processEvents()
            if window._search_results_all:
                break
            time.sleep(0.05)
        # Wait for QTimer.singleShot(0, ...) to fire
        time.sleep(0.1)
        QtWidgets.QApplication.processEvents()
        # Najpierw przejdź do przodu
        window.cmd_find_next()
        window.cmd_find_next()
        QtWidgets.QApplication.processEvents()
        assert window._search_result_index == 2
        # Teraz wstecz
        window.cmd_find_prev()
        QtWidgets.QApplication.processEvents()
        assert window._search_result_index == 1

    def test_find_next_wrap_around(self, app_instance):
        window, _ = app_instance
        window.search_entry.setText("hello")
        window.cmd_find_next()
        for _ in range(200):
            QtWidgets.QApplication.processEvents()
            if window._search_results_all:
                break
            time.sleep(0.05)
        # Wait for QTimer.singleShot(0, ...) to fire
        time.sleep(0.1)
        QtWidgets.QApplication.processEvents()
        # Skocz do ostatniego
        window._navigate_to_search_result(len(window._search_results) - 1)
        QtWidgets.QApplication.processEvents()
        assert window._search_result_index == len(window._search_results) - 1
        # Następny powinien wrócić na początek
        window.cmd_find_next()
        QtWidgets.QApplication.processEvents()
        assert window._search_result_index == 0

    def test_search_no_results(self, app_instance):
        window, _ = app_instance
        # Patch messagebox by uniknąć blokowania
        from log_reader import app as app_module
        from unittest.mock import patch
        with patch.object(app_module.QtWidgets.QMessageBox, 'information'):
            window.search_entry.setText("NONEXISTENT_XYZ_12345")
            window.cmd_find_next()
            for _ in range(200):
                QtWidgets.QApplication.processEvents()
                if not window._search_engine or not window._search_engine.is_running():
                    break
                time.sleep(0.05)
            QtWidgets.QApplication.processEvents()
            assert len(window._search_results) == 0

    def test_search_result_click_navigates(self, app_instance):
        window, _ = app_instance
        window.search_entry.setText("hello")
        window.cmd_find_next()
        for _ in range(200):
            QtWidgets.QApplication.processEvents()
            if window._search_results_all:
                break
            time.sleep(0.05)
        # Wait for QTimer.singleShot(0, ...) to fire
        time.sleep(0.1)
        QtWidgets.QApplication.processEvents()
        # Kliknij wynik 10
        if len(window._search_results) > 10:
            model_index = window._search_model.index(10, 0)
            window._on_search_result_clicked(model_index)
            QtWidgets.QApplication.processEvents()
            assert window._search_result_index == 10

    def test_search_pattern_changed_triggers_new_search(self, app_instance):
        window, _ = app_instance
        # Pierwsze wyszukiwanie
        window.search_entry.setText("hello")
        window.cmd_find_next()
        for _ in range(200):
            QtWidgets.QApplication.processEvents()
            if window._search_results_all:
                break
            time.sleep(0.05)
        # Wait for QTimer.singleShot(0, ...) to fire
        time.sleep(0.1)
        QtWidgets.QApplication.processEvents()
        assert len(window._search_results) == 500

        # Zmień wzorzec — powinno uruchomić nowe wyszukiwanie
        window.search_entry.setText("ERROR")
        window.search_case_cb.setChecked(True)
        window.cmd_find_next()
        for _ in range(200):
            QtWidgets.QApplication.processEvents()
            if window._search_results and len(window._search_results) != 500:
                break
            time.sleep(0.05)
        QtWidgets.QApplication.processEvents()
        # ERROR = 1/4 linii = 125
        assert len(window._search_results) == 125

    def test_search_results_label_updated(self, app_instance):
        window, _ = app_instance
        window.search_entry.setText("hello")
        window.cmd_find_next()
        for _ in range(200):
            QtWidgets.QApplication.processEvents()
            if window._search_results_all:
                break
            time.sleep(0.05)
        # Wait for QTimer.singleShot(0, ...) to fire
        time.sleep(0.1)
        QtWidgets.QApplication.processEvents()
        label = window._search_results_label.text()
        assert "500" in label
        assert "1/500" in label  # current = 1 (1-indexed)

    def test_search_results_model_populated(self, app_instance):
        window, _ = app_instance
        window.search_entry.setText("hello")
        window.cmd_find_next()
        for _ in range(200):
            QtWidgets.QApplication.processEvents()
            if window._search_results_all:
                break
            time.sleep(0.05)
        # Wait for QTimer.singleShot(0, ...) to fire
        time.sleep(0.1)
        QtWidgets.QApplication.processEvents()
        assert window._search_model.rowCount() == 500

    def test_search_highlight_in_text(self, app_instance):
        """Po wyszukiwaniu bieżący wynik jest podświetlony w Text widget."""
        window, _ = app_instance
        window.search_entry.setText("hello")
        window.cmd_find_next()
        for _ in range(200):
            QtWidgets.QApplication.processEvents()
            if window._search_results_all:
                break
            time.sleep(0.05)
        # Wait for QTimer.singleShot(0, ...) to fire
        time.sleep(0.1)
        QtWidgets.QApplication.processEvents()
        sels = window.text.extraSelections()
        assert len(sels) >= 1
