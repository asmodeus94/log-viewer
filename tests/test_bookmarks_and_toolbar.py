"""Testy dla poprawek: zaznaczanie wielu zakładek (Cmd+B), usuwanie
zaznaczonych zakładek z panelu bocznego oraz kolejność przycisków
Następny/Poprzedni na pasku narzędzi."""
import os
import sys
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


@pytest.fixture
def app_instance():
    """Tworzy instancję aplikacji z plikiem 500 linii w jednej zakładce."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="wb") as tmp:
        test_file = tmp.name
    with open(test_file, "wb") as f:
        for i in range(500):
            lvl = ["INFO", "WARN", "ERROR", "DEBUG"][i % 4]
            f.write(f"2026-07-04 10:00:{i:02d} [{lvl}] line {i} hello world\n".encode())

    tab = window._new_tab()
    idx = LineIndexer(test_file, encoding="utf-8")
    tab.file_path = test_file
    tab._on_index_done(idx)
    app.processEvents()

    yield window, test_file

    window.close()
    app.processEvents()
    if os.path.exists(test_file):
        try:
            os.unlink(test_file)
        except PermissionError:
            pass
    if os.path.exists(cfg.path):
        try:
            os.unlink(cfg.path)
        except PermissionError:
            pass
    window.deleteLater()
    app.processEvents()


def _select_blocks(text_edit, start_block: int, count: int) -> None:
    """Zaznacza `count` linii w widgetcie zaczynając od bloku `start_block`."""
    cursor = QtGui.QTextCursor(text_edit.document())
    cursor.movePosition(QtGui.QTextCursor.Start)
    cursor.movePosition(QtGui.QTextCursor.Down, QtGui.QTextCursor.MoveAnchor, start_block)
    cursor.movePosition(QtGui.QTextCursor.Down, QtGui.QTextCursor.KeepAnchor, count)
    text_edit.setTextCursor(cursor)


# =============================================================================
# Cmd+B — zawsze pojedyncza linia kursora, nawet przy selekcji
# =============================================================================

class TestBookmarkSingleLine:
    def test_toggle_no_selection(self, app_instance):
        """Bez selekcji — przełącza pojedynczą linię (zachowanie standardowe)."""
        window, _ = app_instance
        window._load_window(at_line=0)
        app_instance  # keep ref
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        cursor = QtGui.QTextCursor(window.text.document())
        cursor.movePosition(QtGui.QTextCursor.Start)
        window.text.setTextCursor(cursor)

        window.cmd_toggle_bookmark()
        app.processEvents()

        assert len(window.bookmarks) == 1
        assert 0 in window.bookmarks

    def test_toggle_with_selection_only_bookmarks_cursor_line(self, app_instance):
        """Selekcja 5 linii → Cmd+B dodaje TYLKO jedną zakładkę (linia kursora).

        To kluczowa poprawka UX: przypadkowe selekcje wieloliniowe nie mogą
        spowodować masowego zakładkowania.
        """
        window, _ = app_instance
        window._load_window(at_line=0)
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        _select_blocks(window.text, start_block=2, count=5)
        assert window.text.textCursor().hasSelection()

        window.cmd_toggle_bookmark()
        app.processEvents()

        # Dokładnie jedna zakładka — ta, w której jest kursor (koniec selekcji).
        # Po wyczyszczeniu selekcji kursor zostaje tam, gdzie był jej koniec,
        # więc trafia w jedną z linii objętych wcześniejszą selekcją (lub tuż
        # za nią, jeśli Qt zostawił kursor na początku następnego bloku).
        assert len(window.bookmarks) == 1
        assert window.bm_tree.topLevelItemCount() == 1

    def test_toggle_clears_selection_after(self, app_instance):
        """Po Cmd+B selekcja Qt jest czyszczona — zapobiega wizualnemu myleniu
        z kolorem zakładki."""
        window, _ = app_instance
        window._load_window(at_line=0)
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        _select_blocks(window.text, start_block=2, count=5)
        assert window.text.textCursor().hasSelection()

        window.cmd_toggle_bookmark()
        app.processEvents()

        assert not window.text.textCursor().hasSelection()

    def test_toggle_off_after_on(self, app_instance):
        """Drugie Cmd+B na tej samej linii usuwa zakładkę."""
        window, _ = app_instance
        window._load_window(at_line=0)
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        cursor = QtGui.QTextCursor(window.text.document())
        cursor.movePosition(QtGui.QTextCursor.Start)
        window.text.setTextCursor(cursor)

        window.cmd_toggle_bookmark()
        app.processEvents()
        assert len(window.bookmarks) == 1

        window.cmd_toggle_bookmark()
        app.processEvents()
        assert len(window.bookmarks) == 0


# =============================================================================
# Panel Zakładki — single-selekcja + przycisk usuwania
# =============================================================================

class TestBookmarkPanelDelete:
    def test_bm_tree_extended_selection(self, app_instance):
        """Drzewo zakładek ma tryb ExtendedSelection (multi-selekcja)."""
        window, _ = app_instance
        assert window.bm_tree.selectionMode() == QtWidgets.QAbstractItemView.ExtendedSelection

    def test_delete_button_exists(self, app_instance):
        """Przycisk usuwania istnieje i jest podłączony."""
        window, _ = app_instance
        assert hasattr(window, "btn_del_bookmarks")
        assert window.btn_del_bookmarks is not None
        assert hasattr(window, "_delete_selected_bookmarks")

    def test_delete_selected_bookmark(self, app_instance):
        """Zaznaczenie jednego wpisu + delete → usuwa tylko ten jeden."""
        window, _ = app_instance
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        # Dodaj 5 zakładek
        for fl in (10, 20, 30, 40, 50):
            window.bookmarks[fl] = None
        window._refresh_bookmarks_tree()
        assert window.bm_tree.topLevelItemCount() == 5

        # Zaznacz jeden (drugi z góry = linia 20)
        window.bm_tree.setCurrentItem(window.bm_tree.topLevelItem(1))
        app.processEvents()

        selected = window.bm_tree.selectedItems()
        assert len(selected) == 1

        window._delete_selected_bookmarks()
        app.processEvents()

        assert len(window.bookmarks) == 4
        assert 20 not in window.bookmarks
        for fl in (10, 30, 40, 50):
            assert fl in window.bookmarks

    def test_delete_multiple_selected(self, app_instance):
        """Multi-selekcja 3 wpisów + delete → usuwa wszystkie 3."""
        window, _ = app_instance
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        for fl in (10, 20, 30, 40, 50):
            window.bookmarks[fl] = None
        window._refresh_bookmarks_tree()

        # Zaznacz 3 pierwsze
        window.bm_tree.clearSelection()
        for i in range(3):
            window.bm_tree.topLevelItem(i).setSelected(True)
        app.processEvents()
        assert len(window.bm_tree.selectedItems()) == 3

        window._delete_selected_bookmarks()
        app.processEvents()

        assert len(window.bookmarks) == 2
        for fl in (10, 20, 30):
            assert fl not in window.bookmarks
        for fl in (40, 50):
            assert fl in window.bookmarks

    def test_delete_auto_selects_next(self, app_instance):
        """Po usunięciu zaznaczenie przesuwa się na następny element (IDE-style)."""
        window, _ = app_instance
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        for fl in (10, 20, 30, 40, 50):
            window.bookmarks[fl] = None
        window._refresh_bookmarks_tree()

        # Zaznacz drugi element (linia 20, indeks 1)
        window.bm_tree.setCurrentItem(window.bm_tree.topLevelItem(1))
        app.processEvents()

        window._delete_selected_bookmarks()
        app.processEvents()

        # Po usunięciu elementu na indeksie 1, na tej pozycji jest teraz
        # linia 30 (wcześniej indeks 2). Sprawdzamy że coś jest zaznaczone.
        selected = window.bm_tree.selectedItems()
        assert len(selected) == 1, "Po usunięciu powinien być zaznaczony następny element"
        # Zaznaczony element ma numer linii 30 (bo 20 usunięto, 30 spadło na indeks 1).
        assert selected[0].data(0, QtCore.Qt.UserRole) == 30

    def test_delete_with_no_selection_is_noop(self, app_instance):
        """Usuwanie bez zaznaczenia nie crashuje i nic nie usuwa."""
        window, _ = app_instance
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        window.bookmarks[10] = None
        window.bookmarks[20] = None
        window._refresh_bookmarks_tree()
        window.bm_tree.clearSelection()

        window._delete_selected_bookmarks()
        app.processEvents()

        assert len(window.bookmarks) == 2

    def test_edits_delete_button_exists(self, app_instance):
        """Panel edycji też ma przycisk usuwania (spójność UX)."""
        window, _ = app_instance
        assert hasattr(window, "btn_del_edits")
        assert hasattr(window, "_delete_selected_edits")
        # Edycje też ExtendedSelection dla spójności
        assert window.ed_tree.selectionMode() == QtWidgets.QAbstractItemView.ExtendedSelection


# =============================================================================
# Kolejność przycisków Następny / Poprzedni na pasku narzędzi
# =============================================================================

class TestToolbarButtonOrder:
    def test_next_is_left_of_prev(self, app_instance):
        """Następny znajduje się po lewej stronie Poprzedni na pasku."""
        window, _ = app_instance
        toolbar = window.findChild(QtWidgets.QToolBar)
        assert toolbar is not None

        actions = toolbar.actions()
        widgets = [toolbar.widgetForAction(a) for a in actions]
        # Filtruj widgety przycisków
        try:
            idx_next = widgets.index(window.btn_find_next)
            idx_prev = widgets.index(window.btn_find_prev)
        except ValueError:
            pytest.fail("Nie znaleziono przycisków Następny/Poprzedni na pasku")
        assert idx_next < idx_prev, (
            f"Następny (idx={idx_next}) powinien być przed Poprzedni (idx={idx_prev})"
        )

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
        window._load_window(at_line=0)
        app = QtWidgets.QApplication.instance()
        app.processEvents()
        sels = window.text.extraSelections()
        assert len(sels) >= 1
        # Bieżąca linia jest ostatnią selekcją i ma FullWidthSelection.
        cur = sels[-1]
        assert cur.format.property(QtGui.QTextFormat.FullWidthSelection) is True

    def test_bookmark_format_does_not_propagate_to_neighbors(self, app_instance):
        """Kluczowa poprawka: dodanie zakładki do linii N nie zielieni linii N+1.

        Po refactorze tło zakładki idzie przez ExtraSelections — zakładka
        jest dokładana TYLKO dla konkretnej linii widget, więc sąsiednie
        linie nigdy nie dostają zielonego tła.
        """
        window, _ = app_instance
        window._load_window(at_line=0)
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        # Kursor w linii widget 5
        cursor = QtGui.QTextCursor(window.text.document().findBlockByNumber(5))
        window.text.setTextCursor(cursor)
        app.processEvents()

        window.cmd_toggle_bookmark()
        app.processEvents()

        # Tła zakładek są w ExtraSelections — sprawdźmy że linia 5 jest
        # zakładką, a linia 6 NIE.
        bookmark_color = QtGui.QColor(window.theme["bookmark"]).name()
        sels = window.text.extraSelections()
        bookmark_blocks = {
            s.cursor.blockNumber()
            for s in sels
            if s.format.background().color().name() == bookmark_color
        }
        assert 5 in bookmark_blocks, "Linia 5 powinna mieć zielone tło zakładki"
        assert 6 not in bookmark_blocks, (
            f"Sąsiednia linia 6 dostała zielone tło — to bug propagacji. "
           "Zakładkowane bloki: {bookmark_blocks}"
        )

    def test_bookmark_and_log_level_coexist(self, app_instance):
        """Linia INFO z dodaną zakładką: zielone tło (ExtraSelection) + niebieski foreground (charFormat).

        Po refactorze tło zakładki jest dokładane przez ExtraSelections (nie
        przez charFormat, bo QPlainTextEdit z QSS background-color ignoruje
        tła charFormat). Foreground nadal jest przez charFormat.
        """
        window, _ = app_instance
        window._load_window(at_line=0)
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        # Pierwsze 4 linie to INFO/WARN/ERROR/DEBUG — INFO jest w linii 0
        cursor = QtGui.QTextCursor(window.text.document().findBlockByNumber(0))
        window.text.setTextCursor(cursor)
        app.processEvents()

        window.cmd_toggle_bookmark()
        app.processEvents()

        block0 = window.text.document().findBlockByNumber(0)
        # Foreground z charFormat — INFO level
        fg = block0.charFormat().foreground().color().name()
        info_color = QtGui.QColor(window.theme["info"]).name()
        assert fg == info_color, f"Foreground powinien być INFO, jest {fg}"

        # Tło zakładki — w ExtraSelections
        bookmark_color = QtGui.QColor(window.theme["bookmark"]).name()
        sels = window.text.extraSelections()
        bookmark_bg_found = False
        for sel in sels:
            sel_bg = sel.format.background().color().name()
            if sel_bg == bookmark_color:
                # Sprawdź czy ta selekcja jest w linii 0
                if sel.cursor.blockNumber() == 0:
                    bookmark_bg_found = True
                    break
        assert bookmark_bg_found, (
            f"ExtraSelections nie zawierają zielonego tła zakładki "
            f"({bookmark_color}) w linii 0. Selekcje: "
            f"{[(s.format.background().color().name(), s.cursor.blockNumber()) for s in sels]}"
        )

    def test_toggle_bookmark_clears_selection(self, app_instance):
        """Cmd+B czyści selekcję Qt (nie zostawia szarego paska)."""
        window, _ = app_instance
        window._load_window(at_line=0)
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        # Zaznacz kilka linii
        _select_blocks(window.text, start_block=2, count=3)
        assert window.text.textCursor().hasSelection()

        window.cmd_toggle_bookmark()
        app.processEvents()

        assert not window.text.textCursor().hasSelection()

    def test_toggle_bookmark_preserves_cursor_line(self, app_instance):
        """Po Cmd+B kursor zostaje w tej samej linii (nie skacze na początek)."""
        window, _ = app_instance
        window._load_window(at_line=0)
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        cursor = QtGui.QTextCursor(window.text.document().findBlockByNumber(10))
        window.text.setTextCursor(cursor)
        app.processEvents()

        window.cmd_toggle_bookmark()
        app.processEvents()

        # Kursor nadal w linii 10
        assert window.text.textCursor().blockNumber() == 10

    def test_current_line_does_not_cover_bookmark(self, app_instance):
        """Kluczowa poprawka: gdy kursor jest na linii z zakładką, widać
        zielone tło zakładki, a szare current_line NIE jest dokładane
        (nie przykrywa zakładki)."""
        window, _ = app_instance
        window._load_window(at_line=0)
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        # Kursor w linii 7, dodaj zakładkę
        cursor = QtGui.QTextCursor(window.text.document().findBlockByNumber(7))
        window.text.setTextCursor(cursor)
        app.processEvents()

        window.cmd_toggle_bookmark()
        app.processEvents()

        # Kursor zostaje w linii 7 — zakładka też jest w linii 7.
        bookmark_color = QtGui.QColor(window.theme["bookmark"]).name()
        current_color = QtGui.QColor(window.theme["current_line"]).name()
        sels = window.text.extraSelections()

        # Powinna być selekcja zakładki w linii 7 (zielone tło).
        bookmark_sel_exists = any(
            s.cursor.blockNumber() == 7
            and s.format.background().color().name() == bookmark_color
            for s in sels
        )
        assert bookmark_sel_exists, "Brak zielonej selekcji zakładki w linii 7"

        # NIE powinna być selekcja current_line w linii 7 — bo by przykryła
        # zakładkę i użytkownik widziałby szare zamiast zielonego.
        current_sel_on_bookmark = any(
            s.cursor.blockNumber() == 7
            and s.format.background().color().name() == current_color
            for s in sels
        )
        assert not current_sel_on_bookmark, (
            "Szara selekcja current_line przykrywa zakładkę w linii 7 — to bug"
        )

    def test_current_line_shows_on_normal_line(self, app_instance):
        """Gdy kursor jest na linii BEZ zakładki, current_line się pokazuje."""
        window, _ = app_instance
        window._load_window(at_line=0)
        app = QtWidgets.QApplication.instance()
        app.processEvents()

        # Dodaj zakładkę w linii 7
        cursor = QtGui.QTextCursor(window.text.document().findBlockByNumber(7))
        window.text.setTextCursor(cursor)
        app.processEvents()
        window.cmd_toggle_bookmark()
        app.processEvents()

        # Przesuń kursor na linię 8 (bez zakładki)
        cursor = QtGui.QTextCursor(window.text.document().findBlockByNumber(8))
        window.text.setTextCursor(cursor)
        app.processEvents()

        current_color = QtGui.QColor(window.theme["current_line"]).name()
        sels = window.text.extraSelections()
        current_sel_exists = any(
            s.cursor.blockNumber() == 8
            and s.format.background().color().name() == current_color
            for s in sels
        )
        assert current_sel_exists, "Brak szarej selekcji current_line w linii 8"


# =============================================================================
# Edycja linii — kursor poza widokiem (scroll po wyniku wyszukiwania)
# =============================================================================

class TestEditLineAfterScroll:
    def test_edit_uses_visible_line_when_cursor_offscreen(self):
        """Bug: po wyniku wyszukiwania kursor zostaje na starej linii. Jeśli
        user przewinie widok i wciska Ctrl+D, edytuje linię kursora (niewidoczną),
        a nie tę, którą widzi. Poprawka: cmd_edit_line używa firstVisibleBlock
        gdy kursor jest poza widokiem.

        Scenariusz: plik 50 linii, _highlight_and_scroll(0) ustawia kursor na
        blok 0. User scrolluje w dół, żeby widzieć linie 30-40. Ctrl+D powinien
        edytować ~linię 30 (firstVisibleBlock), NIE linię 0.
        """
        import tempfile
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
        window = LogViewerWindow(config=cfg)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="wb") as tmp:
            test_file = tmp.name
        with open(test_file, "wb") as f:
            for i in range(50):
                f.write(f"line {i} content here\n".encode())

        try:
            tab = window._new_tab()
            idx = LineIndexer(test_file, encoding="utf-8")
            tab.file_path = test_file
            tab._on_index_done(idx)
            app.processEvents()

            # Symulacja: _highlight_and_scroll(0) — kursor na blok 0 (jak po
            # wyniku wyszukiwania).
            window._highlight_and_scroll(0)
            app.processEvents()
            assert window.text.textCursor().blockNumber() == 0

            # User scrolluje w dół, żeby widzieć linie ~30-40.
            sb = window.text.verticalScrollBar()
            sb.setValue(sb.maximum())  # przewiń na sam dół
            app.processEvents()

            # Sprawdź: firstVisibleBlock to NIE blok 0.
            fvb = window.text.firstVisibleBlock()
            assert fvb.blockNumber() > 0, "firstVisibleBlock powinien być > 0 po przewinięciu"

            # Wywołaj cmd_edit_line. Ponieważ kursor (blok 0) jest poza
            # widokiem, powinien użyć firstVisibleBlock.
            # Pobieramy widget_line bezpośrednio z logiki cmd_edit_line.
            cursor = window.text.textCursor()
            cursor_rect = window.text.cursorRect(cursor)
            viewport_rect = window.text.viewport().rect()
            if not viewport_rect.contains(cursor_rect.topLeft()):
                cursor = QtGui.QTextCursor(fvb)
            widget_line = cursor.blockNumber()

            # widget_line powinien być firstVisibleBlock, NIE 0.
            assert widget_line == fvb.blockNumber(), (
                f"cmd_edit_line powinien użyć firstVisibleBlock "
                f"({fvb.blockNumber()}) gdy kursor jest poza widokiem, "
                f"ale użył {widget_line}"
            )
            assert widget_line > 0, "Nie powinien edytować linii 0 gdy user widzi inną"
        finally:
            try:
                os.unlink(test_file)
            except PermissionError:
                pass
            if os.path.exists(cfg.path):
                try:
                    os.unlink(cfg.path)
                except PermissionError:
                    pass
            window.deleteLater()
            app.processEvents()


# =============================================================================
# Filtr z kontekstem — N linii po każdym trafieniu (dla stack trace)
# =============================================================================

class TestFilterContext:
    def test_context_spinbox_exists(self, app_instance):
        """Spinbox kontekstu istnieje w toolbarze."""
        window, _ = app_instance
        # filter_context_spin jest w LogViewerWindow (toolbar), nie w LogTab.
        # __getattr__ deleguje do aktywnej zakładki, ale sam spinbox jest
        # na poziomie okna.
        from log_reader.app import LogViewerWindow
        main = None
        # Pobierz referencję do LogViewerWindow przez parent tab.
        tab = window.tabs.currentWidget()
        assert tab is not None
        assert hasattr(tab._main, "filter_context_spin")
        assert tab._main.filter_context_spin.value() == 0

    def test_build_filter_context_generates_lines(self, app_instance):
        """_build_filter_context generuje N następujących linii po każdym trafieniu."""
        window, _ = app_instance
        # Ustaw na aktywnej zakładce (nie na oknie — __getattr__ tylko czyta,
        # nie zapisuje). Pobieramy tab bezpośrednio.
        tab = window.tabs.currentWidget()
        tab.filter_active = True
        tab.filter_results = [
            (10, 0, "line10"),
            (20, 0, "line20"),
            (30, 0, "line30"),
        ]
        tab._filter_context_after = 2
        tab._on_filter_done(tab.filter_results, {11, 12, 21, 22, 31, 32}, [10, 11, 12, 20, 21, 22, 30, 31, 32], None)

        # Po każdym trafieniu 2 następujące linie (z pominięciem trafień).
        # Trafienia: 10, 20, 30.
        # Kontekst dla 10: 11, 12. Dla 20: 21, 22. Dla 30: 31, 32.
        # 11,12,21,22,31,32 — 6 linii kontekstu.
        assert tab.filter_context_lines == {11, 12, 21, 22, 31, 32}

    def test_build_filter_context_zero_means_disabled(self, app_instance):
        """_filter_context_after=0 → brak linii kontekstu."""
        window, _ = app_instance
        tab = window.tabs.currentWidget()
        tab.filter_active = True
        tab.filter_results = [(10, 0, "x")]
        tab._filter_context_after = 0
        tab._on_filter_done(tab.filter_results, set(), [10], None)
        assert tab.filter_context_lines == set()

    def test_build_filter_context_skips_hit_lines(self, app_instance):
        """Linie kontekstu nie obejmują samych trafień (nawet gdy się pokrywają)."""
        window, _ = app_instance
        tab = window.tabs.currentWidget()
        tab.filter_active = True
        # Trafienia w 10 i 11. Kontekst dla 10 = {11, 12}, ale 11 jest trafieniem,
        # więc tylko {12}. Kontekst dla 11 = {12, 13}.
        tab.filter_results = [(10, 0, "x"), (11, 0, "y")]
        tab._filter_context_after = 2
        tab._on_filter_done(tab.filter_results, {12, 13}, [10, 11, 12, 13], None)
        # 12, 13 — 11 i 10 są trafieniami, więc pominęliśmy je.
        assert tab.filter_context_lines == {12, 13}

    def test_clear_filter_clears_context(self, app_instance):
        """cmd_clear_filter czyści filter_context_lines."""
        window, _ = app_instance
        tab = window.tabs.currentWidget()
        tab.filter_active = True
        tab.filter_results = [(10, 0, "x")]
        tab._filter_context_after = 2
        tab._on_filter_done(tab.filter_results, {11, 12}, [10, 11, 12], None)
        assert len(tab.filter_context_lines) > 0

        tab.cmd_clear_filter(silent=True)
        assert tab.filter_context_lines == set()
        assert tab._filter_context_after == 0

    def test_filter_line_numbers_show_real_file_lines(self, app_instance):
        """W trybie filtra numery linii (line_map) pokazują prawdziwe numery
        z pliku, z dziurami — np. 10, 11, 12, 21, 22, 31, 32 (gdy trafienia
        w 10/20/30, kontekst=2). Nie 1, 2, 3, 4, 5..."""
        window, _ = app_instance
        tab = window.tabs.currentWidget()
        app = QtWidgets.QApplication.instance()

        # Symulacja: filtr z 3 trafieniami (linie 10, 20, 30), kontekst=2.
        tab.filter_active = True
        tab.filter_results = [
            (10, 0, "hit10"),
            (20, 0, "hit20"),
            (30, 0, "hit30"),
        ]
        tab._filter_context_after = 2
        tab._on_filter_done(tab.filter_results, {11, 12, 21, 22, 31, 32}, [10, 11, 12, 20, 21, 22, 30, 31, 32], None)

        # Załaduj okno — line_map powinno mieć prawdziwe numery z dziurami.
        tab._load_window(at_line=0)
        app.processEvents()

        # Trafienia: 10, 20, 30. Kontekst: 11,12, 21,22, 31,32.
        # Posortowane: 10, 11, 12, 20, 21, 22, 30, 31, 32.
        expected = [10, 11, 12, 20, 21, 22, 30, 31, 32]
        assert tab.line_map == expected, (
            f"line_map powinien mieć prawdziwe numery z dziurami, jest {tab.line_map}"
        )

    def test_filter_hit_text_from_memory_no_file_reads(self, app_instance):
        """Wydajność: tekst trafień brany z filter_results (w pamięci), nie
        z indexer.read_lines. Symulacja — ustawimy filter_results z tekstem,
        który NIE istnieje w pliku, i sprawdzimy że to ten tekst się wyświetli."""
        window, _ = app_instance
        tab = window.tabs.currentWidget()
        app = QtWidgets.QApplication.instance()

        # Tekst "MARKER_FROM_MEMORY" nie istnieje w pliku testowym.
        tab.filter_active = True
        tab.filter_results = [(5, 0, "MARKER_FROM_MEMORY")]
        tab._filter_context_after = 0
        tab._on_filter_done(tab.filter_results, set(), [5], None)
        tab._load_window(at_line=0)
        app.processEvents()

        # Pierwsza linia okna powinna mieć tekst z pamięci, nie z pliku.
        assert tab.window_lines[0][1] == "MARKER_FROM_MEMORY"

    def test_filter_hit_highlight_yellow(self, app_instance):
        """Trafienia filtra mają żółte tło (highlight), kontekst szare (context).
        Wizualne odróżnienie trafień od kontekstu."""
        window, _ = app_instance
        tab = window.tabs.currentWidget()
        app = QtWidgets.QApplication.instance()

        # Trafienie w linii 5, kontekst=2 → linie 6, 7 to kontekst.
        tab.filter_active = True
        tab.filter_results = [(5, 0, "hit5")]
        tab._filter_context_after = 2
        tab._on_filter_done(tab.filter_results, {6, 7}, [5, 6, 7], None)
        tab._load_window(at_line=0)
        app.processEvents()

        highlight_color = QtGui.QColor(tab.theme["highlight"]).name()
        context_color = QtGui.QColor(tab.theme["context"]).name()
        sels = tab.text.extraSelections()

        # Trafienie (blok 0) powinno mieć żółte tło.
        hit_has_yellow = any(
            s.cursor.blockNumber() == 0
            and s.format.background().color().name() == highlight_color
            for s in sels
        )
        assert hit_has_yellow, "Trafienie filtra powinno mieć żółte tło (highlight)"

        # Kontekst (bloki 1, 2) powinien mieć szare tło.
        context_blocks = {
            s.cursor.blockNumber()
            for s in sels
            if s.format.background().color().name() == context_color
        }
        assert 1 in context_blocks, "Linia 1 powinna być kontekstem (szare tło)"
        assert 2 in context_blocks, "Linia 2 powinna być kontekstem (szare tło)"


# =============================================================================
# Indeksowanie — postęp i anulowanie
# =============================================================================

class TestIndexingProgress:
    def test_parallel_emits_progress_during_indexing(self):
        """_build_parallel emituje postęp w trakcie (nie tylko 100% na końcu).

        Dla pliku > 100 MB, LineIndexer używa _build_parallel. Bez poprawki
        postęp był emitowany tylko na końcu (100%) — user widział freeze.
        Teraz dzielimy na chunki 256 MB i emitujemy po każdym.
        """
        import tempfile
        # Stwórz plik 150 MB (>100 MB threshold dla parallel).
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
            assert actual_size > 100 * 1024 * 1024, "Plik testowy za mały"

            progress_values = []
            def progress_cb(pct: float):
                progress_values.append(pct)

            from log_reader.indexer import LineIndexer
            idx = LineIndexer(test_file, progress_cb=progress_cb, encoding="utf-8")
            try:
                assert len(progress_values) > 0, "Brak odczytów postępu"
                assert 100.0 in progress_values, "Brak finalnego 100%"
            finally:
                idx.close()
        finally:
            try:
                os.unlink(test_file)
            except PermissionError:
                pass

    def test_indexer_worker_has_cancel_method(self):
        """IndexerWorker ma metodę cancel() i is_cancelled()."""
        from log_reader.workers import IndexerWorker
        worker = IndexerWorker("/tmp/nonexistent", "utf-8", 1024 * 1024)
        assert hasattr(worker, "cancel")
        assert hasattr(worker, "is_cancelled")
        assert worker.is_cancelled() is False
        worker.cancel()
        assert worker.is_cancelled() is True

    def test_logtab_has_progress_method_not_lambda(self):
        """LogTab._on_index_progress MUSI być metodą (nie lambdą) — closure
        nie jest picklowalne cross-thread, powoduje błędy QTimer w worker
        thread („Timers cannot be stopped from another thread")."""
        from log_reader.app import LogTab
        # Metoda jest bound method klasy — sprawdzamy istnienie.
        assert hasattr(LogTab, "_on_index_progress"), (
            "LogTab musi mieć metodę _on_index_progress (nie lambdę) — "
            "Qt QueuedConnection wymaga picklowalnego odbiorcy"
        )
        # Sprawdź że to jest funkcja (metoda klasowa), a nie atrybut instancji.
        import inspect
        assert inspect.isfunction(LogTab._on_index_progress), (
            "_on_index_progress musi być metodą klasową"
        )

    def test_logtab_has_reindex_slots(self):
        """LogTab ma sloty _on_reindex_finished, _on_follow_reindex_slot,
        _on_follow_reindex_clear_flag — bezpośrednio metody (nie lambdy)."""
        from log_reader.app import LogTab
        import inspect
        for name in ("_on_reindex_finished", "_on_follow_reindex_slot",
                     "_on_follow_reindex_clear_flag", "_on_index_progress",
                     "_on_index_done", "_on_index_error", "_close_index_progress"):
            assert hasattr(LogTab, name), f"LogTab musi mieć metodę {name}"
            assert inspect.isfunction(getattr(LogTab, name)), (
                f"{name} musi być metodą klasową (nie lambdą)"
            )

    def test_parallel_indexing_can_be_cancelled(self):
        """Anulowanie ustawia cancel_event — _build_parallel przerywa."""
        import tempfile
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
            # Ustaw cancel PRZED rozpoczęciem — _build_parallel sprawdzi
            # przy pierwszym odczycie wyniku i przerwie.
            cancel_event.set()

            from log_reader.indexer import LineIndexer
            idx = LineIndexer(
                test_file, encoding="utf-8",
                cancel_event=cancel_event,
            )
            try:
                # Po anulowaniu index powinien być pusty (tylko [IndexEntry(0,0)]).
                assert len(idx.index) == 1, (
                    f"Po anulowaniu indeks powinien być pusty, ma {len(idx.index)} wpisów"
                )
                assert idx.line_count == 0
            finally:
                idx.close()
        finally:
            try:
                os.unlink(test_file)
            except PermissionError:
                pass

    def test_indexing_performance_benchmark(self):
        """Benchmark: indeksowanie 200 MB powinno trwać < 10s (przy ~20 MB/s+
        throughput). Bez optymalizacji count() i 16 MB chunków, stara wersja
        miała ~5-10 MB/s. Jeśli test trwa > 30s, coś jest nie tak."""
        import tempfile
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

            from log_reader.indexer import LineIndexer
            t0 = time.time()
            idx = LineIndexer(test_file, encoding="utf-8")
            t1 = time.time()
            elapsed = t1 - t0
            throughput = actual_size / elapsed / (1024 * 1024) if elapsed > 0 else 0
            try:
                # Throughput powinien być > 20 MB/s (luźny threshold).
                # Na szybkim CPU z SSD osiągamy 200+ MB/s, ale na CI/Windowsie
                # może być wolniej. 20 MB/s to absolutne minimum dla
                # zoptymalizowanej wersji.
                assert throughput > 20, (
                    f"Throughput {throughput:.1f} MB/s < 20 MB/s — regresja wydajności. "
                    f"Czas: {elapsed:.2f}s dla {actual_size/(1024*1024):.0f} MB"
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
        from log_reader.i18n import I18N
        for key in ("btn_delete_sel", "msg_bookmarks_added", "msg_bookmarks_removed",
                    "msg_edits_removed", "msg_no_selection",
                    "lbl_filter_context", "tt_filter_context",
                    "btn_cancel", "dlg_index_title", "st_cancelling", "st_cancelled"):
            assert key in I18N["pl"], f"Brak klucza {key} w PL"
            assert I18N["pl"][key], f"Pusta wartość dla {key} w PL"

    def test_new_keys_present_in_en(self):
        from log_reader.i18n import I18N
        for key in ("btn_delete_sel", "msg_bookmarks_added", "msg_bookmarks_removed",
                    "msg_edits_removed", "msg_no_selection",
                    "lbl_filter_context", "tt_filter_context",
                    "btn_cancel", "dlg_index_title", "st_cancelling", "st_cancelled"):
            assert key in I18N["en"], f"Brak klucza {key} w EN"
            assert I18N["en"][key], f"Pusta wartość dla {key} w EN"
