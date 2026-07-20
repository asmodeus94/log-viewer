"""Widgety PySide6 — LineNumberArea, LogPlainTextEdit, SettingsDialog, SearchResultsModel, MiniMap."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, Signal, QSize, QAbstractListModel, QModelIndex, QPoint, QRectF
from PySide6.QtGui import (
    QAction, QKeySequence, QColor, QTextCharFormat, QFont, QFontDatabase,
    QPainter, QFontMetrics, QDragEnterEvent, QDropEvent,
)
from PySide6.QtWidgets import (
    QWidget, QPlainTextEdit, QTextEdit, QLabel, QLineEdit, QCheckBox, QPushButton,
    QDialog, QDialogButtonBox, QSpinBox, QFontComboBox, QGridLayout,
    QFrame, QSizePolicy, QListView, QVBoxLayout, QHBoxLayout, QComboBox
)

from .helpers import THEME
from .formatters import FORMATTERS
from .ui.ui_settings_dialog import Ui_SettingsDialog
from .ui.ui_format_dialog import Ui_FormatDialog


class LineNumberArea(QWidget):
    """Widget rysujący numery linii zsynchronizowany z QPlainTextEdit."""

    def __init__(self, editor: "LogPlainTextEdit"):
        super().__init__(editor)
        self._editor = editor
        self._line_map: List[int] = []
        self._width_digits = 5
        self.update_width()

    def set_line_map(self, line_map: List[int]) -> None:
        self._line_map = line_map
        if line_map:
            max_line = max(line_map) + 1 if line_map else 1
            digits = max(5, len(str(max_line)))
            if digits != self._width_digits:
                self._width_digits = digits
                self.update_width()
        self.update()

    def update_width(self) -> None:
        fm = QFontMetrics(self._editor.font())
        width = fm.horizontalAdvance("0" * self._width_digits) + 16
        self.setFixedWidth(width)

    def sizeHint(self) -> QSize:
        return QSize(self.width(), 0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#f0f0f0"))
        painter.setPen(QColor("#666666"))
        painter.setFont(self._editor.font())

        block = self._editor.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self._editor.blockBoundingGeometry(block).translated(self._editor.contentOffset()).top())
        bottom = top + round(self._editor.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                if block_number < len(self._line_map):
                    file_line = self._line_map[block_number] + 1
                    painter.drawText(
                        0, top, self.width() - 8,
                        self._editor.fontMetrics().height(),
                        Qt.AlignRight | Qt.AlignVCenter,
                        str(file_line),
                    )
            block = block.next()
            block_number += 1
            top = bottom
            bottom = top + round(self._editor.blockBoundingRect(block).height())
            if block is None:
                break


class LogPlainTextEdit(QPlainTextEdit):
    """QPlainTextEdit z wbudowanym LineNumberArea i obsługą drag&drop plików."""

    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self._update_line_number_area_width(0)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setReadOnly(True)
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(10)
        font.setStyleHint(QFont.Monospace)
        self.setFont(font)
        self.setAcceptDrops(True)

    def set_line_map(self, line_map: List[int]) -> None:
        self._line_number_area.set_line_map(line_map)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            cr.left(), cr.top(),
            self._line_number_area.width(),
            cr.height(),
        )

    def _update_line_number_area_width(self, new_block_count: int) -> None:
        self.setViewportMargins(self._line_number_area.width(), 0, 0, 0)

    def _update_line_number_area(self, rect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
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


class SettingsDialog(QDialog):
    """Dialog zmiany fontu i parametrów wyświetlania."""

    def __init__(self, parent, app):
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

    def get_values(self) -> Tuple[str, int, int, int, int, int]:
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_results: List[Tuple[int, str]] = []
        self._visible_count = 0
        self._batch_size = 1000

        from .helpers import THEME_DARK
        self._color_error = QColor(THEME_DARK["error"])
        self._color_warn = QColor(THEME_DARK["warn"])
        self._color_info = QColor(THEME_DARK["info"])
        self._color_debug = QColor(THEME_DARK["debug"])

    def set_results(self, results: List[Tuple[int, str]]) -> None:
        """Zastępuje wszystkie wyniki. Wywołuje beginResetModel/endResetModel."""
        self.beginResetModel()
        self._all_results = results
        self._visible_count = min(len(results), self._batch_size)
        self.endResetModel()

    def append_results(self, results: List[Tuple[int, str]]) -> None:
        """Dodaje wyniki na końcu. Wywołuje beginInsertRows/endInsertRows."""
        if not results:
            return
        # Dołączone wyniki powinny być widoczne od razu, jeśli jesteśmy na końcu
        # lub jeśli po prostu chcemy się upewnić, że są dodane czysto.
        # append_results jest obecnie używane tylko w testach (silnik wyszukiwania
        # całkowicie zastępuje wyniki przez set_results).
        # Przywracamy podstawową funkcjonalność: dodanie do all_results i
        # visible_count oraz emisję insertRows, żeby widok się zaktualizował.
        start = self._visible_count
        self.beginInsertRows(QModelIndex(), start, start + len(results) - 1)
        self._all_results.extend(results)
        self._visible_count += len(results)
        self.endInsertRows()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return self._visible_count

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:
        if parent.isValid():
            return False
        return self._visible_count < len(self._all_results)

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

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= self._visible_count:
            return None
        line_no, text = self._all_results[index.row()]
        if role == Qt.DisplayRole:
            display = text[:200]
            if len(text) > 200:
                display += "..."
            return f"  {line_no + 1:>8}:  {display}"
        if role == Qt.UserRole:
            return line_no
        if role == Qt.ForegroundRole:
            upper = text[:200].upper()
            if "[ERROR]" in upper or " ERROR " in upper:
                return self._color_error
            elif "[WARN]" in upper or " WARN " in upper:
                return self._color_warn
            elif "[INFO]" in upper or " INFO " in upper:
                return self._color_info
            elif "[DEBUG]" in upper or " DEBUG " in upper:
                return self._color_debug
        return None

    def clear(self) -> None:
        self.beginResetModel()
        self._all_results = []
        self._visible_count = 0
        self.endResetModel()

    def get_line_no(self, row: int) -> Optional[int]:
        """Zwraca numer linii pliku dla danego wiersza wyników."""
        if 0 <= row < len(self._all_results):
            return self._all_results[row][0]
        return None

    def find_row_by_line_no(self, line_no: int) -> int:
        """
        Zwraca indeks wiersza w modelu dla numeru linii pliku.
        Używa bisect — wyniki są posortowane rosnąco po line_no.
        Zwraca -1 jeśli nie znaleziono dokładnego dopasowania.
        """
        import bisect
        keys = [r[0] for r in self._all_results]
        idx = bisect.bisect_left(keys, line_no)
        if idx < len(self._all_results) and self._all_results[idx][0] == line_no:
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(48)
        self.setMinimumHeight(100)
        self._line_data: List[str] = []  # ["error", "warn", "info", "debug", ""]
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
        self._bg = QColor(THEME["minimap_bg"])
        self._viewport_color = QColor(THEME["minimap_viewport"])

    def set_line_data(self, line_data: List[str], total_lines: int) -> None:
        """Ustawia dane mini-mapy. line_data to lista kategorii per linia."""
        self._line_data = line_data
        self._total_lines = total_lines
        self.update()

    def set_viewport(self, start: float, end: float) -> None:
        """Ustawia pozycję viewportu (0.0–1.0)."""
        self._viewport_start = max(0.0, start)
        self._viewport_end = min(1.0, end)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
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
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(0, 0, self.width() - 1, h - 1)

    def mousePressEvent(self, event):
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
    (np. JSON) i jego podgląd. Pamięta wybrany formatter w trakcie sesji.
    """
    def __init__(self, parent, text: str, initial_formatter: str = "JSON"):
        super().__init__(parent)
        self.ui = Ui_FormatDialog()
        self.ui.setupUi(self)

        self.original_text = text
        self.app = parent.window() if hasattr(parent, 'window') else parent
        self.t = self.app.t if hasattr(self.app, 't') else lambda k: k

        self.setWindowTitle(self.t("dlg_format_title"))
        self.ui.lbl_formatter.setText(self.t("lbl_formatter"))

        self.formatter_combo = self.ui.formatter_combo
        self.formatter_combo.addItems(list(FORMATTERS.keys()))

        # Ustaw początkowy formatter, jeśli istnieje
        idx = self.formatter_combo.findText(initial_formatter)
        if idx >= 0:
            self.formatter_combo.setCurrentIndex(idx)

        self.formatter_combo.currentTextChanged.connect(self._apply_format)

        self.text_edit = self.ui.text_edit
        # Zastosuj czcionkę stałej szerokości
        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(10)
        font.setStyleHint(QFont.Monospace)
        self.text_edit.setFont(font)

        # Zastosuj formatowanie przy otwarciu
        self._apply_format(self.formatter_combo.currentText())

    def _apply_format(self, formatter_name: str) -> None:
        """Aplikuje wybrany formatter. Jeśli się nie uda, pokazuje oryginalny tekst."""
        from .formatters import format_log
        formatted = format_log(self.original_text, formatter_name)
        if formatted:
            self.text_edit.setPlainText(formatted)
        else:
            self.text_edit.setPlainText(self.t("msg_format_failed") + "\n\n" + self.original_text)

    def get_selected_formatter(self) -> str:
        """Zwraca nazwę wybranego formattera, by aplikacja mogła ją zapamiętać."""
        return self.formatter_combo.currentText()








class ExpandingLineEdit(QTextEdit):
    """
    Pole tekstowe, które dynamicznie dostosowuje swoją szerokość i wysokość.
    Udaje jednowierszowy QLineEdit, ale pod spodem jest QTextEdit.
    Po uzyskaniu focusu poszerza się, a w miarę wpisywania dłuższego tekstu rośnie
    aż do limitu (max_width_limit i max_height_limit). Przy przekroczeniu pojemności
    pokazuje suwak.
    Enter zatwierdza, Shift+Enter nowa linia.
    """
    returnPressed = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.base_min_width = 80
        self.focused_min_width = 150
        self.max_width_limit = 500

        self.setMinimumWidth(self.base_min_width)
        self.setMaximumWidth(self.max_width_limit)

        self.base_height = self.fontMetrics().height() + 10
        self.setMaximumHeight(self.base_height)
        self.setMinimumHeight(self.base_height)
        self.max_height_limit = 150

        self.textChanged.connect(self._adjust_size)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setAcceptRichText(False)

    def text(self):
        return self.toPlainText()

    def setText(self, t):
        self.setPlainText(t)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.returnPressed.emit()
                event.accept()
        else:
            super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._adjust_size()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.setMinimumWidth(self.base_min_width)
        self.setMaximumHeight(self.base_height)
        self.setMinimumHeight(self.base_height)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.verticalScrollBar().setValue(0)

    def _adjust_size(self):
        if self.hasFocus():
            fm = self.fontMetrics()
            doc = self.document()

            text_str = self.toPlainText()
            lines = text_str.split('\n')
            max_line_width = max([fm.horizontalAdvance(line) for line in lines] + [0])

            text_width = max_line_width + 30
            new_width = max(self.focused_min_width, text_width)
            new_width = min(new_width, self.max_width_limit)
            self.setMinimumWidth(new_width)

            doc.setTextWidth(new_width)
            doc_height = int(doc.size().height()) + 10
            new_height = min(doc_height, self.max_height_limit)
            new_height = max(new_height, self.base_height)
            self.setMaximumHeight(new_height)
            self.setMinimumHeight(new_height)

            if doc_height > self.max_height_limit:
                self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            else:
                self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
