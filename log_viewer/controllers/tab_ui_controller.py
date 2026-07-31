from PySide6 import QtCore
from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QPoint
import bisect
from PySide6.QtCore import QTimer
from PySide6 import QtGui
from PySide6.QtGui import QKeySequence
from log_viewer.widgets import SearchResultsModel
from PySide6.QtCore import QObject, Qt, Slot, QTimer
from PySide6.QtGui import QFont, QColor, QFontDatabase, QTextCursor
from log_viewer.helpers import THEME_DARK, THEME_LIGHT
from log_viewer.context_view import bisect_left_custom
from typing import Optional, List, Tuple

class UIController(QObject):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab

    def _setup_ui_elements(self) -> None:
        self.tab.splitter = self.tab.ui.splitter
        self.tab.v_splitter = self.tab.ui.v_splitter

        self.tab.splitter.setSizes([200, 900, 48])
        self.tab.v_splitter.setSizes([500, 150])

        # Aliases for convenience
        self.tab._lbl_bookmarks = self.tab.ui._lbl_bookmarks
        self.tab._lbl_edits = self.tab.ui._lbl_edits
        self.tab.bm_tree = self.tab.ui.bm_tree
        self.tab.ed_tree = self.tab.ui.ed_tree
        self.tab.btn_del_bookmarks = self.tab.ui.btn_del_bookmarks
        self.tab.btn_del_edits = self.tab.ui.btn_del_edits
        self.tab.text = self.tab.ui.text
        self.tab._search_results_label = self.tab.ui._search_results_label
        self.tab.search_results_view = self.tab.ui.search_results_view
        self.tab.minimap = self.tab.ui.minimap
        self.tab.pct_label = self.tab.ui.pct_label

        # Set up signals
        self.tab.bm_tree.itemDoubleClicked.connect(self.tab.bookmark_controller._goto_bookmark)
        self.tab.btn_del_bookmarks.clicked.connect(self.tab.bookmark_controller._delete_selected_bookmarks)
        QtGui.QShortcut(QKeySequence.StandardKey.Delete, self.tab.bm_tree,
                        activated=self.tab.bookmark_controller._delete_selected_bookmarks)
        QtGui.QShortcut(QKeySequence("Backspace"), self.tab.bm_tree,
                        activated=self.tab.bookmark_controller._delete_selected_bookmarks)

        self.tab.ed_tree.itemDoubleClicked.connect(self.tab.bookmark_controller._goto_edit)
        self.tab.btn_del_edits.clicked.connect(self.tab.bookmark_controller._delete_selected_edits)
        QtGui.QShortcut(QKeySequence.StandardKey.Delete, self.tab.ed_tree,
                        activated=self.tab.bookmark_controller._delete_selected_edits)
        QtGui.QShortcut(QKeySequence("Backspace"), self.tab.ed_tree,
                        activated=self.tab.bookmark_controller._delete_selected_edits)

        self.tab.text.files_dropped.connect(self.tab._main._on_files_dropped)
        self.tab.text.verticalScrollBar().valueChanged.connect(self.tab._on_scroll_changed)
        # Podłączamy detekcję user_scrolled aby wyłączyć follow
        self.tab.text.user_scrolled.connect(self.tab._on_user_scrolled)
        # Musimy również wyłączyć follow, jeśli użytkownik kliknie bezpośrednio na scrollbar
        self.tab.text.verticalScrollBar().sliderPressed.connect(self.tab._on_user_scrolled)

        # Debouncing dla ładowania krawędzi (przeciwdziała "zamrażaniu" aplikacji przy intensywnym przewijaniu)
        self.tab._edge_load_timer = QTimer(self)
        self.tab._edge_load_timer.setSingleShot(True)
        self.tab._edge_load_timer.setInterval(150)
        self.tab._edge_load_timer.timeout.connect(self.tab._do_check_edges)
        self.tab._search_extra_sel: Optional[QtWidgets.QTextEdit.ExtraSelection] = None
        self.tab.text.cursorPositionChanged.connect(self.tab._update_current_line_highlight)

        self.tab._search_model = SearchResultsModel()
        self.tab.search_results_view.setModel(self.tab._search_model)
        mono_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono_font.setPointSize(9)

        self.tab.search_results_view.setFont(mono_font)
        self.tab.search_results_view.activated.connect(self.tab._on_search_result_clicked)
        # Filtr zdarzeń dla obsługi klawisza Return/Enter na systemie Mac OS
        class ReturnKeyFilter(QtCore.QObject):
            def eventFilter(self, obj, event):
                if event.type() == QtCore.QEvent.KeyPress:
                    if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                        index = obj.currentIndex()
                        if index.isValid():
                            obj.activated.emit(index)
                        return True
                return super().eventFilter(obj, event)
        self.tab._return_key_filter = ReturnKeyFilter(self.tab)
        self.tab.search_results_view.installEventFilter(self.tab._return_key_filter)

        # Poprawka: nawigacja klawiszem enter i double-click
        self.tab.search_results_view.doubleClicked.connect(self.tab._on_search_result_clicked)

        self.tab.minimap.position_clicked.connect(self.tab._on_minimap_click)
        self.tab.pct_label.setStyleSheet(f"color: {THEME_DARK['fg_dim']}; font-size: 10px; padding: 4px;")
        if hasattr(self.tab.ui, 'sep'):
            self.tab.ui.sep.setStyleSheet(f"background-color: {THEME_DARK['border']};")

        # Set up translated labels that UI compiler wouldn't know
        self.tab._lbl_bookmarks.setText(self.tab.t("lbl_bookmarks"))
        self.tab._lbl_edits.setText(self.tab.t("lbl_edits"))
        self.tab.bm_tree.setHeaderLabels([self.tab.t("col_line")])
        self.tab.ed_tree.setHeaderLabels([self.tab.t("col_line")])
        self.tab.btn_del_bookmarks.setText(self.tab.t("btn_delete_sel"))
        self.tab.btn_del_edits.setText(self.tab.t("btn_delete_sel"))
        self.tab._search_results_label.setText(self.tab.t("lbl_search_results_empty"))

    def _apply_font_to_text(self) -> None:
        if self.tab.font_family:
            font = QFont(self.tab.font_family, self.tab.font_size)
        else:
            font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
            font.setPointSize(self.tab.font_size)

        self.tab.text.setFont(font)
        if hasattr(self.tab.text, "_line_number_area"):
            self.tab.text._line_number_area.update_width()
            self.tab.text._line_number_area.update()

    def _apply_theme(self) -> None:
        """Aktualizuje kolory per-tab po zmianie motywu."""
        t = self.tab.theme
        if hasattr(self.tab.text, "_line_number_area"):
            self.tab.text._line_number_area.setStyleSheet(
                f"background-color: {t['bg_main']};"
            )
        if hasattr(self.tab, "minimap"):
            self.tab.minimap._colors = {
                "error": QColor(t["minimap_error"]),
                "warn": QColor(t["minimap_warn"]),
                "info": QColor(t["minimap_info"]),
                "debug": QColor(t["minimap_debug"]),
                "": QColor(t["minimap_bg"]),
            }
            self.tab.minimap._bg = QColor(t["minimap_bg"])
            self.tab.minimap._viewport_color = QColor(t["minimap_viewport"])
            self.tab.minimap.update()
        pal = self.tab.text.palette()
        pal.setColor(QtGui.QPalette.Inactive, QtGui.QPalette.Highlight, pal.color(QtGui.QPalette.Active, QtGui.QPalette.Highlight))
        pal.setColor(QtGui.QPalette.Inactive, QtGui.QPalette.HighlightedText, pal.color(QtGui.QPalette.Active, QtGui.QPalette.HighlightedText))
        pal.setColor(QtGui.QPalette.Inactive, QtGui.QPalette.Base, pal.color(QtGui.QPalette.Active, QtGui.QPalette.Base))
        pal.setColor(QtGui.QPalette.Inactive, QtGui.QPalette.Text, pal.color(QtGui.QPalette.Active, QtGui.QPalette.Text))
        self.tab.text.setPalette(pal)

        self.tab._update_text_colors()

    def _update_text_colors(self) -> None:
        """Aktualizuje kolory tagów w Text widget po zmianie motywu."""
        t = self.tab.theme
        self.tab.text.setExtraSelections([])
        self.tab._search_extra_sel = None
        if self.tab.indexer and self.tab.line_map:
            self.tab._reload_current_view()
        # Po reload przebuduj podświetlenie bieżącej linii nowym kolorem.
        self.tab._update_current_line_highlight()

    def _update_minimap(self) -> None:
        if not self.tab.indexer or self.tab.indexer.line_count == 0:
            return

        if self.tab.filter_active and self.tab._filter_all_lines:
            total = len(self.tab._filter_all_lines)
        else:
            total = self.tab.indexer.line_count

        # Aby zapobiec zawieszaniu UI przy ładowaniu bardzo dużych plików (np. 25 GB)
        # rezygnujemy z pełnego skanowania pliku w poszukiwaniu tagów logów dla
        # kolorowania minimapy. Minimapa posłuży tylko jako żółty wskaźnik pozycji.
        if self.tab.minimap._total_lines != total:
            self.tab.minimap.set_line_data([], total)

        self.tab._update_minimap_viewport()

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
                last_line = self.tab.line_map[cursor_bottom.blockNumber()] if cursor_bottom.blockNumber() < len(self.tab.line_map) else total - 1

            if self.tab.filter_active and self.tab._filter_all_lines:
                flen = len(self.tab._filter_all_lines)
                start_idx = bisect_left_custom(self.tab._filter_all_lines, first_line)
                end_idx = bisect_left_custom(self.tab._filter_all_lines, last_line)
                new_start = start_idx / flen
                new_end = end_idx / flen
            else:
                new_start = first_line / total
                new_end = last_line / total

            # W trybie follow aktualizujemy viewport płynniej
            self.tab.minimap.set_viewport(new_start, new_end)
        except Exception:
            pass
