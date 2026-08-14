"""app.py — LogTab (per-file widget) + LogViewerWindow (tabbed controller)."""

from __future__ import annotations

import os
import time
import bisect
import queue
import atexit
from typing import Optional, List, Tuple, Dict, Union, TYPE_CHECKING, Callable, Set

_running_tasks = set()

from .bitset import Bitset, bisect_left_custom, bisect_right_custom

if TYPE_CHECKING:
    from .main_window import LogViewerWindow

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Signal, Slot, QTimer, QThread, QPoint
from PySide6.QtGui import (
    QColor, QAction
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QMessageBox, QInputDialog,
    QProgressDialog,
)

from .helpers import (
    fmt_size, truncate_for_display,
    TAG_BOOKMARK, TAG_EDITED, TAG_TRUNCATED,
)
from .indexer import LineIndexer
from .controllers import FileController, EditController, SearchController, FilterController, UIController, BookmarkController, ViewportController
from .filter_engine import FilterEngine
from .edit_buffer import EditBuffer
from .workers import IndexerWorker, FilterWorker, SaveWorker, SaveAsWorker, ExportWorker, IncrementalFilterWorker
from .widgets import SearchResultsModel, LogPlainTextEdit
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
    file_loaded = Signal(bool)  # emitowane po załadowaniu pliku z sukcesem (True) lub błędem (False)


    # UI elements type hints (from compiled UI)
    ui: Ui_LogTab
    text: LogPlainTextEdit
    pct_label: QtWidgets.QLabel
    search_results_view: QtWidgets.QListView
    bm_tree: QtWidgets.QTreeWidget
    ed_tree: QtWidgets.QTreeWidget
    btn_del_bookmarks: QtWidgets.QPushButton
    btn_del_edits: QtWidgets.QPushButton
    splitter: QtWidgets.QSplitter
    v_splitter: QtWidgets.QSplitter
    _lbl_bookmarks: QtWidgets.QLabel
    _lbl_edits: QtWidgets.QLabel
    _search_results_label: QtWidgets.QLabel

    def register_thread_worker(self, thread: QtCore.QThread, worker: QtCore.QObject) -> None:
        """Chroni wątek i workera przed Python GC, dopóki nie zakończą pracy."""
        task_ref = (thread, worker)
        _running_tasks.add(task_ref)
        thread.finished.connect(lambda r=task_ref: _running_tasks.discard(r))

    _register_thread_worker = register_thread_worker

    def __init__(self, main_window: "LogViewerWindow", parent=None):
        super().__init__(parent)
        self._main = main_window
        self.file_controller = FileController(self)
        self.edit_controller = EditController(self)
        self.search_controller = SearchController(self)
        self.filter_controller = FilterController(self)
        self.bookmark_controller = BookmarkController(self)
        self.ui_controller = UIController(self)
        self.viewport_controller = ViewportController(self)


        # Stan pliku
        self.file_path: Optional[str] = None
        self.indexer: Optional[LineIndexer] = None
        self.filter_engine: Optional[FilterEngine] = None
        self.edit_buffer = EditBuffer()
        self.bookmarks: Dict[int, None] = {}

        self._file_mtime_at_open: float = 0.0
        self._file_size_at_open: int = 0
        self._last_file_inode: int = 0

        # Cache dla obiektów QColor
        self._theme_colors: Dict[str, QColor] = {}

        # Wirtualne okno
        self.window_start: int = 0
        self.window_lines: List[Tuple[int, str]] = []
        self.line_map: Optional[List[int]] = []

        # Filtr
        self.filter_active: bool = False
        self.filter_results: Optional[Bitset] = None
        self._filter_hit_text_map: Optional[Dict[int, str]] = {}
        self._filter_hit_lines: Optional[Set[int]] = set()
        self._filter_all_lines: Optional[Bitset] = None
        # Linie kontekstu (N linii po każdym trafieniu) — zbiór numerów linii pliku.
        # Tła kontekstu są dodawane przez ExtraSelections (jak zakładki).
        self.filter_context_lines: Optional[Set[int]] = set()
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
        self._search_results_all: List[Union[int, Tuple[int, str]]] = []  # pełne wyniki
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
        self._save_as_thread: Optional[QThread] = None
        self._save_as_worker: Optional[SaveAsWorker] = None
        self._save_as_progress: Optional[QProgressDialog] = None
        self._save_as_path: Optional[str] = None
        self._export_thread: Optional[QThread] = None
        self._export_worker: Optional[ExportWorker] = None
        self._export_progress: Optional[QProgressDialog] = None
        self._export_path: Optional[str] = None

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

        # Timer do ładowania krawędzi zainicjalizowany przed użyciem
        self._edge_load_timer = QTimer(self)

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

        # Konfiguracja niestandardowych opcji menu kontekstowego
        action_refresh = QAction(self.t("btn_refresh"), self)
        action_refresh.triggered.connect(self.cmd_refresh)
        action_reload = QAction(self.t("btn_reload"), self)
        action_reload.triggered.connect(self.cmd_reload)
        self.ui.text.custom_context_actions.extend([action_refresh, action_reload])

    # ------------------------------------------------------------------ helpers
    def t(self, key: str) -> str:
        return self._main.t(key)

    def fmt(self, msg_key: str, **kw) -> str:
        return self._main._fmt(msg_key, **kw)

    def _fmt(self, msg_key: str, **kw) -> str:
        return self.fmt(msg_key, **kw)

    @property
    def main_window(self) -> LogViewerWindow:
        return self._main

    def set_status(self, msg: str) -> None:
        """Aktualizuje status bar (przez sygnał do LogViewerWindow)."""
        self.status_changed.emit(msg)

    def _status(self, msg: str) -> None:
        self.set_status(msg)

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

    # ----- file & worker state properties -----

    @property
    def file_mtime_at_open(self) -> float:
        return self._file_mtime_at_open

    @file_mtime_at_open.setter
    def file_mtime_at_open(self, val: float) -> None:
        self._file_mtime_at_open = val

    @property
    def file_size_at_open(self) -> int:
        return self._file_size_at_open

    @file_size_at_open.setter
    def file_size_at_open(self, val: int) -> None:
        self._file_size_at_open = val

    @property
    def last_formatter(self) -> str:
        return self._last_formatter

    @last_formatter.setter
    def last_formatter(self, val: str) -> None:
        self._last_formatter = val

    @property
    def save_progress(self) -> Optional[QProgressDialog]:
        return self._save_progress

    @save_progress.setter
    def save_progress(self, val: Optional[QProgressDialog]) -> None:
        self._save_progress = val

    @property
    def save_thread(self) -> Optional[QThread]:
        return self._save_thread

    @save_thread.setter
    def save_thread(self, val: Optional[QThread]) -> None:
        self._save_thread = val

    @property
    def save_worker(self) -> Optional[SaveWorker]:
        return self._save_worker

    @save_worker.setter
    def save_worker(self, val: Optional[SaveWorker]) -> None:
        self._save_worker = val

    @property
    def save_as_progress(self) -> Optional[QProgressDialog]:
        return self._save_as_progress

    @save_as_progress.setter
    def save_as_progress(self, val: Optional[QProgressDialog]) -> None:
        self._save_as_progress = val

    @property
    def save_as_path(self) -> Optional[str]:
        return self._save_as_path

    @save_as_path.setter
    def save_as_path(self, val: Optional[str]) -> None:
        self._save_as_path = val

    @property
    def save_as_thread(self) -> Optional[QThread]:
        return self._save_as_thread

    @save_as_thread.setter
    def save_as_thread(self, val: Optional[QThread]) -> None:
        self._save_as_thread = val

    @property
    def save_as_worker(self) -> Optional[SaveAsWorker]:
        return self._save_as_worker

    @save_as_worker.setter
    def save_as_worker(self, val: Optional[SaveAsWorker]) -> None:
        self._save_as_worker = val

    @property
    def export_progress(self) -> Optional[QProgressDialog]:
        return self._export_progress

    @export_progress.setter
    def export_progress(self, val: Optional[QProgressDialog]) -> None:
        self._export_progress = val

    @property
    def export_path(self) -> Optional[str]:
        return self._export_path

    @export_path.setter
    def export_path(self, val: Optional[str]) -> None:
        self._export_path = val

    @property
    def export_thread(self) -> Optional[QThread]:
        return self._export_thread

    @export_thread.setter
    def export_thread(self, val: Optional[QThread]) -> None:
        self._export_thread = val

    @property
    def export_worker(self) -> Optional[ExportWorker]:
        return self._export_worker

    @export_worker.setter
    def export_worker(self, val: Optional[ExportWorker]) -> None:
        self._export_worker = val

    @property
    def assigned_title(self) -> str:
        return getattr(self, "_assigned_title", "")

    @assigned_title.setter
    def assigned_title(self, val: str) -> None:
        self._assigned_title = val

    @property
    def last_file_size(self) -> int:
        return self._last_file_size

    @last_file_size.setter
    def last_file_size(self, val: int) -> None:
        self._last_file_size = val

    @property
    def last_file_inode(self) -> int:
        return getattr(self, "_last_file_inode", 0)

    @last_file_inode.setter
    def last_file_inode(self, val: int) -> None:
        self._last_file_inode = val

    @property
    def indexer_thread(self) -> Optional[QThread]:
        return self._indexer_thread

    @indexer_thread.setter
    def indexer_thread(self, val: Optional[QThread]) -> None:
        self._indexer_thread = val

    @property
    def indexer_worker(self) -> Optional[IndexerWorker]:
        return self._indexer_worker

    @indexer_worker.setter
    def indexer_worker(self, val: Optional[IndexerWorker]) -> None:
        self._indexer_worker = val

    @property
    def index_progress(self) -> Optional[QProgressDialog]:
        return self._index_progress

    @index_progress.setter
    def index_progress(self, val: Optional[QProgressDialog]) -> None:
        self._index_progress = val

    @property
    def filter_thread(self) -> Optional[QThread]:
        return self._filter_thread

    @filter_thread.setter
    def filter_thread(self, val: Optional[QThread]) -> None:
        self._filter_thread = val

    @property
    def filter_worker(self) -> Optional[FilterWorker]:
        return self._filter_worker

    @filter_worker.setter
    def filter_worker(self, val: Optional[FilterWorker]) -> None:
        self._filter_worker = val

    @property
    def filter_all_lines(self) -> Optional[Bitset]:
        return getattr(self, "_filter_all_lines", None)

    @filter_all_lines.setter
    def filter_all_lines(self, val: Optional[Bitset]) -> None:
        self._filter_all_lines = val

    @property
    def filter_context_after(self) -> int:
        return getattr(self, "_filter_context_after", 0)

    @filter_context_after.setter
    def filter_context_after(self, val: int) -> None:
        self._filter_context_after = val

    @property
    def filter_hit_text_map(self) -> Optional[Dict[int, str]]:
        return getattr(self, "_filter_hit_text_map", None)

    @filter_hit_text_map.setter
    def filter_hit_text_map(self, val: Optional[Dict[int, str]]) -> None:
        self._filter_hit_text_map = val

    @property
    def filter_hit_lines(self) -> Optional[Set[int]]:
        return getattr(self, "_filter_hit_lines", None)

    @filter_hit_lines.setter
    def filter_hit_lines(self, val: Optional[Set[int]]) -> None:
        self._filter_hit_lines = val

    @property
    def search_thread(self) -> Optional[QThread]:
        return getattr(self, "_search_thread", None)

    @search_thread.setter
    def search_thread(self, val: Optional[QThread]) -> None:
        self._search_thread = val

    @property
    def search_engine(self) -> Optional[FilterEngine]:
        return getattr(self, "_search_engine", None)

    @search_engine.setter
    def search_engine(self, val: Optional[FilterEngine]) -> None:
        self._search_engine = val

    @property
    def follow_reindexing(self) -> bool:
        return self._follow_reindexing

    @follow_reindexing.setter
    def follow_reindexing(self, val: bool) -> None:
        self._follow_reindexing = val

    @property
    def follow_reindex_size(self) -> int:
        return getattr(self, "_follow_reindex_size", 0)

    @follow_reindex_size.setter
    def follow_reindex_size(self, val: int) -> None:
        self._follow_reindex_size = val

    @property
    def follow_reindex_inode(self) -> int:
        return getattr(self, "_follow_reindex_inode", 0)

    @follow_reindex_inode.setter
    def follow_reindex_inode(self, val: int) -> None:
        self._follow_reindex_inode = val

    @property
    def pending_reload_filter(self) -> bool:
        return getattr(self, "_pending_reload_filter", False)

    @pending_reload_filter.setter
    def pending_reload_filter(self, val: bool) -> None:
        self._pending_reload_filter = val

    @property
    def reindex_saved_line(self) -> int:
        return getattr(self, "_reindex_saved_line", 0)

    @reindex_saved_line.setter
    def reindex_saved_line(self, val: int) -> None:
        self._reindex_saved_line = val

    @property
    def inc_pending_lines(self) -> int:
        return getattr(self, "_inc_pending_lines", 0)

    @inc_pending_lines.setter
    def inc_pending_lines(self, val: int) -> None:
        self._inc_pending_lines = val

    @property
    def inc_new_total_lines(self) -> int:
        return getattr(self, "_inc_new_total_lines", 0)

    @inc_new_total_lines.setter
    def inc_new_total_lines(self, val: int) -> None:
        self._inc_new_total_lines = val

    @property
    def inc_mtime_str(self) -> str:
        return getattr(self, "_inc_mtime_str", "")

    @inc_mtime_str.setter
    def inc_mtime_str(self, val: str) -> None:
        self._inc_mtime_str = val

    @property
    def inc_ctime_str(self) -> str:
        return getattr(self, "_inc_ctime_str", "")

    @inc_ctime_str.setter
    def inc_ctime_str(self, val: str) -> None:
        self._inc_ctime_str = val

    @property
    def inc_filter_thread(self) -> Optional[QThread]:
        return getattr(self, "_inc_filter_thread", None)

    @inc_filter_thread.setter
    def inc_filter_thread(self, val: Optional[QThread]) -> None:
        self._inc_filter_thread = val

    @property
    def inc_filter_worker(self) -> Optional[IncrementalFilterWorker]:
        return getattr(self, "_inc_filter_worker", None)

    @inc_filter_worker.setter
    def inc_filter_worker(self, val: Optional[IncrementalFilterWorker]) -> None:
        self._inc_filter_worker = val

    @property
    def ignore_scroll_events(self) -> bool:
        return getattr(self, "_ignore_scroll_events", False)

    @ignore_scroll_events.setter
    def ignore_scroll_events(self, val: bool) -> None:
        self._ignore_scroll_events = val

    @property
    def minimap_update_timer(self) -> QTimer:
        return self._minimap_update_timer

    # ------------------------------------------------------------------ UI
    def _setup_ui_elements(self) -> None:
        return self.ui_controller._setup_ui_elements()


    def _apply_font_to_text(self) -> None:
        return self.ui_controller._apply_font_to_text()

    def _apply_theme(self) -> None:
        # Odśwież cache QColor przed aktualizacją UI
        t = self.theme
        self._theme_colors = {
            "truncated": QColor(t.get("truncated", "#6a6a6a")),
            "error": QColor(t.get("error", "#f44747")),
            "warn": QColor(t.get("warn", "#cca700")),
            "info": QColor(t.get("info", "#569cd6")),
            "debug": QColor(t.get("debug", "#c586c0")),
            "highlight": QColor(t.get("highlight", "#fff176")),
            "context": QColor(t.get("context", "#3a3d3a")),
            "bookmark": QColor(t.get("bookmark", "#6a9955")),
            "edited": QColor(t.get("edited", "#ce9178")),
            "current_line": QColor(t.get("current_line", "#2a2d2e")),
            "search_active": QColor(t.get("search_active", "#ff8c00")),
            "black": QColor("#000000"),
        }
        return self.ui_controller._apply_theme()

    def _update_text_colors(self) -> None:
        return self.ui_controller._update_text_colors()

    # --------------------------------------------------------- file ops ---
    def open_file(self, path: str, title: Optional[str] = None) -> None:
        return self.file_controller.open_file(path, title)

    @Slot(float)
    def _on_index_progress(self, p: float) -> None:
        return self.file_controller._on_index_progress(p)

    def _cancel_indexing(self) -> None:
        return self.file_controller._cancel_indexing()

    def _close_index_progress(self) -> None:
        return self.file_controller._close_index_progress()

    @Slot(object)
    def _on_index_error(self, err: str) -> None:
        self.file_controller._on_index_error(err)
        self.file_loaded.emit(False)

    @Slot(object)
    def _on_index_done(self, idx: LineIndexer) -> None:
        self.file_controller._on_index_done(idx)
        self.file_loaded.emit(True)

    # -------------------------------------------------- virtual window -----
    def load_window(self, at_line: int, force_reload: bool = False) -> None:
        return self.viewport_controller.load_window(at_line, force_reload)

    def _load_window(self, at_line: int, force_reload: bool = False) -> None:
        return self.load_window(at_line, force_reload)

    def _get_filtered_lines(self, chunk_lines: List[int]) -> List[Tuple[int, str]]:
        return self.viewport_controller._get_filtered_lines(chunk_lines)

    def _load_window_impl(self, at_line: int) -> None:
        return self.viewport_controller._load_window_impl(at_line)

    def _prepare_line_for_display(self, file_line_no: int, original_text: str) -> Tuple[str, List[str]]:
        return self.viewport_controller._prepare_line_for_display(file_line_no, original_text)

    def _apply_line_format(self, block, tag: str) -> None:
        return self.viewport_controller._apply_line_format(block, tag)

    def _check_edges(self) -> None:
        return self.viewport_controller._check_edges()

    def _do_check_edges(self) -> None:
        return self.viewport_controller._do_check_edges()

    def _append_lines(self, new_lines: List[Tuple[int, str]]) -> None:
        return self.viewport_controller._append_lines(new_lines)

    def _prepend_lines(self, new_lines: List[Tuple[int, str]]) -> None:
        return self.viewport_controller._prepend_lines(new_lines)

    # ---------------------------------------------------- position slider ---
    def _on_user_scrolled(self) -> None:
        return self.viewport_controller._on_user_scrolled()

    def _on_scroll_changed(self, value: int) -> None:
        return self.viewport_controller._on_scroll_changed(value)

    def _on_minimap_click(self, line_no: int) -> None:
        return self.viewport_controller._on_minimap_click(line_no)

    def update_minimap(self) -> None:
        return self.ui_controller.update_minimap()

    def _update_minimap(self) -> None:
        return self.update_minimap()

    def update_minimap_viewport(self) -> None:
        return self.ui_controller.update_minimap_viewport()

    def _update_minimap_viewport(self) -> None:
        return self.update_minimap_viewport()

    def _update_slider_from_scroll(self) -> None:
        return self.viewport_controller._update_slider_from_scroll()

    def _update_position_slider(self) -> None:
        return self.viewport_controller._update_position_slider()


    # -------------------------------------------------------------- find ----
    def cmd_find_dialog(self) -> None:
        return self.search_controller.cmd_find_dialog()

    def _compile_search(self) -> Optional[str]:
        return self.search_controller._compile_search()

    def _search_pattern_changed(self) -> bool:
        return self.search_controller._search_pattern_changed()

    def _start_background_search(self) -> None:
        return self.search_controller._start_background_search()

    @Slot(float, int, str)
    def _on_search_progress(self, pct: float, hits: int, state: str) -> None:
        return self.search_controller._on_search_progress(pct, hits, state)

    @Slot(object, object, object, object, object, object)
    def _on_search_finished(self, results, context_lines, filter_all_lines, hit_text_map, hit_lines_set, error) -> None:
        return self.search_controller._on_search_finished(results, context_lines, filter_all_lines, hit_text_map, hit_lines_set, error)

    def _update_search_results_label(self) -> None:
        return self.search_controller._update_search_results_label()

    def _navigate_to_search_result(self, index: int) -> None:
        if not self._search_results_all or index < 0 or index >= len(self._search_results_all):
            return
        self._cancel_follow_if_active()
        self._search_result_index = index
        item = self._search_results_all[index]
        line_no = item[0] if isinstance(item, tuple) else item

        # Używamy ujednoliconego goto, co od razu poprawia błędy nawigacji
        self._goto_file_line(line_no)

        for i, fl in enumerate(self.line_map):
            if fl == line_no:
                self._highlight_and_scroll(i)
                break
        if self._search_model and index < len(self._search_results):
            if hasattr(self._search_model, 'ensure_visible'):
                self._search_model.ensure_visible(index)
            model_index = self._search_model.index(index, 0)
            self.search_results_view.setCurrentIndex(model_index)
            self.search_results_view.scrollTo(model_index, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)
        self._update_search_results_label()

    @Slot(QtCore.QModelIndex)
    def _on_search_result_clicked(self, index: QtCore.QModelIndex) -> None:
        if not index.isValid():
            return
        row = index.row()
        if 0 <= row < len(self._search_results):
            self._navigate_to_search_result(row)

    def cmd_find_next(self) -> None:
        return self.search_controller.cmd_find_next()

    def cmd_find_prev(self) -> None:
        return self.search_controller.cmd_find_prev()

    def cmd_clear_search(self) -> None:
        return self.search_controller.cmd_clear_search()

    def get_display_text(self, file_line_no: int, widget_line_idx: int) -> str:
        return self.viewport_controller.get_display_text(file_line_no, widget_line_idx)

    def _get_display_text(self, file_line_no: int, widget_line_idx: int) -> str:
        return self.get_display_text(file_line_no, widget_line_idx)

    def _highlight_and_scroll(self, widget_line_no: int) -> None:
        return self.viewport_controller._highlight_and_scroll(widget_line_no)

    def _update_current_line_highlight(self) -> None:
        return self.viewport_controller._update_current_line_highlight()


    # ------------------------------------------------------------ filter ---
    def cmd_filter_dialog(self) -> None:
        return self.filter_controller.cmd_filter_dialog()

    def cmd_apply_filter(self) -> None:
        return self.filter_controller.cmd_apply_filter()

    @Slot(float, int, str)
    def _on_filter_progress(self, pct: float, hits: int, state: str) -> None:
        return self.filter_controller._on_filter_progress(pct, hits, state)

    @Slot(object, object, object, object, object, object)
    def _on_filter_done(self, results, context_lines, filter_all_lines, hit_text_map, hit_lines_set, error) -> None:
        return self.filter_controller._on_filter_done(results, context_lines, filter_all_lines, hit_text_map, hit_lines_set, error)

    def _update_filter_cache(self) -> None:
        return self.filter_controller._update_filter_cache()

    def cmd_clear_filter(self, silent: bool = False) -> None:
        return self.filter_controller.cmd_clear_filter(silent)

    # ------------------------------------------------------------- goto ----
    def cmd_goto(self) -> None:
        return self.viewport_controller.cmd_goto()

    def cmd_goto_start(self) -> None:
        return self.viewport_controller.cmd_goto_start()

    def cmd_reload(self) -> None:
        return self.viewport_controller.cmd_reload()

    def cmd_goto_end(self) -> None:
        return self.viewport_controller.cmd_goto_end()


    # ------------------------------------------------------------ edit ----
    def cmd_format_selection(self) -> None:
        return self.edit_controller.cmd_format_selection()

    def cmd_edit_line(self) -> None:
        return self.edit_controller.cmd_edit_line()

    def revert_edit(self, file_line: int) -> None:
        return self.edit_controller.revert_edit(file_line)

    def _revert_edit(self, file_line: int) -> None:
        return self.revert_edit(file_line)

    def cmd_save_edits(self) -> None:
        return self.edit_controller.cmd_save_edits()

    @Slot(str)
    def _on_save_done(self, backup_path: str) -> None:
        return self.edit_controller._on_save_done(backup_path)

    def start_reindex(self, saved_line: int) -> None:
        return self.file_controller.start_reindex(saved_line)

    def _start_reindex(self, saved_line: int) -> None:
        return self.start_reindex(saved_line)

    @Slot(object)
    def _on_reindex_finished(self, idx: LineIndexer) -> None:
        return self.file_controller._on_reindex_finished(idx)

    @Slot(object, int)
    def _on_reindex_after_save(self, idx: LineIndexer, saved_line: int) -> None:
        return self.file_controller._on_reindex_after_save(idx, saved_line)

    @Slot(str)
    def _on_save_error(self, err: str) -> None:
        return self.edit_controller._on_save_error(err)

    @Slot(str)
    def _on_save_file_changed(self, err: str) -> None:
        return self.edit_controller._on_save_file_changed(err)

    @Slot(str)
    def _on_save_compressed(self, err: str) -> None:
        return self.edit_controller._on_save_compressed(err)

    def cmd_clear_edits(self) -> None:
        return self.edit_controller.cmd_clear_edits()

    def cmd_save_as(self) -> None:
        return self.edit_controller.cmd_save_as()

    # ----------------------------------------------------------- export ----
    def cmd_export(self) -> None:
        return self.edit_controller.cmd_export()

    # -------------------------------------------------------- bookmarks ----
    def cmd_toggle_bookmark(self):
        return self.bookmark_controller.cmd_toggle_bookmark()

    def refresh_bookmarks_tree(self) -> None:
        return self.bookmark_controller.refresh_bookmarks_tree()

    def _refresh_bookmarks_tree(self):
        return self.refresh_bookmarks_tree()

    def refresh_edits_tree(self) -> None:
        return self.bookmark_controller.refresh_edits_tree()

    def _refresh_edits_tree(self):
        return self.refresh_edits_tree()

    def _goto_bookmark(self):
        return self.bookmark_controller._goto_bookmark()

    def _goto_edit(self):
        return self.bookmark_controller._goto_edit()

    def goto_file_line(self, ln: int, is_filtered_index: bool = False) -> None:
        return self.viewport_controller.goto_file_line(ln, is_filtered_index)

    def _goto_file_line(self, ln: int, is_filtered_index: bool = False) -> None:
        return self.goto_file_line(ln, is_filtered_index)


    def _delete_selected_bookmarks(self):
        return self.bookmark_controller._delete_selected_bookmarks()

    def _delete_selected_edits(self):
        return self.bookmark_controller._delete_selected_edits()

    def cmd_next_bookmark(self):
        return self.bookmark_controller.cmd_next_bookmark()

    def cmd_prev_bookmark(self):
        return self.bookmark_controller.cmd_prev_bookmark()

    def cmd_clear_bookmarks(self):
        return self.bookmark_controller.cmd_clear_bookmarks()

    # ----------------------------------------------------------- follow ----
    def _cancel_follow_if_active(self) -> None:
        return self.file_controller._cancel_follow_if_active()

    def cmd_toggle_follow(self) -> None:
        return self.file_controller.cmd_toggle_follow()

    def cmd_refresh(self) -> None:
        return self.file_controller.cmd_refresh()

    def cmd_reload(self) -> None:
        return self.file_controller.cmd_reload()

    def _follow_poll(self) -> None:
        return self.file_controller._follow_poll()

    def _start_follow_reindex(self, current_size: int, current_inode: int) -> None:
        return self.file_controller._start_follow_reindex(current_size, current_inode)

    @Slot(object)
    def _on_follow_reindex_slot(self, idx: LineIndexer) -> None:
        return self.file_controller._on_follow_reindex_slot(idx)

    @Slot()
    def _on_follow_reindex_clear_flag(self) -> None:
        return self.file_controller._on_follow_reindex_clear_flag()

    def _on_follow_new_lines(self, new_line_count: int = 0, mtime_str: str = "", ctime_str: str = "") -> None:
        return self.file_controller._on_follow_new_lines(new_line_count, mtime_str, ctime_str)

    @Slot(object, int, int)
    def _on_follow_reindex(self, idx: LineIndexer, new_size: int, new_inode: int) -> None:
        return self.file_controller._on_follow_reindex(idx, new_size, new_inode)

    @Slot(str)
    def _on_follow_reindex_failed(self, err: str) -> None:
        return self.file_controller._on_follow_reindex_failed(err)

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
            self.file_controller._stop_background_threads()
            try:
                self.indexer.close()
            except Exception:
                pass
            self._start_reindex(saved_line)

    # --------------------------------------------------------- misc ----
    def reload_current_view(self) -> None:
        return self.viewport_controller.reload_current_view()

    def _reload_current_view(self) -> None:
        return self.reload_current_view()

    def refresh_status(self) -> None:
        if not self.indexer:
            self.set_status(self.t("st_ready"))
            return
        if self.filter_active:
            hits = len(self.filter_results) if getattr(self, "filter_results", None) is not None else 0
            left = self._fmt("st_filtered", hits=hits, total=self.indexer.line_count)
        else:
            left = self._fmt("st_done", total=self.indexer.line_count, size=fmt_size(self.indexer.size))
        if len(self.edit_buffer) > 0:
            left += "   |   " + self.t("st_edits").format(n=len(self.edit_buffer))
        self.set_status(left)

    def _refresh_status(self) -> None:
        return self.refresh_status()

    def close(self) -> None:
        """Zamyka indexer, anuluje wątki. Wywoływane przy zamykaniu zakładki."""
        # --- Wyłącz follow mode aby przerwać cykl QTimer.singleShot ---
        self.follow_active = False

        try:
            if getattr(self, "_edge_timer", None) is not None:
                self._edge_timer.stop()
            if getattr(self, "_minimap_update_timer", None) is not None:
                self._minimap_update_timer.stop()
            if getattr(self, "_scroll_debounce_timer", None) is not None:
                self._scroll_debounce_timer.stop()
            if getattr(self, "_edge_load_timer", None) is not None:
                self._edge_load_timer.stop()
        except Exception:
            pass
        if getattr(self, "filter_engine", None) and self.filter_engine.is_running():
            self.filter_engine.cancel()
        if getattr(self, "_search_engine", None) and self._search_engine.is_running():
            self._search_engine.cancel()
        if getattr(self, "file_controller", None) is not None:
            self.file_controller._stop_background_threads()
        if getattr(self, "indexer", None) is not None:
            try:
                self.indexer.close()
            except Exception:
                pass
            self.indexer = None

        # --- Wyczyść dane filtrowania (poprawione nazwy atrybutów) ---
        self.line_map = None
        self.filter_results = None
        self._filter_all_lines = None
        self.filter_context_lines = None
        self._filter_hit_text_map = None
        self._filter_hit_lines = None
        self.filter_engine = None
        self._search_engine = None

        # --- Wyczyść dane wyszukiwania ---
        self._search_results = []
        self._search_results_all = []
        if self._search_model is not None:
            self._search_model.clear()
            self._search_model = None
        self._search_worker = None
        self._search_thread = None

        # --- Wyczyść pozostałe duże struktury danych ---
        self.window_lines = []
        self._minimap_data = []
        self.edit_buffer = None
        self.bookmarks = {}
        try:
            self.text.clear()
        except Exception:
            pass



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
