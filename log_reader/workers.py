"""QThread workers — asynchroniczne indeksowanie, filtrowanie, zapis."""

from __future__ import annotations

import threading
import time
from typing import Optional

from PySide6 import QtCore
from PySide6.QtCore import QObject, Signal, Slot

from .indexer import LineIndexer
from .filter_engine import FilterEngine
from .edit_buffer import EditBuffer


class IndexerWorker(QObject):
    """Worker budujący LineIndexer w tle. Emituje sygnały z main thread.

    Możliwy do anulowania — cancel() ustawia flagę, którą _build_parallel
    sprawdza w pętli imap_unordered. Po anulowaniu finished NIE jest emitowane
    (emitowany jest error z komunikatem „cancelled").
    """
    progress = Signal(float)
    finished = Signal(object)  # LineIndexer
    error = Signal(str)

    def __init__(self, path: str, encoding: str, index_interval_bytes: int):
        super().__init__()
        self._path = path
        self._encoding = encoding
        self._index_interval_bytes = index_interval_bytes
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Żąda anulowania. Bezpieczne do wywołania z głównego wątku."""
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @Slot()
    def run(self):
        try:
            def progress_cb(pct: float):
                self.progress.emit(pct)
            # Przekaż cancel_event do LineIndexer — sprawdzi go w pętli
            # imap_unordered i przerwie indeksowanie.
            idx = LineIndexer(
                self._path,
                progress_cb=progress_cb,
                encoding=self._encoding,
                index_interval_bytes=self._index_interval_bytes,
                cancel_event=self._cancel_event,
            )
            if self._cancel_event.is_set():
                self.error.emit("cancelled")
                return
            self.finished.emit(idx)
        except BaseException as e:
            self.error.emit(str(e))


class FilterWorker(QObject):
    """Worker uruchamiający FilterEngine w tle."""
    progress = Signal(float, int, str)
    finished = Signal(object, object, object, object, object, object)  # results, context_lines, filter_all_lines, hit_text_map, hit_lines_set, error

    def __init__(self, engine: FilterEngine, pattern: str, use_regex: bool,
                 case_sensitive: bool, negate: bool,
                 context_after: int = 0):
        super().__init__()
        self._engine = engine
        self._pattern = pattern
        self._use_regex = use_regex
        self._case_sensitive = case_sensitive
        self._negate = negate
        self._context_after = context_after

    @Slot()
    def run(self):
        def on_progress(pct: float, hits: int, state: str = "filtering"):
            self.progress.emit(pct, hits, state)

        def on_done(results, error):
            if error or not results:
                self.finished.emit(results, set(), [], {}, set(), error)
                return

            if self._context_after > 0:
                self.progress.emit(100.0, len(results), "context")
            else:
                self.progress.emit(100.0, len(results), "filtering")

            # Zbuduj kontekst filtru w tle
            context_lines = set()
            hit_lines = {ln for (ln, _off, _text) in results}
            n = self._context_after
            if n > 0 and self._engine.indexer:
                counter = 0
                total = self._engine.indexer.line_count
                for ln in hit_lines:
                    for offset in range(1, n + 1):
                        ctx = ln + offset
                        if ctx >= total:
                            break
                        if ctx not in hit_lines:
                            context_lines.add(ctx)
                    counter += 1
                    if counter % 50000 == 0:
                        time.sleep(0.01)
                        self.progress.emit(100.0, len(results), "context")

            # Zbuduj pełne mapowanie linii
            filter_all_lines = []
            hit_text_map = {}
            if results:
                combined = hit_lines.copy()
                combined.update(context_lines)
                filter_all_lines = sorted(combined)
                # Przygotuj słownik, aby uniknąć blokowania wątku UI
                hit_text_map = {}
                for i, (ln, _off, text) in enumerate(results):
                    hit_text_map[ln] = text
                    if i % 100000 == 0:
                        time.sleep(0.01)

            self.finished.emit(results, context_lines, filter_all_lines, hit_text_map, hit_lines, error)

        self._engine.start(
            self._pattern, self._use_regex, self._case_sensitive, self._negate,
            on_progress, on_done,
        )


class SaveWorker(QObject):
    """Worker zapisujący edycje w tle."""
    progress = Signal(float)
    finished = Signal(str)  # backup_path
    error = Signal(str)
    file_changed = Signal(str)
    compressed = Signal(str)

    def __init__(self, edit_buffer: EditBuffer, file_path: str,
                 expected_mtime: float, expected_size: int,
                 encoding: str = "utf-8"):
        super().__init__()
        self._edit_buffer = edit_buffer
        self._file_path = file_path
        self._expected_mtime = expected_mtime
        self._expected_size = expected_size
        self._encoding = encoding

    @Slot()
    def run(self):
        try:
            def progress_cb(pct: float):
                self.progress.emit(pct)
            backup_path = self._edit_buffer.save_to_file(
                self._file_path,
                progress_cb=progress_cb,
                expected_mtime=self._expected_mtime,
                expected_size=self._expected_size,
                encoding=self._encoding,
            )
            self.finished.emit(backup_path)
        except BaseException as e:
            from .exceptions import FileChangedError, CompressedSaveError
            if isinstance(e, FileChangedError):
                self.file_changed.emit(str(e))
            elif isinstance(e, CompressedSaveError):
                self.compressed.emit(str(e))
            else:
                self.error.emit(str(e))
