from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase, QKeySequence

from log_viewer.bitset import bisect_left_custom
from log_viewer.helpers import THEME_DARK
from log_viewer.widgets import SearchResultsModel

if TYPE_CHECKING:
    from log_viewer.log_tab import LogTab


class UIController(QObject):
    def __init__(self, tab: LogTab) -> None:
        super().__init__(tab)
        self.tab: LogTab = tab

    def _setup_ui_elements(self) -> None:
        self.tab.splitter = self.tab.ui.splitter
        self.tab.v_splitter = self.tab.ui.v_splitter
        self.tab.bm_tree = self.tab.ui.bm_tree
        self.tab.ed_tree = self.tab.ui.ed_tree
        self.tab.btn_del_bookmarks = self.tab.ui.btn_del_bookmarks
        self.tab.btn_del_edits = self.tab.ui.btn_del_edits
        self.tab.text = self.tab.ui.text
        self.tab.search_results_view = self.tab.ui.search_results_view
        self.tab.minimap = self.tab.ui.minimap
        self.tab.pct_label = self.tab.ui.pct_label
        self.tab.lbl_bookmarks = self.tab.ui.lbl_bookmarks
        self.tab.lbl_edits = self.tab.ui.lbl_edits
        self.tab.search_results_label = self.tab.ui.search_results_label

        self.tab.splitter.setSizes([200, 900, 48])
        self.tab.v_splitter.setSizes([500, 150])

        # Set up signals
        self.tab.bm_tree.itemDoubleClicked.connect(self.tab.bookmark_controller.goto_bookmark)
        self.tab.btn_del_bookmarks.clicked.connect(self.tab.bookmark_controller.delete_selected_bookmarks)

        bm_del_shortcut = QtGui.QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.tab.bm_tree)
        bm_del_shortcut.activated.connect(self.tab.bookmark_controller.delete_selected_bookmarks)

        bm_bs_shortcut = QtGui.QShortcut(QKeySequence(Qt.Key.Key_Backspace), self.tab.bm_tree)
        bm_bs_shortcut.activated.connect(self.tab.bookmark_controller.delete_selected_bookmarks)

        self.tab.ed_tree.itemDoubleClicked.connect(self.tab.bookmark_controller.goto_edit)
        self.tab.btn_del_edits.clicked.connect(self.tab.bookmark_controller.delete_selected_edits)

        ed_del_shortcut = QtGui.QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.tab.ed_tree)
        ed_del_shortcut.activated.connect(self.tab.bookmark_controller.delete_selected_edits)

        ed_bs_shortcut = QtGui.QShortcut(QKeySequence(Qt.Key.Key_Backspace), self.tab.ed_tree)
        ed_bs_shortcut.activated.connect(self.tab.bookmark_controller.delete_selected_edits)

        self.tab.text.files_dropped.connect(self.tab.main_window.on_files_dropped)
        self.tab.text.verticalScrollBar().valueChanged.connect(self.tab.on_scroll_changed)
        # Podłączamy detekcję user_scrolled aby wyłączyć follow
        self.tab.text.user_scrolled.connect(self.tab.on_user_scrolled)
        # Musimy również wyłączyć follow, jeśli użytkownik kliknie bezpośrednio na scrollbar
        self.tab.text.verticalScrollBar().sliderPressed.connect(self.tab.on_user_scrolled)

        # Debouncing dla ładowania krawędzi (przeciwdziała "zamrażaniu" aplikacji przy intensywnym przewijaniu)
        self.tab.edge_load_timer.setSingleShot(True)
        self.tab.edge_load_timer.setInterval(150)
        self.tab.edge_load_timer.timeout.connect(self.tab.do_check_edges)
        self.tab.search_extra_sel = None
        self.tab.text.cursorPositionChanged.connect(self.tab.update_current_line_highlight)

        self.tab.search_model = SearchResultsModel()
        self.tab.search_results_view.setModel(self.tab.search_model)
        self.tab.search_results_view.setLayoutMode(QtWidgets.QListView.LayoutMode.Batched)
        self.tab.search_results_view.setBatchSize(100)
        mono_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono_font.setPointSize(9)

        self.tab.search_results_view.setFont(mono_font)
        self.tab.search_results_view.activated.connect(self.tab.on_search_result_clicked)

        class ReturnKeyFilter(QtCore.QObject):
            def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
                if (
                    event.type() == QtCore.QEvent.Type.KeyPress
                    and isinstance(event, QtGui.QKeyEvent)
                    and event.key()
                    in (
                        Qt.Key.Key_Return,
                        Qt.Key.Key_Enter,
                    )
                ):
                    if isinstance(obj, QtWidgets.QAbstractItemView):
                        index = obj.currentIndex()
                        if index.isValid():
                            obj.activated.emit(index)
                    return True
                return super().eventFilter(obj, event)

        self.tab._return_key_filter = ReturnKeyFilter(self.tab)
        self.tab.search_results_view.installEventFilter(self.tab._return_key_filter)

        # Poprawka: nawigacja klawiszem enter i double-click
        self.tab.search_results_view.doubleClicked.connect(self.tab.on_search_result_clicked)

        self.tab.minimap.position_clicked.connect(self.tab.on_minimap_click)
        self.tab.pct_label.setStyleSheet(f"color: {THEME_DARK['fg_dim']}; font-size: 10px; padding: 4px;")
        if hasattr(self.tab.ui, "sep"):
            self.tab.ui.sep.setStyleSheet(f"background-color: {THEME_DARK['border']};")

        # Set up translated labels that UI compiler wouldn't know
        self.tab.lbl_bookmarks.setText(self.tab.t("lbl_bookmarks"))
        self.tab.lbl_edits.setText(self.tab.t("lbl_edits"))
        self.tab.bm_tree.setHeaderLabels([self.tab.t("col_line")])
        self.tab.ed_tree.setHeaderLabels([self.tab.t("col_line")])
        self.tab.btn_del_bookmarks.setText(self.tab.t("btn_delete_sel"))
        self.tab.btn_del_edits.setText(self.tab.t("btn_delete_sel"))
        self.tab.search_results_label.setText(self.tab.t("lbl_search_results_empty"))

    def _apply_font_to_text(self) -> None:
        family = self.tab.font_family
        if family:
            font = QFont(family, self.tab.font_size)
        else:
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
            font.setPointSize(self.tab.font_size)

        self.tab.text.setFont(font)
        if hasattr(self.tab.text, "line_number_area"):
            self.tab.text.line_number_area.update_width()
            self.tab.text.line_number_area.update()

    def _apply_theme(self) -> None:
        """Aktualizuje kolory per-tab po zmianie motywu."""
        t = self.tab.theme
        if hasattr(self.tab.text, "line_number_area"):
            self.tab.text.line_number_area.setStyleSheet(f"background-color: {t['bg_main']};")
        if hasattr(self.tab, "minimap"):
            self.tab.minimap.apply_theme(t)
        pal = self.tab.text.palette()
        pal.setColor(
            QtGui.QPalette.ColorGroup.Inactive,
            QtGui.QPalette.ColorRole.Highlight,
            pal.color(QtGui.QPalette.ColorGroup.Active, QtGui.QPalette.ColorRole.Highlight),
        )
        pal.setColor(
            QtGui.QPalette.ColorGroup.Inactive,
            QtGui.QPalette.ColorRole.HighlightedText,
            pal.color(QtGui.QPalette.ColorGroup.Active, QtGui.QPalette.ColorRole.HighlightedText),
        )
        pal.setColor(
            QtGui.QPalette.ColorGroup.Inactive,
            QtGui.QPalette.ColorRole.Base,
            pal.color(QtGui.QPalette.ColorGroup.Active, QtGui.QPalette.ColorRole.Base),
        )
        pal.setColor(
            QtGui.QPalette.ColorGroup.Inactive,
            QtGui.QPalette.ColorRole.Text,
            pal.color(QtGui.QPalette.ColorGroup.Active, QtGui.QPalette.ColorRole.Text),
        )
        self.tab.text.setPalette(pal)

        self.update_text_colors()

    def update_text_colors(self) -> None:
        """Aktualizuje kolory tagów w Text widget po zmianie motywu."""
        self.tab.text.setExtraSelections([])
        self.tab.search_extra_sel = None
        if self.tab.indexer and self.tab.line_map:
            self.tab.reload_current_view()
        # Po reload przebuduj podświetlenie bieżącej linii nowym kolorem.
        self.tab.update_current_line_highlight()

    def _update_text_colors(self) -> None:
        self.update_text_colors()

    def update_minimap(self) -> None:
        """Publiczny interfejs do aktualizacji minimapy."""
        self._update_minimap()

    def update_minimap_viewport(self) -> None:
        """Publiczny interfejs do aktualizacji wskaźnika pozycji viewportu na minimapie."""
        self._update_minimap_viewport()

    def _update_minimap(self) -> None:
        if not self.tab.indexer or self.tab.indexer.line_count == 0:
            return

        filter_lines = self.tab.filter_all_lines
        if self.tab.filter_active and filter_lines is not None:
            total = len(filter_lines)
        else:
            total = self.tab.indexer.line_count

        # Aby zapobiec zawieszaniu UI przy ładowaniu bardzo dużych plików (np. 25 GB)
        # rezygnujemy z pełnego skanowania pliku w poszukiwaniu tagów logów dla
        # kolorowania minimapy. Minimapa posłuży tylko jako żółty wskaźnik pozycji.
        if self.tab.minimap.total_lines != total:
            self.tab.minimap.set_line_data([], total)

        self.update_minimap_viewport()

    def _update_minimap_viewport(self) -> None:
        if not self.tab.indexer or self.tab.indexer.line_count == 0 or not self.tab.line_map:
            return
        try:
            cursor = self.tab.text.cursorForPosition(QPoint(0, 5))
            first_line = self.tab.line_map[cursor.blockNumber()] if cursor.blockNumber() < len(self.tab.line_map) else 0

            # Jeśli jesteśmy w trybie follow i pasek jest na dole, zakładamy dolną krawędź jako 1.0 (100%)
            total = self.tab.indexer.line_count

            scrollbar = self.tab.text.verticalScrollBar()
            is_at_bottom = scrollbar.value() >= scrollbar.maximum() - 5

            if self.tab.follow_active and is_at_bottom:
                last_line = total - 1
            else:
                cursor_bottom = self.tab.text.cursorForPosition(QPoint(0, self.tab.text.height() - 5))
                last_line = (
                    self.tab.line_map[cursor_bottom.blockNumber()]
                    if cursor_bottom.blockNumber() < len(self.tab.line_map)
                    else total - 1
                )

            filter_lines = self.tab.filter_all_lines
            if self.tab.filter_active and filter_lines is not None:
                flen = len(filter_lines)
                start_idx = bisect_left_custom(filter_lines, first_line)
                end_idx = bisect_left_custom(filter_lines, last_line)
                new_start = start_idx / flen
                new_end = end_idx / flen
            else:
                new_start = first_line / total
                new_end = last_line / total

            # W trybie follow aktualizujemy viewport płynniej
            self.tab.minimap.set_viewport(new_start, new_end)
        except (AttributeError, IndexError, ValueError, ZeroDivisionError):
            pass
