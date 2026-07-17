"""Widgety PySide6 — LineNumberArea, LogPlainTextEdit, SettingsDialog, SearchResultsModel, MiniMap."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, Signal, QSize, QAbstractListModel, QModelIndex, QPoint, QRectF
from PySide6.QtGui import (
    QAction, QKeySequence, QColor, QTextCharFormat, QFont, QPainter,
    QFontMetrics, QDragEnterEvent, QDropEvent,
)
from PySide6.QtWidgets import (
    QWidget, QPlainTextEdit, QLabel, QLineEdit, QCheckBox, QPushButton,
    QDialog, QDialogButtonBox, QSpinBox, QFontComboBox, QGridLayout,
    QFrame, QSizePolicy, QListView,
)

from .helpers import THEME


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
        font = QFont("Monospace", 10)
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
        self._app = app
        self.t = app.t
        self.setWindowTitle(self.t("dlg_settings_title"))
        self.setMinimumWidth(440)

        layout = QGridLayout(self)
        row = 0

        layout.addWidget(QLabel(self.t("lbl_font_family")), row, 0)
        self.font_combo = QFontComboBox()
        if app.font_family:
            self.font_combo.setCurrentFont(QFont(app.font_family))
        layout.addWidget(self.font_combo, row, 1)
        row += 1

        layout.addWidget(QLabel(self.t("lbl_font_size")), row, 0)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 72)
        self.size_spin.setValue(app.font_size)
        layout.addWidget(self.size_spin, row, 1)
        row += 1

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep, row, 0, 1, 2)
        row += 1

        layout.addWidget(QLabel(self.t("lbl_window_size")), row, 0)
        self.ws_spin = QSpinBox()
        self.ws_spin.setRange(100, 50000)
        self.ws_spin.setSingleStep(100)
        self.ws_spin.setValue(app.window_size_lines)
        layout.addWidget(self.ws_spin, row, 1)
        row += 1

        layout.addWidget(QLabel(self.t("lbl_max_display_lines")), row, 0)
        self.md_spin = QSpinBox()
        self.md_spin.setRange(1000, 200000)
        self.md_spin.setSingleStep(1000)
        self.md_spin.setValue(app.max_display_lines)
        layout.addWidget(self.md_spin, row, 1)
        row += 1

        layout.addWidget(QLabel(self.t("lbl_max_line_length")), row, 0)
        self.ml_spin = QSpinBox()
        self.ml_spin.setRange(100, 100000)
        self.ml_spin.setSingleStep(100)
        self.ml_spin.setValue(app.max_display_line_length)
        layout.addWidget(self.ml_spin, row, 1)
        row += 1

        layout.addWidget(QLabel(self.t("lbl_index_interval")), row, 0)
        self.ii_spin = QSpinBox()
        self.ii_spin.setRange(1, 100)
        self.ii_spin.setValue(app.index_interval_bytes // (1024 * 1024))
        layout.addWidget(self.ii_spin, row, 1)
        row += 1

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, row, 0, 1, 2)

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

    Używa QAbstractListModel — QListView renderuje tylko widoczne elementy,
    więc model obsługuje setki tysięcy wyników bez spadku wydajności.

    Każdy wynik to krotka (line_no, text). DisplayRole pokazuje
    "  linia: tekst" (przycięty do 200 znaków). UserRole zwraca line_no.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results: List[Tuple[int, str]] = []

    def set_results(self, results: List[Tuple[int, str]]) -> None:
        """Zastępuje wszystkie wyniki. Wywołuje beginResetModel/endResetModel."""
        self.beginResetModel()
        self._results = results
        self.endResetModel()

    def append_results(self, results: List[Tuple[int, str]]) -> None:
        """Dodaje wyniki na końcu. Wywołuje beginInsertRows/endInsertRows."""
        if not results:
            return
        start = len(self._results)
        self.beginInsertRows(QModelIndex(), start, start + len(results) - 1)
        self._results.extend(results)
        self.endInsertRows()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._results)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._results):
            return None
        line_no, text = self._results[index.row()]
        if role == Qt.DisplayRole:
            display = text[:200]
            if len(text) > 200:
                display += "..."
            return f"  {line_no + 1:>8}:  {display}"
        if role == Qt.UserRole:
            return line_no
        if role == Qt.ForegroundRole:
            # Koloruj według poziomu logu — kolory z motywu (dark/light)
            from .helpers import THEME_DARK
            upper = text.upper()
            if "[ERROR]" in upper or " ERROR " in upper:
                return QColor(THEME_DARK["error"])
            elif "[WARN]" in upper or " WARN " in upper:
                return QColor(THEME_DARK["warn"])
            elif "[INFO]" in upper or " INFO " in upper:
                return QColor(THEME_DARK["info"])
            elif "[DEBUG]" in upper or " DEBUG " in upper:
                return QColor(THEME_DARK["debug"])
        return None

    def clear(self) -> None:
        self.beginResetModel()
        self._results = []
        self.endResetModel()

    def get_line_no(self, row: int) -> Optional[int]:
        """Zwraca numer linii pliku dla danego wiersza wyników."""
        if 0 <= row < len(self._results):
            return self._results[row][0]
        return None

    def find_row_by_line_no(self, line_no: int) -> int:
        """
        Zwraca indeks wiersza w modelu dla numeru linii pliku.
        Używa bisect — wyniki są posortowane rosnąco po line_no.
        Zwraca -1 jeśli nie znaleziono dokładnego dopasowania.
        """
        import bisect
        keys = [r[0] for r in self._results]
        idx = bisect.bisect_left(keys, line_no)
        if idx < len(self._results) and self._results[idx][0] == line_no:
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
