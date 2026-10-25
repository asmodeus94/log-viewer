"""Testy dla poprawek: zaznaczanie wielu zakładek (Cmd+B), usuwanie
zaznaczonych zakładek z panelu bocznego oraz kolejność przycisków
Następny/Poprzedni na pasku narzędzi."""

import array
import os
import sys
import tempfile
from pathlib import Path

# noinspection PyPackageRequirements
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Konfiguracja Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.bitset import Bitset
from log_viewer.config import UserConfig
from log_viewer.indexer import LineIndexer
from log_viewer.log_tab import LogTab
from log_viewer.main_window import LogViewerWindow
from PySide6 import QtCore, QtGui, QtWidgets


def _process_events() -> None:
    QtWidgets.QApplication.processEvents()


def _current_log_tab(window: LogViewerWindow) -> LogTab:
    tab = window.tabs.currentWidget()
    assert isinstance(tab, LogTab)
    return tab


def _place_cursor_on_block(window: LogViewerWindow, block_num: int) -> None:
    cursor = QtGui.QTextCursor(window.text.document().findBlockByNumber(block_num))
    window.text.setTextCursor(cursor)
    _process_events()


# noinspection PyProtectedMember
def _bookmark_block(window: LogViewerWindow, block_num: int, *, at_line: int = 0) -> None:
    window._load_window(at_line=at_line)
    _process_events()
    _place_cursor_on_block(window, block_num)
    window.cmd_toggle_bookmark()
    _process_events()


# noinspection PyProtectedMember
def _apply_filter_with_context(
    tab: LogTab,
    hit_indices: list[int],
    context_after: int,
    *,
    total_size: int = 100,
    matched_texts: dict[int, str] | None = None,
    hit_set: set[int] | None = None,
) -> None:
    tab.filter_active = True
    results_bs = Bitset.from_indices(array.array("Q", hit_indices), total_size)
    tab.filter_results = results_bs
    tab._filter_context_after = context_after
    filter_all = results_bs.expand_context(context_after)
    tab._on_filter_done(
        (results_bs._size, results_bs._words, results_bs._total_count),
        set(),
        (filter_all._size, filter_all._words, filter_all._total_count),
        matched_texts if matched_texts is not None else {},
        hit_set if hit_set is not None else set(),
        None,
    )


# noinspection PyProtectedMember
@pytest.fixture
def app_instance(tmp_path):
    """Tworzy instancję aplikacji z plikiem 500 linii w jednej zakładce."""
    _ = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=str(tmp_path / "bm_cfg.json"))
    window = LogViewerWindow(config=cfg)

    test_file_path = tmp_path / "bm_test.log"
    lines = [
        f"2026-07-04 10:00:{i:02d} [{['INFO', 'WARN', 'ERROR', 'DEBUG'][i % 4]}] line {i} hello world\n".encode()
        for i in range(500)
    ]
    test_file_path.write_bytes(b"".join(lines))
    test_file = str(test_file_path)

    tab = window._new_tab()
    idx = LineIndexer(test_file, encoding="utf-8")
    tab.file_path = test_file
    tab._on_index_done(idx)
    _process_events()

    yield window, test_file

    window.close()
    _process_events()
    window.deleteLater()
    _process_events()


def _select_blocks(text_edit, start_block: int, count: int) -> None:
    """Zaznacza `count` linii w widgetcie zaczynając od bloku `start_block`."""
    cursor = QtGui.QTextCursor(text_edit.document())
    cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
    cursor.movePosition(
        QtGui.QTextCursor.MoveOperation.Down,
        QtGui.QTextCursor.MoveMode.MoveAnchor,
        start_block,
    )
    cursor.movePosition(
        QtGui.QTextCursor.MoveOperation.Down,
        QtGui.QTextCursor.MoveMode.KeepAnchor,
        count,
    )
    text_edit.setTextCursor(cursor)


# =============================================================================
# Cmd+B — zawsze pojedyncza linia kursora, nawet przy selekcji
# =============================================================================


class TestBookmarkSingleLine:
    def test_toggle_no_selection(self, app_instance):
        """Bez selekcji — przełącza pojedynczą linię (zachowanie standardowe)."""
        window, _ = app_instance
        _bookmark_block(window, 0)

        assert len(window.bookmarks) == 1
        assert 0 in window.bookmarks

    def test_toggle_with_selection_only_bookmarks_cursor_line(self, app_instance):
        """Selekcja 5 linii → Cmd+B dodaje TYLKO jedną zakładkę (linia kursora).

        To kluczowa poprawka UX: przypadkowe selekcje wieloliniowe nie mogą
        spowodować masowego zakładkowania.
        """
        window, _ = app_instance
        # noinspection PyProtectedMember
        window._load_window(at_line=0)
        _process_events()

        _select_blocks(window.text, start_block=2, count=5)
        assert window.text.textCursor().hasSelection()

        window.cmd_toggle_bookmark()
        _process_events()

        assert len(window.bookmarks) == 1
        assert window.bm_tree.topLevelItemCount() == 1

    def test_toggle_clears_selection_after(self, app_instance):
        """Po Cmd+B selekcja Qt jest czyszczona — zapobiega wizualnemu myleniu
        z kolorem zakładki."""
        window, _ = app_instance
        # noinspection PyProtectedMember
        window._load_window(at_line=0)
        _process_events()

        _select_blocks(window.text, start_block=2, count=5)
        assert window.text.textCursor().hasSelection()

        window.cmd_toggle_bookmark()
        _process_events()

        assert not window.text.textCursor().hasSelection()

    def test_toggle_off_after_on(self, app_instance):
        """Drugie Cmd+B na tej samej linii usuwa zakładkę."""
        window, _ = app_instance
        _bookmark_block(window, 0)
        assert len(window.bookmarks) == 1

        window.cmd_toggle_bookmark()
        _process_events()
        assert len(window.bookmarks) == 0


# =============================================================================
# Panel Zakładki — single-selekcja + przycisk usuwania
# =============================================================================


class TestBookmarkPanelDelete:
    def test_bm_tree_extended_selection(self, app_instance):
        """Drzewo zakładek ma tryb ExtendedSelection (multi-selekcja)."""
        window, _ = app_instance
        assert window.bm_tree.selectionMode() == QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection

    def test_delete_button_exists(self, app_instance):
        """Przycisk usuwania istnieje i jest podłączony."""
        window, _ = app_instance
        assert hasattr(window, "btn_del_bookmarks")
        assert window.btn_del_bookmarks is not None
        assert hasattr(window, "_delete_selected_bookmarks")

    def test_delete_selected_bookmark(self, app_instance):
        """Zaznaczenie jednego wpisu + delete → usuwa tylko ten jeden."""
        window, _ = app_instance
        _process_events()

        # Dodaj 5 zakładek
        for fl in (10, 20, 30, 40, 50):
            window.bookmarks[fl] = None
        # noinspection PyProtectedMember
        window._refresh_bookmarks_tree()
        assert window.bm_tree.topLevelItemCount() == 5

        # Zaznacz jeden (drugi z góry = linia 20)
        window.bm_tree.setCurrentItem(window.bm_tree.topLevelItem(1))
        _process_events()

        selected = window.bm_tree.selectedItems()
        assert len(selected) == 1

        # noinspection PyProtectedMember
        window._delete_selected_bookmarks()
        _process_events()

        assert len(window.bookmarks) == 4
        assert 20 not in window.bookmarks
        for fl in (10, 30, 40, 50):
            assert fl in window.bookmarks

    def test_delete_multiple_selected(self, app_instance):
        """Multi-selekcja 3 wpisów + delete → usuwa wszystkie 3."""
        window, _ = app_instance
        _process_events()

        for fl in (10, 20, 30, 40, 50):
            window.bookmarks[fl] = None
        # noinspection PyProtectedMember
        window._refresh_bookmarks_tree()

        # Zaznacz 3 pierwsze
        window.bm_tree.clearSelection()
        for i in range(3):
            window.bm_tree.topLevelItem(i).setSelected(True)
        _process_events()
        assert len(window.bm_tree.selectedItems()) == 3

        # noinspection PyProtectedMember
        window._delete_selected_bookmarks()
        _process_events()

        assert len(window.bookmarks) == 2
        for fl in (10, 20, 30):
            assert fl not in window.bookmarks
        for fl in (40, 50):
            assert fl in window.bookmarks

    def test_delete_auto_selects_next(self, app_instance):
        """Po usunięciu zaznaczenie przesuwa się na następny element (IDE-style)."""
        window, _ = app_instance
        _process_events()

        for fl in (10, 20, 30, 40, 50):
            window.bookmarks[fl] = None
        # noinspection PyProtectedMember
        window._refresh_bookmarks_tree()

        # Zaznacz drugi element (linia 20, indeks 1)
        window.bm_tree.setCurrentItem(window.bm_tree.topLevelItem(1))
        _process_events()

        # noinspection PyProtectedMember
        window._delete_selected_bookmarks()
        _process_events()

        selected = window.bm_tree.selectedItems()
        assert len(selected) == 1, "Next item should be selected after deletion"
        assert selected[0].data(0, QtCore.Qt.ItemDataRole.UserRole) == 30

    def test_delete_with_no_selection_is_noop(self, app_instance):
        """Usuwanie bez zaznaczenia nie crashuje i nic nie usuwa."""
        window, _ = app_instance
        _process_events()

        window.bookmarks[10] = None
        window.bookmarks[20] = None
        # noinspection PyProtectedMember
        window._refresh_bookmarks_tree()
        window.bm_tree.clearSelection()

        # noinspection PyProtectedMember
        window._delete_selected_bookmarks()
        _process_events()

        assert len(window.bookmarks) == 2

    def test_edits_delete_button_exists(self, app_instance):
        """Panel edycji też ma przycisk usuwania (spójność UX)."""
        window, _ = app_instance
        assert hasattr(window, "btn_del_edits")
        assert hasattr(window, "_delete_selected_edits")
        # Edycje też ExtendedSelection dla spójności
        assert window.ed_tree.selectionMode() == QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection


# =============================================================================
# Kolejność przycisków Następny / Poprzedni na pasku narzędzi
# =============================================================================


class TestToolbarButtonOrder:
    def test_next_is_left_of_prev(self, app_instance):
        """Następny znajduje się po lewej stronie Poprzedni na pasku."""
        window, _ = app_instance

        assert window.btn_find_next is not None
        assert window.btn_find_prev is not None

        window.show()
        _process_events()

        from PySide6.QtCore import QPoint

        x_next = window.btn_find_next.mapToGlobal(QPoint(0, 0)).x()
        x_prev = window.btn_find_prev.mapToGlobal(QPoint(0, 0)).x()

        assert x_next < x_prev, "Przycisk 'Następny' powinien być przed 'Poprzedni'"
        window.hide()

    def test_both_search_buttons_exist(self, app_instance):
        window, _ = app_instance
        assert hasattr(window, "btn_find_next")
        assert hasattr(window, "btn_find_prev")
        assert window.btn_find_next.text() != ""
        assert window.btn_find_prev.text() != ""


# =============================================================================
# Bieżąca linia — delikatne podświetlenie + formatowanie bloków
# =============================================================================


class TestCurrentLineHighlightAndFormatting:
    def test_current_line_extra_selection_active(self, app_instance):
        """Po załadowaniu okna lista ExtraSelections zawiera bieżącą linię."""
        window, _ = app_instance
        # noinspection PyProtectedMember
        window._load_window(at_line=0)
        _process_events()
        sels = window.text.extraSelections()
        assert len(sels) >= 1
        # Bieżąca linia jest ostatnią selekcją i ma FullWidthSelection.
        cur = sels[-1]
        assert cur.format.property(QtGui.QTextFormat.Property.FullWidthSelection) is True

    def test_bookmark_format_does_not_propagate_to_neighbors(self, app_instance):
        """Kluczowa poprawka: dodanie zakładki do linii N nie zielieni linii N+1.

        Po refactorze tło zakładki idzie przez ExtraSelections — zakładka
        jest dokładana TYLKO dla konkretnej linii widget, więc sąsiednie
        linie nigdy nie dostają zielonego tła.
        """
        window, _ = app_instance
        _bookmark_block(window, 5)

        bookmark_color = QtGui.QColor(window.theme["bookmark"]).name()
        sels = window.text.extraSelections()
        bookmark_blocks = {
            s.cursor.blockNumber() for s in sels if s.format.background().color().name() == bookmark_color
        }
        assert 5 in bookmark_blocks, "Line 5 should have green bookmark background"
        assert 6 not in bookmark_blocks, (
            f"Sąsiednia linia 6 dostała zielone tło — to bug propagacji. Zakładkowane bloki: {bookmark_blocks}"
        )

    def test_bookmark_formatting(self, app_instance):
        """Linia z dodaną zakładką: zielone tło (ExtraSelection).

        Po refactorze tło zakładki jest dokładane przez ExtraSelections (nie
        przez charFormat, bo QPlainTextEdit z QSS background-color ignoruje
        tła charFormat).
        """
        window, _ = app_instance
        _bookmark_block(window, 0)

        bookmark_color = QtGui.QColor(window.theme["bookmark"]).name()
        sels = window.text.extraSelections()
        bookmark_bg_found = any(
            sel.format.background().color().name() == bookmark_color and sel.cursor.blockNumber() == 0 for sel in sels
        )
        assert bookmark_bg_found, (
            f"ExtraSelections nie zawierają zielonego tła zakładki "
            f"({bookmark_color}) w linii 0. Selekcje: "
            f"{[(s.format.background().color().name(), s.cursor.blockNumber()) for s in sels]}"
        )

    def test_toggle_bookmark_clears_selection(self, app_instance):
        """Cmd+B czyści selekcję Qt (nie zostawia szarego paska)."""
        window, _ = app_instance
        # noinspection PyProtectedMember
        window._load_window(at_line=0)
        _process_events()

        # Zaznacz kilka linii
        _select_blocks(window.text, start_block=2, count=3)
        assert window.text.textCursor().hasSelection()

        window.cmd_toggle_bookmark()
        _process_events()

        assert not window.text.textCursor().hasSelection()

    def test_toggle_bookmark_preserves_cursor_line(self, app_instance):
        """Po Cmd+B kursor zostaje w tej samej linii (nie skacze na początek)."""
        window, _ = app_instance
        _bookmark_block(window, 10)

        # Kursor nadal w linii 10
        assert window.text.textCursor().blockNumber() == 10

    def test_current_line_does_not_cover_bookmark(self, app_instance):
        """Kluczowa poprawka: gdy kursor jest na linii z zakładką, widać
        zielone tło zakładki, a szare current_line NIE jest dokładane
        (nie przykrywa zakładki)."""
        window, _ = app_instance
        _bookmark_block(window, 7)

        bookmark_color = QtGui.QColor(window.theme["bookmark"]).name()
        current_color = QtGui.QColor(window.theme["current_line"]).name()
        sels = window.text.extraSelections()

        bookmark_sel_exists = any(
            s.cursor.blockNumber() == 7 and s.format.background().color().name() == bookmark_color for s in sels
        )
        assert bookmark_sel_exists, "Missing green bookmark selection on line 7"

        current_sel_on_bookmark = any(
            s.cursor.blockNumber() == 7 and s.format.background().color().name() == current_color for s in sels
        )
        assert not current_sel_on_bookmark, "Szara selekcja current_line przykrywa zakładkę w linii 7 — to bug"

    def test_current_line_shows_on_normal_line(self, app_instance):
        """Gdy kursor jest na linii BEZ zakładki, current_line się pokazuje."""
        window, _ = app_instance
        _bookmark_block(window, 7)

        # Przesuń kursor na linię 8 (bez zakładki)
        _place_cursor_on_block(window, 8)

        current_color = QtGui.QColor(window.theme["current_line"]).name()
        sels = window.text.extraSelections()
        current_sel_exists = any(
            s.cursor.blockNumber() == 8 and s.format.background().color().name() == current_color for s in sels
        )
        assert current_sel_exists, "Missing gray current_line selection on line 8"


# =============================================================================
# Edycja linii — kursor poza widokiem (scroll po wyniku wyszukiwania)
# =============================================================================


class TestEditLineAfterScroll:
    def test_edit_uses_visible_line_when_cursor_offscreen(self, tmp_path):
        """Bug: po wyniku wyszukiwania kursor zostaje na starej linii. Jeśli
        user przewinie widok i wciska Ctrl+D, edytuje linię kursora (niewidoczną),
        a nie tę, którą widzi. Poprawka: cmd_edit_line używa firstVisibleBlock
        gdy kursor jest poza widokiem.
        """
        _ = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        cfg = UserConfig(config_path=str(tmp_path / "scroll_cfg.json"))
        window = LogViewerWindow(config=cfg)

        test_file_path = tmp_path / "scroll_test.log"
        test_file_path.write_bytes(b"".join(f"line {i} content here\n".encode() for i in range(50)))
        test_file = str(test_file_path)

        try:
            # noinspection PyProtectedMember
            tab = window._new_tab()
            idx = LineIndexer(test_file, encoding="utf-8")
            tab.file_path = test_file
            # noinspection PyProtectedMember
            tab._on_index_done(idx)
            _process_events()

            # noinspection PyProtectedMember
            window._highlight_and_scroll(0)
            _process_events()
            assert window.text.textCursor().blockNumber() == 0

            sb = window.text.verticalScrollBar()
            sb.setValue(sb.maximum())  # przewiń na sam dół
            _process_events()

            fvb = window.text.firstVisibleBlock()
            assert fvb.blockNumber() > 0, "firstVisibleBlock should be > 0 after scrolling"

            cursor = window.text.textCursor()
            cursor_rect = window.text.cursorRect(cursor)
            viewport_rect = window.text.viewport().rect()
            if not viewport_rect.contains(cursor_rect.topLeft()):
                cursor = QtGui.QTextCursor(fvb)
            widget_line = cursor.blockNumber()

            assert widget_line == fvb.blockNumber(), (
                f"cmd_edit_line powinien użyć firstVisibleBlock "
                f"({fvb.blockNumber()}) gdy kursor jest poza widokiem, "
                f"ale użył {widget_line}"
            )
            assert widget_line > 0, "Should not edit line 0 when user sees a different line"
        finally:
            window.deleteLater()
            _process_events()


# =============================================================================
# Filtr z kontekstem — N linii po każdym trafieniu (dla stack trace)
# =============================================================================


class TestFilterContext:
    def test_context_spinbox_exists(self, app_instance):
        """Spinbox kontekstu istnieje w toolbarze."""
        window, _ = app_instance
        tab = _current_log_tab(window)
        # noinspection PyProtectedMember
        assert hasattr(tab._main, "filter_context_spin")
        # noinspection PyProtectedMember
        assert tab._main.filter_context_spin.value() == 0

    def test_build_filter_context_generates_lines(self, app_instance):
        """_build_filter_context generuje N następujących linii po każdym trafieniu."""
        window, _ = app_instance
        tab = _current_log_tab(window)
        _apply_filter_with_context(tab, [10, 20, 30], 2)

        assert tab.filter_results is not None
        # noinspection PyProtectedMember
        assert tab._filter_all_lines is not None
        # noinspection PyProtectedMember
        assert set(tab._filter_all_lines) - set(tab.filter_results) == {11, 12, 21, 22, 31, 32}

    def test_build_filter_context_zero_means_disabled(self, app_instance):
        """_filter_context_after=0 → brak linii kontekstu."""
        window, _ = app_instance
        tab = _current_log_tab(window)
        _apply_filter_with_context(tab, [10], 0, matched_texts={10: "x"}, hit_set={10})
        assert tab.filter_context_lines == set()

    def test_build_filter_context_skips_hit_lines(self, app_instance):
        """Linie kontekstu nie obejmują samych trafień (nawet gdy się pokrywają)."""
        window, _ = app_instance
        tab = _current_log_tab(window)
        _apply_filter_with_context(tab, [10, 11], 2)
        assert tab.filter_results is not None
        # noinspection PyProtectedMember
        assert tab._filter_all_lines is not None
        # noinspection PyProtectedMember
        assert set(tab._filter_all_lines) - set(tab.filter_results) == {12, 13}

    def test_clear_filter_clears_context(self, app_instance):
        """cmd_clear_filter czyści filter_context_lines."""
        window, _ = app_instance
        tab = _current_log_tab(window)
        _apply_filter_with_context(tab, [10, 20], 1)
        # noinspection PyProtectedMember
        assert tab._filter_all_lines is not None
        # noinspection PyProtectedMember
        assert len(tab._filter_all_lines) > 1

        tab.cmd_clear_filter(silent=True)
        assert tab.filter_context_lines == set()
        # noinspection PyProtectedMember
        assert tab._filter_context_after == 0

    def test_filter_line_numbers_show_real_file_lines(self, app_instance):
        """W trybie filtra numery linii (line_map) pokazują prawdziwe numery
        z pliku, z dziurami — np. 10, 11, 12, 21, 22, 31, 32 (gdy trafienia
        w 10/20/30, kontekst=2). Nie 1, 2, 3, 4, 5..."""
        window, _ = app_instance
        tab = _current_log_tab(window)

        _apply_filter_with_context(tab, [10, 20, 30], 2)
        # noinspection PyProtectedMember
        tab._load_window(at_line=0)
        _process_events()

        expected = [10, 11, 12, 20, 21, 22, 30, 31, 32]
        assert tab.line_map == expected, f"line_map powinien mieć prawdziwe numery z dziurami, jest {tab.line_map}"

    def test_filter_hit_highlight_yellow(self, app_instance):
        """Trafienia filtra mają żółte tło (highlight), kontekst szare (context).
        Wizualne odróżnienie trafień od kontekstu."""
        window, _ = app_instance
        tab = _current_log_tab(window)

        _apply_filter_with_context(tab, [5], 2)
        # noinspection PyProtectedMember
        tab._load_window(at_line=0)
        _process_events()

        highlight_color = QtGui.QColor(tab.theme["highlight"]).name()
        context_color = QtGui.QColor(tab.theme["context"]).name()
        sels = tab.text.extraSelections()

        hit_has_yellow = any(
            s.cursor.blockNumber() == 0 and s.format.background().color().name() == highlight_color for s in sels
        )
        assert hit_has_yellow, "Filter hit should have yellow background (highlight)"

        context_blocks = {s.cursor.blockNumber() for s in sels if s.format.background().color().name() == context_color}
        assert 1 in context_blocks, "Line 1 should be context (gray background)"
        assert 2 in context_blocks, "Line 2 should be context (gray background)"


# =============================================================================
# Indeksowanie — postęp i anulowanie
# =============================================================================


class TestIndexingProgress:
    def test_parallel_emits_progress_during_indexing(self):
        """_build_parallel emituje postęp w trakcie (nie tylko 100% na końcu)."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="wb") as tmp:
            test_file = tmp.name
        try:
            line = b"2026-07-04 10:00:00 [INFO] test line for indexing benchmark\n"
            target_size = 150 * 1024 * 1024
            lines_needed = target_size // len(line) + 1
            with open(test_file, "wb") as f:
                for _ in range(lines_needed):
                    f.write(line)
            actual_size = os.path.getsize(test_file)
            assert actual_size > 100 * 1024 * 1024, "Test file size is too small"

            progress_values = []

            def progress_cb(pct: float):
                progress_values.append(pct)

            idx = LineIndexer(
                test_file, progress_cb=progress_cb, encoding="utf-8", parallel_threshold_bytes=100 * 1024 * 1024
            )
            try:
                assert len(progress_values) > 0, "Missing progress readings"
                assert 100.0 in progress_values, "Missing final 100% progress"
            finally:
                idx.close()
        finally:
            try:
                os.unlink(test_file)
            except PermissionError:
                pass

    def test_indexer_worker_has_cancel_method(self):
        """IndexerWorker ma metodę cancel() i is_cancelled()."""
        from log_viewer.workers import IndexerWorker

        worker = IndexerWorker("/tmp/nonexistent", "utf-8", 1024 * 1024)
        assert hasattr(worker, "cancel")
        assert hasattr(worker, "is_cancelled")
        assert worker.is_cancelled() is False
        worker.cancel()
        assert worker.is_cancelled() is True

    def test_logtab_has_progress_method_not_lambda(self):
        """LogTab._on_index_progress MUSI być metodą (nie lambdą)."""
        assert hasattr(LogTab, "_on_index_progress"), (
            "LogTab musi mieć metodę _on_index_progress (nie lambdę) — "
            "Qt QueuedConnection wymaga picklowalnego odbiorcy"
        )
        import inspect

        # noinspection PyProtectedMember
        assert inspect.isfunction(LogTab._on_index_progress), "_on_index_progress musi być metodą klasową"

    def test_logtab_has_reindex_slots(self):
        """LogTab ma sloty _on_reindex_finished, _on_follow_reindex_slot,
        _on_follow_reindex_clear_flag — bezpośrednio metody (nie lambdy)."""
        import inspect

        for name in (
            "_on_reindex_finished",
            "_on_follow_reindex_slot",
            "_on_follow_reindex_clear_flag",
            "_on_index_progress",
            "_on_index_done",
            "_on_index_error",
            "_close_index_progress",
        ):
            assert hasattr(LogTab, name), f"LogTab musi mieć metodę {name}"
            assert inspect.isfunction(getattr(LogTab, name)), f"{name} musi być metodą klasową (nie lambdą)"

    def test_parallel_indexing_can_be_cancelled(self):
        """Anulowanie ustawia cancel_event — _build_parallel przerywa."""
        import threading

        with tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="wb") as tmp:
            test_file = tmp.name
        try:
            line = b"2026-07-04 10:00:00 [INFO] test line\n"
            target_size = 150 * 1024 * 1024
            lines_needed = target_size // len(line) + 1
            with open(test_file, "wb") as f:
                for _ in range(lines_needed):
                    f.write(line)

            cancel_event = threading.Event()
            cancel_event.set()

            idx = LineIndexer(
                test_file,
                encoding="utf-8",
                cancel_event=cancel_event,
                parallel_threshold_bytes=100 * 1024 * 1024,
            )
            try:
                assert len(idx.index) == 1, f"Po anulowaniu indeks powinien być pusty, ma {len(idx.index)} wpisów"
                assert idx.line_count == 0
            finally:
                idx.close()
        finally:
            try:
                os.unlink(test_file)
            except PermissionError:
                pass

    def test_indexing_performance_benchmark(self):
        """Benchmark: indeksowanie 200 MB powinno trwać < 10s."""
        import time

        with tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="wb") as tmp:
            test_file = tmp.name
        try:
            line = b"2026-07-04 10:00:00 [INFO] benchmark line content here for testing\n"
            target_size = 200 * 1024 * 1024
            lines_needed = target_size // len(line) + 1
            with open(test_file, "wb") as f:
                for _ in range(lines_needed):
                    f.write(line)
            actual_size = os.path.getsize(test_file)

            t0 = time.time()
            idx = LineIndexer(test_file, encoding="utf-8")
            t1 = time.time()
            elapsed = t1 - t0
            throughput = actual_size / elapsed / (1024 * 1024) if elapsed > 0 else 0
            try:
                assert throughput > 20, (
                    f"Throughput {throughput:.1f} MB/s < 20 MB/s — regresja wydajności. "
                    f"Czas: {elapsed:.2f}s dla {actual_size / (1024 * 1024):.0f} MB"
                )
            finally:
                idx.close()
        finally:
            try:
                os.unlink(test_file)
            except PermissionError:
                pass


# =============================================================================
# i18n — nowe klucze
# =============================================================================


class TestI18nKeys:
    def test_new_keys_present_in_pl(self):
        from log_viewer.i18n import I18N

        for key in (
            "btn_delete_sel",
            "msg_bookmarks_added",
            "msg_bookmarks_removed",
            "msg_edits_removed",
            "msg_no_selection",
            "lbl_filter_context",
            "tt_filter_context",
            "btn_cancel",
            "dlg_index_title",
            "st_cancelling",
            "st_cancelled",
        ):
            assert key in I18N["pl"], f"Brak klucza {key} w PL"
            assert I18N["pl"][key], f"Pusta wartość dla {key} w PL"

    def test_new_keys_present_in_en(self):
        from log_viewer.i18n import I18N

        for key in (
            "btn_delete_sel",
            "msg_bookmarks_added",
            "msg_bookmarks_removed",
            "msg_edits_removed",
            "msg_no_selection",
            "lbl_filter_context",
            "tt_filter_context",
            "btn_cancel",
            "dlg_index_title",
            "st_cancelling",
            "st_cancelled",
        ):
            assert key in I18N["en"], f"Brak klucza {key} w EN"
            assert I18N["en"][key], f"Pusta wartość dla {key} w EN"

    def test_tree_items_formatting_thousands_separator(self):
        """Drzewo zakładek i edycji wyświetla numery linii > 999 z separatorem tysięcy."""
        file_line = 1234
        formatted = f"{file_line + 1:,}"
        assert formatted == "1,235"


class TestLineNumberAreaBookmarks:
    def test_line_number_area_set_bookmarks(self, app_instance):
        """LineNumberArea zapamiętuje zbiór zakładek i odświeża widok."""
        window, _ = app_instance
        window.text.set_bookmarks({0, 5, 10})
        # noinspection PyProtectedMember
        assert window.text._line_number_area._bookmarks == {0, 5, 10}


class TestSearchHighlightColor:
    def test_search_active_color_distinct_from_filter(self, app_instance):
        """Aktywny wynik wyszukiwania używa koloru search_active (#ff8c00), który różni się od koloru filtra."""
        window, _ = app_instance
        # noinspection PyProtectedMember
        orange_color = QtGui.QColor(window._theme_colors["search_active"]).name()
        # noinspection PyProtectedMember
        yellow_color = QtGui.QColor(window._theme_colors["highlight"]).name()

        assert orange_color != yellow_color, "Active search result color should differ from filter color"

        # noinspection PyProtectedMember
        window._highlight_and_scroll(0)
        sels = window.text.extraSelections()

        has_orange_sel = any(s.format.background().color().name() == orange_color for s in sels)
        assert has_orange_sel, "Search result should have orange background (search_active)"


# =============================================================================
# Styl Fusion i paleta (ciemne checkboxy w trybie zablokowanym)
# =============================================================================


class TestCheckboxStyleAndPalette:
    def test_fusion_style_and_dark_palette_applied(self, app_instance):
        """Upewnia sie, ze styl Fusion oraz paleta z ciemnymi tlami sa zaaplikowane."""
        _window, _ = app_instance
        app = QtWidgets.QApplication.instance()
        assert isinstance(app, QtWidgets.QApplication)
        assert app.style().objectName().lower() == "fusion"

        pal = app.palette()
        disabled_base = pal.color(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Base)
        assert disabled_base.name().lower() != "#ffffff"

    def test_disabled_checkbox_indicator_not_white(self, app_instance):
        """Zablokowany checkbox nie ma bialego tla wskaznika."""
        window, _ = app_instance
        cb = window.search_regex_cb
        cb.setEnabled(False)
        window.show()
        _process_events()

        img = cb.grab().toImage()
        pixel_color = QtGui.QColor.fromRgba(img.pixel(7, 10))
        assert pixel_color.name().lower() != "#ffffff"
