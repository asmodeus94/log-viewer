from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QMessageBox, QTreeWidgetItem
from PySide6 import QtGui


class BookmarkController(QObject):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab

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
        if widget_line < 0 or widget_line >= len(self.tab.line_map):
            return
        file_line = self.tab.line_map[widget_line]
        if file_line in self.tab.bookmarks:
            del self.tab.bookmarks[file_line]
            self.tab._status(self.tab.t("msg_bookmark_removed").format(n=file_line + 1))
        else:
            self.tab.bookmarks[file_line] = None
            self.tab._status(self.tab.t("msg_bookmark_added").format(n=file_line + 1))
        self._refresh_bookmarks_tree()
        self.tab._reload_current_view()
        block = self.tab.text.document().findBlockByNumber(widget_line)
        if block.isValid():
            new_cur = QtGui.QTextCursor(block)
            self.tab.text.setTextCursor(new_cur)

    def _refresh_bookmarks_tree(self) -> None:
        self.tab.bm_tree.clear()
        for ln in sorted(self.tab.bookmarks.keys()):
            item = QTreeWidgetItem([f"{ln + 1:,}"])
            item.setData(0, Qt.UserRole, ln)
            self.tab.bm_tree.addTopLevelItem(item)

    def _refresh_edits_tree(self) -> None:
        self.tab.ed_tree.clear()
        for ln in sorted(self.tab.edit_buffer._edits.keys()):
            item = QTreeWidgetItem([f"{ln + 1:,}"])
            item.setData(0, Qt.UserRole, ln)
            self.tab.ed_tree.addTopLevelItem(item)

    def _goto_bookmark(self) -> None:
        item = self.tab.bm_tree.currentItem()
        if not item:
            return
        ln = item.data(0, Qt.UserRole)
        self.tab._goto_file_line(ln)

    def _goto_edit(self) -> None:
        item = self.tab.ed_tree.currentItem()
        if not item:
            return
        ln = item.data(0, Qt.UserRole)
        self.tab._goto_file_line(ln)

    def _delete_selected_bookmarks(self) -> None:
        """Usuwa wszystkie zaznaczone w drzewie Zakładki.

        Po usunięciu zaznacza następny element w drzewie (jak w IDE —
        zaznaczenie „przesuwa się" na kolejny wpis, zamiast znikać).
        """
        items = self.tab.bm_tree.selectedItems()
        if not items:
            self.tab._status(self.tab.t("msg_no_selection"))
            return
        first_selected_idx = self.tab.bm_tree.indexOfTopLevelItem(items[0])
        removed = 0
        for item in items:
            ln = item.data(0, Qt.UserRole)
            if ln in self.tab.bookmarks:
                del self.tab.bookmarks[ln]
                removed += 1
        if removed:
            self._refresh_bookmarks_tree()
            self.tab._reload_current_view()
            self.tab._status(self.tab.t("msg_bookmarks_removed").format(n=removed))
            count = self.tab.bm_tree.topLevelItemCount()
            if count > 0:
                next_idx = min(first_selected_idx, count - 1)
                self.tab.bm_tree.setCurrentItem(self.tab.bm_tree.topLevelItem(next_idx))

    def _delete_selected_edits(self) -> None:
        """Usuwa wszystkie zaznaczone w drzewie Edycje (czyści bufor dla nich).

        Po usunięciu zaznacza następny element w drzewie (jak w IDE).
        """
        items = self.tab.ed_tree.selectedItems()
        if not items:
            self.tab._status(self.tab.t("msg_no_selection"))
            return
        first_selected_idx = self.tab.ed_tree.indexOfTopLevelItem(items[0])
        removed = 0
        for item in items:
            ln = item.data(0, Qt.UserRole)
            if self.tab.edit_buffer.has(ln):
                self.tab.edit_buffer.discard(ln)
                removed += 1
        if removed:
            self._refresh_edits_tree()
            self.tab._reload_current_view()
            self.tab._refresh_status()
            self.tab._status(self.tab.t("msg_edits_removed").format(n=removed))
            count = self.tab.ed_tree.topLevelItemCount()
            if count > 0:
                next_idx = min(first_selected_idx, count - 1)
                self.tab.ed_tree.setCurrentItem(self.tab.ed_tree.topLevelItem(next_idx))

    def cmd_next_bookmark(self) -> None:
        if not self.tab.bookmarks:
            QMessageBox.information(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_no_bookmarks"))
            return
        cursor = self.tab.text.textCursor()
        current_file_line = self.tab.line_map[cursor.blockNumber()] if self.tab.line_map else -1
        sorted_bms = sorted(self.tab.bookmarks.keys())
        for ln in sorted_bms:
            if ln > current_file_line:
                self.tab._goto_file_line(ln)
                return
        self.tab._goto_file_line(sorted_bms[0])

    def cmd_prev_bookmark(self) -> None:
        if not self.tab.bookmarks:
            QMessageBox.information(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_no_bookmarks"))
            return
        cursor = self.tab.text.textCursor()
        current_file_line = self.tab.line_map[cursor.blockNumber()] if self.tab.line_map else (self.tab.indexer.line_count if self.tab.indexer else 0)
        sorted_bms = sorted(self.tab.bookmarks.keys(), reverse=True)
        for ln in sorted_bms:
            if ln < current_file_line:
                self.tab._goto_file_line(ln)
                return
        self.tab._goto_file_line(sorted_bms[0])

    def cmd_clear_bookmarks(self) -> None:
        if not self.tab.bookmarks:
            return
        self.tab.bookmarks.clear()
        self._refresh_bookmarks_tree()
        self.tab._reload_current_view()
