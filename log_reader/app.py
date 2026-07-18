"""app.py — LogTab (per-file widget) + LogViewerWindow (tabbed controller)."""

from __future__ import annotations

import os
import re
import sys
import time
import bisect
from typing import Optional, List, Tuple, Dict, Any

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QThread, QSize, QPoint
from PySide6.QtGui import (
    QAction, QKeySequence, QColor, QTextCharFormat, QFont, QDragEnterEvent,
    QDropEvent, QCursor,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QPlainTextEdit, QWidget, QVBoxLayout,
    QHBoxLayout, QGridLayout, QLabel, QLineEdit, QCheckBox, QPushButton,
    QMenuBar, QMenu, QStatusBar, QFileDialog, QMessageBox, QInputDialog,
    QProgressBar, QSplitter, QTreeWidget, QTreeWidgetItem, QSlider,
    QDialog, QDialogButtonBox, QSpinBox, QComboBox, QFontComboBox,
    QSizePolicy, QToolBar, QFrame, QProgressDialog, QListView, QTabWidget,
)

from .exceptions import FileChangedError, CompressedSaveError
from .helpers import (
    fmt_size, truncate_for_display, parse_dnd_files, dnd_files_to_open,
    is_compressed, open_maybe_compressed,
    WINDOW_SIZE_LINES, MAX_DISPLAY_LINES, MAX_DISPLAY_LINE_LENGTH,
    FOLLOW_POLL_MS, FILTER_PROGRESS_MS, DEFAULT_ENCODING,
    TAG_HIGHLIGHT, TAG_BOOKMARK, TAG_EDITED, TAG_TRUNCATED,
    TAG_ERROR, TAG_WARN, TAG_INFO, TAG_DEBUG, TAG_TIMESTAMP, TAG_CONTEXT,
    SUPPORTED_ENCODINGS, OPEN_FILETYPES, THEME_DARK, THEME_LIGHT,
)
from .i18n import I18N
from .config import UserConfig
from .indexer import LineIndexer, IndexEntry
from .filter_engine import FilterEngine
from .edit_buffer import EditBuffer
from .workers import IndexerWorker, FilterWorker, SaveWorker
from .widgets import LogPlainTextEdit, SettingsDialog, SearchResultsModel, MiniMap, FormatDialog


# =====================================================================
# LogTab — jedna zakładka = jeden plik
# =====================================================================

class LogTab(QWidget):
    """Jedna zakładka = jeden plik. Zawiera całą logikę per-file.

    Komunikuje się z LogViewerWindow przez:
      - self._main (referencja do LogViewerWindow) — config, lang, theme,
        toolbar widgets (search_entry, filter_entry, etc.)
      - signal status_changed(str) — aktualizacja status bara
      - signal title_changed(str) — aktualizacja nazwy zakładki
    """

    status_changed = Signal(str)
    title_changed = Signal(str)

    def __init__(self, main_window: "LogViewerWindow", parent=None):
        super().__init__(parent)
        self._main = main_window

        # Stan pliku
        self.file_path: Optional[str] = None
        self.indexer: Optional[LineIndexer] = None
        self.filter_engine: Optional[FilterEngine] = None
        self.edit_buffer = EditBuffer()
        self.bookmarks: Dict[int, None] = {}

        self._file_mtime_at_open: float = 0.0
        self._file_size_at_open: int = 0
        self._last_file_inode: int = 0

        # Wirtualne okno
        self.window_start: int = 0
        self.window_lines: List[Tuple[int, str]] = []
        self.line_map: List[int] = []

        # Filtr
        self.filter_active: bool = False
        self.filter_results: List[Tuple[int, int, str]] = []
        # Linie kontekstu (N linii po każdym trafieniu) — zbiór numerów linii pliku.
        # Tła kontekstu są dodawane przez ExtraSelections (jak zakładki).
        self.filter_context_lines: set = set()
        # Ile linii kontekstu po każdym trafieniu (0 = wyłączone).
        self._filter_context_after: int = 0

        # Wyszukiwanie
        self.search_pattern: str = ""
        self._search_compiled = None
        self._search_case: bool = False
        self._search_negate: bool = False
        self._last_search_regex: bool = False
        self._last_search_case: bool = False
        self._last_search_negate: bool = False
        # Wyniki wyszukiwania (panel dolny)
        self._search_results: List[Tuple[int, str]] = []
        self._search_results_all: List[Tuple[int, str]] = []  # pełne wyniki
        self._search_result_index: int = -1
        self._search_engine: Optional[FilterEngine] = None
        self._search_thread: Optional[QThread] = None
        self._search_worker: Optional[FilterWorker] = None
        self._search_model: Optional[SearchResultsModel] = None

        # Scroll tracking — JEDEN timer do debouncing
        self._scroll_debounce_timer = QTimer(self)
        self._scroll_debounce_timer.setSingleShot(True)
        self._scroll_debounce_timer.setInterval(80)
        self._scroll_debounce_timer.timeout.connect(self._update_slider_from_scroll)
        self._is_updating_slider = False
        self._is_loading = False
        self._last_edge_load_time: float = 0.0
        self._minimap_data: List[str] = []
        self._minimap_update_timer = QTimer(self)
        self._minimap_update_timer.setSingleShot(True)
        self._minimap_update_timer.setInterval(500)
        self._minimap_update_timer.timeout.connect(self._update_minimap)

        # Follow mode
        self.follow_active: bool = False
        self._last_file_size: int = 0
        self._follow_reindexing: bool = False

        # QThread workers (per-tab)
        self._indexer_thread: Optional[QThread] = None
        self._indexer_worker: Optional[IndexerWorker] = None
        self._index_progress: Optional[QProgressDialog] = None
        self._filter_thread: Optional[QThread] = None
        self._filter_worker: Optional[FilterWorker] = None
        self._save_thread: Optional[QThread] = None
        self._save_worker: Optional[SaveWorker] = None
        self._save_progress: Optional[QProgressDialog] = None

        # Ostatnio wybrany formatter w sesji
        self._last_formatter: str = "JSON"

        # Build UI
        self._build_ui()
        self._apply_font_to_text()

        # Timer do sprawdzania krawędzi
        self._edge_timer = QTimer(self)
        self._edge_timer.setInterval(300)
        self._edge_timer.timeout.connect(self._check_edges)
        self._edge_timer.start()

    # ------------------------------------------------------------------ helpers
    def t(self, key: str) -> str:
        return self._main.t(key)

    def _fmt(self, msg_key: str, **kw) -> str:
        return self._main._fmt(msg_key, **kw)

    def _status(self, msg: str) -> None:
        """Aktualizuje status bar (przez sygnał do LogViewerWindow)."""
        self.status_changed.emit(msg)

    # ----- config accessors (delegowane do LogViewerWindow) -----

    @property
    def encoding(self) -> str:
        return self._main.encoding

    @property
    def window_size_lines(self) -> int:
        return self._main.window_size_lines

    @property
    def max_display_lines(self) -> int:
        return self._main.max_display_lines

    @property
    def max_display_line_length(self) -> int:
        return self._main.max_display_line_length

    @property
    def index_interval_bytes(self) -> int:
        return self._main.index_interval_bytes

    @property
    def font_family(self) -> Optional[str]:
        return self._main.font_family

    @property
    def font_size(self) -> int:
        return self._main.font_size

    @property
    def theme(self) -> dict:
        return self._main.theme

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(self.splitter)

        # Panel boczny
        side_panel = QWidget()
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(4, 4, 4, 4)
        self.splitter.addWidget(side_panel)

        self._lbl_bookmarks = QLabel(self.t("lbl_bookmarks"))
        side_layout.addWidget(self._lbl_bookmarks)
        self.bm_tree = QTreeWidget()
        self.bm_tree.setHeaderLabels([self.t("col_line")])
        self.bm_tree.setRootIsDecorated(False)
        self.bm_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.bm_tree.setUniformRowHeights(True)
        self.bm_tree.itemDoubleClicked.connect(self._goto_bookmark)
        side_layout.addWidget(self.bm_tree, 1)
        self.btn_del_bookmarks = QPushButton(self.t("btn_delete_sel"))
        self.btn_del_bookmarks.clicked.connect(self._delete_selected_bookmarks)
        side_layout.addWidget(self.btn_del_bookmarks)
        # Delete/Backspace usuwa zaznaczone zakładki (pojedynczą lub wiele)
        QtGui.QShortcut(QKeySequence.StandardKey.Delete, self.bm_tree,
                        activated=self._delete_selected_bookmarks)
        QtGui.QShortcut(QKeySequence("Backspace"), self.bm_tree,
                        activated=self._delete_selected_bookmarks)

        self._lbl_edits = QLabel(self.t("lbl_edits"))
        side_layout.addWidget(self._lbl_edits)
        self.ed_tree = QTreeWidget()
        self.ed_tree.setHeaderLabels([self.t("col_line")])
        self.ed_tree.setRootIsDecorated(False)
        self.ed_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.ed_tree.setUniformRowHeights(True)
        self.ed_tree.itemDoubleClicked.connect(self._goto_edit)
        side_layout.addWidget(self.ed_tree, 1)
        self.btn_del_edits = QPushButton(self.t("btn_delete_sel"))
        self.btn_del_edits.clicked.connect(self._delete_selected_edits)
        side_layout.addWidget(self.btn_del_edits)
        QtGui.QShortcut(QKeySequence.StandardKey.Delete, self.ed_tree,
                        activated=self._delete_selected_edits)
        QtGui.QShortcut(QKeySequence("Backspace"), self.ed_tree,
                        activated=self._delete_selected_edits)

        # Log view + Search results (vertical splitter)
        self.text = LogPlainTextEdit()
        # DnD z text widget → deleguj do LogViewerWindow
        self.text.files_dropped.connect(self._main._on_files_dropped)
        # Połącz scrollbar z aktualizacją slidera (z debouncing)
        self.text.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        # Bieżąca linia — delikatne tło pod kursorem (VS Code-style).
        # Utrzymujemy listę ExtraSelections z aktualną linią + ewentualnym
        # podświetleniem wyniku wyszukiwania. cursorPositionChanged odświeża
        # tę listę przy każdym ruchu kursora (strzałki, klik, skok z panelu).
        self._search_extra_sel: Optional[QtWidgets.QTextEdit.ExtraSelection] = None
        self.text.cursorPositionChanged.connect(self._update_current_line_highlight)
        # Uruchomione lazy — pierwsze przerysowanie po _load_window.

        # Panel wyników wyszukiwania (pod log view)
        search_panel = QWidget()
        search_layout = QVBoxLayout(search_panel)
        search_layout.setContentsMargins(4, 0, 4, 4)
        self._search_results_label = QLabel(self.t("lbl_search_results_empty"))
        search_layout.addWidget(self._search_results_label)
        self._search_model = SearchResultsModel()
        self.search_results_view = QListView()
        self.search_results_view.setUniformItemSizes(True)
        self.search_results_view.setModel(self._search_model)
        self.search_results_view.setAlternatingRowColors(True)
        mono_font = QFont("Menlo", 9) if sys.platform == "darwin" else QFont("DejaVu Sans Mono", 9)
        mono_font.setStyleHint(QFont.Monospace)
        self.search_results_view.setFont(mono_font)
        self.search_results_view.clicked.connect(self._on_search_result_clicked)
        search_layout.addWidget(self.search_results_view, 1)

        self.v_splitter = QSplitter(Qt.Vertical)
        self.v_splitter.addWidget(self.text)
        self.v_splitter.addWidget(search_panel)
        self.v_splitter.setSizes([500, 150])

        self.splitter.addWidget(self.v_splitter)
        self.splitter.setSizes([200, 900])

        # Mini-map + Slider pozycji (po prawej)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.minimap = MiniMap()
        self.minimap.position_clicked.connect(self._on_minimap_click)
        right_layout.addWidget(self.minimap, 1)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {THEME_DARK['border']};")
        right_layout.addWidget(sep)

        self.pct_label = QLabel("0%")
        self.pct_label.setAlignment(Qt.AlignCenter)
        self.pct_label.setStyleSheet(f"color: {THEME_DARK['fg_dim']}; font-size: 10px; padding: 4px;")
        right_layout.addWidget(self.pct_label)

        self.splitter.addWidget(right_panel)

    def _apply_font_to_text(self) -> None:
        family = self.font_family or (
            "Consolas" if sys.platform == "win32"
            else "Menlo" if sys.platform == "darwin"
            else "DejaVu Sans Mono"
        )
        font = QFont(family, self.font_size)
        font.setStyleHint(QFont.Monospace)
        self.text.setFont(font)
        if hasattr(self.text, "_line_number_area"):
            self.text._line_number_area.update_width()
            self.text._line_number_area.update()

    def _apply_theme(self) -> None:
        """Aktualizuje kolory per-tab po zmianie motywu."""
        t = self.theme
        if hasattr(self.text, "_line_number_area"):
            self.text._line_number_area.setStyleSheet(
                f"background-color: {t['bg_main']};"
            )
        if hasattr(self, "minimap"):
            self.minimap._colors = {
                "error": QColor(t["minimap_error"]),
                "warn": QColor(t["minimap_warn"]),
                "info": QColor(t["minimap_info"]),
                "debug": QColor(t["minimap_debug"]),
                "": QColor(t["minimap_bg"]),
            }
            self.minimap._bg = QColor(t["minimap_bg"])
            self.minimap._viewport_color = QColor(t["minimap_viewport"])
            self.minimap.update()
        self._update_text_colors()

    def _update_text_colors(self) -> None:
        """Aktualizuje kolory tagów w Text widget po zmianie motywu."""
        t = self.theme
        self.text.setExtraSelections([])
        self._search_extra_sel = None
        if self.indexer and self.line_map:
            self._reload_current_view()
        # Po reload przebuduj podświetlenie bieżącej linii nowym kolorem.
        self._update_current_line_highlight()

    # --------------------------------------------------------- file ops ---
    def open_file(self, path: str) -> None:
        if not os.path.isfile(path):
            QMessageBox.critical(self._main, self.t("app_title"), self.t("msg_no_file"))
            return
        self.cmd_clear_filter(silent=True)
        if self.follow_active:
            self.cmd_toggle_follow()
        self.file_path = path
        self._status(self.t("st_opening"))
        self.title_changed.emit(os.path.basename(path))
        self.window_start = 0
        self.window_lines = []
        self.line_map = []
        self.edit_buffer.clear()
        self.bookmarks.clear()
        self._refresh_bookmarks_tree()
        self._refresh_edits_tree()
        self.pct_label.setText("0%")
        self.text.setPlainText("")
        self.text.set_line_map([])

        encoding = self.encoding

        # QProgressDialog — pokazuje postęp indeksowania z przyciskiem Anuluj.
        # Dla małych plików (< 100 MB) dialog się nie pojawi (indeksowanie
        # trwa < 1s, Qt automatycznie ukrywa dialog jeśli minDuration nie upłynął).
        file_size = os.path.getsize(path)
        # Pokaż dialog tylko dla plików > 50 MB — dla mniejszych indeksowanie
        # jest błyskawiczne i dialog by tylko mig­nął.
        show_dialog = file_size > 50 * 1024 * 1024
        if show_dialog:
            self._index_progress = QProgressDialog(
                self._fmt("st_indexing", pct="0.0"),
                self.t("btn_cancel"),
                0, 100, self._main,
            )
            self._index_progress.setWindowTitle(self.t("dlg_index_title"))
            self._index_progress.setMinimumDuration(500)  # pokaż po 500ms
            self._index_progress.setAutoClose(True)
            self._index_progress.setAutoReset(True)
            self._index_progress.canceled.connect(self._cancel_indexing)
        else:
            self._index_progress = None

        self._indexer_thread = QThread()
        self._indexer_worker = IndexerWorker(path, encoding, self.index_interval_bytes)
        self._indexer_worker.moveToThread(self._indexer_thread)
        self._indexer_thread.started.connect(self._indexer_worker.run)

        # Używamy metod-slotów (nie closure) — Qt QueuedConnection wymaga
        # picklowalnych odbiorców, a closure nie jest picklowalne. To była
        # przyczyna błędu „Timers cannot be stopped from another thread" —
        #Qt nie mógł zakolejkować wywołania i wywoływał slot w worker thread.
        self._indexer_worker.progress.connect(self._on_index_progress, Qt.QueuedConnection)
        self._indexer_worker.finished.connect(self._on_index_done, Qt.QueuedConnection)
        self._indexer_worker.error.connect(self._on_index_error, Qt.QueuedConnection)
        self._indexer_worker.finished.connect(self._indexer_thread.quit, Qt.QueuedConnection)
        self._indexer_worker.error.connect(self._indexer_thread.quit, Qt.QueuedConnection)
        self._indexer_worker.finished.connect(self._indexer_worker.deleteLater, Qt.QueuedConnection)
        self._indexer_worker.error.connect(self._indexer_worker.deleteLater, Qt.QueuedConnection)
        self._indexer_thread.finished.connect(self._indexer_thread.deleteLater, Qt.QueuedConnection)
        # Cleanup dialog przy zakończeniu (sukces lub błąd).
        self._indexer_worker.finished.connect(self._close_index_progress, Qt.QueuedConnection)
        self._indexer_worker.error.connect(self._close_index_progress, Qt.QueuedConnection)
        self._indexer_thread.start()

    @Slot(float)
    def _on_index_progress(self, p: float) -> None:
        """Slot dla sygnału progress z IndexerWorker. Aktualizuje status bar
        i dialog postępu. MUSI być metodą (nie closure) żeby Qt QueuedConnection
        działał poprawnie — closure nie jest picklowalne cross-thread."""
        self._status(self._fmt("st_indexing", pct=f"{p:.1f}"))
        if self._index_progress is not None:
            self._index_progress.setValue(int(p))
            self._index_progress.setLabelText(self._fmt("st_indexing", pct=f"{p:.1f}"))

    def _cancel_indexing(self) -> None:
        """Anuluje indeksowanie — ustawia flagę w workerze. Pool zostanie
        przerwany w _build_parallel."""
        if self._indexer_worker is not None:
            self._indexer_worker.cancel()
        self._status(self.t("st_cancelling"))

    def _close_index_progress(self) -> None:
        """Zamyka dialog postępu indeksowania (sukces, błąd, anulowanie)."""
        if self._index_progress is not None:
            self._index_progress.close()
            self._index_progress = None

    @Slot(object)
    def _on_index_error(self, err: str) -> None:
        if err == "cancelled":
            # Anulowane przez usera — nie pokazuj jako błąd, tylko status.
            self._status(self.t("st_cancelled"))
            return
        QMessageBox.critical(self._main, self.t("app_title"), self.t("msg_index_error").format(e=err))
        self._status(self.t("st_ready"))

    @Slot(object)
    def _on_index_done(self, idx: LineIndexer) -> None:
        if self.indexer is not None:
            try:
                self.indexer.close()
            except Exception:
                pass
        self.indexer = idx
        self._last_file_size = idx.size
        try:
            st = os.stat(self.file_path) if self.file_path else None
            if st is not None:
                self._file_mtime_at_open = st.st_mtime_ns
                self._file_size_at_open = st.st_size
                self._last_file_inode = st.st_ino
        except OSError:
            pass
        self._status(self._fmt("st_done", total=idx.line_count, size=fmt_size(idx.size)))
        self._load_window(at_line=0)
        self._refresh_bookmarks_tree()
        self._refresh_edits_tree()
        # Zaktualizuj tytuł zakładki — nazwa pliku + liczba linii
        if self.file_path:
            self.title_changed.emit(os.path.basename(self.file_path))
        # Zaktualizuj mini-mapę — natychmiast (dla małych plików) + debounced (dla dużych)
        self._update_minimap()
        self._minimap_update_timer.start()

    # -------------------------------------------------- virtual window -----
    def _load_window(self, at_line: int) -> None:
        if not self.indexer:
            return
        self._is_loading = True

        # Pokaż progress dialog dla skakania w dużych plikach,
        # bo `indexer.read_lines` -> `offset_of_line` musi przeczytać
        # potencjalnie wiele megabajtów za pomocą readline().
        distance = abs(at_line - self.window_start)
        show_progress = distance > 100000

        if show_progress:
            progress = QProgressDialog(self.t("st_loading"), self.t("btn_cancel"), 0, 0, self._main)
            progress.setWindowTitle(self.t("app_title"))
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(500)
            progress.show()
            QApplication.processEvents()

        try:
            self._load_window_impl(at_line)
        finally:
            if show_progress:
                progress.close()

    def _load_window_impl(self, at_line: int) -> None:
        if self.filter_active and self.filter_results:
            # W trybie filtra łączymy trafienia + linie kontekstu w jedną
            # posortowaną listę numerów linii pliku. Trafienia i kontekst
            # są pokazywane razem, z różnym tłem (żółte = trafienie,
            # delikatne szaro-zielone = kontekst). Bez tego stack trace poniżej
            # błędu byłby niewidoczny w widoku filtrowanym.
            #
            # WYDAJNOŚĆ: tekst trafień jest już w filter_results (w pamięci).
            # Tylko linie kontekstu wymagają odczytu z pliku — i to batchowo
            # dla ciągłych zakresów (zamiast read_lines(ln, 1) per linia).
            hit_text_map: Dict[int, str] = {ln: text for (ln, _off, text) in self.filter_results}
            hit_lines = set(hit_text_map.keys())
            all_lines = sorted(hit_lines | self.filter_context_lines)
            n = len(all_lines)
            start = max(0, min(at_line, n - 1))
            chunk_lines = all_lines[start:start + self.window_size_lines]

            # Krok 1: zbierz teksty trafień z pamięci (zero I/O).
            lines: List[Tuple[int, str]] = []
            context_needed: List[int] = []  # linie kontekstu wymagające odczytu
            for ln in chunk_lines:
                if ln in hit_text_map:
                    lines.append((ln, hit_text_map[ln]))
                else:
                    context_needed.append(ln)

            # Krok 2: batch-odczyt kontekstu — połącz ciągłe zakresy w jedno
            # wywołanie read_lines(start, count). O(N_ranges) zamiast O(N_lines).
            if context_needed:
                context_text_map: Dict[int, str] = {}
                # Znajdź ciągłe zakresy w context_needed (posortowane).
                i = 0
                while i < len(context_needed):
                    j = i
                    while (j + 1 < len(context_needed)
                           and context_needed[j + 1] == context_needed[j] + 1):
                        j += 1
                    range_start = context_needed[i]
                    range_count = j - i + 1
                    read = self.indexer.read_lines(range_start, range_count)
                    if read:
                        for (rln, rtext) in read:
                            context_text_map[rln] = rtext
                    i = j + 1
                # Teraz złóż lines w oryginalnej kolejności chunk_lines.
                lines = []
                for ln in chunk_lines:
                    if ln in hit_text_map:
                        lines.append((ln, hit_text_map[ln]))
                    elif ln in context_text_map:
                        lines.append((ln, context_text_map[ln]))
                    # else: linia zniknęła z pliku (rotacja) — pomiń
        else:
            start = max(0, min(at_line, max(0, self.indexer.line_count - 1)))
            lines = self.indexer.read_lines(start, self.window_size_lines)

        self.window_start = start
        self.window_lines = lines
        self.line_map = [ln for (ln, _t) in lines]

        text_parts = []
        tag_data: Dict[str, List[int]] = {}
        # Indeksy widget-linii, które mają tło — do ExtraSelections.
        bookmark_widget_lines: List[int] = []
        edited_widget_lines: List[int] = []
        context_widget_lines: List[int] = []
        filter_hit_widget_lines: List[int] = []  # trafienia filtra (żółte tło)
        # W trybie filtra: sprawdź które linie są trafieniami (nie kontekstem).
        hit_line_set = set()
        if self.filter_active:
            hit_line_set = {ln for (ln, _off, _text) in self.filter_results}
        for i, (ln, text) in enumerate(lines):
            display_text, tags = self._prepare_line_for_display(ln, text)
            text_parts.append(display_text)
            for tag in tags:
                if tag not in tag_data:
                    tag_data[tag] = []
                tag_data[tag].append(i)
            if ln in self.bookmarks:
                bookmark_widget_lines.append(i)
            if self.edit_buffer.has(ln):
                edited_widget_lines.append(i)
            if ln in self.filter_context_lines:
                context_widget_lines.append(i)
            if ln in hit_line_set:
                filter_hit_widget_lines.append(i)

        self.text.setPlainText("\n".join(text_parts))
        cursor = self.text.textCursor()
        for tag, line_indices in tag_data.items():
            for li in line_indices:
                block = cursor.document().findBlockByNumber(li)
                if block.isValid():
                    self._apply_line_format(block, tag)
        # Zbuduj ExtraSelections: zakładki (zielone tło) + edycje (pomarańczowe)
        # + kontekst filtra (delikatne tło) + trafienia filtra (żółte tło)
        # + bieżąca linia (delikatne szare tło).
        # Robimy to po setPlainText, żeby selekcje odnosiły się do poprawnego dokumentu.
        self._bookmark_widget_lines = bookmark_widget_lines
        self._edited_widget_lines = edited_widget_lines
        self._context_widget_lines = context_widget_lines
        self._filter_hit_widget_lines = filter_hit_widget_lines
        self._search_extra_sel = None
        self.text.set_line_map(self.line_map)
        self._update_position_slider()
        self._update_minimap_viewport()
        cursor.movePosition(QtGui.QTextCursor.Start)
        self.text.setTextCursor(cursor)
        self._refresh_status()
        self._is_loading = False
        # Po odblokowaniu _is_loading przebuduj selekcje (kursor już na starcie).
        self._update_current_line_highlight()

    def _prepare_line_for_display(self, file_line_no: int, original_text: str) -> Tuple[str, List[str]]:
        is_edited = self.edit_buffer.has(file_line_no)
        text = self.edit_buffer.get(file_line_no) if is_edited else original_text
        display_text, was_truncated = truncate_for_display(text, max_length=self.max_display_line_length)
        tags: List[str] = []
        if is_edited:
            tags.append(TAG_EDITED)
        if file_line_no in self.bookmarks:
            tags.append(TAG_BOOKMARK)
        if was_truncated:
            tags.append(TAG_TRUNCATED)
        tags.extend(self._detect_log_tags(display_text))
        return display_text, tags

    def _detect_log_tags(self, text: str) -> List[str]:
        tags = []
        upper = text.upper()
        if " ERROR " in upper or upper.startswith("ERROR") or "[ERROR]" in upper:
            tags.append(TAG_ERROR)
        elif " WARN " in upper or " WARNING " in upper or upper.startswith("WARN") or "[WARN]" in upper:
            tags.append(TAG_WARN)
        elif " INFO " in upper or upper.startswith("INFO") or "[INFO]" in upper:
            tags.append(TAG_INFO)
        elif " DEBUG " in upper or upper.startswith("DEBUG") or "[DEBUG]" in upper:
            tags.append(TAG_DEBUG)
        return tags

    def _apply_line_format(self, block, tag: str) -> None:
        """Nakłada formatowanie dla pojedynczego tagu na bloku.

        Podział odpowiedzialności:
          - **Foreground** (kolor tekstu: ERROR/WARN/INFO/DEBUG, italic
            truncated) — przez `setBlockCharFormat`, działa niezawodnie.
          - **Background** (zakładka, edycja) — NIE tu, lecz w
            `_build_extra_selections` przez `ExtraSelections`. W Qt QPlainTextEdit
            z QSS ustawionym `background-color`, tła z charFormat są często
            ignorowane w renderowaniu. ExtraSelections zawsze się renderują.

        Metoda modyfikuje tylko foreground. Tła dokłada osobna metoda po
        pełnym przeładowaniu okna.
        """
        if not block.isValid():
            return
        cursor = QtGui.QTextCursor(block)
        fmt = cursor.blockCharFormat()
        t = self.theme
        if tag == TAG_TRUNCATED:
            fmt.setForeground(QColor(t["truncated"]))
            font = fmt.font()
            font.setItalic(True)
            fmt.setFont(font)
        elif tag == TAG_ERROR:
            fmt.setForeground(QColor(t["error"]))
        elif tag == TAG_WARN:
            fmt.setForeground(QColor(t["warn"]))
        elif tag == TAG_INFO:
            fmt.setForeground(QColor(t["info"]))
        elif tag == TAG_DEBUG:
            fmt.setForeground(QColor(t["debug"]))
        # TAG_BOOKMARK i TAG_EDITED — tło dokładane przez ExtraSelections.
        cursor.setBlockCharFormat(fmt)

    def _check_edges(self) -> None:
        if not self.indexer or self.filter_active or self._is_loading:
            return
        now = time.time()
        if (now - self._last_edge_load_time) < 0.5:
            return
        try:
            scrollbar = self.text.verticalScrollBar()
            value = scrollbar.value()
            maximum = scrollbar.maximum()
            if maximum > 0 and value >= maximum - 10 and self.line_map:
                current_last_line = self.line_map[-1] if self.line_map else 0
                next_start = current_last_line + 1
                if next_start < self.indexer.line_count:
                    new_lines = self.indexer.read_lines(next_start, self.window_size_lines)
                    if new_lines:
                        self._last_edge_load_time = now
                        self._is_loading = True
                        self._append_lines(new_lines)
                        self._is_loading = False
            elif value <= 10 and self.line_map and self.line_map[0] > 0:
                prev_start = max(0, self.line_map[0] - self.window_size_lines)
                new_lines = self.indexer.read_lines(prev_start, self.line_map[0] - prev_start)
                if new_lines:
                    self._last_edge_load_time = now
                    self._is_loading = True
                    self._prepend_lines(new_lines)
                    self._is_loading = False
        except Exception:
            self._is_loading = False

    def _append_lines(self, new_lines: List[Tuple[int, str]]) -> None:
        if not new_lines:
            return
        cursor = self.text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        for ln, text in new_lines:
            display_text, tags = self._prepare_line_for_display(ln, text)
            cursor.insertText("\n" + display_text)
            block = cursor.block()
            for tag in tags:
                self._apply_line_format(block, tag)
            self.line_map.append(ln)
        if len(self.line_map) > self.max_display_lines:
            to_remove = len(self.line_map) - self.max_display_lines
            cursor.movePosition(QtGui.QTextCursor.Start)
            for _ in range(to_remove):
                cursor.select(QtGui.QTextCursor.LineUnderCursor)
                cursor.removeSelectedText()
                cursor.deleteChar()
            self.line_map = self.line_map[to_remove:]
        self.text.set_line_map(self.line_map)
        self._update_position_slider()

    def _prepend_lines(self, new_lines: List[Tuple[int, str]]) -> None:
        if not new_lines:
            return
        old_first_file_line = self.line_map[0] if self.line_map else 0
        cursor = self.text.textCursor()
        cursor.movePosition(QtGui.QTextCursor.Start)
        for ln, text in reversed(new_lines):
            display_text, tags = self._prepare_line_for_display(ln, text)
            cursor.insertText(display_text + "\n")
        for i, (ln, text) in enumerate(new_lines):
            display_text, tags = self._prepare_line_for_display(ln, text)
            block = cursor.document().findBlockByNumber(i)
            if block.isValid():
                for tag in tags:
                    self._apply_line_format(block, tag)
        self.line_map = [ln for (ln, _t) in new_lines] + self.line_map
        if len(self.line_map) > self.max_display_lines:
            to_remove = len(self.line_map) - self.max_display_lines
            cursor.movePosition(QtGui.QTextCursor.End)
            for _ in range(to_remove):
                cursor.deletePreviousChar()
            self.line_map = self.line_map[:self.max_display_lines]
        self.text.set_line_map(self.line_map)
        try:
            new_index = self.line_map.index(old_first_file_line)
            self.text.verticalScrollBar().setValue(new_index)
        except ValueError:
            pass
        self._update_position_slider()

    # ---------------------------------------------------- position slider ---
    def _on_scroll_changed(self, value: int) -> None:
        if not self.indexer or not self.line_map or self._is_loading:
            return
        self._scroll_debounce_timer.start()

    def _on_minimap_click(self, line_no: int) -> None:
        if not self.indexer or line_no < 0:
            return
        line_no = min(line_no, self.indexer.line_count - 1)
        self._load_window(at_line=max(0, line_no - 10))

    def _update_minimap(self) -> None:
        if not self.indexer or self.indexer.line_count == 0:
            return
        total = self.indexer.line_count

        # Aby zapobiec zawieszaniu UI przy ładowaniu bardzo dużych plików (np. 25 GB)
        # rezygnujemy z pełnego skanowania pliku w poszukiwaniu tagów logów dla
        # kolorowania minimapy. Minimapa posłuży tylko jako żółty wskaźnik pozycji.
        self.minimap.set_line_data([], total)
        self._update_minimap_viewport()

    def _update_minimap_viewport(self) -> None:
        if not self.indexer or self.indexer.line_count == 0 or not self.line_map:
            return
        try:
            cursor = self.text.cursorForPosition(QPoint(0, 5))
            first_line = self.line_map[cursor.blockNumber()] if cursor.blockNumber() < len(self.line_map) else 0
            cursor_bottom = self.text.cursorForPosition(QPoint(0, self.text.height() - 5))
            last_line = self.line_map[cursor_bottom.blockNumber()] if cursor_bottom.blockNumber() < len(self.line_map) else self.indexer.line_count - 1
            total = self.indexer.line_count
            self.minimap.set_viewport(first_line / total, last_line / total)
        except Exception:
            pass

    def _update_slider_from_scroll(self) -> None:
        if not self.indexer or not self.line_map or self._is_loading:
            return
        try:
            cursor = self.text.cursorForPosition(QPoint(0, 5))
            widget_line = cursor.blockNumber()
            if 0 <= widget_line < len(self.line_map):
                file_line = self.line_map[widget_line]
                if self.filter_active:
                    total = max(1, len(self.filter_results))
                else:
                    total = max(1, self.indexer.line_count)
                pct = int(file_line / total * 1000)
                self.pct_label.setText(f"{pct // 10}%")
                self._update_minimap_viewport()
        except Exception:
            pass

    def _update_position_slider(self) -> None:
        if not self.indexer or self.indexer.line_count == 0:
            self.pct_label.setText("0%")
            return
        if self.filter_active:
            total = max(1, len(self.filter_results))
        else:
            total = max(1, self.indexer.line_count)
        pct = int(self.window_start / total * 1000)
        self.pct_label.setText(f"{pct // 10}%")

    # -------------------------------------------------------------- find ----
    def cmd_find_dialog(self) -> None:
        self._main.search_entry.setFocus()
        self._main.search_entry.selectAll()

    def _compile_search(self) -> Optional[str]:
        pattern = self._main.search_entry.text().strip()
        if not pattern:
            return None
        use_regex = self._main.search_regex_cb.isChecked()
        case = self._main.search_case_cb.isChecked()
        negate = self._main.search_negate_cb.isChecked()
        if use_regex:
            try:
                flags = 0 if case else re.IGNORECASE
                self._search_compiled = re.compile(pattern, flags)
            except re.error as e:
                QMessageBox.critical(self._main, self.t("app_title"), self.t("msg_filter_error").format(e=e))
                return None
        else:
            self._search_compiled = None
        self.search_pattern = pattern
        self._search_case = case
        self._search_negate = negate
        return pattern

    def _search_pattern_changed(self) -> bool:
        pattern = self._main.search_entry.text().strip()
        use_regex = self._main.search_regex_cb.isChecked()
        case = self._main.search_case_cb.isChecked()
        negate = self._main.search_negate_cb.isChecked()
        if pattern != self.search_pattern:
            return True
        if use_regex != self._last_search_regex:
            return True
        if case != self._last_search_case:
            return True
        if negate != self._last_search_negate:
            return True
        return False

    def _start_background_search(self) -> None:
        if not self.indexer or not self.file_path:
            return
        pattern = self._compile_search()
        if pattern is None:
            return
        self.search_pattern = pattern
        self._last_search_regex = self._main.search_regex_cb.isChecked()
        self._last_search_case = self._main.search_case_cb.isChecked()
        self._last_search_negate = self._main.search_negate_cb.isChecked()

        # Anuluj poprzednie wyszukiwanie
        if self._search_engine and self._search_engine.is_running():
            self._search_engine.cancel()
        # Sprawdź czy stary thread żyje — deleteLater może go już zwolnić
        if self._search_thread is not None:
            try:
                if self._search_thread.isRunning():
                    self._search_thread.quit()
                    self._search_thread.wait(2000)
            except RuntimeError:
                pass
        self._search_thread = None
        self._search_worker = None

        if self._search_engine is None or self._search_engine.path != self.file_path:
            self._search_engine = FilterEngine(self.file_path, self.indexer)

        self._search_results = []
        self._search_results_all = []
        self._search_result_index = -1
        if self._search_model:
            self._search_model.clear()
        self._search_results_label.setText(self.t("lbl_search_results_searching"))
        self._status(self._fmt("st_filtering", pct=0.0, hits=0))

        self._search_thread = QThread()
        self._search_worker = FilterWorker(
            self._search_engine, pattern,
            self._last_search_regex, self._last_search_case, self._last_search_negate,
        )
        self._search_worker.moveToThread(self._search_thread)
        self._search_thread.started.connect(self._search_worker.run)
        self._search_worker.progress.connect(self._on_search_progress, Qt.QueuedConnection)
        self._search_worker.finished.connect(self._on_search_finished, Qt.QueuedConnection)
        self._search_worker.finished.connect(self._search_thread.quit, Qt.QueuedConnection)
        self._search_worker.finished.connect(self._search_worker.deleteLater, Qt.QueuedConnection)
        self._search_thread.finished.connect(self._search_thread.deleteLater, Qt.QueuedConnection)
        self._search_thread.start()

    @Slot(float, int)
    def _on_search_progress(self, pct: float, hits: int) -> None:
        self._status(self._fmt("st_filtering", pct=f"{pct:.1f}", hits=hits))
        self._search_results_label.setText(
            f"{self.t('lbl_search_results_searching')} ({hits})"
        )

    @Slot(list, object)
    def _on_search_finished(self, results, error) -> None:
        if error:
            self._search_results_label.setText(self.t("lbl_search_results_empty"))
            return
        self._search_results_all = [(ln, text) for (ln, _off, text) in results]
        total_hits = len(self._search_results_all)

        self._search_results = self._search_results_all

        if self._search_model:
            self._search_model.set_results(self._search_results)

        self._status(self.t("st_search_done").format(n=total_hits))

        if total_hits == 0:
            self._search_results_label.setText(self.t("lbl_search_results_empty"))
            return

        # Skocz do pierwszego wyniku — odroczone przez QTimer.singleShot
        self._search_result_index = 0
        QTimer.singleShot(0, lambda: self._navigate_to_search_result(0))

        self._update_search_results_label()

    def _update_search_results_label(self) -> None:
        total = len(self._search_results_all)
        if total == 0:
            self._search_results_label.setText(self.t("lbl_search_results_empty"))
        else:
            current = self._search_result_index + 1
            self._search_results_label.setText(
                self.t("lbl_search_results_count").format(n=total, current=current, total=total)
            )

    def _navigate_to_search_result(self, index: int) -> None:
        if not self._search_results_all or index < 0 or index >= len(self._search_results_all):
            return
        self._search_result_index = index
        line_no, _text = self._search_results_all[index]
        if self.filter_active:
            keys = [r[0] for r in self.filter_results]
            idx = bisect.bisect_left(keys, line_no)
            self._load_window(at_line=max(0, idx - 10))
        else:
            start = max(0, line_no - 10)
            self._load_window(at_line=start)
        for i, fl in enumerate(self.line_map):
            if fl == line_no:
                self._highlight_and_scroll(i)
                break
        if self._search_model and index < len(self._search_results):
            model_index = self._search_model.index(index, 0)
            self.search_results_view.setCurrentIndex(model_index)
            self.search_results_view.scrollTo(model_index, QtWidgets.QAbstractItemView.PositionAtCenter)
        self._update_search_results_label()

    @Slot(QtCore.QModelIndex)
    def _on_search_result_clicked(self, index: QtCore.QModelIndex) -> None:
        if not index.isValid():
            return
        row = index.row()
        if 0 <= row < len(self._search_results):
            self._navigate_to_search_result(row)

    def cmd_find_next(self) -> None:
        if not self.indexer:
            return
        if self._search_pattern_changed() or not self._search_results_all:
            self._start_background_search()
            return
        if self._search_result_index < len(self._search_results_all) - 1:
            self._navigate_to_search_result(self._search_result_index + 1)
        else:
            self._navigate_to_search_result(0)

    def cmd_find_prev(self) -> None:
        if not self.indexer:
            return
        if self._search_pattern_changed() or not self._search_results_all:
            self._start_background_search()
            return
        if self._search_result_index > 0:
            self._navigate_to_search_result(self._search_result_index - 1)
        else:
            self._navigate_to_search_result(len(self._search_results_all) - 1)

    def cmd_clear_search(self) -> None:
        if self._search_engine and self._search_engine.is_running():
            self._search_engine.cancel()
        if self._search_thread is not None:
            try:
                if self._search_thread.isRunning():
                    self._search_thread.quit()
                    self._search_thread.wait(2000)
            except RuntimeError:
                pass
        self._search_thread = None
        self._search_worker = None

        self.search_pattern = ""
        self._search_results = []
        self._search_results_all = []
        self._search_result_index = -1
        self._search_extra_sel = None

        if self._search_model:
            self._search_model.clear()

        self._search_results_label.setText(self.t("lbl_search_results_empty"))
        self._main.search_entry.clear()

        self._update_current_line_highlight()
        self._refresh_status()

    def _get_display_text(self, file_line_no: int, widget_line_idx: int) -> str:
        if self.edit_buffer.has(file_line_no):
            return self.edit_buffer.get(file_line_no)
        if widget_line_idx < len(self.window_lines):
            return self.window_lines[widget_line_idx][1]
        return ""

    def _highlight_and_scroll(self, widget_line_no: int) -> None:
        cursor = QtGui.QTextCursor(self.text.document().findBlockByNumber(widget_line_no))
        cursor.select(QtGui.QTextCursor.LineUnderCursor)
        sel = QtWidgets.QTextEdit.ExtraSelection()
        sel.cursor = cursor
        sel.format.setBackground(QColor(self.theme["highlight"]))
        self._search_extra_sel = sel
        # Po setTextCursor cursorPositionChanged odpali się i przebuduje listę
        # ExtraSelections łącznie z bieżącą linią + tym podświetleniem.
        self.text.setTextCursor(cursor)

    def _update_current_line_highlight(self) -> None:
        """Przebudowuje listę ExtraSelections.

        Kolejność dodawania (OSTATNIE wygrywa w Qt):
          1. Kontekst filtra (delikatne szaro-zielone tło) — rysowane pierwsze,
             najniższy priorytet.
          2. Trafienia filtra (żółte tło) — wyższy priorytet niż kontekst.
          3. Zakładki (zielone tło).
          4. Edycje (pomarańczowe tło) — nadpisuje zakładkę dla edytowanej linii.
          5. Podświetlenie wyniku wyszukiwania (żółte, silniejsze).
          6. Bieżąca linia (delikatne szare tło) — POMIJANE gdy linia ma już
             inne tło (zakładka/edycja/wynik wyszukiwania/trafienie). Bez tego
             current_line (rysowane na końcu z FullWidthSelection) przykryłoby
             zielone tło zakładki i wyglądałoby, jakby zakładka się nie dodała.

        W praktyce:
          - Linia z kursorem, bez zakładki → delikatne szare tło.
          - Linia z kursorem i z zakładką → zielone tło (bez szarego nakładania).
          - Linia z zakładką, bez kursora → zielone tło.
          - Linia kontekstowa filtra → delikatne szaro-zielone tło.
          - Trafienie filtra → żółte tło (wyraźnie odróżnia od kontekstu).
        """
        if self._is_loading:
            return
        sels: List[QtWidgets.QTextEdit.ExtraSelection] = []
        doc = self.text.document()
        t = self.theme

        bookmark_set = set(getattr(self, "_bookmark_widget_lines", []))
        edited_set = set(getattr(self, "_edited_widget_lines", []))
        context_set = set(getattr(self, "_context_widget_lines", []))
        filter_hit_set = set(getattr(self, "_filter_hit_widget_lines", []))

        # 1) Kontekst filtra — delikatne tło (pierwsze, najniższy priorytet).
        for li in context_set:
            block = doc.findBlockByNumber(li)
            if block.isValid():
                sel = QtWidgets.QTextEdit.ExtraSelection()
                sel.cursor = QtGui.QTextCursor(block)
                sel.cursor.select(QtGui.QTextCursor.LineUnderCursor)
                sel.format.setBackground(QColor(t["context"]))
                sels.append(sel)

        # 2) Trafienia filtra — żółte tło (wyraźnie odróżnia od kontekstu).
        # Tylko gdy filtr jest aktywny — bez filtra lista jest pusta.
        for li in filter_hit_set:
            block = doc.findBlockByNumber(li)
            if block.isValid():
                sel = QtWidgets.QTextEdit.ExtraSelection()
                sel.cursor = QtGui.QTextCursor(block)
                sel.cursor.select(QtGui.QTextCursor.LineUnderCursor)
                sel.format.setBackground(QColor(t["highlight"]))
                sels.append(sel)

        # 3) Zakładki — zielone tło.
        for li in bookmark_set:
            block = doc.findBlockByNumber(li)
            if block.isValid():
                sel = QtWidgets.QTextEdit.ExtraSelection()
                sel.cursor = QtGui.QTextCursor(block)
                sel.cursor.select(QtGui.QTextCursor.LineUnderCursor)
                sel.format.setBackground(QColor(t["bookmark"]))
                sels.append(sel)

        # 4) Edycje — pomarańczowe tło (nadpisuje zakładkę dla edytowanej linii).
        for li in edited_set:
            block = doc.findBlockByNumber(li)
            if block.isValid():
                sel = QtWidgets.QTextEdit.ExtraSelection()
                sel.cursor = QtGui.QTextCursor(block)
                sel.cursor.select(QtGui.QTextCursor.LineUnderCursor)
                sel.format.setBackground(QColor(t["edited"]))
                sels.append(sel)

        # 5) Podświetlenie wyniku wyszukiwania (żółte, silniejsze).
        search_block = -1
        if self._search_extra_sel is not None:
            sels.append(self._search_extra_sel)
            search_block = self._search_extra_sel.cursor.blockNumber()

        # 6) Bieżąca linia — delikatne tło, pełna szerokość.
        # POMIŃ jeśli linia ma już inne tło (zakładka/edycja/wynik/trafienie).
        # Kontekst filtra NIE blokuje current_line — kontekst jest delikatny.
        current_block = self.text.textCursor().blockNumber()
        if (current_block not in bookmark_set
                and current_block not in edited_set
                and current_block not in filter_hit_set
                and current_block != search_block):
            cur = QtWidgets.QTextEdit.ExtraSelection()
            cur_cursor = QtGui.QTextCursor(self.text.textCursor())
            cur_cursor.select(QtGui.QTextCursor.LineUnderCursor)
            cur.cursor = cur_cursor
            cur.format.setBackground(QColor(t["current_line"]))
            cur.format.setProperty(QtGui.QTextFormat.FullWidthSelection, True)
            sels.append(cur)

        self.text.setExtraSelections(sels)

    # ------------------------------------------------------------ filter ---
    def cmd_filter_dialog(self) -> None:
        self._main.filter_entry.setFocus()
        self._main.filter_entry.selectAll()

    def cmd_apply_filter(self) -> None:
        if not self.indexer:
            QMessageBox.information(self._main, self.t("app_title"), self.t("msg_no_file"))
            return
        pattern = self._main.filter_entry.text().strip()
        if not pattern:
            self.cmd_clear_filter()
            return
        use_regex = self._main.filter_regex_cb.isChecked()
        case = self._main.filter_case_cb.isChecked()
        negate = self._main.filter_negate_cb.isChecked()
        # Ile linii kontekstu po każdym trafieniu (0 = bez kontekstu).
        # Przydatne dla stack trace PHP/Python — pokazuje błąd + N linii poniżej.
        self._filter_context_after = 0
        if hasattr(self._main, "filter_context_spin"):
            self._filter_context_after = int(self._main.filter_context_spin.value())

        if use_regex:
            try:
                flags = 0 if case else re.IGNORECASE
                re.compile(pattern, flags)
            except re.error as e:
                QMessageBox.critical(self._main, self.t("app_title"), self.t("msg_filter_error").format(e=e))
                return

        if self.filter_engine and self.filter_engine.is_running():
            self.filter_engine.cancel()

        if self.filter_engine is None or self.filter_engine.path != self.file_path:
            self.filter_engine = FilterEngine(self.file_path, self.indexer)
        self.filter_active = True
        self.filter_results = []

        self._filter_thread = QThread()
        self._filter_worker = FilterWorker(self.filter_engine, pattern, use_regex, case, negate)
        self._filter_worker.moveToThread(self._filter_thread)
        self._filter_thread.started.connect(self._filter_worker.run)
        self._filter_worker.progress.connect(self._on_filter_progress, Qt.QueuedConnection)
        self._filter_worker.finished.connect(self._on_filter_done, Qt.QueuedConnection)
        self._filter_worker.finished.connect(self._filter_thread.quit, Qt.QueuedConnection)
        self._filter_worker.finished.connect(self._filter_worker.deleteLater, Qt.QueuedConnection)
        self._filter_thread.finished.connect(self._filter_thread.deleteLater, Qt.QueuedConnection)
        self._filter_thread.start()
        self._status(self._fmt("st_filtering", pct=0.0, hits=0))

    @Slot(float, int)
    def _on_filter_progress(self, pct: float, hits: int) -> None:
        self._status(self._fmt("st_filtering", pct=f"{pct:.1f}", hits=hits))

    @Slot(list, object)
    def _on_filter_done(self, results, error) -> None:
        if error:
            QMessageBox.critical(self._main, self.t("app_title"), self.t("msg_filter_error").format(e=error))
            self.filter_active = False
            self._refresh_status()
            self._update_position_slider()
            return
        self.filter_results = results
        if not results:
            QMessageBox.information(self._main, self.t("app_title"), self.t("msg_no_matches"))
            self.filter_active = False
            self._refresh_status()
            self._update_position_slider()
            return
        # Buduj linie kontekstu: dla każdego trafienia dokładaj N następujących
        # linii (z pominięciem duplikatów i samych trafień). Używane głównie
        # dla stack trace PHP/Python — przefiltrowany błąd + kontekst poniżej.
        self._build_filter_context()
        self._load_window(at_line=0)
        self._status(self._fmt("st_filtered", hits=len(results), total=self.indexer.line_count))

    def _build_filter_context(self) -> None:
        """Generuje zbiór numerów linii pliku będących kontekstem filtru.

        Dla każdego trafienia w filter_results dokładamy N następujących linii
        (linie 1..N po trafieniu). Linie te są potem pokazywane w widoku
        filtrowanym razem z trafieniami, z delikatnym tłem (TAG_CONTEXT).
        Duplikaty są eliminowane przez set. Same trafienia NIE są kontekstem.

        Wymaga filter_active + indexer + _filter_context_after > 0.
        """
        self.filter_context_lines = set()
        if not self.filter_active or not self.indexer:
            return
        n = self._filter_context_after
        if n <= 0:
            return
        hit_lines = {ln for (ln, _off, _text) in self.filter_results}
        total = self.indexer.line_count
        for ln in hit_lines:
            for offset in range(1, n + 1):
                ctx = ln + offset
                if ctx >= total:
                    break
                if ctx not in hit_lines:
                    self.filter_context_lines.add(ctx)

    def cmd_clear_filter(self, silent: bool = False) -> None:
        was_active = self.filter_active
        if self.filter_engine and self.filter_engine.is_running():
            self.filter_engine.cancel()
        self.filter_active = False
        self.filter_results = []
        self.filter_context_lines = set()
        self._filter_context_after = 0
        if not silent:
            self._main.filter_entry.clear()
        if was_active and self.indexer:
            self._load_window(at_line=0)
        else:
            self._update_position_slider()
        self._refresh_status()

    # ------------------------------------------------------------- goto ----
    def cmd_goto(self) -> None:
        if not self.indexer:
            QMessageBox.information(self._main, self.t("app_title"), self.t("msg_no_file"))
            return
        answer, ok = QInputDialog.getText(
            self._main, self.t("dlg_goto_title"), self.t("dlg_goto_prompt"),
            QtWidgets.QLineEdit.Normal, "",
        )
        if not ok or not answer:
            return
        answer = answer.strip()
        if answer.startswith("b:") or answer.startswith("B:"):
            try:
                byte_offset = int(answer[2:])
            except ValueError:
                QMessageBox.critical(self._main, self.t("app_title"), "Invalid byte offset")
                return
            byte_offset = max(0, min(byte_offset, self.indexer.size))
            line_no, _ = self.indexer.line_at_byte_offset(byte_offset)
        else:
            try:
                line_no = int(answer)
            except ValueError:
                QMessageBox.critical(self._main, self.t("app_title"), "Invalid line number")
                return
            line_no = max(1, line_no) - 1
            line_no = min(line_no, max(0, self.indexer.line_count - 1))

        if self.filter_active:
            keys = [r[0] for r in self.filter_results]
            idx = bisect.bisect_left(keys, line_no)
            self._load_window(at_line=idx)
        else:
            self._load_window(at_line=line_no)
        self.text.verticalScrollBar().setValue(0)

    def cmd_goto_end(self) -> None:
        if not self.indexer:
            return
        if self.filter_active:
            total = max(1, len(self.filter_results))
            self._load_window(at_line=total - 1)
        else:
            total = max(1, self.indexer.line_count)
            self._load_window(at_line=total - 1)

        # Opcjonalnie można zjechać scrollem na sam dół
        scrollbar = self.text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    # ------------------------------------------------------------ edit ----
    def cmd_format_selection(self) -> None:
        """Pobiera zaznaczony tekst i wywołuje dialog do jego sformatowania."""
        cursor = self.text.textCursor()
        if not cursor.hasSelection():
            # Jeżeli nie ma zaznaczenia, bierzemy całą bieżącą linię
            cursor.select(QtGui.QTextCursor.LineUnderCursor)

        selected_text = cursor.selectedText().replace("\u2029", "\n")

        if not selected_text.strip():
            return

        dialog = FormatDialog(self, selected_text, self._last_formatter)
        dialog.exec()

        # Zapisz na przyszłość (tylko w sesji) wybór formattera
        self._last_formatter = dialog.get_selected_formatter()

    def cmd_edit_line(self) -> None:
        if not self.indexer:
            QMessageBox.information(self._main, self.t("app_title"), self.t("msg_no_file"))
            return
        cursor = self.text.textCursor()
        # Jeśli kursor jest POZA widocznym obszarem (np. po przewinięciu
        # widoku po wyniku wyszukiwania), użyj pierwszej widocznej linii.
        # Bez tego user widzi jedną linię, ale edytuje inną (tę, na której
        # pozostał kursor) — to było zgłoszone jako błąd „edytuje linię
        # kilka pozycji niżej".
        cursor_rect = self.text.cursorRect(cursor)
        viewport_rect = self.text.viewport().rect()
        if not viewport_rect.contains(cursor_rect.topLeft()):
            fvb = self.text.firstVisibleBlock()
            if fvb.isValid():
                cursor = QtGui.QTextCursor(fvb)
                self.text.setTextCursor(cursor)
        widget_line = cursor.blockNumber()
        if widget_line < 0 or widget_line >= len(self.line_map):
            return
        file_line = self.line_map[widget_line]
        current_text = self._get_display_text(file_line, widget_line)

        dialog = QDialog(self._main)
        dialog.setWindowTitle(self.t("dlg_edit_title").format(n=file_line + 1))
        dialog.setMinimumWidth(500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(self.t("dlg_edit_title").format(n=file_line + 1)))
        edit = QPlainTextEdit()
        edit.setPlainText(current_text)
        edit.setMinimumHeight(120)
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        if self.edit_buffer.has(file_line):
            revert_btn = QPushButton(self.t("mi_clear_edits"))
            buttons.addButton(revert_btn, QDialogButtonBox.ActionRole)
            revert_btn.clicked.connect(dialog.reject)
            revert_btn.clicked.connect(lambda: self._revert_edit(file_line))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            new_text = edit.toPlainText().rstrip("\n")
            self.edit_buffer.set(file_line, new_text)
            self._refresh_edits_tree()
            self._reload_current_view()
            self._refresh_status()

    def _revert_edit(self, file_line: int) -> None:
        if self.edit_buffer.has(file_line):
            self.edit_buffer.discard(file_line)
            self._refresh_edits_tree()
            self._reload_current_view()
            self._refresh_status()

    def cmd_save_edits(self) -> None:
        if not self.file_path or not self.indexer:
            QMessageBox.information(self._main, self.t("app_title"), self.t("msg_no_file"))
            return
        if len(self.edit_buffer) == 0:
            QMessageBox.information(self._main, self.t("app_title"), self.t("msg_no_edits"))
            return
        size = fmt_size(self.indexer.size)
        # Ostrzeżenie o czasie zapisu dla dużych plików
        save_warning = ""
        if self.indexer.size > 1 * 1024 * 1024 * 1024:  # > 1 GB
            est_seconds = self.indexer.size / (500 * 1024 * 1024)  # ~500 MB/s
            if est_seconds > 5:
                save_warning = f"\n\n⚠️ Plik ma {size} — zapis potrwa ~{est_seconds:.0f}s."
        if not QMessageBox.question(
            self._main, self.t("app_title"),
            self.t("msg_confirm_save").format(n=len(self.edit_buffer), size=size, path=self.file_path) + save_warning,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            return

        if self.follow_active:
            self.cmd_toggle_follow()

        self._save_progress = QProgressDialog(self.t("mi_save"), self.t("btn_cancel"), 0, 100, self._main)
        self._save_progress.setWindowTitle(self.t("mi_save"))
        self._save_progress.setWindowModality(Qt.WindowModal)
        self._save_progress.setValue(0)

        self._save_thread = QThread()
        self._save_worker = SaveWorker(
            self.edit_buffer, self.file_path,
            self._file_mtime_at_open, self._file_size_at_open,
            self.encoding,
        )
        self._save_worker.moveToThread(self._save_thread)
        self._save_thread.started.connect(self._save_worker.run)
        # QueuedConnection + metoda-slot (nie lambda) — cross-thread safe.
        self._save_worker.progress.connect(self._save_progress.setValue, Qt.QueuedConnection)
        self._save_worker.finished.connect(self._on_save_done, Qt.QueuedConnection)
        self._save_worker.error.connect(self._on_save_error, Qt.QueuedConnection)
        self._save_worker.file_changed.connect(self._on_save_file_changed, Qt.QueuedConnection)
        self._save_worker.compressed.connect(self._on_save_compressed, Qt.QueuedConnection)
        self._save_worker.finished.connect(self._save_thread.quit, Qt.QueuedConnection)
        self._save_worker.error.connect(self._save_thread.quit, Qt.QueuedConnection)
        self._save_worker.file_changed.connect(self._save_thread.quit, Qt.QueuedConnection)
        self._save_worker.compressed.connect(self._save_thread.quit, Qt.QueuedConnection)
        # deleteLater — zwolnij pamięć C++ po zakończeniu
        self._save_worker.finished.connect(self._save_worker.deleteLater, Qt.QueuedConnection)
        self._save_worker.error.connect(self._save_worker.deleteLater, Qt.QueuedConnection)
        self._save_worker.file_changed.connect(self._save_worker.deleteLater, Qt.QueuedConnection)
        self._save_worker.compressed.connect(self._save_worker.deleteLater, Qt.QueuedConnection)
        self._save_thread.finished.connect(self._save_thread.deleteLater)
        self._save_thread.start()

    @Slot(str)
    def _on_save_done(self, backup_path: str) -> None:
        if self._save_progress:
            self._save_progress.close()
            self._save_progress = None
        QMessageBox.information(self._main, self.t("app_title"),
                                self.t("msg_save_ok").format(n=len(self.edit_buffer), path=self.file_path))
        try:
            cursor = self.text.textCursor()
            saved_line = self.line_map[cursor.blockNumber()] if self.line_map else 0
        except Exception:
            saved_line = 0
        self.edit_buffer.clear()
        self._refresh_edits_tree()
        self._start_reindex(saved_line)

    def _start_reindex(self, saved_line: int) -> None:
        self._status(self.t("st_opening"))
        self._reindex_saved_line = saved_line
        self._indexer_thread = QThread()
        self._indexer_worker = IndexerWorker(self.file_path, self.encoding, self.index_interval_bytes)
        self._indexer_worker.moveToThread(self._indexer_thread)
        self._indexer_thread.started.connect(self._indexer_worker.run)
        # QueuedConnection + metoda-slot (nie lambda) — closure nie jest
        # picklowalne cross-thread, powoduje błędy QTimer w worker thread.
        self._indexer_worker.progress.connect(self._on_index_progress, Qt.QueuedConnection)
        self._indexer_worker.finished.connect(self._on_reindex_finished, Qt.QueuedConnection)
        self._indexer_worker.error.connect(self._on_index_error, Qt.QueuedConnection)
        self._indexer_worker.finished.connect(self._indexer_thread.quit, Qt.QueuedConnection)
        self._indexer_worker.error.connect(self._indexer_thread.quit, Qt.QueuedConnection)
        self._indexer_worker.finished.connect(self._indexer_worker.deleteLater, Qt.QueuedConnection)
        self._indexer_worker.error.connect(self._indexer_worker.deleteLater, Qt.QueuedConnection)
        self._indexer_thread.finished.connect(self._indexer_thread.deleteLater, Qt.QueuedConnection)
        self._indexer_thread.start()

    @Slot(object)
    def _on_reindex_finished(self, idx: LineIndexer) -> None:
        """Slot dla sygnału finished z reindex workera — przekazuje do
        _on_reindex_after_save z zapamiętanym saved_line."""
        saved_line = getattr(self, "_reindex_saved_line", 0)
        self._on_reindex_after_save(idx, saved_line)

    @Slot(object, int)
    def _on_reindex_after_save(self, idx: LineIndexer, saved_line: int) -> None:
        if self.indexer is not None:
            try:
                self.indexer.close()
            except Exception:
                pass
        self.indexer = idx
        self._last_file_size = idx.size
        try:
            st = os.stat(self.file_path) if self.file_path else None
            if st is not None:
                self._file_mtime_at_open = st.st_mtime_ns
                self._file_size_at_open = st.st_size
                self._last_file_inode = st.st_ino
        except OSError:
            pass
        self._status(self._fmt("st_done", total=idx.line_count, size=fmt_size(idx.size)))
        self._load_window(at_line=saved_line)

    @Slot(str)
    def _on_save_error(self, err: str) -> None:
        if self._save_progress:
            self._save_progress.close()
            self._save_progress = None
        QMessageBox.critical(self._main, self.t("app_title"), f"Save error: {err}")

    @Slot(str)
    def _on_save_file_changed(self, err: str) -> None:
        if self._save_progress:
            self._save_progress.close()
            self._save_progress = None
        choice = QMessageBox.question(
            self._main, self.t("app_title"),
            self.t("msg_file_changed").format(error=err, n=len(self.edit_buffer)),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if choice == QMessageBox.Cancel:
            self._refresh_status()
            return
        if choice == QMessageBox.Yes:
            self.edit_buffer.clear()
            self._refresh_edits_tree()
            try:
                cursor = self.text.textCursor()
                saved_line = self.line_map[cursor.blockNumber()] if self.line_map else 0
            except Exception:
                saved_line = 0
            self._start_reindex(saved_line)
        else:
            self._refresh_status()
            QMessageBox.information(self._main, self.t("app_title"), self.t("msg_save_as_suggested"))

    @Slot(str)
    def _on_save_compressed(self, err: str) -> None:
        if self._save_progress:
            self._save_progress.close()
            self._save_progress = None
        QMessageBox.warning(self._main, self.t("app_title"), self.t("mi_compressed_warn"))

    def cmd_clear_edits(self) -> None:
        if len(self.edit_buffer) == 0:
            return
        if not QMessageBox.question(
            self._main, self.t("app_title"),
            self.t("msg_clear_edits").format(n=len(self.edit_buffer)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            return
        self.edit_buffer.clear()
        self._refresh_edits_tree()
        self._reload_current_view()
        self._refresh_status()

    def cmd_save_as(self) -> None:
        if not self.file_path or not self.indexer:
            QMessageBox.information(self._main, self.t("app_title"), self.t("msg_no_file"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self._main, self.t("mi_save_as"), "", "Log files (*.log);;Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            with open_maybe_compressed(self.file_path, "rb") as src, \
                 open_maybe_compressed(path, "wb") as dst:
                line_no = 0
                for raw in src:
                    if line_no in self.edit_buffer._edits:
                        new_text = self.edit_buffer._edits[line_no]
                        dst.write(new_text.encode(self.encoding, errors="replace"))
                        if not new_text.endswith("\n"):
                            dst.write(b"\n")
                    else:
                        dst.write(raw)
                    line_no += 1
            QMessageBox.information(self._main, self.t("app_title"),
                                    self.t("msg_save_ok").format(n=len(self.edit_buffer), path=path))
        except Exception as e:
            QMessageBox.critical(self._main, self.t("app_title"), f"Save error: {e}")

    # ----------------------------------------------------------- export ----
    def cmd_export(self) -> None:
        if not self.indexer:
            QMessageBox.information(self._main, self.t("app_title"), self.t("msg_no_file"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self._main, self.t("dlg_export_title"), "", "Log files (*.log);;Text files (*.txt);;All files (*)"
        )
        if not path:
            return
        try:
            count = 0
            if self.filter_active and self.filter_results:
                with open_maybe_compressed(path, "wb") as out:
                    for (ln, _off, text) in self.filter_results:
                        out.write(text.encode(self.encoding, errors="replace"))
                        out.write(b"\n")
                        count += 1
            else:
                with open_maybe_compressed(self.file_path, "rb") as src, \
                     open_maybe_compressed(path, "wb") as out:
                    for raw in src:
                        out.write(raw)
                        count += 1
            QMessageBox.information(self._main, self.t("app_title"),
                                    self.t("msg_exported").format(n=count, path=path))
        except Exception as e:
            QMessageBox.critical(self._main, self.t("app_title"), f"Export error: {e}")

    # -------------------------------------------------------- bookmarks ----
    def cmd_toggle_bookmark(self) -> None:
        """Przełącza zakładkę w LINII KURSORA.

        Działa wyłącznie na jednej linii — bez względu na to, czy istnieje
        selekcja. To celowa decyzja UX: wieloliniowe, przypadkowe selekcje
        (np. Shift+klik po przewinięciu, Cmd+A) nie powinny powodować
        masowego zakładkowania całego zakresu. Przed dodaniem zakładki
        selekcja jest czyszczona (anulowana), żeby zniknęło podświetlenie
        Qt, które można pomylić z kolorem zakładki.
        """
        cursor = self.text.textCursor()
        # Wyczyść selekcję (zostaw sam kursor) — eliminuje wizualne mylenie
        # zaznaczenia Qt z kolorem zakładki i zapobiega przypadkowym
        # operacjom na wielu liniach. clearSelection() zostawia kursor w
        # obecnej pozycji bez ryzyka przesunięcia na koniec dokumentu.
        if cursor.hasSelection():
            cursor.clearSelection()
            self.text.setTextCursor(cursor)
            cursor = self.text.textCursor()
        widget_line = cursor.blockNumber()
        if widget_line < 0 or widget_line >= len(self.line_map):
            return
        file_line = self.line_map[widget_line]
        if file_line in self.bookmarks:
            del self.bookmarks[file_line]
            self._status(self.t("msg_bookmark_removed").format(n=file_line + 1))
        else:
            self.bookmarks[file_line] = None
            self._status(self.t("msg_bookmark_added").format(n=file_line + 1))
        self._refresh_bookmarks_tree()
        self._reload_current_view()
        # Po reload ustaw kursor z powrotem na tę samą linię — bez tego
        # _load_window przesuwa go na początek dokumentu.
        block = self.text.document().findBlockByNumber(widget_line)
        if block.isValid():
            new_cur = QtGui.QTextCursor(block)
            self.text.setTextCursor(new_cur)

    def _refresh_bookmarks_tree(self) -> None:
        self.bm_tree.clear()
        for ln in sorted(self.bookmarks.keys()):
            item = QTreeWidgetItem([str(ln + 1)])
            item.setData(0, Qt.UserRole, ln)
            self.bm_tree.addTopLevelItem(item)

    def _refresh_edits_tree(self) -> None:
        self.ed_tree.clear()
        for ln in sorted(self.edit_buffer._edits.keys()):
            item = QTreeWidgetItem([str(ln + 1)])
            item.setData(0, Qt.UserRole, ln)
            self.ed_tree.addTopLevelItem(item)

    def _goto_bookmark(self) -> None:
        item = self.bm_tree.currentItem()
        if not item:
            return
        ln = item.data(0, Qt.UserRole)
        self._goto_file_line(ln)

    def _goto_edit(self) -> None:
        item = self.ed_tree.currentItem()
        if not item:
            return
        ln = item.data(0, Qt.UserRole)
        self._goto_file_line(ln)

    def _goto_file_line(self, ln: int) -> None:
        if self.filter_active:
            keys = [r[0] for r in self.filter_results]
            idx = bisect.bisect_left(keys, ln)
            self._load_window(at_line=idx)
        else:
            self._load_window(at_line=ln)
        self.text.verticalScrollBar().setValue(0)

    def _delete_selected_bookmarks(self) -> None:
        """Usuwa wszystkie zaznaczone w drzewie Zakładki.

        Po usunięciu zaznacza następny element w drzewie (jak w IDE —
        zaznaczenie „przesuwa się" na kolejny wpis, zamiast znikać).
        """
        items = self.bm_tree.selectedItems()
        if not items:
            self._status(self.t("msg_no_selection"))
            return
        # Zapamiętaj indeks pierwszego zaznaczonego elementu — po odświeżeniu
        # spróbujemy zaznaczyć element na tej samej pozycji (czyli następny).
        first_selected_idx = self.bm_tree.indexOfTopLevelItem(items[0])
        removed = 0
        for item in items:
            ln = item.data(0, Qt.UserRole)
            if ln in self.bookmarks:
                del self.bookmarks[ln]
                removed += 1
        if removed:
            self._refresh_bookmarks_tree()
            self._reload_current_view()
            self._status(self.t("msg_bookmarks_removed").format(n=removed))
            # Auto-zaznacz następny element na tej samej pozycji.
            count = self.bm_tree.topLevelItemCount()
            if count > 0:
                next_idx = min(first_selected_idx, count - 1)
                self.bm_tree.setCurrentItem(self.bm_tree.topLevelItem(next_idx))

    def _delete_selected_edits(self) -> None:
        """Usuwa wszystkie zaznaczone w drzewie Edycje (czyści bufor dla nich).

        Po usunięciu zaznacza następny element w drzewie (jak w IDE).
        """
        items = self.ed_tree.selectedItems()
        if not items:
            self._status(self.t("msg_no_selection"))
            return
        first_selected_idx = self.ed_tree.indexOfTopLevelItem(items[0])
        removed = 0
        for item in items:
            ln = item.data(0, Qt.UserRole)
            if self.edit_buffer.has(ln):
                self.edit_buffer.discard(ln)
                removed += 1
        if removed:
            self._refresh_edits_tree()
            self._reload_current_view()
            self._refresh_status()
            self._status(self.t("msg_edits_removed").format(n=removed))
            # Auto-zaznacz następny element na tej samej pozycji.
            count = self.ed_tree.topLevelItemCount()
            if count > 0:
                next_idx = min(first_selected_idx, count - 1)
                self.ed_tree.setCurrentItem(self.ed_tree.topLevelItem(next_idx))

    def cmd_next_bookmark(self) -> None:
        if not self.bookmarks:
            QMessageBox.information(self._main, self.t("app_title"), self.t("msg_no_bookmarks"))
            return
        cursor = self.text.textCursor()
        current_file_line = self.line_map[cursor.blockNumber()] if self.line_map else -1
        sorted_bms = sorted(self.bookmarks.keys())
        for ln in sorted_bms:
            if ln > current_file_line:
                self._goto_file_line(ln)
                return
        self._goto_file_line(sorted_bms[0])

    def cmd_prev_bookmark(self) -> None:
        if not self.bookmarks:
            QMessageBox.information(self._main, self.t("app_title"), self.t("msg_no_bookmarks"))
            return
        cursor = self.text.textCursor()
        current_file_line = self.line_map[cursor.blockNumber()] if self.line_map else (self.indexer.line_count if self.indexer else 0)
        sorted_bms = sorted(self.bookmarks.keys(), reverse=True)
        for ln in sorted_bms:
            if ln < current_file_line:
                self._goto_file_line(ln)
                return
        self._goto_file_line(sorted_bms[0])

    def cmd_clear_bookmarks(self) -> None:
        if not self.bookmarks:
            return
        self.bookmarks.clear()
        self._refresh_bookmarks_tree()
        self._reload_current_view()

    # ----------------------------------------------------------- follow ----
    def cmd_toggle_follow(self) -> None:
        if not self.indexer:
            return
        self.follow_active = not self.follow_active
        if self._main._follow_action is not None:
            self._main._follow_action.setChecked(self.follow_active)
        if self.follow_active:
            self._last_file_size = self.indexer.size
            try:
                self._last_file_inode = os.stat(self.file_path).st_ino
            except OSError:
                self._last_file_inode = 0
            self._follow_poll()
        else:
            self._refresh_status()

    def _follow_poll(self) -> None:
        if not self.follow_active or not self.file_path:
            return
        if self._follow_reindexing:
            QTimer.singleShot(FOLLOW_POLL_MS, self._follow_poll)
            return
        try:
            current_stat = os.stat(self.file_path)
        except OSError:
            QTimer.singleShot(FOLLOW_POLL_MS, self._follow_poll)
            return
        current_size = current_stat.st_size
        current_inode = current_stat.st_ino

        if current_inode != self._last_file_inode and self._last_file_inode != 0:
            self._follow_reindexing = True
            self._start_follow_reindex(current_size, current_inode)
            QTimer.singleShot(FOLLOW_POLL_MS, self._follow_poll)
            return

        mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(current_stat.st_mtime))
        ctime_str = time.strftime("%H:%M:%S")

        if current_size > self._last_file_size:
            next_poll = 200
            try:
                new_lines = self.indexer.update_from(current_size)
                self._last_file_size = current_size
                if new_lines > 0:
                    self._on_follow_new_lines(new_lines, mtime_str, ctime_str)
            except Exception:
                pass
        elif current_size < self._last_file_size:
            next_poll = FOLLOW_POLL_MS
            self._follow_reindexing = True
            self._start_follow_reindex(current_size, current_inode)
        else:
            next_poll = 1000
            self._status(self.t("st_following").format(mtime=mtime_str, ctime=ctime_str))
        QTimer.singleShot(next_poll, self._follow_poll)

    def _start_follow_reindex(self, current_size: int, current_inode: int) -> None:
        self._follow_reindex_size = current_size
        self._follow_reindex_inode = current_inode
        self._indexer_thread = QThread()
        self._indexer_worker = IndexerWorker(self.file_path, self.encoding, self.index_interval_bytes)
        self._indexer_worker.moveToThread(self._indexer_thread)
        self._indexer_thread.started.connect(self._indexer_worker.run)
        # QueuedConnection + metoda-slot (nie lambda) — closure nie jest
        # picklowalne cross-thread, powoduje błędy QTimer w worker thread.
        self._indexer_worker.finished.connect(self._on_follow_reindex_slot, Qt.QueuedConnection)
        self._indexer_worker.error.connect(self._on_follow_reindex_failed, Qt.QueuedConnection)
        self._indexer_worker.finished.connect(self._indexer_thread.quit, Qt.QueuedConnection)
        self._indexer_worker.error.connect(self._indexer_thread.quit, Qt.QueuedConnection)
        self._indexer_worker.finished.connect(self._on_follow_reindex_clear_flag, Qt.QueuedConnection)
        self._indexer_worker.error.connect(self._on_follow_reindex_clear_flag, Qt.QueuedConnection)
        self._indexer_worker.finished.connect(self._indexer_worker.deleteLater, Qt.QueuedConnection)
        self._indexer_worker.error.connect(self._indexer_worker.deleteLater, Qt.QueuedConnection)
        self._indexer_thread.finished.connect(self._indexer_thread.deleteLater, Qt.QueuedConnection)
        self._indexer_thread.start()

    @Slot(object)
    def _on_follow_reindex_slot(self, idx: LineIndexer) -> None:
        """Slot pośredniczący — odbiera idx z workera i woła _on_follow_reindex
        z zapamiętanymi parametrami. Bez lambdy (cross-thread safe)."""
        size = getattr(self, "_follow_reindex_size", 0)
        inode = getattr(self, "_follow_reindex_inode", 0)
        self._on_follow_reindex(idx, size, inode)

    @Slot()
    def _on_follow_reindex_clear_flag(self) -> None:
        """Czyści flagę _follow_reindexing po zakończeniu reindex."""
        self._follow_reindexing = False

    def _on_follow_new_lines(self, new_line_count: int = 0, mtime_str: str = "", ctime_str: str = "") -> None:
        if not self.indexer or self.indexer.line_count == 0:
            return
        if new_line_count > 0 and self.line_map:
            current_last = self.line_map[-1] if self.line_map else -1
            if current_last >= 0 and current_last + 1 < self.indexer.line_count:
                distance_to_end = self.indexer.line_count - current_last
                if distance_to_end < self.window_size_lines:
                    last_start = max(0, self.indexer.line_count - self.window_size_lines)
                    self._load_window(at_line=last_start)
                    self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())
        elif not self.line_map:
            last_start = max(0, self.indexer.line_count - self.window_size_lines)
            self._load_window(at_line=last_start)
            self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())
        self._status(self.t("st_following").format(mtime=mtime_str, ctime=ctime_str))

    @Slot(object, int, int)
    def _on_follow_reindex(self, idx: LineIndexer, new_size: int, new_inode: int) -> None:
        if self.indexer is not None:
            try:
                self.indexer.close()
            except Exception:
                pass
        self.indexer = idx
        self._last_file_size = new_size
        if new_inode != 0:
            self._last_file_inode = new_inode
        last_start = max(0, idx.line_count - self.window_size_lines)
        self._load_window(at_line=last_start)
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())
        mtime_str = ""
        try:
            mtime = os.stat(self.file_path).st_mtime
            mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
        except OSError:
            mtime_str = "?"
        ctime_str = time.strftime("%H:%M:%S")
        self._status(self.t("st_following").format(mtime=mtime_str, ctime=ctime_str))

    @Slot(str)
    def _on_follow_reindex_failed(self, err: str) -> None:
        self._status(self.t("st_follow_reindex_failed"))

    # ----------------------------------------------------------- encoding ---
    def cmd_set_encoding(self, encoding: str) -> None:
        if encoding == self.encoding:
            return
        self._main.encoding = encoding
        self._main.config.set("encoding", encoding)
        if self.file_path and self.indexer:
            try:
                cursor = self.text.textCursor()
                saved_line = self.line_map[cursor.blockNumber()] if self.line_map else 0
            except Exception:
                saved_line = 0
            try:
                self.indexer.close()
            except Exception:
                pass
            self._start_reindex(saved_line)

    # --------------------------------------------------------- misc ----
    def _reload_current_view(self) -> None:
        if not self.indexer:
            return
        self._load_window(at_line=self.window_start)

    def _refresh_status(self) -> None:
        if not self.indexer:
            self._status(self.t("st_ready"))
            return
        if self.filter_active:
            left = self._fmt("st_filtered", hits=len(self.filter_results), total=self.indexer.line_count)
        else:
            left = self._fmt("st_done", total=self.indexer.line_count, size=fmt_size(self.indexer.size))
        if len(self.edit_buffer) > 0:
            left += "   |   " + self.t("st_edits").format(n=len(self.edit_buffer))
        self._status(left)

    def close(self) -> None:
        """Zamyka indexer, anuluje wątki. Wywoływane przy zamykaniu zakładki."""
        try:
            if self._edge_timer:
                self._edge_timer.stop()
            if self._minimap_update_timer:
                self._minimap_update_timer.stop()
            if self._scroll_debounce_timer:
                self._scroll_debounce_timer.stop()
        except Exception:
            pass
        if self.filter_engine and self.filter_engine.is_running():
            self.filter_engine.cancel()
        if self._search_engine and self._search_engine.is_running():
            self._search_engine.cancel()
        # Anuluj indeksowanie w tle — bez tego pool multiprocessing będzie
        # działał dalej nawet po zamknięciu karty.
        if self._indexer_worker is not None:
            try:
                self._indexer_worker.cancel()
            except Exception:
                pass
        for t in (self._indexer_thread, self._filter_thread, self._save_thread, self._search_thread):
            if t is not None:
                try:
                    if t.isRunning():
                        t.quit()
                        t.wait(2000)
                except RuntimeError:
                    pass
        if self.indexer is not None:
            try:
                self.indexer.close()
            except Exception:
                pass


# =====================================================================
# LogViewerWindow — kontroler z QTabWidget
# =====================================================================

class LogViewerWindow(QMainWindow):
    """Główna aplikacja PySide6 — kontroler zarządzający zakładkami.

    Per-tab cleanup pattern (w LogTab.close / open_file / cmd_save_edits):
        self._indexer_worker.deleteLater
        self._filter_worker.deleteLater
        self._save_worker.deleteLater
        self._indexer_thread.deleteLater
        self._filter_thread.deleteLater
        self._save_thread.deleteLater

    Ostrzeżenie o czasie zapisu dla dużych plików (est_seconds / save_warning /
    "zapis potrwa") znajduje się w LogTab.cmd_save_edits.
    """

    def __init__(self, config: Optional[UserConfig] = None,
                 initial_file: Optional[str] = None):
        super().__init__()
        self.config = config if config is not None else UserConfig()
        self.lang = self.config.get("language", "pl")
        self.encoding: str = self.config.get("encoding", DEFAULT_ENCODING)
        self.window_size_lines: int = int(self.config.get("window_size_lines", WINDOW_SIZE_LINES))
        self.max_display_lines: int = int(self.config.get("max_display_lines", MAX_DISPLAY_LINES))
        self.max_display_line_length: int = int(self.config.get("max_display_line_length", MAX_DISPLAY_LINE_LENGTH))
        self.index_interval_bytes: int = int(self.config.get("index_interval_bytes", 1024 * 1024))
        self.font_family: Optional[str] = self.config.get("font_family", None)
        self.font_size: int = int(self.config.get("font_size", 10))

        # Aktywny motyw (dark/light) — wykrywany z systemu
        self.theme: dict = THEME_DARK
        self._follow_action: Optional[QAction] = None
        self._enc_action_group: Optional[QtGui.QActionGroup] = None

        # Build UI
        self._build_ui()
        self._apply_language()
        self._apply_theme()

        if initial_file:
            QTimer.singleShot(100, lambda: self.open_file_in_tab(initial_file))

    # ------------------------------------------------------------------ delegation
    def __getattr__(self, name: str):
        """Deleguje tab-specific atrybuty/metody do aktywnej zakładki.

        Wywoływane tylko gdy normalne wyszukiwanie atrybutu zawiedzie.
        Dzięki temu window.text, window.position_slider, window._search_results,
        window._load_window(), window._on_index_done() itp. działają tak samo
        jak przed refaktorem (delegując do aktywnej zakładki).
        """
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        tabs = self.__dict__.get("tabs")
        if tabs is None:
            raise AttributeError(name)
        tab = tabs.currentWidget()
        if tab is None:
            raise AttributeError(name)
        return getattr(tab, name)

    def _delegate_to_tab(self, method_name: str, *args, **kwargs):
        """Woła metodę na aktywnej zakładce. No-op jeśli brak zakładki."""
        tab = self.tabs.currentWidget()
        if tab is None:
            return None
        method = getattr(tab, method_name, None)
        if method is None:
            return None
        return method(*args, **kwargs)

    # ------------------------------------------------------------------ i18n
    def t(self, key: str) -> str:
        return I18N[self.lang].get(key, key)

    def _fmt(self, msg_key: str, **kw) -> str:
        return self.t(msg_key).format(**kw)

    def set_language(self, lang: str) -> None:
        if lang not in I18N:
            return
        self.lang = lang
        self.config.set("language", lang)
        self._apply_language()
        self._rebuild_menubar()
        # Retranslate przycisków paska narzędzi
        if hasattr(self, "btn_find_next"):
            self.btn_find_next.setText(self.t("btn_find_next"))
        if hasattr(self, "btn_find_prev"):
            self.btn_find_prev.setText(self.t("btn_find_prev"))
        if hasattr(self, "btn_clear_search"):
            self.btn_clear_search.setText(self.t("btn_clear_search"))
        if hasattr(self, "btn_apply_filter"):
            self.btn_apply_filter.setText(self.t("btn_apply_filter"))
        if hasattr(self, "btn_clear_filter"):
            self.btn_clear_filter.setText(self.t("btn_clear_filter"))

        # Zaktualizuj labelki i opcje paska narzędzi
        if hasattr(self, "lbl_search"):
            self.lbl_search.setText(self.t("lbl_search"))
        if hasattr(self, "lbl_filter"):
            self.lbl_filter.setText(self.t("lbl_filter"))
        if hasattr(self, "lbl_filter_context"):
            self.lbl_filter_context.setText(self.t("lbl_filter_context"))
        if hasattr(self, "search_regex_cb"):
            self.search_regex_cb.setText(self.t("cb_regex"))
        if hasattr(self, "search_case_cb"):
            self.search_case_cb.setText(self.t("cb_case"))
        if hasattr(self, "search_negate_cb"):
            self.search_negate_cb.setText(self.t("cb_negate"))
        if hasattr(self, "filter_regex_cb"):
            self.filter_regex_cb.setText(self.t("cb_regex"))
        if hasattr(self, "filter_case_cb"):
            self.filter_case_cb.setText(self.t("cb_case"))
        if hasattr(self, "filter_negate_cb"):
            self.filter_negate_cb.setText(self.t("cb_negate"))

        # Zaktualizuj każdy tab (labelki paneli bocznych, etc.)
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, LogTab):
                tab._lbl_bookmarks.setText(self.t("lbl_bookmarks"))
                tab._lbl_edits.setText(self.t("lbl_edits"))
                tab.bm_tree.setHeaderLabels([self.t("col_line")])
                tab.ed_tree.setHeaderLabels([self.t("col_line")])
                tab.btn_del_bookmarks.setText(self.t("btn_delete_sel"))
                tab.btn_del_edits.setText(self.t("btn_delete_sel"))
                tab._search_results_label.setText(self.t("lbl_search_results_empty"))
                tab._refresh_status()
        tab = self.tabs.currentWidget()
        if isinstance(tab, LogTab):
            tab._refresh_status()
        else:
            self.statusBar().showMessage(self.t("st_ready"))

    def _apply_language(self) -> None:
        self.setWindowTitle(self.t("app_title"))

    # ------------------------------------------------------------------ UI
    def _detect_system_theme(self) -> dict:
        """Wykrywa motyw systemowy (dark/light) i zwraca odpowiedni THEME."""
        try:
            from PySide6.QtGui import QGuiApplication
            hints = QGuiApplication.styleHints()
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return THEME_DARK
            elif scheme == Qt.ColorScheme.Light:
                return THEME_LIGHT
        except Exception:
            pass
        try:
            from PySide6.QtGui import QPalette
            pal = QtWidgets.QApplication.palette()
            bg = pal.color(QPalette.Window)
            text_color = pal.color(QPalette.WindowText)
            if text_color.lightness() > bg.lightness():
                return THEME_DARK
            return THEME_LIGHT
        except Exception:
            pass
        return THEME_DARK

    def _apply_theme(self) -> None:
        """Aplikuje motyw (dark lub light) zgodny z systemem operacyjnym."""
        self.theme = self._detect_system_theme()
        t = self.theme
        qss = f"""
            QMainWindow, QWidget {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
            }}
            QPlainTextEdit, QTextEdit {{
                background-color: {t["bg_main"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                font-family: "Menlo", "DejaVu Sans Mono", "Consolas", "Courier New", monospace;
            }}
            QToolBar {{
                background-color: {t["bg_panel"]};
                border: none;
                border-bottom: 1px solid {t["border"]};
                spacing: 2px;
                padding: 2px;
            }}
            QMenuBar {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
                border-bottom: 1px solid {t["border"]};
            }}
            QMenuBar::item:selected {{
                background-color: {t["accent"]};
            }}
            QMenu {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
            }}
            QMenu::item:selected {{
                background-color: {t["accent"]};
            }}
            QStatusBar {{
                background-color: {t["bg_statusbar"]};
                color: {t["fg_dim"]};
                border-top: 1px solid {t["border"]};
            }}
            QLabel {{
                color: {t["fg_main"]};
                background: transparent;
            }}
            QLineEdit {{
                background-color: {t["bg_input"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                padding: 2px 4px;
                border-radius: 2px;
            }}
            QLineEdit:focus {{
                border: 1px solid {t["accent"]};
            }}
            QCheckBox {{
                color: {t["fg_main"]};
                spacing: 4px;
            }}
            QPushButton {{
                background-color: {t["bg_input"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                padding: 3px 10px;
                border-radius: 2px;
                font-weight: normal;
            }}
            QPushButton:hover {{
                background-color: {t["bg_hover"]};
                border: 1px solid {t["border_light"]};
            }}
            QPushButton:pressed {{
                background-color: {t["bg_selected"]};
                color: {t["fg_bright"]};
            }}
            QSlider {{
                background: transparent;
            }}
            QSlider::groove:vertical {{
                background: {t["bg_input"]};
                width: 6px;
                border-radius: 3px;
            }}
            QSlider::handle:vertical {{
                background: {t["accent"]};
                height: 16px;
                width: 12px;
                margin: -5px 0;
                border-radius: 3px;
            }}
            QTreeWidget {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                alternate-background-color: {t["bg_alt"]};
            }}
            QTreeWidget::item:selected {{
                background-color: {t["bg_selected"]};
            }}
            QListView {{
                background-color: {t["bg_main"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                alternate-background-color: {t["bg_alt"]};
            }}
            QListView::item:selected {{
                background-color: {t["bg_selected"]};
            }}
            QSplitter::handle {{
                background-color: {t["border"]};
            }}
            QSplitter::handle:horizontal {{
                width: 2px;
            }}
            QSplitter::handle:vertical {{
                height: 2px;
            }}
            QTabWidget::pane {{
                border: 1px solid {t["border"]};
                top: -1px;
            }}
            QTabBar::tab {{
                background-color: {t["bg_panel"]};
                color: {t["fg_dim"]};
                border: 1px solid {t["border"]};
                padding: 4px 12px;
                border-bottom: none;
            }}
            QTabBar::tab:selected {{
                background-color: {t["bg_main"]};
                color: {t["fg_main"]};
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {t["bg_hover"]};
            }}
            QTabBar::close-button {{
                image: none;
                subcontrol-position: right;
                border: 1px solid {t["border"]};
                border-radius: 2px;
                padding: 2px;
                margin: 2px;
            }}
            QTabBar::close-button:hover {{
                background-color: {t["accent"]};
            }}
            QHeaderView::section {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
                border: none;
                border-bottom: 1px solid {t["border"]};
                padding: 2px 4px;
            }}
            QScrollBar:vertical {{
                background: {t["bg_main"]};
                width: 10px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {t["border_light"]};
                min-height: 20px;
                border-radius: 5px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {t["fg_dim"]};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar:horizontal {{
                background: {t["bg_main"]};
                height: 10px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {t["border_light"]};
                min-width: 20px;
                border-radius: 5px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: {t["fg_dim"]};
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
            QProgressBar {{
                background-color: {t["bg_input"]};
                border: 1px solid {t["border"]};
                border-radius: 2px;
                text-align: center;
                color: {t["fg_main"]};
            }}
            QProgressBar::chunk {{
                background-color: {t["accent"]};
                border-radius: 1px;
            }}
            QProgressDialog {{
                background-color: {t["bg_panel"]};
            }}
            QDialog {{
                background-color: {t["bg_panel"]};
                color: {t["fg_main"]};
            }}
            QSpinBox, QComboBox, QFontComboBox {{
                background-color: {t["bg_input"]};
                color: {t["fg_main"]};
                border: 1px solid {t["border"]};
                padding: 2px;
                border-radius: 2px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {t["bg_input"]};
                color: {t["fg_main"]};
                selection-background-color: {t["accent"]};
            }}
        """
        self.setStyleSheet(qss)
        # Zaktualizuj każdy tab
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, LogTab):
                tab._apply_theme()

    def _build_ui(self) -> None:
        self.setMinimumSize(900, 600)
        self.setAcceptDrops(True)

        central = QWidget()
        self.setCentralWidget(central)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # QTabWidget — każda zakładka to LogTab
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        central_layout.addWidget(self.tabs)

        self._build_toolbar()
        self._rebuild_menubar()
        self.statusBar().showMessage(self.t("st_ready"))

    def _build_toolbar(self) -> None:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.lbl_search = QLabel(self.t("lbl_search"))
        toolbar.addWidget(self.lbl_search)
        self.search_entry = QLineEdit()
        self.search_entry.setMaximumWidth(250)
        self.search_entry.returnPressed.connect(self.cmd_find_next)
        toolbar.addWidget(self.search_entry)

        self.search_regex_cb = QCheckBox(self.t("cb_regex"))
        self.search_case_cb = QCheckBox(self.t("cb_case"))
        self.search_negate_cb = QCheckBox(self.t("cb_negate"))
        toolbar.addWidget(self.search_regex_cb)
        toolbar.addWidget(self.search_case_cb)
        toolbar.addWidget(self.search_negate_cb)

        # Kolejność: Następny po lewej, Poprzedni po prawej (wg preferencji
        # użytkownika) — Enter w polu wyszukiwania nadal = Następny.
        self.btn_find_next = QPushButton(self.t("btn_find_next"))
        self.btn_find_next.clicked.connect(self.cmd_find_next)
        toolbar.addWidget(self.btn_find_next)
        self.btn_find_prev = QPushButton(self.t("btn_find_prev"))
        self.btn_find_prev.clicked.connect(self.cmd_find_prev)
        toolbar.addWidget(self.btn_find_prev)
        self.btn_clear_search = QPushButton(self.t("btn_clear_search"))
        self.btn_clear_search.clicked.connect(self.cmd_clear_search)
        toolbar.addWidget(self.btn_clear_search)

        toolbar.addSeparator()

        self.lbl_filter = QLabel(self.t("lbl_filter"))
        toolbar.addWidget(self.lbl_filter)
        self.filter_entry = QLineEdit()
        self.filter_entry.setMaximumWidth(250)
        self.filter_entry.returnPressed.connect(self.cmd_apply_filter)
        toolbar.addWidget(self.filter_entry)

        self.filter_regex_cb = QCheckBox(self.t("cb_regex"))
        self.filter_case_cb = QCheckBox(self.t("cb_case"))
        self.filter_negate_cb = QCheckBox(self.t("cb_negate"))
        toolbar.addWidget(self.filter_regex_cb)
        toolbar.addWidget(self.filter_case_cb)
        toolbar.addWidget(self.filter_negate_cb)

        # Separator — oddziela opcje filtru (regex/case/negacja) od kontekstu.
        toolbar.addSeparator()

        # Ile linii kontekstu po każdym trafieniu filtru (dla stack trace).
        self.lbl_filter_context = QLabel(self.t("lbl_filter_context"))
        toolbar.addWidget(self.lbl_filter_context)
        self.filter_context_spin = QSpinBox()
        self.filter_context_spin.setRange(0, 50)
        self.filter_context_spin.setValue(0)
        self.filter_context_spin.setFixedWidth(56)
        self.filter_context_spin.setToolTip(self.t("tt_filter_context"))
        toolbar.addWidget(self.filter_context_spin)

        self.btn_apply_filter = QPushButton(self.t("btn_apply_filter"))
        self.btn_apply_filter.clicked.connect(self.cmd_apply_filter)
        toolbar.addWidget(self.btn_apply_filter)
        self.btn_clear_filter = QPushButton(self.t("btn_clear_filter"))
        self.btn_clear_filter.clicked.connect(self.cmd_clear_filter)
        toolbar.addWidget(self.btn_clear_filter)

    def _rebuild_menubar(self) -> None:
        menubar = self.menuBar()
        menubar.clear()

        file_menu = menubar.addMenu(self.t("menu_file"))
        file_menu.addAction(self._mkaction(self.t("mi_open"), "Ctrl+O", self.cmd_open))
        file_menu.addAction(self._mkaction(self.t("mi_save"), "Ctrl+S", self.cmd_save_edits))
        file_menu.addAction(self._mkaction(self.t("mi_save_as"), "", self.cmd_save_as))
        file_menu.addSeparator()
        file_menu.addAction(self._mkaction(self.t("mi_export"), "Ctrl+E", self.cmd_export))
        file_menu.addSeparator()
        file_menu.addAction(self._mkaction(self.t("mi_exit"), "Ctrl+Q", self.close))

        edit_menu = menubar.addMenu(self.t("menu_edit"))
        edit_menu.addAction(self._mkaction(self.t("mi_find"), "Ctrl+F", self.cmd_find_dialog))
        edit_menu.addAction(self._mkaction(self.t("mi_find_next"), "F3", self.cmd_find_next))
        edit_menu.addAction(self._mkaction(self.t("mi_find_prev"), "Shift+F3", self.cmd_find_prev))
        edit_menu.addAction(self._mkaction(self.t("btn_clear_search"), "", self.cmd_clear_search))
        edit_menu.addSeparator()
        edit_menu.addAction(self._mkaction(self.t("mi_filter"), "Ctrl+L", self.cmd_filter_dialog))
        edit_menu.addAction(self._mkaction(self.t("mi_clear_filter"), "", self.cmd_clear_filter))
        edit_menu.addSeparator()
        edit_menu.addAction(self._mkaction(self.t("mi_edit_line"), "Ctrl+D", self.cmd_edit_line))
        edit_menu.addAction(self._mkaction(self.t("mi_format_selection"), "Ctrl+K", self.cmd_format_selection))
        edit_menu.addSeparator()
        edit_menu.addAction(self._mkaction(self.t("mi_save_edits"), "", self.cmd_save_edits))
        edit_menu.addAction(self._mkaction(self.t("mi_clear_edits"), "", self.cmd_clear_edits))

        view_menu = menubar.addMenu(self.t("menu_view"))
        self._follow_action = self._mkaction(self.t("mi_follow"), "", self.cmd_toggle_follow)
        self._follow_action.setCheckable(True)
        view_menu.addAction(self._follow_action)
        view_menu.addSeparator()

        enc_menu = view_menu.addMenu(self.t("mi_encoding"))
        self._enc_action_group = QtGui.QActionGroup(self)
        self._enc_action_group.setExclusive(True)
        for enc_code, enc_label in SUPPORTED_ENCODINGS:
            act = enc_menu.addAction(enc_label)
            act.setCheckable(True)
            act.setChecked(enc_code == self.encoding)
            act.triggered.connect(lambda checked, c=enc_code: self.cmd_set_encoding(c))
            self._enc_action_group.addAction(act)
        view_menu.addSeparator()

        lang_menu = view_menu.addMenu(self.t("mi_lang"))

        self._lang_action_group = QtGui.QActionGroup(self)
        self._lang_action_group.setExclusive(True)

        act_pl = self._mkaction(self.t("mi_lang_pl"), "", lambda: self.set_language("pl"))
        act_pl.setCheckable(True)
        act_pl.setChecked(self.lang == "pl")
        self._lang_action_group.addAction(act_pl)
        lang_menu.addAction(act_pl)

        act_en = self._mkaction(self.t("mi_lang_en"), "", lambda: self.set_language("en"))
        act_en.setCheckable(True)
        act_en.setChecked(self.lang == "en")
        self._lang_action_group.addAction(act_en)
        lang_menu.addAction(act_en)

        view_menu.addSeparator()
        view_menu.addAction(self._mkaction(self.t("mi_settings"), "", self.cmd_settings))

        bm_menu = menubar.addMenu(self.t("menu_bookmarks"))
        bm_menu.addAction(self._mkaction(self.t("mi_toggle_bookmark"), "Ctrl+B", self.cmd_toggle_bookmark))
        bm_menu.addAction(self._mkaction(self.t("mi_next_bookmark"), "F4", self.cmd_next_bookmark))
        bm_menu.addAction(self._mkaction(self.t("mi_prev_bookmark"), "Shift+F4", self.cmd_prev_bookmark))
        bm_menu.addSeparator()
        bm_menu.addAction(self._mkaction(self.t("mi_clear_bookmarks"), "", self.cmd_clear_bookmarks))

        goto_menu = menubar.addMenu(self.t("menu_goto"))
        goto_menu.addAction(self._mkaction(self.t("mi_goto"), "Ctrl+G", self.cmd_goto))
        goto_menu.addAction(self._mkaction(self.t("mi_goto_end"), "Ctrl+End", self.cmd_goto_end))

        help_menu = menubar.addMenu(self.t("menu_help"))
        help_menu.addAction(self._mkaction(self.t("mi_about"), "", self.cmd_about))

    def _mkaction(self, label: str, shortcut: str, handler) -> QAction:
        act = QAction(label, self)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(handler)
        return act

    # --------------------------------------------------------- tab mgmt ---
    def _new_tab(self, title: str = "") -> LogTab:
        """Tworzy nową pustą zakładkę i ustawia ją jako aktywną."""
        tab = LogTab(self)
        # Połącz sygnały tab z oknem
        tab.status_changed.connect(self._on_tab_status_changed)
        tab.title_changed.connect(lambda title_str, t=tab: self._on_tab_title_changed(t, title_str))
        self.tabs.addTab(tab, title or self.t("st_ready"))
        self.tabs.setCurrentWidget(tab)
        # Aplikuj motyw do nowej zakładki
        tab._apply_theme()
        tab._apply_font_to_text()
        return tab

    def open_file_in_tab(self, path: str) -> Optional[LogTab]:
        """Otwiera plik w nowej zakładce."""
        if not os.path.isfile(path):
            QMessageBox.critical(self, self.t("app_title"), self.t("msg_no_file"))
            return None
        tab = self._new_tab(title=os.path.basename(path))
        tab.open_file(path)
        return tab

    def _on_tab_status_changed(self, msg: str) -> None:
        """Aktualizuje status bar — tylko dla aktywnej zakładki."""
        sender = self.sender()
        if sender is None or sender is self.tabs.currentWidget():
            self.statusBar().showMessage(msg)

    def _on_tab_title_changed(self, tab: LogTab, title: str) -> None:
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.setTabText(index, title)

    def _on_tab_changed(self, index: int) -> None:
        """Aktualizuje status bar, slider, minimap i follow action po zmianie zakładki."""
        if index < 0:
            self.statusBar().showMessage(self.t("st_ready"))
            if self._follow_action is not None:
                self._follow_action.setChecked(False)
            return
        tab = self.tabs.widget(index)
        if isinstance(tab, LogTab):
            tab._refresh_status()
            tab._update_position_slider()
            tab._update_minimap_viewport()
            if self._follow_action is not None:
                self._follow_action.setChecked(tab.follow_active)
            # Auto-focus na text widget — bez tego szare podświetlenie
            # bieżącej linii nie jest widoczne po przełączeniu zakładki
            # (kursor jest tam, ale Qt nie renderuje ExtraSelections dla
            # nieważnego widżetu). setFocus + _update_current_line_highlight
            # wymusza przerysowanie.
            tab.text.setFocus()
            tab._update_current_line_highlight()

    def _on_tab_close_requested(self, index: int) -> None:
        tab = self.tabs.widget(index)
        if not isinstance(tab, LogTab):
            return
        if len(tab.edit_buffer) > 0:
            choice = QMessageBox.question(
                self, self.t("app_title"),
                self.t("msg_clear_edits").format(n=len(tab.edit_buffer)),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if choice != QMessageBox.Yes:
                return
        tab.close()
        self.tabs.removeTab(index)
        tab.deleteLater()

    # ----------------------------------------------------------- file ops ---
    def cmd_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.t("mi_open"), "", OPEN_FILETYPES)
        if path:
            self.open_file_in_tab(path)

    # ---------------------------------------------- delegated cmd_* methods ---
    # Te metody są definiowane na oknie aby toolbar/menubar mogły się do nich
    # podpiąć przy budowie UI (zanim istnieje jakakolwiek zakładka). Delegują
    # wywołanie do aktywnej zakładki (tabs.currentWidget()).

    def cmd_find_dialog(self):
        return self._delegate_to_tab("cmd_find_dialog")

    def cmd_find_next(self):
        return self._delegate_to_tab("cmd_find_next")

    def cmd_find_prev(self):
        return self._delegate_to_tab("cmd_find_prev")

    def cmd_clear_search(self):
        return self._delegate_to_tab("cmd_clear_search")

    def cmd_filter_dialog(self):
        return self._delegate_to_tab("cmd_filter_dialog")

    def cmd_apply_filter(self):
        return self._delegate_to_tab("cmd_apply_filter")

    def cmd_clear_filter(self, silent: bool = False):
        return self._delegate_to_tab("cmd_clear_filter", silent)

    def cmd_goto(self):
        return self._delegate_to_tab("cmd_goto")

    def cmd_goto_end(self):
        return self._delegate_to_tab("cmd_goto_end")

    def cmd_edit_line(self):
        return self._delegate_to_tab("cmd_edit_line")

    def cmd_format_selection(self):
        return self._delegate_to_tab("cmd_format_selection")

    def cmd_save_edits(self):
        return self._delegate_to_tab("cmd_save_edits")

    def cmd_save_as(self):
        return self._delegate_to_tab("cmd_save_as")

    def cmd_export(self):
        return self._delegate_to_tab("cmd_export")

    def cmd_clear_edits(self):
        return self._delegate_to_tab("cmd_clear_edits")

    def cmd_toggle_bookmark(self):
        return self._delegate_to_tab("cmd_toggle_bookmark")

    def cmd_next_bookmark(self):
        return self._delegate_to_tab("cmd_next_bookmark")

    def cmd_prev_bookmark(self):
        return self._delegate_to_tab("cmd_prev_bookmark")

    def cmd_clear_bookmarks(self):
        return self._delegate_to_tab("cmd_clear_bookmarks")

    def cmd_toggle_follow(self):
        return self._delegate_to_tab("cmd_toggle_follow")

    def cmd_set_encoding(self, encoding: str):
        return self._delegate_to_tab("cmd_set_encoding", encoding)

    # --------------------------------------------------------- settings ---
    def cmd_settings(self) -> None:
        dialog = SettingsDialog(self, self)
        if dialog.exec() == QDialog.Accepted:
            family, size, ws, md, ml, ii = dialog.get_values()
            font_changed = (family != self.font_family) or (size != self.font_size)
            self.font_family = family
            self.font_size = size
            self.window_size_lines = ws
            self.max_display_lines = md
            self.max_display_line_length = ml
            self.index_interval_bytes = ii
            self.config.set("font_family", family)
            self.config.set("font_size", size)
            self.config.set("window_size_lines", ws)
            self.config.set("max_display_lines", md)
            self.config.set("max_display_line_length", ml)
            self.config.set("index_interval_bytes", ii)
            # Aplikuj zmiany do wszystkich zakładek
            for i in range(self.tabs.count()):
                t = self.tabs.widget(i)
                if not isinstance(t, LogTab):
                    continue
                if font_changed:
                    t._apply_font_to_text()
                if t.file_path and t.indexer:
                    if t.indexer.index_interval_bytes != ii:
                        try:
                            cursor = t.text.textCursor()
                            saved_line = t.line_map[cursor.blockNumber()] if t.line_map else 0
                        except Exception:
                            saved_line = 0
                        try:
                            t.indexer.close()
                        except Exception:
                            pass
                        t._start_reindex(saved_line)
                    else:
                        try:
                            cursor = t.text.textCursor()
                            saved_line = t.line_map[cursor.blockNumber()] if t.line_map else 0
                        except Exception:
                            saved_line = 0
                        t._load_window(at_line=saved_line)
            QMessageBox.information(self, self.t("app_title"), self.t("msg_settings_applied"))

    # ----------------------------------------------------------- DnD ----
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        paths = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                paths.append(local)
        if paths:
            self._on_files_dropped(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    @Slot(list)
    def _on_files_dropped(self, paths: List[str]) -> None:
        """DnD otwiera każdy plik w nowej zakładce."""
        existing = [p for p in paths if os.path.isfile(p)]
        if not existing:
            QMessageBox.information(self, self.t("app_title"), self.t("msg_dnd_no_files"))
            return
        if len(existing) == 1:
            self.open_file_in_tab(existing[0])
            self.statusBar().showMessage(self.t("msg_dnd_opened").format(path=existing[0]))
        else:
            choice, ok = QInputDialog.getInt(
                self, self.t("app_title"),
                self.t("msg_dnd_multiple").format(n=len(existing)),
                1, 1, len(existing), 1,
            )
            if ok:
                self.open_file_in_tab(existing[choice - 1])

    # ----------------------------------------------------------- misc ----
    def cmd_about(self) -> None:
        QMessageBox.about(self, self.t("app_title"), self.t("msg_about"))

    def closeEvent(self, event):
        """Zamknij wszystkie zakładki (z pytaniem o niezapisane edycje)."""
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if not isinstance(tab, LogTab):
                continue
            if len(tab.edit_buffer) > 0:
                if not QMessageBox.question(
                    self, self.t("app_title"),
                    self.t("msg_clear_edits").format(n=len(tab.edit_buffer)) + "\n\n" + self.t("mi_exit") + "?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                ) == QMessageBox.Yes:
                    event.ignore()
                    return
            tab.close()
        event.accept()
