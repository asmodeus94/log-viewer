"""Testy dla nowych funkcji: slider inversion, scroll update, search results panel."""

import array
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

# noinspection PyPackageRequirements
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.bitset import Bitset
from log_viewer.config import UserConfig
from log_viewer.indexer import LineIndexer
from log_viewer.log_tab import LogTab
from log_viewer.main_window import LogViewerWindow
from log_viewer.widgets import SearchResultsModel
from PySide6 import QtCore, QtGui, QtWidgets


class _CountingIndexer:
    def __init__(self, template: str = "Line {start}") -> None:
        self.calls = 0
        self._template = template

    def read_lines(self, start: int, _count: int) -> list[tuple[int, str]]:
        self.calls += 1
        return [(start, self._template.format(start=start, calls=self.calls))]


# noinspection PyProtectedMember
def _run_search_and_wait(window: LogViewerWindow, pattern: str = "hello") -> None:
    window.search_entry.setText(pattern)
    window.cmd_find_next()
    for _ in range(200):
        QtWidgets.QApplication.processEvents()
        if window._search_results_all:
            break
        time.sleep(0.05)
    time.sleep(0.1)
    QtWidgets.QApplication.processEvents()


def _apply_error_filter_and_wait(window: LogViewerWindow, tab: LogTab) -> None:
    window.filter_entry.setText("ERROR")
    window.filter_case_cb.setChecked(True)
    window.cmd_apply_filter()

    for _ in range(200):
        QtWidgets.QApplication.processEvents()
        if tab.filter_active and tab.filter_results is not None and len(tab.filter_results) == 125:
            break
        time.sleep(0.05)
    QtWidgets.QApplication.processEvents()


# noinspection PyProtectedMember
@pytest.fixture
def app_instance(tmp_path: Path):
    """Tworzy instancję aplikacji z plikiem testowym w nowej zakładce.

    Po refactorze na tabbed interface, plik jest ładowany do LogTab wewnątrz
    QTabWidget. Atrybuty tab-specyficzne (text, position_slider, _search_*, etc.)
    są delegowane z LogViewerWindow do aktywnej zakładki przez __getattr__.
    """
    if QtWidgets.QApplication.instance() is None:
        QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=str(tmp_path / "cfg.json"))
    window = LogViewerWindow(config=cfg)

    test_file_path = tmp_path / "test_search.log"
    with test_file_path.open("wb") as f:
        for i in range(500):
            lvl = ["INFO", "WARN", "ERROR", "DEBUG"][i % 4]
            f.write(f"2026-07-04 10:00:{i:02d} [{lvl}] line {i} hello world\n".encode())
    test_file = str(test_file_path)

    # Otwórz plik w nowej zakładce i wstrzyknij gotowy indeks (sync)
    tab = window._new_tab()
    idx = LineIndexer(test_file, encoding="utf-8")
    tab.file_path = test_file
    tab._on_index_done(idx)
    QtWidgets.QApplication.processEvents()

    yield window, test_file

    window.close()
    window.deleteLater()
    QtWidgets.QApplication.processEvents()


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
        data = model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert "1" in data  # line 0 → display as 1 (1-indexed)
        assert "line 0" in data
        # UserRole
        line_no = model.data(model.index(1, 0), QtCore.Qt.ItemDataRole.UserRole)
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
        color = model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.ForegroundRole)
        assert color is None
        # Kolor zależy od motywu — sprawdzamy tylko że jest niepusty

    def test_foreground_role_info(self):
        model = SearchResultsModel()
        model.set_results([(0, "[INFO] something happened")])
        color = model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.ForegroundRole)
        assert color is None

    def test_long_text_truncated(self):
        model = SearchResultsModel()
        long_text = "x" * 500
        model.set_results([(0, long_text)])
        data = model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert "..." in data
        assert len(data) < 250

    def test_thousands_separator_in_display_role(self):
        """SearchResultsModel formatuje numery linii > 999 z separatorem tysięcy."""
        model = SearchResultsModel()
        model.set_results([(1234, "test line content")])
        data = model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert "1,235" in data

    def test_bitset_results_performance_and_operations(self):
        """Weryfikacja, że SearchResultsModel obsługuje Bitset bez konwersji do list i działa błyskawicznie."""
        bs = Bitset(1_000_000)
        # noinspection PyProtectedMember
        words = array.array("Q", [0xAAAAAAAAAAAAAAAA] * bs._num_words)
        # noinspection PyProtectedMember
        bs._words = words
        # noinspection PyProtectedMember
        bs._counts = None
        # noinspection PyProtectedMember
        bs._total_count = -1

        model = SearchResultsModel()
        start = time.perf_counter()
        model.set_results(bs)
        elapsed = time.perf_counter() - start

        # Ustawienie wyników musi być natychmiastowe (O(1), bez freeze)
        assert elapsed < 0.1
        # noinspection PyProtectedMember
        assert isinstance(model._all_results, Bitset)
        assert model.rowCount() == 1000
        assert model.canFetchMore()

        # Sprawdzenie get_line_no i find_row_by_line_no
        assert model.get_line_no(0) == 1
        assert model.find_row_by_line_no(1) == 0
        assert model.find_row_by_line_no(3) == 1
        assert model.find_row_by_line_no(2) == -1

    def test_user_role_does_not_read_file(self):
        indexer = _CountingIndexer()
        model = SearchResultsModel()
        model.set_results([10, 20, 30], indexer=indexer)
        # Query UserRole
        line_no = model.data(model.index(1, 0), QtCore.Qt.ItemDataRole.UserRole)
        assert line_no == 20
        assert indexer.calls == 0

    def test_non_display_role_does_not_read_file(self):
        indexer = _CountingIndexer()
        model = SearchResultsModel()
        model.set_results([10, 20, 30], indexer=indexer)
        res = model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.ForegroundRole)
        assert res is None
        res_tip = model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.ToolTipRole)
        assert res_tip is None
        assert indexer.calls == 0

    def test_lru_cache_hit_avoids_repeated_reads(self):
        indexer = _CountingIndexer("Content for line {start}")
        model = SearchResultsModel()
        model.set_results([5, 15, 25], indexer=indexer)

        # First read
        data1 = model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert "Content for line 5" in data1
        assert indexer.calls == 1

        # Second read (cache hit)
        data2 = model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert "Content for line 5" in data2
        assert indexer.calls == 1

    # noinspection PyUnreachableCode
    def test_cache_invalidation_on_set_results_and_clear(self):
        indexer = _CountingIndexer("Content {start} (call {calls})")
        model = SearchResultsModel()
        model.set_results([42], indexer=indexer)

        model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert indexer.calls == 1

        # Clear cache explicitly
        model.clear_cache()
        # noinspection PyProtectedMember
        assert len(model._line_cache) == 0
        model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert indexer.calls == 2

        # Invalidate via set_results
        model.set_results([42], indexer=indexer)
        # noinspection PyProtectedMember
        assert len(model._line_cache) == 0
        model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert indexer.calls == 3

        # Invalidate via clear
        model.clear()
        # noinspection PyProtectedMember
        assert len(model._line_cache) == 0

    def test_lru_cache_eviction(self):
        indexer = _CountingIndexer()
        model = SearchResultsModel()
        # noinspection PyProtectedMember
        model._max_cache_size = 2
        model.set_results([100, 200, 300], indexer=indexer)

        # Read 100, then 200 (cache: [100, 200])
        model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        model.data(model.index(1, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert indexer.calls == 2

        # Read 300 (evicts 100, cache: [200, 300])
        model.data(model.index(2, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert indexer.calls == 3
        # noinspection PyProtectedMember
        assert 100 not in model._line_cache

        # Read 200 (hit, moves 200 to end: [300, 200])
        model.data(model.index(1, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert indexer.calls == 3

        # Read 100 (miss, re-reads, evicts 300: [200, 100])
        model.data(model.index(0, 0), QtCore.Qt.ItemDataRole.DisplayRole)
        assert indexer.calls == 4
        # noinspection PyProtectedMember
        assert 300 not in model._line_cache


class TestSearchFlow:
    def test_search_finds_results(self, app_instance):
        """Wyszukiwanie znajduje wyniki i populuje panel."""
        window, _ = app_instance
        _run_search_and_wait(window, "hello")
        # noinspection PyProtectedMember
        assert len(window._search_results) == 500
        # noinspection PyProtectedMember
        assert window._search_result_index == 0

    def test_find_next_navigates(self, app_instance):
        window, _ = app_instance
        _run_search_and_wait(window, "hello")
        # noinspection PyProtectedMember
        assert window._search_result_index == 0
        window.cmd_find_next()
        QtWidgets.QApplication.processEvents()
        # noinspection PyProtectedMember
        assert window._search_result_index == 1
        window.cmd_find_next()
        QtWidgets.QApplication.processEvents()
        # noinspection PyProtectedMember
        assert window._search_result_index == 2

    def test_find_prev_navigates(self, app_instance):
        window, _ = app_instance
        _run_search_and_wait(window, "hello")
        # Najpierw przejdź do przodu
        window.cmd_find_next()
        window.cmd_find_next()
        QtWidgets.QApplication.processEvents()
        # noinspection PyProtectedMember
        assert window._search_result_index == 2
        # Teraz wstecz
        window.cmd_find_prev()
        QtWidgets.QApplication.processEvents()
        # noinspection PyProtectedMember
        assert window._search_result_index == 1

    def test_find_next_wrap_around(self, app_instance):
        window, _ = app_instance
        _run_search_and_wait(window, "hello")
        # Skocz do ostatniego
        # noinspection PyProtectedMember
        window._navigate_to_search_result(len(window._search_results) - 1)
        QtWidgets.QApplication.processEvents()
        # noinspection PyProtectedMember
        assert window._search_result_index == len(window._search_results) - 1
        # Następny powinien wrócić na początek
        window.cmd_find_next()
        QtWidgets.QApplication.processEvents()
        # noinspection PyProtectedMember
        assert window._search_result_index == 0

    def test_search_no_results(self, app_instance):
        window, _ = app_instance
        with patch.object(QtWidgets.QMessageBox, "information"):
            window.search_entry.setText("NONEXISTENT_XYZ_12345")
            window.cmd_find_next()
            for _ in range(200):
                QtWidgets.QApplication.processEvents()
                # noinspection PyProtectedMember
                if not window._search_engine or not window._search_engine.is_running():
                    break
                time.sleep(0.05)
            QtWidgets.QApplication.processEvents()
            # noinspection PyProtectedMember
            assert len(window._search_results) == 0

    def test_search_result_click_navigates(self, app_instance):
        window, _ = app_instance
        _run_search_and_wait(window, "hello")
        # Kliknij wynik 10
        # noinspection PyProtectedMember
        if len(window._search_results) > 10:
            # noinspection PyProtectedMember
            model_index = window._search_model.index(10, 0)
            # noinspection PyProtectedMember
            window._on_search_result_clicked(model_index)
            QtWidgets.QApplication.processEvents()
            # noinspection PyProtectedMember
            assert window._search_result_index == 10

    def test_search_pattern_changed_triggers_new_search(self, app_instance):
        window, _ = app_instance
        # Pierwsze wyszukiwanie
        _run_search_and_wait(window, "hello")
        # noinspection PyProtectedMember
        assert len(window._search_results) == 500

        # Zmień wzorzec — powinno uruchomić nowe wyszukiwanie
        window.search_entry.setText("ERROR")
        window.search_case_cb.setChecked(True)
        window.cmd_find_next()
        for _ in range(200):
            QtWidgets.QApplication.processEvents()
            # noinspection PyProtectedMember
            if window._search_results and len(window._search_results) != 500:
                break
            time.sleep(0.05)
        QtWidgets.QApplication.processEvents()
        # ERROR = 1/4 linii = 125
        # noinspection PyProtectedMember
        assert len(window._search_results) == 125

    def test_search_results_label_updated(self, app_instance):
        window, _ = app_instance
        _run_search_and_wait(window, "hello")
        # noinspection PyProtectedMember
        label = window._search_results_label.text()
        assert "500" in label
        assert "1/500" in label  # current = 1 (1-indexed)

    def test_search_results_model_populated(self, app_instance):
        window, _ = app_instance
        _run_search_and_wait(window, "hello")
        # noinspection PyProtectedMember
        assert window._search_model.rowCount() == 500

    def test_search_highlight_in_text(self, app_instance):
        """Po wyszukiwaniu bieżący wynik jest podświetlony w Text widget."""
        window, _ = app_instance
        _run_search_and_wait(window, "hello")
        sels = window.text.extraSelections()
        assert len(sels) >= 1
        # noinspection PyProtectedMember
        search_active_color = QtGui.QColor(window._theme_colors["search_active"]).name()
        has_orange = any(s.format.background().color().name() == search_active_color for s in sels)
        assert has_orange, "Current search result should have orange background (search_active)"

    def test_search_in_filtered(self, app_instance):
        """Wyszukiwanie z zaznaczoną opcją 'szukaj w odfiltrowanych'."""
        window, _ = app_instance
        tab = window.tabs.currentWidget()
        assert isinstance(tab, LogTab)

        # 1. Filtrujemy tylko linie ERROR
        _apply_error_filter_and_wait(window, tab)
        assert tab.filter_active
        assert tab.filter_results is not None
        assert len(tab.filter_results) == 125

        # 2. Wyszukujemy "line 10" z opcją search_in_filter_cb
        window.search_in_filter_cb.setChecked(True)
        _run_search_and_wait(window, "line 10")

        # Weryfikacja, że wszystkie znalezione wyniki są w filtrze i zawierają ERROR oraz "line 10"
        assert tab.search_results_all is not None
        filter_lines = tab.filter_all_lines
        assert filter_lines is not None
        assert tab.indexer is not None
        assert len(tab.search_results_all) > 0
        for line_no in tab.search_results_all:
            assert isinstance(line_no, int)
            assert line_no in filter_lines
            line_text = tab.indexer.read_lines(line_no, 1)[0][1]
            assert "[ERROR]" in line_text
            assert "line 10" in line_text

    def test_navigation_in_filtered_mode(self, app_instance):
        """Weryfikacja skakania (start, end, goto) w trybie przefiltrowanym."""
        window, _ = app_instance
        tab = window.tabs.currentWidget()
        assert isinstance(tab, LogTab)

        # 1. Filtrujemy tylko linie ERROR (125 linii z 500)
        _apply_error_filter_and_wait(window, tab)
        assert tab.filter_active
        filter_lines = tab.filter_all_lines
        assert filter_lines is not None

        # 2. Skok na koniec (cmd_goto_end)
        window.cmd_goto_end()
        QtWidgets.QApplication.processEvents()
        assert tab.text.verticalScrollBar().value() == tab.text.verticalScrollBar().maximum()
        line_map_end = tab.line_map
        assert line_map_end is not None
        assert line_map_end[-1] == filter_lines[-1]

        # 3. Skok na początek (cmd_goto_start)
        window.cmd_goto_start()
        QtWidgets.QApplication.processEvents()
        assert tab.text.verticalScrollBar().value() == 0
        line_map_start = tab.line_map
        assert line_map_start is not None
        assert line_map_start[0] == filter_lines[0]
        assert tab.text.textCursor().position() == 0
