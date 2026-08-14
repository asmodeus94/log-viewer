"""tab_bookmark_controller.py — Kontroler zakładek i widoku edycji dla LogTab."""

from __future__ import annotations

from typing import TYPE_CHECKING
from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QMessageBox, QTreeWidgetItem
from PySide6 import QtGui

if TYPE_CHECKING:
    from log_viewer.log_tab import LogTab


class BookmarkController(QObject):
    """Kontroler odpowiedzialny za zarządzanie zakładkami (bookmarks)
    oraz panelem widoku bufora edycji w pojedynczej karcie.
    """

    def __init__(self, tab: LogTab) -> None:
        super().__init__(tab)
        self.tab: LogTab = tab

    def cmd_toggle_bookmark(self) -> None:
        """Przełącza zakładkę w LINII KURSORA.

        Działa wyłącznie na jednej linii — bez względu na to, czy istnieje
        selekcja. To celowa decyzja UX: wieloliniowe, przypadkowe selekcje
        (np. Shift+klik po przewinięciu, Cmd+A) nie powinny powodować
        masowego zakładkowania całego zakresu. Przed dodaniem zakładki
        selekcja jest czyszczona (anulowana), żeby zniknęło podświetlenie
        Qt, które można pomylić z kolorem zakładki.
        """
        cursor = self.tab.text.textCursor()
        if cursor.hasSelection():
            cursor.clearSelection()
            self.tab.text.setTextCursor(cursor)
            cursor = self.tab.text.textCursor()
        widget_line = cursor.blockNumber()
        if widget_line < 0 or self.tab.line_map is None or widget_line >= len(self.tab.line_map):
            return
        file_line = self.tab.line_map[widget_line]
        if file_line in self.tab.bookmarks:
            del self.tab.bookmarks[file_line]
            self.tab.set_status(self.tab.t("msg_bookmark_removed").format(n=file_line + 1))
        else:
            self.tab.bookmarks[file_line] = None
            self.tab.set_status(self.tab.t("msg_bookmark_added").format(n=file_line + 1))
        self.refresh_bookmarks_tree()
        self.tab.viewport_controller.rebuild_extra_selections()
        self.tab.viewport_controller.update_current_line_highlight()
        block = self.tab.text.document().findBlockByNumber(widget_line)
        if block.isValid():
            new_cur = QtGui.QTextCursor(block)
            self.tab.text.setTextCursor(new_cur)

    def refresh_bookmarks_tree(self) -> None:
        """Odświeża drzewo zakładek w panelu bocznym."""
        self.tab.bm_tree.clear()
        sorted_keys: list[int] = sorted(self.tab.bookmarks.keys())
        for ln in sorted_keys:
            item = QTreeWidgetItem([f"{ln + 1:,}"])
            item.setData(0, int(Qt.ItemDataRole.UserRole), ln)
            self.tab.bm_tree.addTopLevelItem(item)

    _refresh_bookmarks_tree = refresh_bookmarks_tree

    def refresh_edits_tree(self) -> None:
        """Odświeża drzewo edycji w panelu bocznym."""
        self.tab.ed_tree.clear()
        sorted_keys: list[int] = sorted(self.tab.edit_buffer.keys())
        for ln in sorted_keys:
            item = QTreeWidgetItem([f"{ln + 1:,}"])
            item.setData(0, int(Qt.ItemDataRole.UserRole), ln)
            self.tab.ed_tree.addTopLevelItem(item)

    _refresh_edits_tree = refresh_edits_tree

    def goto_bookmark(self) -> None:
        """Przechodzi do wybranej w drzewie zakładki."""
        item = self.tab.bm_tree.currentItem()
        if not item:
            return
        val = item.data(0, int(Qt.ItemDataRole.UserRole))
        if val is not None:
            self.tab.goto_file_line(int(val))

    _goto_bookmark = goto_bookmark

    def goto_edit(self) -> None:
        """Przechodzi do wybranej w drzewie zmodyfikowanej linii."""
        item = self.tab.ed_tree.currentItem()
        if not item:
            return
        val = item.data(0, int(Qt.ItemDataRole.UserRole))
        if val is not None:
            self.tab.goto_file_line(int(val))

    _goto_edit = goto_edit

    def delete_selected_bookmarks(self) -> None:
        """Usuwa wszystkie zaznaczone w drzewie Zakładki.

        Po usunięciu zaznacza następny element w drzewie (jak w IDE —
        zaznaczenie „przesuwa się" na kolejny wpis, zamiast znikać).
        """
        items = self.tab.bm_tree.selectedItems()
        if not items:
            self.tab.set_status(self.tab.t("msg_no_selection"))
            return
        first_selected_idx: int = self.tab.bm_tree.indexOfTopLevelItem(items[0])
        removed = 0
        for item in items:
            val = item.data(0, int(Qt.ItemDataRole.UserRole))
            if val is not None:
                ln = int(val)
                if ln in self.tab.bookmarks:
                    del self.tab.bookmarks[ln]
                    removed += 1
        if removed:
            self.refresh_bookmarks_tree()
            self.tab.viewport_controller.rebuild_extra_selections()
            self.tab.viewport_controller.update_current_line_highlight()
            self.tab.set_status(self.tab.t("msg_bookmarks_removed").format(n=removed))
            count = self.tab.bm_tree.topLevelItemCount()
            if count > 0:
                next_idx: int = int(min(first_selected_idx, count - 1))
                top_item = self.tab.bm_tree.topLevelItem(next_idx)
                if top_item is not None:
                    self.tab.bm_tree.setCurrentItem(top_item)

    _delete_selected_bookmarks = delete_selected_bookmarks

    def delete_selected_edits(self) -> None:
        """Usuwa wszystkie zaznaczone w drzewie Edycje (czyści bufor dla nich).

        Po usunięciu zaznacza następny element w drzewie (jak w IDE).
        """
        items = self.tab.ed_tree.selectedItems()
        if not items:
            self.tab.set_status(self.tab.t("msg_no_selection"))
            return
        first_selected_idx: int = self.tab.ed_tree.indexOfTopLevelItem(items[0])
        removed = 0
        for item in items:
            val = item.data(0, int(Qt.ItemDataRole.UserRole))
            if val is not None:
                ln = int(val)
                if self.tab.edit_buffer.has(ln):
                    self.tab.edit_buffer.discard(ln)
                    removed += 1
        if removed:
            self.refresh_edits_tree()
            self.tab.reload_current_view()
            self.tab.refresh_status()
            self.tab.set_status(self.tab.t("msg_edits_removed").format(n=removed))
            count = self.tab.ed_tree.topLevelItemCount()
            if count > 0:
                next_idx: int = int(min(first_selected_idx, count - 1))
                top_item = self.tab.ed_tree.topLevelItem(next_idx)
                if top_item is not None:
                    self.tab.ed_tree.setCurrentItem(top_item)

    _delete_selected_edits = delete_selected_edits

    def cmd_next_bookmark(self) -> None:
        """Nawiguje do kolejnej zakładki po aktualnej linii."""
        if not self.tab.bookmarks:
            QMessageBox.information(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_no_bookmarks"))
            return
        cursor = self.tab.text.textCursor()
        block_num = cursor.blockNumber()
        current_file_line: int = -1
        if self.tab.line_map and 0 <= block_num < len(self.tab.line_map):
            current_file_line = self.tab.line_map[block_num]

        sorted_bms: list[int] = sorted(self.tab.bookmarks.keys())
        for ln in sorted_bms:
            if ln > current_file_line:
                self.tab.goto_file_line(ln)
                return
        self.tab.goto_file_line(sorted_bms[0])

    def cmd_prev_bookmark(self) -> None:
        """Nawiguje do poprzedniej zakładki przed aktualną linią."""
        if not self.tab.bookmarks:
            QMessageBox.information(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_no_bookmarks"))
            return
        cursor = self.tab.text.textCursor()
        block_num = cursor.blockNumber()
        current_file_line: int = self.tab.indexer.line_count if self.tab.indexer else 0
        if self.tab.line_map and 0 <= block_num < len(self.tab.line_map):
            current_file_line = self.tab.line_map[block_num]

        sorted_bms: list[int] = sorted(self.tab.bookmarks.keys(), reverse=True)
        for ln in sorted_bms:
            if ln < current_file_line:
                self.tab.goto_file_line(ln)
                return
        self.tab.goto_file_line(sorted_bms[0])

    def cmd_clear_bookmarks(self) -> None:
        """Usuwa wszystkie zakładki z bieżącej karty."""
        if not self.tab.bookmarks:
            return
        self.tab.bookmarks.clear()
        self.refresh_bookmarks_tree()
        self.tab.viewport_controller.rebuild_extra_selections()
        self.tab.viewport_controller.update_current_line_highlight()
