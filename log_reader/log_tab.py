"""app.py — LogTab (per-file widget) + LogViewerWindow (tabbed controller)."""

from __future__ import annotations

import os
_running_tasks = set()
import re
import sys
import time
import bisect
from typing import Optional, List, Tuple, Dict, Any

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QThread, QSize, QPoint
from PySide6.QtGui import (
    QAction, QKeySequence, QColor, QTextCharFormat, QFont, QFontDatabase,
    QDragEnterEvent, QDropEvent, QCursor,
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
from .ui.ui_log_tab import Ui_LogTab

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

    def _register_thread_worker(self, thread: QThread, worker: QObject) -> None:
        """Chroni wątek i workera przed Python GC, dopóki nie zakończą pracy."""
        task_ref = (thread, worker)
        _running_tasks.add(task_ref)
        thread.finished.connect(lambda r=task_ref: _running_tasks.discard(r))

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
        self._filter_hit_text_map: Dict[int, str] = {}
        self._filter_hit_lines: set = set()
        self._filter_all_lines: List[int] = []
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

        # Stan Toolbara
        self.tb_search_text: str = ""
        self.tb_search_regex: bool = False
        self.tb_search_case: bool = False
        self.tb_search_negate: bool = False

        self.tb_filter_text: str = ""
        self.tb_filter_regex: bool = False
        self.tb_filter_case: bool = False
        self.tb_filter_negate: bool = False
        self.tb_filter_context: int = 0

        # Build UI
        self.ui = Ui_LogTab()
        self.ui.setupUi(self)
        self._setup_ui_elements()
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
    def _setup_ui_elements(self) -> None:
        self.splitter = self.ui.splitter
        self.v_splitter = self.ui.v_splitter

        self.splitter.setSizes([200, 900, 48])
        self.v_splitter.setSizes([500, 150])

        # Aliases for convenience
        self._lbl_bookmarks = self.ui._lbl_bookmarks
        self._lbl_edits = self.ui._lbl_edits
        self.bm_tree = self.ui.bm_tree
        self.ed_tree = self.ui.ed_tree
        self.btn_del_bookmarks = self.ui.btn_del_bookmarks
        self.btn_del_edits = self.ui.btn_del_edits
        self.text = self.ui.text
        self._search_results_label = self.ui._search_results_label
        self.search_results_view = self.ui.search_results_view
        self.minimap = self.ui.minimap
        self.pct_label = self.ui.pct_label

        # Set up signals
        self.bm_tree.itemDoubleClicked.connect(self._goto_bookmark)
        self.btn_del_bookmarks.clicked.connect(self._delete_selected_bookmarks)
        QtGui.QShortcut(QKeySequence.StandardKey.Delete, self.bm_tree,
                        activated=self._delete_selected_bookmarks)
        QtGui.QShortcut(QKeySequence("Backspace"), self.bm_tree,
                        activated=self._delete_selected_bookmarks)

        self.ed_tree.itemDoubleClicked.connect(self._goto_edit)
        self.btn_del_edits.clicked.connect(self._delete_selected_edits)
        QtGui.QShortcut(QKeySequence.StandardKey.Delete, self.ed_tree,
                        activated=self._delete_selected_edits)
        QtGui.QShortcut(QKeySequence("Backspace"), self.ed_tree,
                        activated=self._delete_selected_edits)

        self.text.files_dropped.connect(self._main._on_files_dropped)
        self.text.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        # Podłączamy detekcję user_scrolled aby wyłączyć follow
        self.text.user_scrolled.connect(self._on_user_scrolled)
        # Musimy również wyłączyć follow, jeśli użytkownik kliknie bezpośrednio na scrollbar
        self.text.verticalScrollBar().sliderPressed.connect(self._on_user_scrolled)
        self._search_extra_sel: Optional[QtWidgets.QTextEdit.ExtraSelection] = None
        self.text.cursorPositionChanged.connect(self._update_current_line_highlight)

        self._search_model = SearchResultsModel()
        self.search_results_view.setModel(self._search_model)
        mono_font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono_font.setPointSize(9)

        self.search_results_view.setFont(mono_font)
        self.search_results_view.clicked.connect(self._on_search_result_clicked)

        self.minimap.position_clicked.connect(self._on_minimap_click)
        self.pct_label.setStyleSheet(f"color: {THEME_DARK['fg_dim']}; font-size: 10px; padding: 4px;")
        if hasattr(self.ui, 'sep'):
            self.ui.sep.setStyleSheet(f"background-color: {THEME_DARK['border']};")

        # Set up translated labels that UI compiler wouldn't know
        self._lbl_bookmarks.setText(self.t("lbl_bookmarks"))
        self._lbl_edits.setText(self.t("lbl_edits"))
        self.bm_tree.setHeaderLabels([self.t("col_line")])
        self.ed_tree.setHeaderLabels([self.t("col_line")])
        self.btn_del_bookmarks.setText(self.t("btn_delete_sel"))
        self.btn_del_edits.setText(self.t("btn_delete_sel"))
        self._search_results_label.setText(self.t("lbl_search_results_empty"))


    def _apply_font_to_text(self) -> None:
        if self.font_family:
            font = QFont(self.font_family, self.font_size)
        else:
            font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
            font.setPointSize(self.font_size)

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
    def open_file(self, path: str, title: Optional[str] = None) -> None:
        if not os.path.isfile(path):
            QMessageBox.critical(self._main, self.t("app_title"), self.t("msg_no_file"))
            return
        self.cmd_clear_filter(silent=True)
        if self.follow_active:
            self.cmd_toggle_follow()
        self.file_path = path
        self._assigned_title = title or os.path.basename(path)
        self._status(self.t("st_opening"))
        self.title_changed.emit(self._assigned_title)
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
        self._register_thread_worker(self._indexer_thread, self._indexer_worker)

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
            if self.indexer is None:
                idx = self._main.tabs.indexOf(self)
                if idx >= 0:
                    self._main._on_tab_close_requested(idx)
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
        # Zaktualizuj tytuł zakładki — przywróć właściwy tytuł z sufiksem
        if self.file_path:
            self.title_changed.emit(getattr(self, "_assigned_title", os.path.basename(self.file_path)))
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
            hit_text_map = self._filter_hit_text_map
            all_lines = self._filter_all_lines
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
        hit_line_set = self._filter_hit_lines if self.filter_active else set()
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

        # Odsprzęgnięta aktualizacja minimapy (szczególnie przydatna dla Follow)
        if not self._minimap_update_timer.isActive():
            self._minimap_update_timer.start(100)

        if self.follow_active:
            cursor.movePosition(QtGui.QTextCursor.End)
        else:
            cursor.movePosition(QtGui.QTextCursor.Start)

        self.text.setTextCursor(cursor)
        self._refresh_status()
        self._is_loading = False
        self._last_edge_load_time = 0.0  # Zresetuj blokadę ładowania (przydatne przy natychmiastowym przewijaniu po skoku)
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
        idx = bisect.bisect_left(self.line_map, old_first_file_line)
        if idx != len(self.line_map) and self.line_map[idx] == old_first_file_line:
            self.text.verticalScrollBar().setValue(idx)
        self._update_position_slider()

    # ---------------------------------------------------- position slider ---
    def _on_user_scrolled(self) -> None:
        """Wywoływane przy ręcznym zdarzeniu wheelEvent lub po naciśnięciu ScrollBara."""
        if self.follow_active:
            # Rozmyślnie przerywamy follow mode
            self.cmd_toggle_follow()

    def _on_scroll_changed(self, value: int) -> None:
        if not self.indexer or not self.line_map or self._is_loading:
            return
        self._scroll_debounce_timer.start()

    def _on_minimap_click(self, line_no: int) -> None:
        if not self.indexer or line_no < 0:
            return
        self._cancel_follow_if_active()
        line_no = min(line_no, self.indexer.line_count - 1)
        self._load_window(at_line=max(0, line_no - 10))

    def _update_minimap(self) -> None:
        if not self.indexer or self.indexer.line_count == 0:
            return
        total = self.indexer.line_count

        # Aby zapobiec zawieszaniu UI przy ładowaniu bardzo dużych plików (np. 25 GB)
        # rezygnujemy z pełnego skanowania pliku w poszukiwaniu tagów logów dla
        # kolorowania minimapy. Minimapa posłuży tylko jako żółty wskaźnik pozycji.
        if self.minimap._total_lines != total:
            self.minimap.set_line_data([], total)

        self._update_minimap_viewport()

    def _update_minimap_viewport(self) -> None:
        if not self.indexer or self.indexer.line_count == 0 or not self.line_map:
            return
        try:
            cursor = self.text.cursorForPosition(QPoint(0, 5))
            first_line = self.line_map[cursor.blockNumber()] if cursor.blockNumber() < len(self.line_map) else 0

            # Jeśli jesteśmy w trybie follow i pasek jest na dole, zakładamy dolną krawędź jako 1.0 (100%)
            total = self.indexer.line_count

            scrollbar = self.text.verticalScrollBar()
            is_at_bottom = scrollbar.value() >= scrollbar.maximum() - 5

            if self.follow_active and is_at_bottom:
                last_line = total - 1
            else:
                cursor_bottom = self.text.cursorForPosition(QPoint(0, self.text.height() - 5))
                last_line = self.line_map[cursor_bottom.blockNumber()] if cursor_bottom.blockNumber() < len(self.line_map) else total - 1

            new_start = first_line / total
            new_end = last_line / total

            # W trybie follow aktualizujemy viewport płynniej
            self.minimap.set_viewport(new_start, new_end)
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
                # Zamiast bezpośrednio wzywać _update_minimap_viewport opóźniamy/dławimy
                if not self._minimap_update_timer.isActive():
                    self._minimap_update_timer.start(100)
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

        # Gdy skrolujemy po ułamkowych wartościach
        # a jesteśmy pod koniec widoku używając follow lub gdy widoczny koniec pliku
        scrollbar = self.text.verticalScrollBar()
        if scrollbar.value() >= scrollbar.maximum() - 5:
            pct = 1000
        else:
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
        self._register_thread_worker(self._search_thread, self._search_worker)
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
        self._cancel_follow_if_active()
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
        block_cursor = QtGui.QTextCursor(self.text.document().findBlockByNumber(widget_line_no))
        sel_cursor = QtGui.QTextCursor(block_cursor)
        sel_cursor.select(QtGui.QTextCursor.LineUnderCursor)

        sel = QtWidgets.QTextEdit.ExtraSelection()
        sel.cursor = sel_cursor
        sel.format.setBackground(QColor(self.theme["highlight"]))
        sel.format.setForeground(QColor("#000000")) # Czarny tekst dla czytelności na żółtym tle
        self._search_extra_sel = sel

        # Ustawiamy kursor bez fizycznego zaznaczenia tekstu w kontrolce,
        # aby uniknąć szarego systemowego tła (nieaktywnego zaznaczenia)
        # nakładającego się na nasze żółte tło ExtraSelection.
        # Po setTextCursor cursorPositionChanged odpali się i przebuduje listę
        # ExtraSelections łącznie z bieżącą linią + tym podświetleniem.
        self.text.setTextCursor(block_cursor)

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
                sel.format.setForeground(QColor("#000000")) # Czarny tekst dla czytelności na żółtym tle
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
        self._register_thread_worker(self._filter_thread, self._filter_worker)
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
        self._update_filter_cache()
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

    def _update_filter_cache(self) -> None:
        if self.filter_active and self.filter_results:
            self._filter_hit_text_map = {ln: text for (ln, _off, text) in self.filter_results}
            self._filter_hit_lines = set(self._filter_hit_text_map.keys())
            self._filter_all_lines = sorted(self._filter_hit_lines | self.filter_context_lines)
        else:
            self._filter_hit_text_map.clear()
            self._filter_hit_lines.clear()
            self._filter_all_lines.clear()

    def cmd_clear_filter(self, silent: bool = False) -> None:
        was_active = self.filter_active
        if self.filter_engine and self.filter_engine.is_running():
            self.filter_engine.cancel()
        self.filter_active = False
        self.filter_results = []
        self.filter_context_lines = set()
        self._filter_hit_text_map.clear()
        self._filter_hit_lines.clear()
        self._filter_all_lines.clear()
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

        self._cancel_follow_if_active()

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

    def cmd_goto_start(self) -> None:
        if not self.indexer:
            return
        self._cancel_follow_if_active()
        self._load_window(at_line=0)

    def cmd_reload(self) -> None:
        """Przeładowuje bieżący plik i jego indeks (kasuje edycje po ostrzeżeniu)."""
        if not self.file_path:
            return

        if len(self.edit_buffer) > 0:
            choice = QMessageBox.question(
                self, self.t("app_title"),
                self.t("msg_clear_edits").format(n=len(self.edit_buffer)),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if choice != QMessageBox.Yes:
                return

        # Zatrzymujemy follow jeżeli działa
        if self.follow_active:
            self.cmd_toggle_follow()

        # Zapiszmy aktualną pozycję (fizyczny wiersz przed przeładowaniem)
        try:
            cursor = self.text.textCursor()
            saved_line = self.line_map[cursor.blockNumber()] if self.line_map else 0
        except Exception:
            saved_line = 0

        # Pamiętaj oryginalny tytuł zakładki (z ew. literą w nawiasie)
        main_tabs = getattr(self.window(), "tabs", None)
        title = os.path.basename(self.file_path)
        if main_tabs is not None:
            idx = main_tabs.indexOf(self)
            if idx >= 0:
                title = main_tabs.tabText(idx)

        # Zwolnij plik
        try:
            if self.indexer:
                self.indexer.close()
        except Exception:
            pass

        self.open_file(self.file_path, title=title)

        # Wywołanie _start_reindex by ustawić scroll po zakończeniu indeksowania
        # jeśli proces odczytu to reindex po reload. W open_file po reindeksie
        # jest _load_window(0). Ponieważ jest on asynchroniczny, przekażemy go
        # przez wymuszenie reindeksu i ominięcie _load_window na 0.
        # By uniknąć komplikacji w async open_file, najprościej uzyć mechanizmu
        # z zapisu (np. użyć tej samej logiki odzyskiwania pozycji po indeksie).
        if hasattr(self, "_start_reindex"):
            self._start_reindex(saved_line)

    def cmd_goto_end(self) -> None:
        if not self.indexer:
            return
        self._cancel_follow_if_active()
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
        self._register_thread_worker(self._save_thread, self._save_worker)
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
        self._register_thread_worker(self._indexer_thread, self._indexer_worker)
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
        try:
            self._load_window(at_line=saved_line)
        except OSError:
            # Ignorujemy potencjalne usunięcie pliku z dysku pod maską w trakcie lub tuż po reindeksie.
            pass

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
        self._cancel_follow_if_active()
        # Cofamy start o 50 linii (lub do 0), by zakładka nie była na samej ścianie (value=0 paska),
        # co blokowałoby przewijanie w górę (brak zdarzeń scrolla).
        offset = 50
        if self.filter_active:
            keys = [r[0] for r in self.filter_results]
            idx = bisect.bisect_left(keys, ln)
            start_idx = max(0, idx - offset)
            self._load_window(at_line=start_idx)
            try:
                # Szukamy gdzie wylądował oryginalny idx
                target_ln = keys[idx] if idx < len(keys) else -1
                if target_ln in self.line_map:
                    self.text.verticalScrollBar().setValue(self.line_map.index(target_ln))
                else:
                    self.text.verticalScrollBar().setValue(0)
            except Exception:
                self.text.verticalScrollBar().setValue(0)
        else:
            start_ln = max(0, ln - offset)
            self._load_window(at_line=start_ln)
            try:
                if ln in self.line_map:
                    self.text.verticalScrollBar().setValue(self.line_map.index(ln))
                else:
                    self.text.verticalScrollBar().setValue(0)
            except ValueError:
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
    def _cancel_follow_if_active(self) -> None:
        """Helper to cancel follow mode proactively when manual jumps happen."""
        if self.follow_active:
            self.cmd_toggle_follow()

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

            if self.indexer and self.indexer.line_count > 0:
                last_start = max(0, self.indexer.line_count - self.window_size_lines)
                self._load_window(at_line=last_start)
                self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

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
        self._register_thread_worker(self._indexer_thread, self._indexer_worker)
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
        if new_line_count > 0 or not self.line_map:
            last_start = max(0, self.indexer.line_count - self.window_size_lines)
            # Blokujemy aktualizacje scrollbara uzytkownika podczas tej operacji aby uniknąć false positivów
            self.text.verticalScrollBar().blockSignals(True)
            try:
                self._load_window(at_line=last_start)
                self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())
            finally:
                self.text.verticalScrollBar().blockSignals(False)
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
        # Zapamiętujemy pozycję paska przewijania, żeby uniknąć przeskakiwania
        # zawartości przy odświeżaniu okna (np. po dodaniu zakładki).
        scrollbar = self.text.verticalScrollBar()
        old_val = scrollbar.value()
        self._load_window(at_line=self.window_start)
        scrollbar.setValue(old_val)

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


import atexit

def _cleanup_running_tasks():
    for task_ref in list(_running_tasks):
        try:
            thread, worker = task_ref
            if hasattr(worker, 'cancel'):
                worker.cancel()
            if thread.isRunning():
                thread.quit()
                thread.wait(2000)
        except Exception:
            pass

atexit.register(_cleanup_running_tasks)
