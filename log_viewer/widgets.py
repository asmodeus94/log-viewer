"""Widgety PySide6 — LineNumberArea, LogPlainTextEdit, SettingsDialog, SearchResultsModel, MiniMap."""

from __future__ import annotations

import bisect
from collections import OrderedDict
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

from PySide6 import QtCore, QtGui
from PySide6.QtCore import QAbstractListModel, QEvent, QModelIndex, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFocusEvent,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QResizeEvent,
    QTextCursor,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QSizePolicy,
    QTextEdit,
    QWidget,
)

from .bitset import Bitset
from .formatters import FORMATTER_MAX_CHARS, FORMATTERS, format_log
from .helpers import THEME, THEME_DARK, fmt_size
from .highlighters import MAX_HIGHLIGHT_DOC_CHARS, StructuredCodeHighlighter
from .ui.ui_format_dialog import Ui_FormatDialog
from .ui.ui_settings_dialog import Ui_SettingsDialog

if TYPE_CHECKING:
    from .log_tab import LogTab

MAX_DIALOG_EXTRA_SELECTIONS = 1500


def sync_inactive_palette(widget: QWidget) -> None:
    """Kopiuje kolory palety Active do Inactive, aby podświetlenie i tekst nie bladły po utracie fokusu."""
    pal = widget.palette()
    for role in (
        QtGui.QPalette.ColorRole.Highlight,
        QtGui.QPalette.ColorRole.HighlightedText,
        QtGui.QPalette.ColorRole.Base,
        QtGui.QPalette.ColorRole.Text,
    ):
        pal.setColor(
            QtGui.QPalette.ColorGroup.Inactive,
            role,
            pal.color(QtGui.QPalette.ColorGroup.Active, role),
        )
    widget.setPalette(pal)


def handle_shift_wheel_scroll(
    scroll_area: QAbstractScrollArea,
    event: QWheelEvent,
) -> bool:
    """Obsługuje przewijanie poziome przy przytrzymanym klawiszu Shift i ruchu kółkiem myszy.

    Działa wieloplatformowo (Windows, Linux, macOS) niezależnie od tego, czy system
    operacyjny przesyła deltę w osi Y (Windows/Linux), czy zamienia ją na oś X (macOS).
    """
    modifiers = event.modifiers()
    if (modifiers & Qt.KeyboardModifier.ShiftModifier) and not (modifiers & Qt.KeyboardModifier.ControlModifier):
        hbar = scroll_area.horizontalScrollBar()
        if hbar is not None:
            QApplication.sendEvent(hbar, event)
            event.accept()
            return True
    return False


class _ShiftWheelScrollFilter(QObject):
    """Filtr zdarzeń przekierowujący Shift + kółko myszy do poziomego paska przewijania."""

    def __init__(self, scroll_area: QAbstractScrollArea) -> None:
        super().__init__(scroll_area)
        self._scroll_area = scroll_area

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:
        return bool(
            event.type() == QEvent.Type.Wheel
            and isinstance(event, QWheelEvent)
            and handle_shift_wheel_scroll(self._scroll_area, event)
        )


def install_shift_wheel_scroll(scroll_area: QAbstractScrollArea) -> QObject:
    """Instaluje obsługę poziomego przewijania (Shift + Scroll) na podanym QAbstractScrollArea."""
    flt = _ShiftWheelScrollFilter(scroll_area)
    scroll_area.installEventFilter(flt)
    viewport = scroll_area.viewport()
    if viewport is not None:
        viewport.installEventFilter(flt)
    return flt


def _find_parent_tab(widget: QWidget | None) -> LogTab | None:
    """Wyszukuje nadrzędny obiekt LogTab dla podanego widgetu."""
    cur: QObject | None = widget
    while cur is not None:
        if (hasattr(cur, "get_display_text") and hasattr(cur, "line_map")) or hasattr(cur, "last_formatter"):
            return cast("LogTab", cur)
        cur = cur.parent()
    return None


def get_full_line_text_at_cursor(editor: QPlainTextEdit | QTextEdit, tab: LogTab | None = None) -> str:
    """Pobiera pełny (nieucięty przez widok) tekst linii w miejscu kursora."""
    target_tab = tab if tab is not None else _find_parent_tab(editor)
    cursor = editor.textCursor()
    widget_line = cursor.blockNumber()
    if target_tab is not None and hasattr(target_tab, "get_display_text") and hasattr(target_tab, "line_map"):
        line_map = target_tab.line_map
        if line_map is not None and 0 <= widget_line < len(line_map):
            file_line = line_map[widget_line]
            full_text = target_tab.get_display_text(file_line, widget_line)
            if full_text:
                return full_text

    sel_cursor = QTextCursor(cursor)
    sel_cursor.select(QTextCursor.SelectionType.LineUnderCursor)
    return sel_cursor.selectedText().replace("\u2029", "\n")


class LineNumberArea(QWidget):
    """Widget rysujący numery linii zsynchronizowany z QPlainTextEdit."""

    def __init__(self, editor: LogPlainTextEdit):
        super().__init__(editor)
        self._editor = editor
        self._line_map: list[int] = []
        self._bookmarks: set[int] = set()
        self._width_digits = 5
        # Pocache'owane obiekty rysujące do eliminacji alokacji w paintEvent
        self._bg_color = QColor("#f0f0f0")
        self._text_pen = QPen(QColor("#666666"))
        self._bookmark_brush = QBrush(QColor("#6a9955"))
        self._bookmark_pen = QPen(Qt.PenStyle.NoPen)
        self.update_width()

    def set_line_map(self, line_map: list[int]) -> None:
        self._line_map = line_map
        if line_map:
            max_line = max(line_map) + 1 if line_map else 1
            digits = max(5, len(str(max_line)))
            if digits != self._width_digits:
                self._width_digits = digits
                self.update_width()
        self.update()

    def set_bookmarks(self, bookmarks: set[int]) -> None:
        if self._bookmarks != bookmarks:
            self._bookmarks = set(bookmarks)
            self.update()

    def update_width(self) -> None:
        fm = QFontMetrics(self._editor.font())
        sample_text = f"{10**self._width_digits - 1:,}"
        width = fm.horizontalAdvance(sample_text) + 16
        self.setFixedWidth(width)

    def sizeHint(self) -> QSize:
        return QSize(self.width(), 0)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(event.rect(), self._bg_color)
        painter.setPen(self._text_pen)
        painter.setFont(self._editor.font())

        block = self._editor.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self._editor.blockBoundingGeometry(block).translated(self._editor.contentOffset()).top())
        bottom = top + round(self._editor.blockBoundingRect(block).height())
        font_height = self._editor.fontMetrics().height()

        has_bookmarks = len(self._bookmarks) > 0

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top() and block_number < len(self._line_map):
                raw_file_line = self._line_map[block_number]
                file_line_str = f"{raw_file_line + 1:,}"

                # Rysuj kropkę wskaźnika zakładek na marginesie
                if has_bookmarks and raw_file_line in self._bookmarks:
                    painter.setPen(self._bookmark_pen)
                    painter.setBrush(self._bookmark_brush)
                    cy = top + (font_height // 2)
                    painter.drawEllipse(QPoint(6, cy), 3, 3)
                    painter.setPen(self._text_pen)

                painter.drawText(
                    0,
                    top,
                    self.width() - 8,
                    font_height,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    file_line_str,
                )
            block = block.next()
            block_number += 1
            top = bottom
            bottom = top + round(self._editor.blockBoundingRect(block).height())


class LogPlainTextEdit(QPlainTextEdit):
    """QPlainTextEdit z wbudowanym LineNumberArea i obsługą drag&drop plików."""

    files_dropped = Signal(list)
    user_scrolled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.custom_context_actions: list[QAction] = []
        self._line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self._update_line_number_area_width(0)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setReadOnly(True)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(10)

        self.setFont(font)
        self.setAcceptDrops(True)
        self._setup_actions()

        # Wymuszamy, aby nieaktywny stan kontrolki miał identyczne kolory
        # jak aktywny (tło, podświetlenie, tekst), żeby np. wynik wyszukiwania
        # (żółte tło) nie bladł, gdy focus jest na dolnej liście wyników.
        sync_inactive_palette(self)

    @property
    def line_number_area(self) -> LineNumberArea:
        """Zwraca skojarzony widget rysujący numery linii."""
        return self._line_number_area

    def _setup_actions(self) -> None:
        self._action_copy = QAction(self)
        self._action_copy.setShortcut(QKeySequence("Ctrl+C"))
        self._action_copy.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self._action_copy.setShortcutVisibleInContextMenu(True)
        self._action_copy.triggered.connect(self._do_copy)
        self.addAction(self._action_copy)

        self._action_copy_line = QAction(self)
        self._action_copy_line.setShortcut(QKeySequence("Ctrl+Shift+L"))
        self._action_copy_line.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self._action_copy_line.setShortcutVisibleInContextMenu(True)
        self._action_copy_line.triggered.connect(self._do_copy_line)
        self.addAction(self._action_copy_line)

        self._action_format_sel = QAction(self)
        self._action_format_sel.setShortcut(QKeySequence("Ctrl+I"))
        self._action_format_sel.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self._action_format_sel.setShortcutVisibleInContextMenu(True)
        self._action_format_sel.triggered.connect(self._do_format_selection)
        self.addAction(self._action_format_sel)

        self._action_format_line = QAction(self)
        self._action_format_line.setShortcut(QKeySequence("Ctrl+Shift+I"))
        self._action_format_line.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self._action_format_line.setShortcutVisibleInContextMenu(True)
        self._action_format_line.triggered.connect(self._do_format_line)
        self.addAction(self._action_format_line)

        self.selectionChanged.connect(self._update_actions_state)

    def _update_actions_state(self) -> None:
        has_sel = self.textCursor().hasSelection()
        self._action_copy.setEnabled(has_sel)
        self._action_format_sel.setEnabled(has_sel)

    def _do_copy(self) -> None:
        if self.textCursor().hasSelection():
            self.copy()

    def _do_copy_line(self) -> None:
        selected_text = get_full_line_text_at_cursor(self)
        if selected_text:
            QApplication.clipboard().setText(selected_text)

    def _do_format_selection(self) -> None:
        if not self.textCursor().hasSelection():
            return
        app = self.window() if hasattr(self, "window") else None
        if app and hasattr(app, "cmd_format_selection"):
            app.cmd_format_selection()

    def _do_format_line(self) -> None:
        tab = _find_parent_tab(self)
        selected_text = get_full_line_text_at_cursor(self, tab)
        if not selected_text.strip():
            return

        last_formatter = tab.last_formatter if (tab is not None and hasattr(tab, "last_formatter")) else "JSON"
        dialog = FormatDialog(self, selected_text, last_formatter)
        dialog.exec()

        if tab is not None and hasattr(tab, "last_formatter"):
            tab.last_formatter = dialog.get_selected_formatter()

        dialog.deleteLater()

    def set_line_map(self, line_map: list[int]) -> None:
        self._line_number_area.set_line_map(line_map)

    def set_bookmarks(self, bookmarks: set[int]) -> None:
        self._line_number_area.set_bookmarks(bookmarks)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            cr.left(),
            cr.top(),
            self._line_number_area.width(),
            cr.height(),
        )

    def update_line_number_area_width(self, new_block_count: int = 0) -> None:
        self._update_line_number_area_width(new_block_count)

    def _update_line_number_area_width(self, _new_block_count: int = 0) -> None:
        self.setViewportMargins(self._line_number_area.width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QtCore.QRect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def wheelEvent(self, e: QtGui.QWheelEvent) -> None:
        if handle_shift_wheel_scroll(self, e):
            return
        # Wykrycie przewijania kółkiem myszy i zatrzymanie np. trybu follow
        self.user_scrolled.emit()
        super().wheelEvent(e)

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                paths.append(local)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        app = self.window() if hasattr(self, "window") else None
        t = getattr(app, "t", lambda k: k)

        menu = QMenu(self)

        if not self.textCursor().hasSelection():
            cursor = self.cursorForPosition(event.pos())
            self.setTextCursor(cursor)

        self._action_copy.setText(t("mi_copy"))
        self._action_copy_line.setText(t("mi_copy_line"))
        self._action_format_sel.setText(t("mi_format_selection"))
        self._action_format_line.setText(t("mi_format_line"))

        self._update_actions_state()

        menu.addAction(self._action_copy)
        menu.addAction(self._action_copy_line)
        menu.addAction(self._action_format_sel)
        menu.addAction(self._action_format_line)

        if self.custom_context_actions:
            menu.addSeparator()
            for action in self.custom_context_actions:
                i18n_key = action.property("i18n_key")
                if i18n_key:
                    action.setText(t(i18n_key))
                menu.addAction(action)

        menu.exec(event.globalPos())


class SettingsDialog(QDialog):
    """Dialog zmiany fontu i parametrów wyświetlania."""

    def __init__(self, parent: QWidget | None, app: Any) -> None:
        super().__init__(parent)
        self.ui = Ui_SettingsDialog()
        self.ui.setupUi(self)
        self._app = app
        self.t = app.t
        self.setWindowTitle(self.t("dlg_settings_title"))

        self.font_combo = self.ui.font_combo
        self.size_spin = self.ui.size_spin
        self.ws_spin = self.ui.ws_spin
        self.md_spin = self.ui.md_spin
        self.ml_spin = self.ui.ml_spin
        self.ii_spin = self.ui.ii_spin

        # Inicjalizacja wartości z configu
        if app.font_family:
            self.font_combo.setCurrentFont(QFont(app.font_family))
        self.size_spin.setValue(app.font_size)
        self.ws_spin.setValue(app.window_size_lines)
        self.md_spin.setValue(app.max_display_lines)
        self.ml_spin.setValue(app.max_display_line_length)
        self.ii_spin.setValue(app.index_interval_bytes // (1024 * 1024))

        # Ustaw etykiety z i18n
        self.ui.lbl_font_family.setText(self.t("lbl_font_family"))
        self.ui.lbl_font_size.setText(self.t("lbl_font_size"))
        self.ui.lbl_window_size.setText(self.t("lbl_window_size"))
        self.ui.lbl_max_display_lines.setText(self.t("lbl_max_display_lines"))
        self.ui.lbl_max_line_length.setText(self.t("lbl_max_line_length"))
        self.ui.lbl_index_interval.setText(self.t("lbl_index_interval"))

    def get_values(self) -> tuple[str, int, int, int, int, int]:
        family = self.font_combo.currentFont().family()
        return (
            family,
            self.size_spin.value(),
            self.ws_spin.value(),
            self.md_spin.value(),
            self.ml_spin.value(),
            self.ii_spin.value() * 1024 * 1024,
        )


class SearchResultsModel(QAbstractListModel):
    """
    Model dla QListView wyświetlający wyniki wyszukiwania.

    Używa QAbstractListModel z canFetchMore/fetchMore, aby doładowywać
    kolejne elementy po przescrollowaniu, zapobiegając opóźnieniom.

    Każdy wynik to krotka (line_no, text). DisplayRole pokazuje
    "  linia: tekst" (przycięty do 200 znaków). UserRole zwraca line_no.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._all_results: list[int | tuple[int, str]] | Bitset = []
        self._visible_count = 0
        self._batch_size = 1000
        self._indexer: Any = None
        self._line_cache: OrderedDict[int, str] = OrderedDict()
        self._max_cache_size: int = 1024

        self._color_error = QColor(THEME_DARK["error"])
        self._color_warn = QColor(THEME_DARK["warn"])
        self._color_info = QColor(THEME_DARK["info"])
        self._color_debug = QColor(THEME_DARK["debug"])

    def set_results(
        self,
        results: Sequence[int | tuple[int, str]] | Bitset | None,
        indexer: object | None = None,
    ) -> None:
        """Zastępuje wszystkie wyniki. Wywołuje beginResetModel/endResetModel."""
        self.beginResetModel()
        self._line_cache.clear()
        if results is None:
            self._all_results = []
        elif isinstance(results, (Bitset, list)):
            self._all_results = results
        else:
            self._all_results = list(results)
        self._indexer = indexer
        self._visible_count = min(len(self._all_results), self._batch_size)
        self.endResetModel()

    def append_results(self, results: list[tuple[int, str]]) -> None:
        """Dodaje wyniki na końcu. Wywołuje beginInsertRows/endInsertRows."""
        if not results:
            return

        start = self._visible_count
        self.beginInsertRows(QModelIndex(), start, start + len(results) - 1)

        if isinstance(self._all_results, list):
            self._all_results.extend(results)
        else:
            # Wysokowydajna obsługa dla struktury Bitset (zapobiega alokacji ogromnej listy)
            max_line = max(r[0] for r in results)
            if max_line >= self._all_results.size:
                self._all_results.resize(max_line + 1)
            self._all_results.update_indices(r[0] for r in results)

        self._visible_count += len(results)
        self.endInsertRows()

    # noinspection PyMethodOverriding
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return self._visible_count

    # noinspection PyMethodOverriding
    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:
        if parent.isValid():
            return False
        return self._visible_count < len(self._all_results)

    # noinspection PyMethodOverriding
    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:
        if parent.isValid():
            return
        remainder = len(self._all_results) - self._visible_count
        items_to_fetch = min(remainder, self._batch_size)
        if items_to_fetch <= 0:
            return

        self.beginInsertRows(QModelIndex(), self._visible_count, self._visible_count + items_to_fetch - 1)
        self._visible_count += items_to_fetch
        self.endInsertRows()

    def ensure_visible(self, target_row: int) -> None:
        """Wymusza wirtualne wyrenderowanie zasięgu aż do podanego wiersza w czasie O(1)."""
        if target_row < self._visible_count:
            return

        if target_row >= len(self._all_results):
            target_row = len(self._all_results) - 1
            if target_row < self._visible_count:
                return

        new_visible = min(target_row + self._batch_size, len(self._all_results))
        items_to_fetch = new_visible - self._visible_count

        self.beginInsertRows(QModelIndex(), self._visible_count, self._visible_count + items_to_fetch - 1)
        self._visible_count = new_visible
        self.endInsertRows()

    # noinspection PyMethodOverriding
    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or index.row() >= self._visible_count:
            return None
        item = self._all_results[index.row()]
        if isinstance(item, tuple):
            line_no, text = item
        else:
            line_no = item
            text = None

        if role == Qt.ItemDataRole.UserRole:
            return line_no
        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if text is None:
            if line_no in self._line_cache:
                text = self._line_cache[line_no]
                self._line_cache.move_to_end(line_no)
            else:
                if hasattr(self, "_indexer") and self._indexer:
                    lines = self._indexer.read_lines(line_no, 1)
                    text = lines[0][1] if lines else ""
                else:
                    text = ""
                self._line_cache[line_no] = text
                if len(self._line_cache) > self._max_cache_size:
                    self._line_cache.popitem(last=False)

        display = text[:200]
        if len(text) > 200:
            display += "..."
        formatted_line = f"{line_no + 1:>8,}"
        return f"  {formatted_line}:  {display}"

    def clear(self) -> None:
        self.beginResetModel()
        self._all_results = []
        self._visible_count = 0
        self._indexer = None
        self._line_cache.clear()
        self.endResetModel()

    def clear_cache(self) -> None:
        """Czyści pamięć podręczną pobranych linii tekstu."""
        self._line_cache.clear()

    def get_line_no(self, row: int) -> int | None:
        """Zwraca numer linii pliku dla danego wiersza wyników."""
        if 0 <= row < len(self._all_results):
            item = self._all_results[row]
            return item[0] if isinstance(item, tuple) else item
        return None

    def find_row_by_line_no(self, line_no: int) -> int:
        """
        Zwraca indeks wiersza w modelu dla numeru linii pliku.
        Używa bisect — wyniki są posortowane rosnąco po line_no.
        Zwraca -1 jeśli nie znaleziono dokładnego dopasowania.
        """
        total = len(self._all_results)
        if hasattr(self._all_results, "bisect_left"):
            idx_obj = self._all_results.bisect_left(line_no)
            idx = int(idx_obj)
            if idx < total and self._all_results[idx] == line_no:
                # Doładowanie widocznych wierszy
                if idx >= self._visible_count:
                    items_to_fetch = idx - self._visible_count + 1
                    self.beginInsertRows(QModelIndex(), self._visible_count, self._visible_count + items_to_fetch - 1)
                    self._visible_count += items_to_fetch
                    self.endInsertRows()
                return idx
            return -1

        keys = [r[0] if isinstance(r, tuple) else r for r in self._all_results]
        idx = bisect.bisect_left(keys, line_no)
        if idx < total and keys[idx] == line_no:
            # Upewnij się, że element jest widoczny (doładowany) w jednym kroku
            if idx >= self._visible_count:
                items_to_fetch = idx - self._visible_count + 1
                self.beginInsertRows(QModelIndex(), self._visible_count, self._visible_count + items_to_fetch - 1)
                self._visible_count += items_to_fetch
                self.endInsertRows()
            return idx
        return -1


class MiniMap(QWidget):
    """
    Mini-map pokazująca gęstość ERROR/WARN/INFO/DEBUG w całym pliku.

    Wąski pasek (~40px) po prawej stronie Text widget. Rysuje kropki
    dla każdej linii skategoryzowanej według poziomu logu. Klik = skok
    do tej pozycji. Pokazuje viewport jako półprzezroczysty prostokąt.

    Dane są próbkowane — jeśli plik ma 1M linii, a mini-map ma 600px,
    to każda piksel reprezentuje ~1700 linii. Najwyższy priorytet
    (ERROR > WARN > INFO > DEBUG) wygrywa w danym pikselu.
    """

    position_clicked = Signal(int)  # emituje numer linii (0-indexed)

    # Priorytety: niższy = ważniejszy
    _PRIORITY = {"error": 0, "warn": 1, "info": 2, "debug": 3}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(48)
        self.setMinimumHeight(100)
        self._line_data: list[str] = []  # ["error", "warn", "info", "debug", ""]
        self._total_lines: int = 0
        self._viewport_start: float = 0.0  # 0.0–1.0
        self._viewport_end: float = 0.0  # 0.0–1.0
        self._colors = {
            "error": QColor(THEME["minimap_error"]),
            "warn": QColor(THEME["minimap_warn"]),
            "info": QColor(THEME["minimap_info"]),
            "debug": QColor(THEME["minimap_debug"]),
            "": QColor(THEME["minimap_bg"]),
        }
        self._bg: QColor = QColor(THEME["minimap_bg"])
        self._viewport_color: QColor = QColor(THEME["minimap_viewport"])

    @property
    def total_lines(self) -> int:
        """Zwraca całkowitą liczbę linii reprezentowanych na minimapie."""
        return self._total_lines

    def apply_theme(self, theme: dict) -> None:
        """Aktualizuje kolory minimapy na podstawie przekazanego słownika motywu."""
        self._colors = {
            "error": QColor(theme["minimap_error"]),
            "warn": QColor(theme["minimap_warn"]),
            "info": QColor(theme["minimap_info"]),
            "debug": QColor(theme["minimap_debug"]),
            "": QColor(theme["minimap_bg"]),
        }
        self._bg = QColor(theme["minimap_bg"])
        self._viewport_color = QColor(theme["minimap_viewport"])
        self.update()

    def set_line_data(self, line_data: list[str], total_lines: int) -> None:
        """Ustawia dane mini-mapy. line_data to lista kategorii per linia."""
        self._line_data = line_data
        self._total_lines = total_lines
        self.update()

    def set_viewport(self, start: float, end: float) -> None:
        """Ustawia pozycję viewportu (0.0–1.0)."""
        new_start = max(0.0, start)
        new_end = min(1.0, end)

        # Ogranicz bezsensowne przerysowania jeśli zmiana jest niezauważalna (np. poniżej 0.1%)
        if abs(self._viewport_start - new_start) > 0.001 or abs(self._viewport_end - new_end) > 0.001:
            self._viewport_start = new_start
            self._viewport_end = new_end
            self.update()

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        rect = self.rect()
        painter.fillRect(rect, self._bg)

        h = rect.height()
        if h < 2 or self._total_lines == 0:
            return

        # Próbkuj dane — jedna piksel na ~total_lines/h linii
        lines_per_pixel = max(1, self._total_lines // h)
        available_data = len(self._line_data)

        for y in range(h):
            line_start = int(y * self._total_lines / h)
            line_end = min(line_start + lines_per_pixel, self._total_lines)

            # Znajdź najwyższy priorytet w tym zakresie
            best_cat = ""
            best_pri = 999
            for ln in range(line_start, min(line_end, available_data)):
                cat = self._line_data[ln]
                if cat and self._PRIORITY.get(cat, 999) < best_pri:
                    best_pri = self._PRIORITY[cat]
                    best_cat = cat

            color = self._colors.get(best_cat, self._bg)
            painter.setPen(color)
            # Rysuj jako krótką kreskę (2px grubości dla lepszej widoczności)
            painter.drawRect(2, y, self.width() - 4, 1)

        # Rysuj viewport jako półprzezroczysty prostokąt
        vp_y = int(self._viewport_start * h)
        vp_h = max(2, int((self._viewport_end - self._viewport_start) * h))
        painter.setPen(self._viewport_color)
        painter.setBrush(self._viewport_color)
        painter.drawRect(0, vp_y, self.width(), vp_h)

        # Obramowanie
        painter.setPen(QColor(THEME["border"]))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(0, 0, self.width() - 1, h - 1)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        """Klik = skok do pozycji w pliku proporcjonalnej do klikniętej pozycji."""
        if self._total_lines == 0:
            return
        y = event.position().y()
        pct = y / self.height()
        line_no = int(pct * self._total_lines)
        self.position_clicked.emit(line_no)


class FormatDialog(QDialog):
    """
    Dialog pozwalający na sformatowanie przekazanego fragmentu tekstu
    (np. JSON, XML), kolorowanie składni oraz wyszukiwanie wewnątrz sformatowanego kodu.
    Pamięta wybrany formatter w trakcie sesji.
    """

    editor: QPlainTextEdit | QTextEdit | None

    def __init__(self, parent: QWidget | None, text: str, initial_formatter: str = "JSON") -> None:
        super().__init__(parent)
        self.ui = Ui_FormatDialog()
        self.ui.setupUi(self)

        # Umożliwienie maksymalizacji i minimalizacji okna dialogowego
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)

        self.original_text = text
        self.app = parent.window() if (parent and hasattr(parent, "window")) else parent
        self.t = self.app.t if (self.app and hasattr(self.app, "t")) else lambda k: k
        app_theme = getattr(self.app, "theme", None)
        self._theme: dict[str, str] = app_theme if isinstance(app_theme, dict) else THEME

        self.editor = None
        if isinstance(parent, (QPlainTextEdit, QTextEdit)):
            self.editor = parent
        elif parent is not None:
            parent_text = getattr(parent, "text", None)
            if isinstance(parent_text, (QPlainTextEdit, QTextEdit)):
                self.editor = parent_text

        self.setWindowTitle(self.t("dlg_format_title"))
        self.ui.lbl_formatter.setText(self.t("lbl_formatter"))

        self.formatter_combo = self.ui.formatter_combo
        self.formatter_combo.setMinimumWidth(100)
        self.formatter_combo.addItems(list(FORMATTERS.keys()))

        # Ustaw początkowy formatter, jeśli istnieje
        idx = self.formatter_combo.findText(initial_formatter)
        if idx >= 0:
            self.formatter_combo.setCurrentIndex(idx)

        self.formatter_combo.currentTextChanged.connect(self._apply_format)

        self.ui.btn_reformat.setText(self.t("btn_reformat"))
        self.ui.btn_reformat.clicked.connect(self._reformat_current_text)
        self.ui.btn_prev.setText(self.t("btn_prev_line"))
        self.ui.btn_prev.clicked.connect(self._on_prev_line)
        self.ui.btn_next.setText(self.t("btn_next_line"))
        self.ui.btn_next.clicked.connect(self._on_next_line)

        self.ui.btn_copy.setText(self.t("btn_copy"))
        self.ui.btn_copy.clicked.connect(self._on_copy)
        self.ui.wrap_cb.setText(self.t("cb_word_wrap"))
        self.ui.wrap_cb.toggled.connect(self._on_wrap_toggled)

        # Tłumaczenie systemowego przycisku 'Close'
        self.ui.buttons.button(QDialogButtonBox.StandardButton.Close).setText(self.t("btn_close"))

        self.text_edit = self.ui.text_edit
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(10)
        self.text_edit.setFont(font)

        # Zachowaj identyczną paletę przy utracie fokusu (gdy fokus jest w polu wyszukiwania)
        sync_inactive_palette(self.text_edit)
        self._shift_wheel_filter = install_shift_wheel_scroll(self.text_edit)

        # Inicjalizacja highlightera składni
        self.highlighter = StructuredCodeHighlighter(
            self.text_edit.document(),
            mode=self.formatter_combo.currentText(),
            theme=self._theme,
        )

        # Wyszukiwarka wewnątrz FormatDialog
        self.search_input = self.ui.search_input
        self.search_input.setPlaceholderText(self.t("ph_format_search"))
        self.btn_search_prev = self.ui.btn_search_prev
        self.btn_search_next = self.ui.btn_search_next
        self.search_case_cb = self.ui.search_case_cb
        self.search_case_cb.setText(self.t("cb_case"))
        self.lbl_search_count = self.ui.lbl_search_count

        self._search_matches: list[tuple[int, int]] = []
        self._current_match_idx: int = -1
        self._search_timer = QtCore.QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(120)
        self._search_timer.timeout.connect(lambda: self._perform_search(reset_index=True))

        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_case_cb.toggled.connect(lambda _checked: self._perform_search(reset_index=True))
        self.btn_search_next.clicked.connect(self.find_next)
        self.btn_search_prev.clicked.connect(self.find_prev)

        # Skrót Ctrl+F do aktywacji pola wyszukiwania
        self._action_find = QAction(self)
        self._action_find.setShortcut(QKeySequence("Ctrl+F"))
        self._action_find.triggered.connect(self.focus_search)
        self.addAction(self._action_find)

        # Zastosuj formatowanie przy otwarciu
        self._apply_format(self.formatter_combo.currentText())

    def focus_search(self) -> None:
        """Ustawia focus na polu wyszukiwania i zaznacza tekst."""
        self.search_input.setFocus()
        self.search_input.selectAll()

    def clear_search(self) -> None:
        """Czyści pole wyszukiwania oraz podświetlenia w oknie FormatDialog."""
        self._search_timer.stop()
        self.search_input.clear()
        self._search_matches = []
        self._current_match_idx = -1
        self.lbl_search_count.setText("")
        self.text_edit.setExtraSelections([])

    def _on_copy(self) -> None:
        """Kopiuje zaznaczony tekst lub całą zawartość okna do schowka."""
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            text_to_copy = cursor.selectedText().replace("\u2029", "\n")
        else:
            text_to_copy = self.text_edit.toPlainText()
        if text_to_copy:
            QApplication.clipboard().setText(text_to_copy)

    def _on_wrap_toggled(self, checked: bool) -> None:
        """Przełącza zawijanie wierszy w edytorze FormatDialog."""
        mode = QPlainTextEdit.LineWrapMode.WidgetWidth if checked else QPlainTextEdit.LineWrapMode.NoWrap
        self.text_edit.setLineWrapMode(mode)

    def _on_search_text_changed(self, text: str) -> None:
        if not text or len(self.text_edit.toPlainText()) < 50_000:
            self._perform_search(reset_index=True)
        else:
            self._search_timer.start(120)

    def _perform_search(self, reset_index: bool = True) -> None:
        self._search_timer.stop()
        pattern = self.search_input.text()
        if not pattern:
            self._search_matches = []
            self._current_match_idx = -1
            self.lbl_search_count.setText("")
            self.text_edit.setExtraSelections([])
            return

        doc_text = self.text_edit.toPlainText()
        case = self.search_case_cb.isChecked()
        haystack = doc_text if case else doc_text.lower()
        needle = pattern if case else pattern.lower()
        pat_len = len(needle)

        matches: list[tuple[int, int]] = []
        if pat_len > 0 and doc_text:
            pos = 0
            while True:
                idx = haystack.find(needle, pos)
                if idx == -1:
                    break
                matches.append((idx, idx + pat_len))
                pos = idx + pat_len

        self._search_matches = matches
        if not matches:
            self._current_match_idx = -1
            empty_msg = self.t("lbl_format_search_empty")
            self.lbl_search_count.setText(empty_msg if empty_msg != "lbl_format_search_empty" else "0 / 0")
            self.text_edit.setExtraSelections([])
            return

        if reset_index or self._current_match_idx < 0 or self._current_match_idx >= len(matches):
            self._current_match_idx = 0

        self._update_search_selections_and_scroll()

    def _update_search_selections_and_scroll(self) -> None:
        matches = self._search_matches
        total = len(matches)
        if total == 0 or self._current_match_idx < 0 or self._current_match_idx >= total:
            self.text_edit.setExtraSelections([])
            return

        current_1based = self._current_match_idx + 1
        tpl = self.t("lbl_format_search_count")
        if "{current" in tpl:
            self.lbl_search_count.setText(tpl.format(current=current_1based, total=total))
        else:
            self.lbl_search_count.setText(f"{current_1based:,} / {total:,}")

        doc = self.text_edit.document()
        fmt_passive = QtGui.QTextCharFormat()
        fmt_passive.setBackground(QColor(self._theme.get("highlight", "#fff176")))
        fmt_passive.setForeground(QColor("#000000"))

        fmt_active = QtGui.QTextCharFormat()
        fmt_active.setBackground(QColor(self._theme.get("search_active", "#ff8c00")))
        fmt_active.setForeground(QColor("#000000"))
        fmt_active.setFontWeight(QFont.Weight.Bold)

        # Ogranicz liczbę pasywnych ExtraSelection dla zachowania płynności przy tysiącach trafień
        half_win = MAX_DIALOG_EXTRA_SELECTIONS // 2
        start_slice = max(0, self._current_match_idx - half_win)
        end_slice = min(total, start_slice + MAX_DIALOG_EXTRA_SELECTIONS)

        sels: list[QTextEdit.ExtraSelection] = []
        for idx in range(start_slice, end_slice):
            if idx == self._current_match_idx:
                continue
            m_start, m_end = matches[idx]
            sel = QTextEdit.ExtraSelection()
            c = QTextCursor(doc)
            c.setPosition(m_start)
            c.setPosition(m_end, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = c
            sel.format = fmt_passive
            sels.append(sel)

        act_start, act_end = matches[self._current_match_idx]
        act_sel = QTextEdit.ExtraSelection()
        act_cursor = QTextCursor(doc)
        act_cursor.setPosition(act_start)
        act_cursor.setPosition(act_end, QTextCursor.MoveMode.KeepAnchor)
        act_sel.cursor = act_cursor
        act_sel.format = fmt_active
        sels.append(act_sel)

        self.text_edit.setExtraSelections(sels)

        # Przewiń widok do aktywnego trafienia bez modyfikacji selekcji kursora
        nav_cursor = QTextCursor(doc)
        nav_cursor.setPosition(act_end)
        self.text_edit.setTextCursor(nav_cursor)
        self.text_edit.ensureCursorVisible()
        nav_cursor.setPosition(act_start)
        self.text_edit.setTextCursor(nav_cursor)
        self.text_edit.ensureCursorVisible()

    def find_next(self) -> None:
        """Przechodzi do następnego wyniku wyszukiwania w FormatDialog."""
        if not self._search_matches:
            if self.search_input.text():
                self._perform_search(reset_index=True)
            return
        self._current_match_idx = (self._current_match_idx + 1) % len(self._search_matches)
        self._update_search_selections_and_scroll()

    def find_prev(self) -> None:
        """Przechodzi do poprzedniego wyniku wyszukiwania w FormatDialog."""
        if not self._search_matches:
            if self.search_input.text():
                self._perform_search(reset_index=True)
            return
        self._current_match_idx = (self._current_match_idx - 1) % len(self._search_matches)
        self._update_search_selections_and_scroll()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and (self.search_input.hasFocus() or self.search_input.text()):
            self.clear_search()
            self.text_edit.setFocus()
            event.accept()
            return

        if event.key() == Qt.Key.Key_F3:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.find_prev()
            else:
                self.find_next()
            event.accept()
            return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.search_input.hasFocus():
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.find_prev()
            else:
                self.find_next()
            event.accept()
            return

        super().keyPressEvent(event)

    def _reformat_current_text(self) -> None:
        """Pobiera obecny tekst, aktualizuje oryginał i ponawia formatowanie."""
        current_text = self.text_edit.toPlainText()
        failed_msg = self.t("msg_format_failed") + "\n\n"
        if current_text.startswith(failed_msg):
            current_text = current_text[len(failed_msg) :]

        self.original_text = current_text
        self._apply_format(self.formatter_combo.currentText())

    def _apply_format(self, formatter_name: str) -> None:
        """Aplikuje wybrany formatter. Jeśli się nie uda, pokazuje oryginalny tekst."""
        max_chars = FORMATTER_MAX_CHARS.get(formatter_name, 5_000_000)
        if len(self.original_text) > max_chars:
            tpl = self.t("msg_format_too_large")
            warn_prefix = (
                tpl.format(size=fmt_size(len(self.original_text)))
                if "{size" in tpl
                else f"Line too large ({fmt_size(len(self.original_text))}):"
            )
            target_text = warn_prefix + "\n\n" + self.original_text
            self.highlighter.set_mode(formatter_name, rehighlight=False)
            self.highlighter.enabled = False
            self.text_edit.setPlainText(target_text)
            if self.search_input.text():
                self._perform_search(reset_index=True)
            return

        use_wait_cursor = len(self.original_text) > 100_000
        if use_wait_cursor:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            formatted = format_log(self.original_text, formatter_name)
            if formatted:
                target_text = formatted
            else:
                target_text = self.t("msg_format_failed") + "\n\n" + self.original_text

            self.highlighter.set_mode(formatter_name, rehighlight=False)
            self.highlighter.enabled = len(target_text) <= MAX_HIGHLIGHT_DOC_CHARS
            self.text_edit.setPlainText(target_text)
        finally:
            if use_wait_cursor:
                QApplication.restoreOverrideCursor()

        if self.search_input.text():
            self._perform_search(reset_index=True)

    def get_selected_formatter(self) -> str:
        """Zwraca nazwę wybranego formattera, by aplikacja mogła ją zapamiętać."""
        return self.formatter_combo.currentText()

    def _move_editor_line(
        self,
        primary_op: QtGui.QTextCursor.MoveOperation,
        wrap_op: QtGui.QTextCursor.MoveOperation,
    ) -> None:
        if not self.editor:
            return
        cursor = self.editor.textCursor()
        if not cursor.movePosition(primary_op):
            cursor.movePosition(wrap_op)
        cursor.select(QtGui.QTextCursor.SelectionType.LineUnderCursor)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()
        self._load_cursor_text(cursor)

    def _on_prev_line(self) -> None:
        self._move_editor_line(QtGui.QTextCursor.MoveOperation.Up, QtGui.QTextCursor.MoveOperation.End)

    def _on_next_line(self) -> None:
        self._move_editor_line(QtGui.QTextCursor.MoveOperation.Down, QtGui.QTextCursor.MoveOperation.Start)

    def _load_cursor_text(self, cursor: QtGui.QTextCursor) -> None:
        if self.editor is not None:
            selected_text = get_full_line_text_at_cursor(self.editor)
        else:
            selected_text = cursor.selectedText().replace("\u2029", "\n")
        self.original_text = selected_text
        self._apply_format(self.formatter_combo.currentText())


class _GlobalClickFilter(QObject):
    """Filtr zdarzeń myszy wykrywający kliknięcia poza obszarem kontrolki (niezawodny blur)."""

    def __init__(self, target_widget: QWidget, overlay_widget: QWidget, on_click_outside: Any) -> None:
        super().__init__(target_widget)
        self._target = target_widget
        self._overlay = overlay_widget
        self._callback = on_click_outside
        self._installed = False

    def install(self) -> None:
        if not self._installed:
            app = QApplication.instance()
            if app:
                app.installEventFilter(self)
                self._installed = True

    def remove(self) -> None:
        if self._installed:
            app = QApplication.instance()
            if app:
                app.removeEventFilter(self)
            self._installed = False

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(event, QMouseEvent):
            global_pos = event.globalPosition().toPoint()
            in_target = False
            if self._target.isVisible():
                target_rect = QRect(
                    self._target.mapToGlobal(QPoint(0, 0)),
                    self._target.size(),
                )
                in_target = target_rect.contains(global_pos)

            in_overlay = False
            if self._overlay.isVisible():
                overlay_rect = QRect(
                    self._overlay.mapToGlobal(QPoint(0, 0)),
                    self._overlay.size(),
                )
                in_overlay = overlay_rect.contains(global_pos)

            if not in_target and not in_overlay:
                self._callback()
        return False


class _BaseLineEdit(QLineEdit):
    """Wewnętrzne jednowierszowe pole bazowe dla ExpandingLineEdit."""

    shift_enter_pressed = Signal()

    def __init__(self, owner: ExpandingLineEdit) -> None:
        super().__init__(owner)
        self._owner = owner

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.shift_enter_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        if "\n" in self._owner.text():
            self._owner.open_overlay()


class _MultiLineOverlay(QPlainTextEdit):
    """Pływająca nakładka edycyjna dla ExpandingLineEdit rozwijająca się w dół ponad innymi kontrolkami."""

    def __init__(self, owner: ExpandingLineEdit) -> None:
        super().__init__()
        self._owner = owner
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setTabChangesFocus(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self) -> None:
        self._owner.on_overlay_text_changed()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                self._owner.update_overlay_geometry()
                event.accept()
                return
            self._owner.returnPressed.emit()
            self._owner.collapse()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._owner.collapse()
            self._owner.clearFocus()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Tab:
            self._owner.collapse()
            self._owner.focusNextChild()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Backtab:
            self._owner.collapse()
            self._owner.focusPreviousChild()
            event.accept()
            return

        super().keyPressEvent(event)
        self._owner.update_overlay_geometry()

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        if not self._owner.base_edit.hasFocus():
            self._owner.collapse()


class ExpandingLineEdit(QWidget):
    """
    Pole tekstowe zachowujące stały rozmiar jednowierszowy w layoucie,
    które w przypadku tekstu wielowierszowego lub naciśnięcia Shift+Enter
    płynnie rozwija nakładkę (overlay) w dół, nie spychając innych kontrolkami.
    """

    returnPressed = Signal()
    textChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.max_height_limit = 150

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.base_edit = _BaseLineEdit(self)
        self.base_edit.returnPressed.connect(self._on_base_return_pressed)
        self.base_edit.textChanged.connect(self._on_base_text_changed)
        self.base_edit.shift_enter_pressed.connect(self.open_overlay)
        layout.addWidget(self.base_edit)

        self._overlay = _MultiLineOverlay(self)
        self._overlay.hide()

        self._click_filter = _GlobalClickFilter(self, self._overlay, self.collapse)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._update_base_height()

    def _update_base_height(self) -> None:
        font_metrics = self.fontMetrics()
        self.base_height = font_metrics.lineSpacing() + 10
        self.setFixedHeight(self.base_height)

    def text(self) -> str:
        """Zwraca pełny tekst wpisany w kontrolce."""
        if self._overlay.isVisible():
            return self._overlay.toPlainText()
        return self._full_text

    # noinspection PyPep8Naming
    def setText(self, t: str) -> None:
        """Ustawia tekst w kontrolce."""
        self._full_text = t
        self._overlay.blockSignals(True)
        self._overlay.setPlainText(t)
        self._overlay.blockSignals(False)

        first_line = t.split("\n")[0] if "\n" in t else t
        self.base_edit.blockSignals(True)
        self.base_edit.setText(first_line)
        self.base_edit.blockSignals(False)

        self.textChanged.emit()

    def clear(self) -> None:
        """Czyści zawartość pola i zwija nakładkę."""
        self.setText("")
        self.collapse()

    # noinspection PyPep8Naming
    def selectAll(self) -> None:
        """Zaznacza cały tekst w aktywnym polu."""
        if self._overlay.isVisible():
            self._overlay.selectAll()
        else:
            self.base_edit.selectAll()

    def setFocus(self) -> None:
        """Ustawia focus na kontrolce."""
        if "\n" in self._full_text:
            self.open_overlay()
        else:
            self.base_edit.setFocus()

    def hasFocus(self) -> bool:
        """Sprawdza czy kontrolka lub jej nakładka posiada focus."""
        return self.base_edit.hasFocus() or self._overlay.hasFocus() or super().hasFocus()

    # noinspection PyPep8Naming
    def setPlaceholderText(self, text: str) -> None:
        """Ustawia placeholder text."""
        self.base_edit.setPlaceholderText(text)
        self._overlay.setPlaceholderText(text)

    def setToolTip(self, text: str) -> None:
        """Ustawia tooltip."""
        super().setToolTip(text)
        self.base_edit.setToolTip(text)
        self._overlay.setToolTip(text)

    # noinspection PyMethodOverriding
    def setFont(self, font: QFont) -> None:
        """Ustawia czcionkę dla pola bazowego i nakładki."""
        super().setFont(font)
        self.base_edit.setFont(font)
        self._overlay.setFont(font)
        self._update_base_height()

    def collapse(self) -> None:
        """Zwija nakładkę wielowierszową do widoku 1-wierszowego."""
        if self._overlay.isVisible():
            self._full_text = self._overlay.toPlainText()
            first_line = self._full_text.split("\n")[0] if "\n" in self._full_text else self._full_text
            self.base_edit.blockSignals(True)
            self.base_edit.setText(first_line)
            self.base_edit.blockSignals(False)
            self._overlay.hide()
            self._click_filter.remove()

    def open_overlay(self) -> None:
        """Otwiera nakładkę wielowierszową rozwijającą się w dół ponad innymi kontrolkami."""
        win = self.window()
        if not win:
            return

        self._overlay.setParent(win)
        self._overlay.blockSignals(True)
        self._overlay.setPlainText(self._full_text)
        self._overlay.blockSignals(False)

        cursor = self._overlay.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._overlay.setTextCursor(cursor)

        self.update_overlay_geometry()
        self._overlay.show()
        self._overlay.raise_()
        self._overlay.setFocus()
        self._click_filter.install()

    def update_overlay_geometry(self) -> None:
        """Przelicza i ustawia pozycję oraz wysokość nakładki."""
        win = self.window()
        if not win or not self.isVisible():
            return

        global_pos = self.mapToGlobal(QPoint(0, 0))
        local_pos = win.mapFromGlobal(global_pos)

        doc = self._overlay.document()
        line_count = max(1, doc.blockCount())
        line_spacing = self._overlay.fontMetrics().lineSpacing()
        calc_height = line_count * line_spacing + 14
        target_height = min(max(calc_height, self.base_height), self.max_height_limit)

        if calc_height > self.max_height_limit:
            self._overlay.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        else:
            self._overlay.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._overlay.setGeometry(local_pos.x(), local_pos.y(), self.width(), target_height)

    def _on_base_return_pressed(self) -> None:
        self.returnPressed.emit()

    def _on_base_text_changed(self, text: str) -> None:
        if "\n" in text:
            self._full_text = text
            self.open_overlay()
        else:
            self._full_text = text
            self._overlay.blockSignals(True)
            self._overlay.setPlainText(text)
            self._overlay.blockSignals(False)
        self.textChanged.emit()

    def on_overlay_text_changed(self) -> None:
        self._full_text = self._overlay.toPlainText()
        self.update_overlay_geometry()
        self.textChanged.emit()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._overlay.isVisible():
            self.update_overlay_geometry()

    def moveEvent(self, event: QtGui.QMoveEvent) -> None:
        super().moveEvent(event)
        if self._overlay.isVisible():
            self.update_overlay_geometry()
