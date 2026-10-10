"""LineIndexer — rzadki indeks (byte_offset, line_number) co ~1 MB."""

from __future__ import annotations

import bisect
import itertools
import multiprocessing
import multiprocessing.sharedctypes
import operator
import sys
import threading
import time
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .helpers import (
    DEFAULT_ENCODING,
    INDEX_CHUNK_BYTES,
    INDEX_INTERVAL_BYTES,
    PARALLEL_INDEX_THRESHOLD_BYTES,
    is_compressed,
    open_maybe_compressed,
)


@dataclass
class IndexEntry:
    __slots__ = ("offset", "line")
    offset: int  # byte offset początku tej linii (0-indexed)
    line: int  # numer linii (0-indexed)


_ENTRY_LINE = operator.attrgetter("line")
_ENTRY_OFFSET = operator.attrgetter("offset")

_shared_progress_bytes: multiprocessing.sharedctypes.Synchronized[int] | Any = None


def _init_worker(progress_val: multiprocessing.sharedctypes.Synchronized[int] | Any) -> None:
    global _shared_progress_bytes
    _shared_progress_bytes = progress_val


_INDEXER_READ_CHUNK_SIZE = 32 * 1024 * 1024  # 32 MB — większy = lepsza lokalność, wykorzystuje cache OS


def _collect_chunk_index_entries(
    chunk: bytes,
    base_offset: int,
    base_line: int,
    last_indexed_offset: int,
    interval: int,
    entries: list[Any],
    create_entry: Callable[[int, int], Any] | None = None,
) -> tuple[int, int]:
    """Wyszukuje granice indeksowania w chunku i dodaje nowe wpisy do entries.

    Zwraca krotkę (nowy_last_indexed_offset, total_chunk_newlines).
    Działa całkowicie bez alokacji pamięci pośredniej w pętli (zero-copy w C),
    zliczając znaki nowej linii w jednym przebiegu bez redundancji.
    """
    current_end_offset = base_offset + len(chunk)
    last_counted_pos = 0
    running_line = base_line
    while current_end_offset - last_indexed_offset >= interval:
        target_offset = last_indexed_offset + interval
        target_in_chunk = max(0, target_offset - base_offset)

        nl = chunk.find(b"\n", target_in_chunk)
        if nl == -1:
            break

        offset = base_offset + nl + 1
        running_line += chunk.count(b"\n", last_counted_pos, nl) + 1
        last_counted_pos = nl + 1
        entry = (offset, running_line) if create_entry is None else create_entry(offset, running_line)
        entries.append(entry)
        last_indexed_offset = offset

    total_chunk_nls = (running_line - base_line) + chunk.count(b"\n", last_counted_pos)
    return last_indexed_offset, total_chunk_nls


def _indexer_worker_chunk(args: tuple[int, int, str, int, int]) -> tuple[int, list[tuple[int, int]], int]:
    """
    Funkcja robocza (worker) dla modułu multiprocessing — indeksuje fragment pliku.

    Optymalizacje wydajności:
      1. Użycie `bytes.count(b"\n")` do szybkiego liczenia znaków nowej linii w kodzie maszynowym (C).
      2. Wykorzystanie większych fragmentów odczytu (`_INDEXER_READ_CHUNK_SIZE` = 32 MB) dla lepszej lokalności pamięci podręcznej.
      3. Liniowe, bezalokacyjne zliczanie nowych linii w buforze (zero-copy) bez łączenia buforów i bez kopiowania carry.
      4. Wywoływanie operacji `find()` jedynie w momencie, gdy minął wymagany `interval` bajtów.
      5. Zastosowanie dużego bufora wejściowego przy wywoływaniu `open()`.

    Liczy znaki nowej linii w przydzielonym zakresie `[start, end)`. Każdy proces
    przetwarza wyłącznie bajty zadeklarowane w swoim wycinku.
    """
    start, end, path_str, interval, chunk_id = args
    try:
        line_count = 0
        index_entries: list[tuple[int, int]] = []
        last_idx = start  # ostatni offset gdzie zapisaliśmy index entry
        local_line = 0
        bytes_processed = 0  # ile bajtów z [start, end) przetworzono

        with open(path_str, "rb", buffering=1024 * 1024) as f:
            f.seek(start)
            while bytes_processed < (end - start):
                to_read = min(_INDEXER_READ_CHUNK_SIZE, (end - start) - bytes_processed)
                chunk = f.read(to_read)
                if not chunk:
                    break
                chunk_len = len(chunk)

                # Aktualizuj postęp płynnie
                if _shared_progress_bytes is not None:
                    with _shared_progress_bytes.get_lock():
                        _shared_progress_bytes.value += chunk_len

                current_base_offset = start + bytes_processed

                last_idx, nl_count = _collect_chunk_index_entries(
                    chunk,
                    current_base_offset,
                    local_line,
                    last_idx,
                    interval,
                    index_entries,
                )

                line_count += nl_count
                local_line += nl_count
                bytes_processed += chunk_len

        return line_count, index_entries, chunk_id
    except (OSError, ValueError, RuntimeError) as e:
        print(f"Warning: indexer worker {chunk_id} failed: {e}", file=sys.stderr)
        return 0, [], chunk_id


class LineIndexer:
    """
    Buduje rzadki indeks pliku: co ~1 MB zapisuje (byte_offset, line_number).
    Pozwala na O(log N) skok do dowolnej linii bez wczytywania całego pliku.

    Utrzymuje jeden otwarty deskryptor pliku (z blokadą).
    Wspiera pliki skompresowane (.gz/.bz2/.xz).
    Wspiera konfigurowalne kodowanie.
    """

    def __init__(
        self,
        path: str | Path,
        progress_cb: Callable[[float], None] | None = None,
        encoding: str = DEFAULT_ENCODING,
        index_interval_bytes: int | None = None,
        cancel_event: threading.Event | None = None,
        parallel_threshold_bytes: int | None = None,
    ) -> None:
        self.path: Path = Path(path)
        self.encoding: str = encoding
        self.is_compressed: bool = is_compressed(str(self.path))
        self.index_interval_bytes = index_interval_bytes if index_interval_bytes is not None else INDEX_INTERVAL_BYTES
        self.parallel_threshold_bytes = (
            parallel_threshold_bytes if parallel_threshold_bytes is not None else PARALLEL_INDEX_THRESHOLD_BYTES
        )
        self.size: int = 0
        self.line_count: int = 0
        self.has_trailing_newline: bool = True
        self.index: list[IndexEntry] = [IndexEntry(0, 0)]
        self._progress_cb = progress_cb
        self._cancel_event = cancel_event  # None = nie można anulować
        self._file_cache: typing.IO[bytes] | None = None
        self._file_lock = threading.Lock()
        self._last_indexed_offset = 0
        self._cursor_pos: tuple[int, int] | None = None  # (line_number, byte_offset)
        self._build()

    def __del__(self) -> None:
        try:
            self.close()
        except (OSError, RuntimeError, AttributeError):
            pass

    def close(self) -> None:
        with self._file_lock:
            if self._file_cache is not None:
                try:
                    self._file_cache.close()
                except OSError:
                    pass
                self._file_cache = None
            self._cursor_pos = None

    def _get_file(self) -> typing.IO[bytes]:
        if self._file_cache is None:
            buffering = 1024 * 1024 if not self.is_compressed else -1
            self._file_cache = open_maybe_compressed(str(self.path), "rb", buffering=buffering)
        file_obj = self._file_cache
        assert file_obj is not None
        return file_obj

    def _build(self) -> None:
        try:
            self.size = self.path.stat().st_size
        except OSError:
            self.size = 0
        # Dla bardzo dużych plików użyj multiprocessing — na Windows dopiero >300 MB amortyzuje spawn 6 procesów.
        if not self.is_compressed and self.size > self.parallel_threshold_bytes:
            try:
                self._build_parallel()
                return
            except (OSError, RuntimeError, ValueError) as e:
                print(f"Warning: parallel indexing failed ({e}), falling back to single-thread", file=sys.stderr)
                self.index = [IndexEntry(0, 0)]
                self._last_indexed_offset = 0
        self._build_single()

    def _build_parallel(self) -> None:
        """Indeksowanie równoległe z multiprocessing.

        Emituje postęp niezwykle płynnie przy użyciu współdzielonego licznika.
        Dzieli plik na chunki dla workerów proporcjonalnie do ich ilości.
        """
        n_workers = min(max(2, multiprocessing.cpu_count()), 6)
        n_chunks = n_workers * 2
        chunk_size = self.size // n_chunks
        ranges = []
        for i in range(n_chunks):
            start = i * chunk_size
            end = (i + 1) * chunk_size if i < n_chunks - 1 else self.size
            if end > start:  # zapobiega przekazaniu pustych przedziałów
                ranges.append((start, end, str(self.path), self.index_interval_bytes, i))

        if not ranges:
            self._build_single()
            return

        shared_progress = multiprocessing.Value("Q", 0)
        results: list[tuple[int, list[tuple[int, int]], int]] = []
        cancelled = False

        with multiprocessing.Pool(n_workers, initializer=_init_worker, initargs=(shared_progress,)) as pool:
            result_async = pool.map_async(_indexer_worker_chunk, ranges)

            while not result_async.ready():
                if self._cancel_event is not None and self._cancel_event.is_set():
                    cancelled = True
                    pool.terminate()
                    break

                if self._progress_cb:
                    bytes_done = shared_progress.value
                    pct = (bytes_done / self.size) * 100.0 if self.size else 0.0
                    if pct > 99.9:
                        pct = 99.9
                    self._progress_cb(pct)

                time.sleep(0.05)  # Częstotliwość odświeżania paska: 20 FPS

            if not cancelled:
                results = result_async.get()

        if cancelled:
            # Nie buduj indeksu — wróć z pustym. Worker sprawdzi cancel_event i wyemituje error("cancelled").
            self.index = [IndexEntry(0, 0)]
            self.line_count = 0
            self._last_indexed_offset = 0
            return

        results.sort(key=lambda x: x[2])
        total_lines = 0
        full_index: list[IndexEntry] = [IndexEntry(0, 0)]
        last_indexed_offset = 0
        for line_count, index_entries, _chunk_id in results:
            line_offset = total_lines
            for offset, local_line in index_entries:
                global_line = line_offset + local_line
                if offset - last_indexed_offset >= self.index_interval_bytes:
                    full_index.append(IndexEntry(offset, global_line))
                    last_indexed_offset = offset
            total_lines += line_count

        if self._progress_cb:
            self._progress_cb(100.0)

        last_byte = b""
        if self.size > 0:
            try:
                with open(str(self.path), "rb") as f:
                    f.seek(self.size - 1)
                    last_byte = f.read(1)
            except OSError:
                pass

        if self.size > 0 and last_byte != b"\n":
            total_lines += 1
            self.has_trailing_newline = False
        else:
            self.has_trailing_newline = True

        self.index = full_index
        self.line_count = total_lines
        self._last_indexed_offset = last_indexed_offset

    def _decode_raw_line(self, raw: bytes) -> str:
        """Dekoduje surowe bajty linii tekstu i usuwa znaki nowej linii."""
        raw = raw.rstrip(b"\r\n")
        try:
            return raw.decode(self.encoding, errors="replace")
        except (UnicodeError, LookupError, ValueError, TypeError):
            return repr(raw)

    def _consume_chunk_index_entries(
        self,
        chunk: bytes,
        base_offset: int,
        base_line: int,
        last_indexed_offset: int,
    ) -> tuple[int, int]:
        """Wyszukuje granice indeksowania w chunku i dodaje nowe IndexEntry.

        Zwraca krotkę (nowy_last_indexed_offset, total_chunk_newlines).
        Zoptymalizowane pod kątem alokacji pamięci: bez kopii wycinków,
        zliczanie znaków nowej linii w jednym przebiegu w kodzie C.
        """
        return _collect_chunk_index_entries(
            chunk,
            base_offset,
            base_line,
            last_indexed_offset,
            self.index_interval_bytes,
            self.index,
            create_entry=IndexEntry,
        )

    def _build_single(self) -> None:
        """Implementacja single-thread — fallback i dla małych plików."""
        line_num = 0
        last_indexed_offset = 0
        bytes_read = 0
        last_byte = b""
        with open_maybe_compressed(str(self.path), "rb") as f:
            while True:
                if self._cancel_event is not None and self._cancel_event.is_set():
                    self.index = [IndexEntry(0, 0)]
                    self.line_count = 0
                    self._last_indexed_offset = 0
                    return
                chunk = f.read(INDEX_CHUNK_BYTES)
                if not chunk:
                    break
                chunk_len = len(chunk)

                last_indexed_offset, nl_count = self._consume_chunk_index_entries(
                    chunk, bytes_read, line_num, last_indexed_offset
                )

                line_num += nl_count
                bytes_read += chunk_len
                last_byte = chunk[-1:]
                if self._progress_cb and self.size > 0:
                    self._progress_cb(bytes_read / self.size * 100.0)

        if bytes_read > 0 and last_byte != b"\n":
            line_num += 1
            self.has_trailing_newline = False
        else:
            self.has_trailing_newline = True

        self.line_count = line_num
        self._last_indexed_offset = last_indexed_offset

    def update_from(self, new_size: int, progress_cb: Callable[[float], None] | None = None) -> int:
        """Inkrementalna aktualizacja indeksu. Dla skompresowanych zwraca 0."""
        if new_size <= self.size:
            return 0
        if self.is_compressed:
            return 0
        old_size = self.size
        bytes_to_read = new_size - old_size
        line_num = self.line_count
        last_indexed_offset = self._last_indexed_offset
        bytes_read = 0
        total_new_nls = 0
        last_byte = b""
        had_trailing_nl = self.has_trailing_newline
        base_line = line_num if had_trailing_nl else max(0, line_num - 1)

        with open(str(self.path), "rb") as f:
            f.seek(old_size)
            while True:
                chunk = f.read(INDEX_CHUNK_BYTES)
                if not chunk:
                    break
                chunk_len = len(chunk)
                last_byte = chunk[-1:]

                base = old_size + bytes_read
                last_indexed_offset, nl_count = self._consume_chunk_index_entries(
                    chunk, base, base_line, last_indexed_offset
                )
                total_new_nls += nl_count

                base_line += nl_count
                bytes_read += chunk_len
                if progress_cb and bytes_to_read > 0:
                    progress_cb(min(100.0, bytes_read / bytes_to_read * 100.0))
        with self._file_lock:
            if self._file_cache is not None:
                try:
                    self._file_cache.close()
                except OSError:
                    pass
                self._file_cache = None
            self._cursor_pos = None

        if had_trailing_nl:
            new_lines = total_new_nls + (1 if bytes_read > 0 and last_byte != b"\n" else 0)
        else:
            if total_new_nls == 0:
                new_lines = 0
            else:
                new_lines = (total_new_nls - 1) + (1 if last_byte != b"\n" else 0)

        self.has_trailing_newline = (bytes_read == 0 and had_trailing_nl) or (last_byte == b"\n")
        self.line_count = line_num + new_lines
        self._last_indexed_offset = last_indexed_offset
        self.size = old_size + bytes_read
        return new_lines

    @staticmethod
    def _advance_lines(f: typing.IO[bytes], needed: int) -> bool:
        """Szybkie pomijanie `needed` linii w otwartym pliku bez alokowania zbędnych obiektów."""
        if needed <= 0:
            return True
        if needed <= 16:
            return all(bool(f.readline()) for _ in range(needed))

        chunk_size = 64 * 1024
        while needed > 0:
            chunk = f.read(chunk_size)
            if not chunk:
                return False
            c = chunk.count(b"\n")
            if needed > c:
                needed -= c
            else:
                idx = -1
                for _ in range(needed):
                    idx = chunk.find(b"\n", idx + 1)
                f.seek(f.tell() - len(chunk) + idx + 1)
                break
        return True

    def _seek_to_line(self, f: typing.IO[bytes], target_line: int) -> bool:
        """Ustawia wskaźnik otwartego pliku na początek `target_line`.

        Optymalizacja: jeśli ostatnia pozycja kursora (`_cursor_pos`) znajduje się
        pomiędzy wpisem z indeksu a `target_line`, wznawia odczyt w przód bez
        cofania się do punktu indeksu (f.seek).
        """
        idx = bisect.bisect_right(self.index, target_line, key=_ENTRY_LINE) - 1
        start: IndexEntry = self.index[max(0, idx)]

        if self._cursor_pos is not None and start.line <= self._cursor_pos[0] <= target_line:
            cur_line, cur_off = self._cursor_pos
            if f.tell() != cur_off:
                f.seek(cur_off)
            needed = target_line - cur_line
        else:
            f.seek(start.offset)
            needed = target_line - start.line

        if not self._advance_lines(f, needed):
            self._cursor_pos = None
            return False

        self._cursor_pos = (target_line, f.tell())
        return True

    def offset_of_line(self, target_line: int) -> int | None:
        if target_line < 0:
            target_line = 0
        if target_line >= self.line_count:
            return None

        with self._file_lock:
            f = self._get_file()
            if not self._seek_to_line(f, target_line):
                return None
            return f.tell()

    def read_specific_lines(self, target_lines: list[int]) -> list[tuple[int, str]]:
        """
        Zoptymalizowana metoda do wczytywania wielu konkretnych (potencjalnie rzadkich) linii naraz.
        Wykorzystuje globalny kursor pozycji oraz sekwencyjny odczyt w przód bez zbędnych przewinięć.
        """
        if not target_lines:
            return []

        # Usunięcie duplikatów i posortowanie ułatwia sekwencyjny odczyt
        targets = sorted(set(target_lines))
        out: list[tuple[int, str]] = []

        with self._file_lock:
            f = self._get_file()
            for target_line in targets:
                if target_line < 0 or target_line >= self.line_count:
                    continue

                if not self._seek_to_line(f, target_line):
                    break

                raw = f.readline()
                if not raw:
                    self._cursor_pos = None
                    break
                text = self._decode_raw_line(raw)
                out.append((target_line, text))
                self._cursor_pos = (target_line + 1, f.tell())

        return out

    def read_lines(self, start_line: int, count: int) -> list[tuple[int, str]]:
        if start_line < 0:
            start_line = 0
        if start_line >= self.line_count or count <= 0:
            return []

        out: list[tuple[int, str]] = []
        with self._file_lock:
            f = self._get_file()
            if not self._seek_to_line(f, start_line):
                return []
            for i, raw in enumerate(itertools.islice(f, count)):
                text = self._decode_raw_line(raw)
                out.append((start_line + i, text))
            if out:
                self._cursor_pos = (start_line + len(out), f.tell())
        return out

    def line_at_byte_offset(self, byte_offset: int) -> tuple[int, int]:
        """Zwraca (numer_linii, byte_offset_początku_linii) dla wskazanego byte_offset.

        Zoptymalizowane pod kątem suwaka/slidera: zamiast powolnego wczytywania
        tysięcy linii za pomocą `f.readline()`, czyta blok bajtów i szybko zlicza
        znaki nowej linii w kodzie maszynowym C (`bytes.count` oraz `bytes.rfind`).
        """
        if byte_offset < 0:
            byte_offset = 0
        if byte_offset > self.size:
            byte_offset = self.size
        if self.line_count == 0:
            return 0, 0
        if byte_offset >= self.size:
            return (self.line_count - 1, self.size) if self.line_count > 0 else (0, 0)

        idx = bisect.bisect_right(self.index, byte_offset, key=_ENTRY_OFFSET) - 1
        start = self.index[max(0, idx)]

        to_read = byte_offset - start.offset
        nl_cnt = 0
        last_nl_global = -1
        bytes_left = to_read
        chunk_size = 512 * 1024

        with self._file_lock:
            f = self._get_file()
            f.seek(start.offset)
            read_pos = start.offset
            while bytes_left > 0:
                chunk = f.read(min(chunk_size, bytes_left))
                if not chunk:
                    break
                c = chunk.count(b"\n")
                if c > 0:
                    nl_cnt += c
                    last_nl_global = read_pos + chunk.rfind(b"\n")
                read_pos += len(chunk)
                bytes_left -= len(chunk)

        curr_line = start.line + nl_cnt
        curr_offset = last_nl_global + 1 if last_nl_global != -1 else start.offset
        if 0 < self.line_count <= curr_line:
            curr_line = self.line_count - 1
        return curr_line, curr_offset

    def read_tail(self, max_lines: int) -> list[tuple[int, str]]:
        if self.line_count == 0:
            return []
        start_line = max(0, self.line_count - max_lines)
        return self.read_lines(start_line, max_lines)
