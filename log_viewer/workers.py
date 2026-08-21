"""QThread workers — asynchroniczne indeksowanie, filtrowanie, zapis."""

from __future__ import annotations

import array
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from .bitset import Bitset
from .edit_buffer import EditBuffer
from .exceptions import CompressedSaveError, FileChangedError
from .filter_engine import FilterEngine, FilterStrategy, PlainTextStrategy, RegexStrategy
from .helpers import open_maybe_compressed
from .indexer import LineIndexer


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
    def run(self) -> None:
        try:

            def progress_cb(pct: float) -> None:
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

    progress = Signal(float, int, str, object)  # pct, hits, state, partial_results (array lub None)
    finished = Signal(
        object, object, object, object, object, object
    )  # results, context_lines, filter_all_lines, hit_text_map, hit_lines_set, error

    def __init__(
        self,
        engine: FilterEngine,
        pattern: str,
        use_regex: bool,
        case_sensitive: bool,
        negate: bool,
        context_after: int = 0,
        search_in_filter: bool = False,
        filtered_lines: Bitset | None = None,
    ):
        super().__init__()
        self._engine = engine
        self._pattern = pattern
        self._use_regex = use_regex
        self._case_sensitive = case_sensitive
        self._negate = negate
        self._context_after = context_after
        self._search_in_filter = search_in_filter
        self._filtered_lines = filtered_lines

    @Slot()
    def run(self) -> None:
        def on_progress(pct: float, hits: int, state: str = "filtering", partial_results: object = None) -> None:
            self.progress.emit(pct, hits, state, partial_results)

        def on_done(results: Any, error: str | None) -> None:
            if error or not results or len(results) == 0:
                self.finished.emit(None, set(), None, {}, set(), error)
                return

            if self._context_after > 0:
                self.progress.emit(100.0, len(results), "context", None)
            else:
                self.progress.emit(100.0, len(results), "filtering", None)

            filter_all_lines = results.expand_context(self._context_after)

            res_tuple = results.to_raw()
            all_tuple = filter_all_lines.to_raw()

            self.finished.emit(res_tuple, set(), all_tuple, {}, set(), error)

        self._engine.start(
            self._pattern,
            self._use_regex,
            self._case_sensitive,
            self._negate,
            on_progress,
            on_done,
            search_in_filter=self._search_in_filter,
            filtered_lines=self._filtered_lines,
        )


class SaveWorker(QObject):
    """Worker zapisujący edycje w tle."""

    progress = Signal(float)
    finished = Signal(str)  # backup_path
    error = Signal(str)
    file_changed = Signal(str)
    compressed = Signal(str)

    def __init__(
        self,
        edit_buffer: EditBuffer,
        file_path: str,
        expected_mtime: float,
        expected_size: int,
        encoding: str = "utf-8",
    ):
        super().__init__()
        self._edit_buffer = edit_buffer
        self._file_path = file_path
        self._expected_mtime = expected_mtime
        self._expected_size = expected_size
        self._encoding = encoding

    @Slot()
    def run(self) -> None:
        try:

            def progress_cb(pct: float) -> None:
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
            if isinstance(e, FileChangedError):
                self.file_changed.emit(str(e))
            elif isinstance(e, CompressedSaveError):
                self.compressed.emit(str(e))
            else:
                self.error.emit(str(e))


class SaveAsWorker(QObject):
    """Worker zapisujący zawartość (wraz z modyfikacjami z edit_buffer) do nowego pliku w tle."""

    progress = Signal(float)
    finished = Signal()
    error = Signal(str)

    def __init__(
        self, edit_buffer: EditBuffer, src_path: str, dst_path: str, encoding: str = "utf-8", total_lines: int = 0
    ):
        super().__init__()
        self._edit_buffer = edit_buffer
        self._src_path = src_path
        self._dst_path = dst_path
        self._encoding = encoding
        self._total_lines = total_lines
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            edits = self._edit_buffer.edits
            cancel_set = self._cancel_event.is_set
            line_no = 0
            last_progress_lines = 0

            with open_maybe_compressed(self._src_path, "rb") as src, open_maybe_compressed(self._dst_path, "wb") as dst:
                for raw in src:
                    if line_no in edits:
                        new_text = edits[line_no]
                        if new_text is not None:
                            dst.write(new_text.encode(self._encoding, errors="replace") + b"\n")
                    else:
                        dst.write(raw)

                    line_no += 1
                    if self._total_lines and (line_no - last_progress_lines) > 5000:
                        if cancel_set():
                            break
                        self.progress.emit(line_no / self._total_lines * 100.0)
                        last_progress_lines = line_no

            if cancel_set():
                self.error.emit("cancelled")
                try:
                    Path(self._dst_path).unlink()
                except OSError:
                    pass
                return

            self.finished.emit()
        except BaseException as e:
            self.error.emit(str(e))


class ExportWorker(QObject):
    """Worker eksportujący wyniki filtrowania lub pełny plik z uwzględnieniem edycji z edit_buffer."""

    progress = Signal(float)
    finished = Signal(int)  # count
    error = Signal(str)

    def __init__(
        self,
        edit_buffer: EditBuffer,
        src_path: str,
        dst_path: str,
        encoding: str = "utf-8",
        filter_active: bool = False,
        filter_results: Bitset | None = None,
        total_lines: int = 0,
    ) -> None:
        super().__init__()
        self._edit_buffer = edit_buffer
        self._src_path = src_path
        self._dst_path = dst_path
        self._encoding = encoding
        self._filter_active = filter_active
        self._filter_results = filter_results
        self._total_lines = total_lines
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            count = 0

            cancel_set = self._cancel_event.is_set
            edits = self._edit_buffer.edits
            line_no = 0
            last_progress_lines = 0

            with open_maybe_compressed(self._src_path, "rb") as src, open_maybe_compressed(self._dst_path, "wb") as out:
                if not self._filter_active:
                    for raw in src:
                        if line_no in edits:
                            new_text = edits[line_no]
                            if new_text is not None:
                                out.write(new_text.encode(self._encoding, errors="replace") + b"\n")
                                count += 1
                        else:
                            out.write(raw)
                            count += 1

                        line_no += 1
                        if self._total_lines and (line_no - last_progress_lines) > 5000:
                            if cancel_set():
                                break
                            self.progress.emit(line_no / self._total_lines * 100.0)
                            last_progress_lines = line_no
                elif self._filter_results is not None:
                    words = self._filter_results.words
                    num_words = len(words)
                    word_idx = 0
                    bit_idx = 0
                    w = words[0] if num_words > 0 else 0

                    for raw in src:
                        if w & (1 << bit_idx):
                            if line_no in edits:
                                new_text = edits[line_no]
                                if new_text is not None:
                                    out.write(new_text.encode(self._encoding, errors="replace") + b"\n")
                                    count += 1
                            else:
                                out.write(raw)
                                count += 1

                        line_no += 1
                        if self._total_lines and (line_no - last_progress_lines) > 5000:
                            if cancel_set():
                                break
                            self.progress.emit(line_no / self._total_lines * 100.0)
                            last_progress_lines = line_no

                        bit_idx += 1
                        if bit_idx == 64:
                            bit_idx = 0
                            word_idx += 1
                            if word_idx < num_words:
                                w = words[word_idx]
                            else:
                                w = 0

            if cancel_set():
                self.error.emit("cancelled")
                try:
                    Path(self._dst_path).unlink()
                except OSError:
                    pass
                return

            self.finished.emit(count)
        except BaseException as e:
            self.error.emit(str(e))


class IncrementalFilterWorker(QObject):
    """Worker wykonujący szybkie, inkrementalne wyszukiwanie w locie (dla nowych danych w Follow)."""

    finished = Signal(object)  # zwraca: krotkę z danymi trafień (res_tuple)

    def __init__(
        self,
        indexer: LineIndexer,
        start_line: int,
        end_line: int,
        pattern: str,
        use_regex: bool,
        case_sensitive: bool,
        negate: bool,
        encoding: str,
    ) -> None:
        super().__init__()
        self._indexer = indexer
        self._start_line = start_line
        self._end_line = end_line
        self._pattern = pattern
        self._use_regex = use_regex
        self._case_sensitive = case_sensitive
        self._negate = negate
        self._encoding = encoding
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """Anuluje działanie workera inkrementalnego filtrowania."""
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        results_array = array.array("Q")
        # Bezpieczne tłumienie wyjątków w wątku pobocznym przy przerwaniu wyszukiwania (stabilność QThread)
        # noinspection PyBroadException
        try:
            strategy: FilterStrategy
            if self._use_regex:
                strategy = RegexStrategy(self._pattern, self._case_sensitive, self._negate, self._encoding)
            else:
                strategy = PlainTextStrategy(self._pattern, self._case_sensitive, self._negate, self._encoding)

            start_offset = self._indexer.offset_of_line(self._start_line)
            if start_offset is not None and not self._indexer.is_compressed:
                with open(str(self._indexer.path), "rb") as f:
                    f.seek(start_offset)
                    chunk = f.read()
                if not self._cancel_event.is_set():
                    hits = strategy.match_chunk(chunk, self._start_line)
                    results_array.extend(hits)
            else:
                lines = self._indexer.read_lines(self._start_line, self._end_line - self._start_line)
                cancel_set = self._cancel_event.is_set
                for line_no, text in lines:
                    if cancel_set():
                        return
                    text_bytes = text.encode(self._encoding, errors="replace")
                    if strategy.match(text_bytes):
                        results_array.append(line_no)
        except BaseException:
            pass

        if self._cancel_event.is_set():
            return

        res_tuple: tuple[int, array.array[int], int]
        if len(results_array) > 0:
            results_bitset = Bitset.from_indices(results_array, self._end_line)
            res_tuple = results_bitset.to_raw()
        else:
            res_tuple = (self._end_line, array.array("Q"), 0)

        self.finished.emit(res_tuple)
