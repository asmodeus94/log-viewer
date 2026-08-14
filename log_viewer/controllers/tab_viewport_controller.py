"""tab_viewport_controller.py — Viewport, scrolling, navigation and ExtraSelections for LogTab."""

from __future__ import annotations

import bisect
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QObject, QPoint
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QProgressDialog

from log_viewer.bitset import bisect_left_custom, bisect_right_custom
from log_viewer.helpers import (
    TAG_BOOKMARK,
    TAG_EDITED,
    TAG_TRUNCATED,
    truncate_for_display,
)

if TYPE_CHECKING:
    from log_viewer.log_tab import LogTab


class ViewportController(QObject):
    """Kontroler odpowiedzialny za wirtualne okno (viewport), przewijanie (scrolling),
    nawigację (goto) oraz podświetlenia linii (ExtraSelections).
    """

    def __init__(self, tab: LogTab):
        super().__init__(tab)
        self.tab = tab

    def rebuild_extra_selections(self) -> None:
        """Publiczny interfejs do przebudowywania zaznaczeń dodatkowych."""
        self._rebuild_extra_selections()

    def update_current_line_highlight(self, force: bool = False) -> None:
        """Publiczny interfejs do aktualizacji podświetlenia bieżącej linii."""
        self._update_current_line_highlight(force=force)

    def goto_file_line(self, ln: int, is_filtered_index: bool = False) -> None:
        """Publiczny interfejs nawigacji do linii pliku."""
        self._goto_file_line(ln, is_filtered_index=is_filtered_index)

    def reload_current_view(self) -> None:
        """Publiczny interfejs do przeładowania bieżącego widoku."""
        self._reload_current_view()

    def load_window(self, at_line: int, force_reload: bool = False) -> None:
        """Publiczny interfejs do załadowania okna linii do widoku edytora."""
        self._load_window(at_line, force_reload=force_reload)

    def _load_window(self, at_line: int, force_reload: bool = False) -> None:
        indexer = self.tab.indexer
        if not indexer:
            return

        filter_lines = self.tab.filter_all_lines
        if self.tab.filter_active and self.tab.filter_results and filter_lines is not None:
            n = len(filter_lines)
            start = max(0, min(at_line, n - 1))
        else:
            start = max(0, min(at_line, max(0, indexer.line_count - 1)))

        if not force_reload and start == self.tab.window_start and self.tab.line_map:
            # Optymalizacja: Pomiń ponowne ładowanie dokładnie tego samego okna
            return

        self.tab.is_loading = True

        # Pokaż progress dialog dla skakania w dużych plikach,
        # bo `indexer.read_lines` -> `offset_of_line` musi przeczytać
        # potencjalnie wiele megabajtów za pomocą readline().
        distance = abs(at_line - self.tab.window_start)
        show_progress = distance > 100000

        progress = None
        if show_progress:
            progress = QProgressDialog(self.tab.t("st_loading"), self.tab.t("btn_cancel"), 0, 0, self.tab.main_window)
            progress.setWindowTitle(self.tab.t("app_title"))
            progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(500)
            progress.show()
            QApplication.processEvents()

        try:
            self._load_window_impl(at_line)
        finally:
            if progress:
                progress.close()

    def _get_filtered_lines(self, chunk_lines: Sequence[int]) -> list[tuple[int, str]]:
        indexer = self.tab.indexer
        if not chunk_lines or not indexer:
            return []

        read_lines = indexer.read_specific_lines(list(chunk_lines))
        context_text_map = {ln: text for ln, text in read_lines}

        lines = []
        for ln in chunk_lines:
            if ln in context_text_map:
                lines.append((ln, context_text_map[ln]))
        return lines

    def _load_window_impl(self, at_line: int) -> None:
        indexer = self.tab.indexer
        edit_buffer = self.tab.edit_buffer
        if indexer is None or edit_buffer is None:
            return

        all_lines = self.tab.filter_all_lines
        if self.tab.filter_active and self.tab.filter_results and all_lines is not None:
            n = len(all_lines)
            start = max(0, min(at_line, n - 1))
            chunk_lines = all_lines[start : start + self.tab.window_size_lines]
            lines = self._get_filtered_lines(chunk_lines)
        else:
            start = max(0, min(at_line, max(0, indexer.line_count - 1)))
            lines = indexer.read_lines(start, self.tab.window_size_lines)

        line_map = [ln for (ln, _t) in lines]
        self.tab.window_start = start
        self.tab.window_lines = lines
        self.tab.line_map = line_map

        text_parts = []
        tag_data: dict[str, list[int]] = {}
        bookmark_widget_lines: list[int] = []
        edited_widget_lines: list[int] = []
        context_widget_lines: list[int] = []
        filter_hit_widget_lines: list[int] = []

        for i, (ln, text) in enumerate(lines):
            display_text, tags = self._prepare_line_for_display(ln, text)
            text_parts.append(display_text)
            for tag in tags:
                if tag not in tag_data:
                    tag_data[tag] = []
                tag_data[tag].append(i)
            if ln in self.tab.bookmarks:
                bookmark_widget_lines.append(i)
            if edit_buffer.has(ln):
                edited_widget_lines.append(i)

            filter_res = self.tab.filter_results
            if self.tab.filter_active and filter_res is not None:
                is_hit = ln in filter_res
                if is_hit:
                    filter_hit_widget_lines.append(i)
                else:
                    context_widget_lines.append(i)

        self.tab.text.setPlainText("\n".join(text_parts))
        cursor = self.tab.text.textCursor()
        doc = self.tab.text.document()
        block = doc.begin()
        i = 0
        tag_lines_set = {tag: set(indices) for tag, indices in tag_data.items()}
        while block.isValid():
            for tag, indices_set in tag_lines_set.items():
                if i in indices_set:
                    self._apply_line_format(block, tag)
            block = block.next()
            i += 1

        self.tab._bookmark_widget_lines = bookmark_widget_lines
        self.tab._edited_widget_lines = edited_widget_lines
        self.tab._context_widget_lines = context_widget_lines
        self.tab._filter_hit_widget_lines = filter_hit_widget_lines
        self.tab.search_extra_sel = None
        self.tab.text.set_line_map(line_map)

        self._rebuild_extra_selections()

        self._update_position_slider()

        if not self.tab.minimap_update_timer.isActive():
            self.tab.minimap_update_timer.start(100)

        if self.tab.follow_active:
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        else:
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)

        self.tab.text.setTextCursor(cursor)
        self.tab.refresh_status()
        self.tab.is_loading = False
        self.tab.last_edge_load_time = 0.0
        self._update_current_line_highlight()

    def _rebuild_extra_selections(self) -> None:
        """Przebudowuje _static_extra_sels i listy indeksów widgetowych
        na podstawie bieżącego line_map. Wywoływana po _load_window_impl,
        _append_lines i _prepend_lines, aby doładowane linie też miały
        poprawne podświetlenie filtra i kontekstu."""
        line_map = self.tab.line_map
        if not line_map:
            self.tab._static_extra_sels = []
            self.tab._filter_hit_widget_lines = []
            self.tab._context_widget_lines = []
            self.tab._bookmark_widget_lines = []
            self.tab._edited_widget_lines = []
            return

        filter_hit_widget_lines: list[int] = []
        context_widget_lines: list[int] = []
        bookmark_widget_lines: list[int] = []
        edited_widget_lines: list[int] = []

        is_filtered = bool(self.tab.filter_active and self.tab.filter_results is not None)
        filter_res = self.tab.filter_results

        for i, ln in enumerate(line_map):
            if ln in self.tab.bookmarks:
                bookmark_widget_lines.append(i)
            if self.tab.edit_buffer and self.tab.edit_buffer.has(ln):
                edited_widget_lines.append(i)
            if is_filtered and filter_res is not None:
                is_hit = ln in filter_res
                if is_hit:
                    filter_hit_widget_lines.append(i)
                else:
                    context_widget_lines.append(i)

        self.tab._filter_hit_widget_lines = filter_hit_widget_lines
        self.tab._context_widget_lines = context_widget_lines
        self.tab._bookmark_widget_lines = bookmark_widget_lines
        self.tab._edited_widget_lines = edited_widget_lines

        # Buduj ExtraSelections dla kontekstu i trafień filtra
        self.tab._static_extra_sels = []
        colors = self.tab.theme_colors
        color_context = colors.get("context", QtGui.QColor("#3a3d3a"))
        color_highlight = colors.get("highlight", QtGui.QColor("#fff176"))
        color_black = colors.get("black", QtGui.QColor("#000000"))
        color_bookmark = colors.get("bookmark", QtGui.QColor("#6a9955"))
        color_edited = colors.get("edited", QtGui.QColor("#ce9178"))

        fmt_context = QtGui.QTextCharFormat()
        fmt_context.setBackground(color_context)

        fmt_highlight = QtGui.QTextCharFormat()
        fmt_highlight.setBackground(color_highlight)
        fmt_highlight.setForeground(color_black)
        fmt_highlight.setProperty(QtGui.QTextFormat.Property.FullWidthSelection, True)

        fmt_bookmark = QtGui.QTextCharFormat()
        fmt_bookmark.setBackground(color_bookmark)

        fmt_edited = QtGui.QTextCharFormat()
        fmt_edited.setBackground(color_edited)

        context_set = set(context_widget_lines)
        filter_hit_set = set(filter_hit_widget_lines)
        bookmark_set = set(bookmark_widget_lines)
        edited_set = set(edited_widget_lines)

        doc = self.tab.text.document()
        block = doc.begin()
        i = 0
        while block.isValid():
            if i in context_set:
                sel = QtWidgets.QTextEdit.ExtraSelection()
                sel.cursor = QtGui.QTextCursor(block)
                sel.cursor.select(QtGui.QTextCursor.SelectionType.LineUnderCursor)
                sel.format = fmt_context
                self.tab._static_extra_sels.append(sel)

            if i in filter_hit_set:
                sel = QtWidgets.QTextEdit.ExtraSelection()
                sel.cursor = QtGui.QTextCursor(block)
                sel.cursor.select(QtGui.QTextCursor.SelectionType.LineUnderCursor)
                sel.format = fmt_highlight
                self.tab._static_extra_sels.append(sel)

            if i in bookmark_set:
                sel = QtWidgets.QTextEdit.ExtraSelection()
                sel.cursor = QtGui.QTextCursor(block)
                sel.cursor.select(QtGui.QTextCursor.SelectionType.LineUnderCursor)
                sel.format = fmt_bookmark
                self.tab._static_extra_sels.append(sel)

            if i in edited_set:
                sel = QtWidgets.QTextEdit.ExtraSelection()
                sel.cursor = QtGui.QTextCursor(block)
                sel.cursor.select(QtGui.QTextCursor.SelectionType.LineUnderCursor)
                sel.format = fmt_edited
                self.tab._static_extra_sels.append(sel)

            block = block.next()
            i += 1

    def _prepare_line_for_display(self, file_line_no: int, original_text: str) -> tuple[str, list[str]]:
        is_edited = False
        if getattr(self.tab, "edit_buffer", None) is not None:
            is_edited = self.tab.edit_buffer.has(file_line_no)
        text = self.tab.edit_buffer.get(file_line_no) if is_edited else original_text
        display_text, was_truncated = truncate_for_display(text, max_length=self.tab.max_display_line_length)
        tags: list[str] = []
        if is_edited:
            tags.append(TAG_EDITED)
        if file_line_no in self.tab.bookmarks:
            tags.append(TAG_BOOKMARK)
        if was_truncated:
            tags.append(TAG_TRUNCATED)
        return display_text, tags

    def _apply_line_format(self, block: QtGui.QTextBlock, tag: str) -> None:
        if not block.isValid():
            return
        cursor = QtGui.QTextCursor(block)
        fmt = cursor.blockCharFormat()

        if tag == TAG_TRUNCATED:
            fmt.setForeground(self.tab.theme_colors.get("truncated", QColor("#6a6a6a")))
            font = fmt.font()
            font.setItalic(True)
            fmt.setFont(font)
        cursor.setBlockCharFormat(fmt)

    def _check_edges(self) -> None:
        if not self.tab.indexer or self.tab.is_loading:
            return
        self.tab.edge_load_timer.start()

    def _do_check_edges(self) -> None:
        indexer = self.tab.indexer
        line_map = self.tab.line_map
        if not indexer or not line_map or self.tab.is_loading:
            return

        now = time.time()
        if (now - self.tab.last_edge_load_time) < 0.1:
            return

        try:
            scrollbar = self.tab.text.verticalScrollBar()
            value = scrollbar.value()
            maximum = scrollbar.maximum()

            if maximum > 0 and value >= maximum - 1000 and line_map:
                current_last_line = line_map[-1]

                filter_lines = self.tab.filter_all_lines
                if self.tab.filter_active and filter_lines is not None:
                    idx = bisect_right_custom(filter_lines, current_last_line)
                    if idx < len(filter_lines):
                        self.tab.is_loading = True
                        self.tab.ignore_scroll_events = True
                        try:
                            chunk_lines = filter_lines[idx : idx + self.tab.window_size_lines]
                            new_lines = self._get_filtered_lines(chunk_lines)
                            if new_lines:
                                self.tab.last_edge_load_time = time.time()
                                self._append_lines(new_lines)
                        finally:
                            self.tab.is_loading = False
                            self._update_current_line_highlight()
                            QtCore.QTimer.singleShot(150, lambda: setattr(self.tab, "ignore_scroll_events", False))
                else:
                    next_start = current_last_line + 1
                    if next_start < indexer.line_count:
                        self.tab.is_loading = True
                        self.tab.ignore_scroll_events = True
                        try:
                            new_lines = indexer.read_lines(next_start, self.tab.window_size_lines)
                            if new_lines:
                                self.tab.last_edge_load_time = time.time()
                                self._append_lines(new_lines)
                        finally:
                            self.tab.is_loading = False
                            self._update_current_line_highlight()
                            QtCore.QTimer.singleShot(150, lambda: setattr(self.tab, "ignore_scroll_events", False))

            elif value <= 1000 and line_map and line_map[0] > 0:
                current_first_line = line_map[0]

                filter_lines = self.tab.filter_all_lines
                if self.tab.filter_active and filter_lines is not None:
                    idx = bisect_left_custom(filter_lines, current_first_line)
                    if idx > 0:
                        self.tab.is_loading = True
                        self.tab.ignore_scroll_events = True
                        try:
                            start_idx = max(0, idx - self.tab.window_size_lines)
                            chunk_lines = filter_lines[start_idx:idx]
                            new_lines = self._get_filtered_lines(chunk_lines)
                            if new_lines:
                                self.tab.last_edge_load_time = time.time()
                                self._prepend_lines(new_lines)
                        finally:
                            self.tab.ignore_scroll_events = False
                            self.tab.is_loading = False
                            self._update_current_line_highlight()
                else:
                    prev_start = max(0, current_first_line - self.tab.window_size_lines)
                    if current_first_line > 0:
                        self.tab.is_loading = True
                        self.tab.ignore_scroll_events = True
                        try:
                            new_lines = indexer.read_lines(prev_start, current_first_line - prev_start)
                            if new_lines:
                                self.tab.last_edge_load_time = time.time()
                                self._prepend_lines(new_lines)
                        finally:
                            self.tab.ignore_scroll_events = False
                            self.tab.is_loading = False
                            self._update_current_line_highlight()
        except (AttributeError, IndexError, ValueError, TypeError, RuntimeError):
            self.tab.is_loading = False
            self._update_current_line_highlight()
            QtCore.QTimer.singleShot(150, lambda: setattr(self.tab, "ignore_scroll_events", False))

    def _append_lines(self, new_lines: list[tuple[int, str]]) -> None:
        if not new_lines:
            return

        scrollbar = self.tab.text.verticalScrollBar()
        old_signals_blocked = scrollbar.blockSignals(True)

        old_value = scrollbar.value()
        line_map = self.tab.line_map if self.tab.line_map is not None else []
        try:
            cursor = self.tab.text.textCursor()
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
            cursor.setCharFormat(QtGui.QTextCharFormat())
            cursor.beginEditBlock()
            for ln, text in new_lines:
                display_text, tags = self._prepare_line_for_display(ln, text)
                cursor.insertText("\n" + display_text)
                block = cursor.block()
                for tag in tags:
                    self._apply_line_format(block, tag)
                line_map.append(ln)
            cursor.endEditBlock()
            if len(line_map) > self.tab.max_display_lines:
                to_remove = len(line_map) - self.tab.max_display_lines
                cursor.beginEditBlock()
                cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
                cursor.movePosition(
                    QtGui.QTextCursor.MoveOperation.NextBlock, QtGui.QTextCursor.MoveMode.KeepAnchor, to_remove
                )
                cursor.removeSelectedText()
                cursor.endEditBlock()
                line_map = line_map[to_remove:]

                old_value -= to_remove

            self.tab.line_map = line_map
            self.tab.text.set_line_map(line_map)
            self._rebuild_extra_selections()
            self._update_current_line_highlight(force=True)
            scrollbar.setValue(max(0, old_value))
            self._update_position_slider()
        finally:
            scrollbar.blockSignals(old_signals_blocked)

    def _prepend_lines(self, new_lines: list[tuple[int, str]]) -> None:
        if not new_lines:
            return

        scrollbar = self.tab.text.verticalScrollBar()
        old_signals_blocked = scrollbar.blockSignals(True)
        line_map = self.tab.line_map if self.tab.line_map is not None else []
        try:
            old_first_file_line = line_map[0] if line_map else 0
            old_val = scrollbar.value()
            cursor = self.tab.text.textCursor()
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.Start)
            cursor.setCharFormat(QtGui.QTextCharFormat())
            cursor.beginEditBlock()
            for ln, text in reversed(new_lines):
                display_text, tags = self._prepare_line_for_display(ln, text)
                cursor.insertText(display_text + "\n")
            for i, (ln, text) in enumerate(new_lines):
                display_text, tags = self._prepare_line_for_display(ln, text)
                block = cursor.document().findBlockByNumber(i)
                if block.isValid():
                    for tag in tags:
                        self._apply_line_format(block, tag)
            cursor.endEditBlock()
            new_line_map = [ln for (ln, _t) in new_lines] + line_map
            if len(new_line_map) > self.tab.max_display_lines:
                to_remove = len(new_line_map) - self.tab.max_display_lines
                cursor.beginEditBlock()
                cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
                cursor.movePosition(QtGui.QTextCursor.MoveOperation.StartOfBlock, QtGui.QTextCursor.MoveMode.MoveAnchor)
                if to_remove > 1:
                    cursor.movePosition(
                        QtGui.QTextCursor.MoveOperation.PreviousBlock,
                        QtGui.QTextCursor.MoveMode.MoveAnchor,
                        to_remove - 1,
                    )
                cursor.movePosition(
                    QtGui.QTextCursor.MoveOperation.PreviousCharacter, QtGui.QTextCursor.MoveMode.MoveAnchor
                )
                cursor.movePosition(QtGui.QTextCursor.MoveOperation.End, QtGui.QTextCursor.MoveMode.KeepAnchor)
                cursor.removeSelectedText()
                cursor.endEditBlock()
                new_line_map = new_line_map[: self.tab.max_display_lines]
            self.tab.line_map = new_line_map
            self.tab.text.set_line_map(new_line_map)
            self._rebuild_extra_selections()
            self._update_current_line_highlight(force=True)
            try:
                idx = bisect.bisect_left(new_line_map, old_first_file_line)
                if idx != len(new_line_map) and new_line_map[idx] == old_first_file_line:
                    self.tab.text.verticalScrollBar().setValue(idx + old_val)
            except (AttributeError, IndexError, ValueError, TypeError):
                pass
            self._update_position_slider()
        finally:
            scrollbar.blockSignals(old_signals_blocked)

    def _on_user_scrolled(self) -> None:
        if self.tab.follow_active:
            self.tab.cmd_toggle_follow()

    def _on_scroll_changed(self, _value: int) -> None:
        if not self.tab.indexer or not self.tab.line_map or self.tab.is_loading or self.tab.ignore_scroll_events:
            return
        self.tab.scroll_debounce_timer.start()

    def _on_minimap_click(self, line_no: int) -> None:
        indexer = self.tab.indexer
        if not indexer or line_no < 0:
            return
        filter_lines = self.tab.filter_all_lines
        if self.tab.filter_active and filter_lines is not None:
            line_no = min(line_no, len(filter_lines) - 1)
            self._goto_file_line(line_no, is_filtered_index=True)
        else:
            line_no = min(line_no, indexer.line_count - 1)
            self._goto_file_line(line_no)

    def _update_slider_from_scroll(self) -> None:
        indexer = self.tab.indexer
        line_map = self.tab.line_map
        if not indexer or not line_map or self.tab.is_loading:
            return
        try:
            cursor = self.tab.text.cursorForPosition(QPoint(0, 5))
            widget_line = cursor.blockNumber()
            if 0 <= widget_line < len(line_map):
                file_line = line_map[widget_line]
                filter_lines = self.tab.filter_all_lines
                if (
                    getattr(self.tab, "filter_active", False)
                    and getattr(self.tab, "filter_results", None) is not None
                    and filter_lines is not None
                ):
                    total = max(1, len(filter_lines))
                    idx = bisect_left_custom(filter_lines, file_line)
                    pct = int((idx / total) * 1000)
                else:
                    total = max(1, indexer.line_count)
                    pct = int((file_line / total) * 1000)
                self.tab.pct_label.setText(f"{pct // 10}%")
                if not self.tab.minimap_update_timer.isActive():
                    self.tab.minimap_update_timer.start(100)
        except (AttributeError, IndexError, ValueError, TypeError, ZeroDivisionError, RuntimeError):
            pass

    def _update_position_slider(self) -> None:
        indexer = self.tab.indexer
        if not indexer or indexer.line_count == 0:
            self.tab.pct_label.setText("0%")
            return
        filter_lines = self.tab.filter_all_lines
        if getattr(self.tab, "filter_active", False) and filter_lines is not None:
            total = max(1, len(filter_lines))
        else:
            total = max(1, indexer.line_count)

        scrollbar = self.tab.text.verticalScrollBar()
        if scrollbar.value() >= scrollbar.maximum() - 5:
            pct = 1000
        else:
            pct = int((self.tab.window_start / total) * 1000)

        self.tab.pct_label.setText(f"{pct // 10}%")

    def get_display_text(self, file_line_no: int, widget_line_idx: int) -> str:
        if self.tab.edit_buffer and self.tab.edit_buffer.has(file_line_no):
            edit = self.tab.edit_buffer.get(file_line_no)
            if edit is not None:
                return edit
        if widget_line_idx < len(self.tab.window_lines):
            return self.tab.window_lines[widget_line_idx][1]
        return ""

    _get_display_text = get_display_text

    def _highlight_and_scroll(self, widget_line_no: int) -> None:
        block_cursor = QtGui.QTextCursor(self.tab.text.document().findBlockByNumber(widget_line_no))
        sel_cursor = QtGui.QTextCursor(block_cursor)
        sel_cursor.select(QtGui.QTextCursor.SelectionType.LineUnderCursor)

        sel = QtWidgets.QTextEdit.ExtraSelection()
        sel.cursor = sel_cursor
        colors = self.tab.theme_colors
        sel.format.setBackground(colors.get("search_active", QColor("#ff8c00")))
        sel.format.setForeground(colors.get("black", QColor("#000000")))
        sel.format.setProperty(QtGui.QTextFormat.Property.FullWidthSelection, True)
        self.tab.search_extra_sel = sel

        block_cursor.clearSelection()
        self.tab.text.setTextCursor(block_cursor)

        self._update_current_line_highlight()
        self.tab.text.viewport().update()

    def _update_current_line_highlight(self, force: bool = False) -> None:
        if self.tab.is_loading and not force:
            return

        if hasattr(self.tab.text, "set_bookmarks"):
            self.tab.text.set_bookmarks(set(self.tab.bookmarks.keys()))

        sels: list[QtWidgets.QTextEdit.ExtraSelection] = list(getattr(self.tab, "_static_extra_sels", []))

        bookmark_set = getattr(self.tab, "_bookmark_widget_lines", [])
        edited_set = getattr(self.tab, "_edited_widget_lines", [])
        filter_hit_set = getattr(self.tab, "_filter_hit_widget_lines", [])

        color_current_line = self.tab.theme_colors.get("current_line", QColor("#2a2d2e"))

        search_block = -1
        search_sel = self.tab.search_extra_sel
        if search_sel is not None:
            sels.append(search_sel)
            search_block = search_sel.cursor.blockNumber()

        current_block = self.tab.text.textCursor().blockNumber()
        if (
            current_block not in bookmark_set
            and current_block not in edited_set
            and current_block not in filter_hit_set
            and current_block != search_block
        ):
            cur = QtWidgets.QTextEdit.ExtraSelection()
            cur_cursor = QtGui.QTextCursor(self.tab.text.textCursor())
            cur_cursor.select(QtGui.QTextCursor.SelectionType.LineUnderCursor)
            cur.cursor = cur_cursor
            cur.format.setBackground(color_current_line)
            cur.format.setProperty(QtGui.QTextFormat.Property.FullWidthSelection, True)
            sels.append(cur)

        self.tab.text.setExtraSelections(sels)

    def cmd_goto(self) -> None:
        indexer = self.tab.indexer
        if not indexer:
            QMessageBox.information(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_no_file"))
            return

        self.tab.cancel_follow_if_active()

        answer, ok = QInputDialog.getText(
            self.tab.main_window,
            self.tab.t("dlg_goto_title"),
            self.tab.t("dlg_goto_prompt"),
            QtWidgets.QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok or not answer:
            return
        answer = answer.strip()
        if answer.startswith("b:") or answer.startswith("B:"):
            try:
                byte_offset = int(answer[2:])
            except ValueError:
                QMessageBox.critical(self.tab.main_window, self.tab.t("app_title"), "Invalid byte offset")
                return
            byte_offset = max(0, min(byte_offset, indexer.size))
            line_no, _ = indexer.line_at_byte_offset(byte_offset)
        else:
            try:
                line_no = int(answer)
            except ValueError:
                QMessageBox.critical(self.tab.main_window, self.tab.t("app_title"), "Invalid line number")
                return
            line_no = max(1, line_no) - 1
            line_no = min(line_no, max(0, indexer.line_count - 1))

        filter_lines = self.tab.filter_all_lines
        if self.tab.filter_active and filter_lines is not None:
            idx = bisect_left_custom(filter_lines, line_no)
            self._load_window(at_line=idx)
        else:
            self._load_window(at_line=line_no)
        self.tab.text.verticalScrollBar().setValue(0)

    def cmd_goto_start(self) -> None:
        if not self.tab.indexer:
            return
        self.tab.cancel_follow_if_active()
        self._load_window(at_line=0)

    def cmd_goto_end(self) -> None:
        indexer = self.tab.indexer
        if not indexer:
            return
        self.tab.cancel_follow_if_active()
        filter_lines = self.tab.filter_all_lines
        if self.tab.filter_active and filter_lines is not None:
            total = max(1, len(filter_lines))
            start = max(0, total - self.tab.window_size_lines)
            self._load_window(at_line=start)
        else:
            total = max(1, indexer.line_count)
            start = max(0, total - self.tab.window_size_lines)
            self._load_window(at_line=start)

        scrollbar = self.tab.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _goto_file_line(self, ln: int, is_filtered_index: bool = False) -> None:
        self.tab.cancel_follow_if_active()
        offset = self.tab.window_size_lines // 2

        self.tab.last_edge_load_time = 0.0

        target_idx_in_map = 0
        line_map = self.tab.line_map
        filter_lines = self.tab.filter_all_lines

        if line_map:
            try:
                target_ln = (
                    filter_lines[ln]
                    if (self.tab.filter_active and is_filtered_index and filter_lines is not None)
                    else ln
                )
                if line_map[0] <= target_ln <= line_map[-1]:
                    idx_in_map = bisect.bisect_left(line_map, target_ln)
                    if idx_in_map != len(line_map) and line_map[idx_in_map] == target_ln:
                        self.tab.text.verticalScrollBar().setValue(idx_in_map)
                        target_idx_in_map = idx_in_map

                        block = self.tab.text.document().findBlockByNumber(target_idx_in_map)
                        if block.isValid():
                            new_cur = QtGui.QTextCursor(block)
                            self.tab.text.setTextCursor(new_cur)
                        return
            except (AttributeError, IndexError, ValueError, TypeError):
                pass

        if self.tab.filter_active and filter_lines is not None:
            if is_filtered_index:
                idx = ln
            else:
                idx = bisect_left_custom(filter_lines, ln)
            start_idx = max(0, idx - offset)
            self._load_window(at_line=start_idx)
            current_line_map = self.tab.line_map if self.tab.line_map is not None else []
            try:
                target_ln = filter_lines[idx] if idx < len(filter_lines) else -1
                idx_in_map = bisect.bisect_left(current_line_map, target_ln)
                if idx_in_map != len(current_line_map) and current_line_map[idx_in_map] == target_ln:
                    self.tab.text.verticalScrollBar().setValue(idx_in_map)
                    target_idx_in_map = idx_in_map
                else:
                    self.tab.text.verticalScrollBar().setValue(0)
            except (AttributeError, IndexError, ValueError, TypeError):
                self.tab.text.verticalScrollBar().setValue(0)
        else:
            start_ln = max(0, ln - offset)
            self._load_window(at_line=start_ln)
            current_line_map = self.tab.line_map if self.tab.line_map is not None else []
            try:
                idx_in_map = bisect.bisect_left(current_line_map, ln)
                if idx_in_map != len(current_line_map) and current_line_map[idx_in_map] == ln:
                    self.tab.text.verticalScrollBar().setValue(idx_in_map)
                    target_idx_in_map = idx_in_map
                else:
                    self.tab.text.verticalScrollBar().setValue(0)
            except (AttributeError, IndexError, ValueError, TypeError):
                self.tab.text.verticalScrollBar().setValue(0)

        block = self.tab.text.document().findBlockByNumber(target_idx_in_map)
        if block.isValid():
            new_cur = QtGui.QTextCursor(block)
            self.tab.text.setTextCursor(new_cur)

    def _reload_current_view(self) -> None:
        if not self.tab.indexer:
            return
        scrollbar = self.tab.text.verticalScrollBar()

        # Odtwórz aktualnie *widoczną* pierwszą linię na ekranie (a nie line_map[0] jeśli ekran jest przewinięty)
        # Zamiast polegać na old_val (które ucina się przy limitach bufora), po prostu startujemy okno od widocznego top_line.
        top_line = self.tab.window_start
        line_map = self.tab.line_map
        if line_map:
            cursor = self.tab.text.cursorForPosition(QPoint(0, 5))
            widget_line = cursor.blockNumber()
            if widget_line < 0 or widget_line >= len(line_map):
                widget_line = scrollbar.value()  # Fallback

            if 0 <= widget_line < len(line_map):
                file_line = line_map[widget_line]
                filter_lines = self.tab.filter_all_lines
                if self.tab.filter_active and filter_lines is not None:
                    try:
                        idx = bisect_left_custom(filter_lines, file_line)
                        top_line = idx
                    except (AttributeError, IndexError, ValueError, TypeError):
                        pass
                else:
                    top_line = file_line

        self._load_window(at_line=top_line, force_reload=True)
        # Ponieważ okno zaczyna się dokładnie w miejscu widocznej wcześniej linii,
        # suwak wewnątrz nowo utworzonego widoku powinien być na samej górze.
        scrollbar.setValue(0)
