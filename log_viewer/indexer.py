"""LineIndexer — rzadki indeks (byte_offset, line_number) co ~1 MB."""

from __future__ import annotations

import array
import bisect
import itertools
import multiprocessing
import multiprocessing.sharedctypes
import operator
import sys
import threading
import time
import types
import typing
from collections.abc import Callable, Iterator, Sequence
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


@dataclass(slots=True)
class IndexEntry:
    offset: int  # byte offset początku tej linii (0-indexed)
    line: int  # numer linii (0-indexed)


class IndexView(Sequence[IndexEntry]):
    """Wstecznie kompatybilny widok sekwencji dla indeksu bazującego na array.array."""

    __slots__ = ("_offsets", "_lines")
    __hash__ = None  # type: ignore[assignment]

    def __init__(self, offsets: array.array[int], lines: array.array[int]) -> None:
        self._offsets = offsets
        self._lines = lines

    def __len__(self) -> int:
        return len(self._offsets)

    @typing.overload
    def __getitem__(self, idx: int) -> IndexEntry: ...

    @typing.overload
    def __getitem__(self, idx: slice) -> list[IndexEntry]: ...

    def __getitem__(self, idx: int | slice) -> IndexEntry | list[IndexEntry]:
        if isinstance(idx, slice):
            return [IndexEntry(self._offsets[i], self._lines[i]) for i in range(*idx.indices(len(self._offsets)))]
        return IndexEntry(self._offsets[idx], self._lines[idx])

    def __iter__(self) -> Iterator[IndexEntry]:
        for i in range(len(self._offsets)):
            yield IndexEntry(self._offsets[i], self._lines[i])

    def __repr__(self) -> str:
        return f"IndexView(len={len(self._offsets)})"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, IndexView):
            return self._offsets == other._offsets and self._lines == other._lines
        if isinstance(other, (list, tuple)):
            if len(self) != len(other):
                return False
            return all(a == b for a, b in zip(self, other, strict=True))
        return False


_ENTRY_LINE = operator.attrgetter("line")
_ENTRY_OFFSET = operator.attrgetter("offset")

_shared_progress_bytes: multiprocessing.sharedctypes.Synchronized[int] | Any = None


def _init_worker(progress_val: multiprocessing.sharedctypes.Synchronized[int] | Any) -> None:
    global _shared_progress_bytes
    _shared_progress_bytes = progress_val


_INDEXER_READ_CHUNK_SIZE = 32 * 1024 * 1024  # 32 MB — większy = lepsza lokalność, wykorzystuje cache OS
_SINGLE_THREAD_READ_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB — optymalny dla single-thread I/O


def _collect_chunk_index_entries(
    chunk: bytes | bytearray,
    base_offset: int,
    base_line: int,
    last_indexed_offset: int,
    interval: int,
    entries: list[Any] | None = None,
    create_entry: Callable[[int, int], Any] | type[IndexEntry] | None = None,
    offsets: array.array[int] | None = None,
    lines: array.array[int] | None = None,
    chunk_len: int | None = None,
) -> tuple[int, int]:
    """Wyszukuje granice indeksowania w chunku i dodaje nowe wpisy do entries lub tablic offsets/lines.

    Zwraca krotkę (nowy_last_indexed_offset, total_chunk_newlines).
    Działa całkowicie bez alokacji pamięci pośredniej w pętli (zero-copy w C),
    zliczając znaki nowej linii w jednym przebiegu bez redundancji.
    Progi indeksowania są wyznaczane jako bezwzględne wielokrotności `interval`.
    Obsługuje prealokowane bufory bytearray bez konieczności ich wycinania (chunk_len).
    """
    valid_len = len(chunk) if chunk_len is None else chunk_len
    current_end_offset = base_offset + valid_len
    last_counted_pos = 0
    running_line = base_line

    if last_indexed_offset == 0:
        target_offset = interval
    else:
        target_offset = ((last_indexed_offset // interval) + 1) * interval

    while current_end_offset >= target_offset:
        target_in_chunk = max(0, target_offset - base_offset)

        nl = chunk.find(b"\n", target_in_chunk, valid_len)
        if nl == -1:
            break

        offset = base_offset + nl + 1
        running_line += chunk.count(b"\n", last_counted_pos, nl) + 1
        last_counted_pos = nl + 1

        if offsets is not None and lines is not None:
            offsets.append(offset)
            lines.append(running_line)

        if entries is not None:
            entry = (offset, running_line) if create_entry is None else create_entry(offset, running_line)
            entries.append(entry)

        last_indexed_offset = offset
        target_offset = ((last_indexed_offset // interval) + 1) * interval

    total_chunk_nls = (running_line - base_line) + chunk.count(b"\n", last_counted_pos, valid_len)
    return last_indexed_offset, total_chunk_nls


def _indexer_worker_chunk(args: tuple[int, int, str, int, int]) -> tuple[int, list[tuple[int, int]], int]:
    """
    Funkcja robocza (worker) dla modułu multiprocessing — indeksuje fragment pliku.

    Optymalizacje wydajności:
      1. Użycie `bytes.count(b"\\n")` do szybkiego liczenia znaków nowej linii w kodzie maszynowym (C).
      2. Wykorzystanie większych fragmentów odczytu (`_INDEXER_READ_CHUNK_SIZE` = 32 MB) dla lepszej lokalności pamięci podręcznej.
      3. Liniowe, bezalokacyjne zliczanie nowych linii w buforze (zero-copy) bez łączenia buforów i bez kopiowania carry.
      4. Deterministyczne wyznaczanie progów jako bezwzględnych wielokrotności `interval` (brak luk na granicach).
      5. Prealokacja bufora `bytearray` i odczyt przez `f.readinto()` eliminujący cykliczne alokacje 32 MB w pętli.
      6. Zastosowanie dużego bufora wejściowego przy wywoływaniu `open()`.

    Liczy znaki nowej linii w przydzielonym zakresie `[start, end)`. Każdy proces
    przetwarza wyłącznie bajty zadeklarowane w swoim wycinku.
    """
    start, end, path_str, interval, chunk_id = args
    try:
        line_count = 0
        index_entries: list[tuple[int, int]] = []
        if start == 0:
            last_idx = 0
        else:
            first_target = ((start + interval - 1) // interval) * interval
            last_idx = first_target - 1
        local_line = 0
        bytes_processed = 0  # ile bajtów z [start, end) przetworzono

        with open(path_str, "rb", buffering=1024 * 1024) as f:
            f.seek(start)
            buf = bytearray(_INDEXER_READ_CHUNK_SIZE)
            mv = memoryview(buf)
            readinto_fn: Callable[[Any], int] | None = getattr(f, "readinto", None)
            while bytes_processed < (end - start):
                to_read = min(_INDEXER_READ_CHUNK_SIZE, (end - start) - bytes_processed)
                if readinto_fn is not None:
                    target_buf = mv[:to_read] if to_read < _INDEXER_READ_CHUNK_SIZE else buf
                    n = readinto_fn(target_buf)
                else:
                    chunk = f.read(to_read)
                    n = len(chunk)
                    buf[:n] = chunk
                if not n:
                    break

                # Aktualizuj postęp płynnie
                if _shared_progress_bytes is not None:
                    with _shared_progress_bytes.get_lock():
                        _shared_progress_bytes.value += n

                current_base_offset = start + bytes_processed

                last_idx, nl_count = _collect_chunk_index_entries(
                    buf,
                    current_base_offset,
                    local_line,
                    last_idx,
                    interval,
                    entries=index_entries,
                    chunk_len=n,
                )

                line_count += nl_count
                local_line += nl_count
                bytes_processed += n

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
        self._offsets: array.array[int] = array.array("Q", [0])
        self._lines: array.array[int] = array.array("Q", [0])
        self._progress_cb = progress_cb
        self._cancel_event = cancel_event  # None = nie można anulować
        self._file_cache: typing.IO[bytes] | None = None
        self._file_lock = threading.RLock()
        self._last_indexed_offset = 0
        self._cursor_pos: tuple[int, int] | None = None  # (line_number, byte_offset)
        self._build()

    @property
    def offsets(self) -> array.array[int]:
        return self._offsets

    @property
    def lines(self) -> array.array[int]:
        return self._lines

    @property
    def index(self) -> IndexView:
        return IndexView(self._offsets, self._lines)

    @index.setter
    def index(self, entries: Sequence[Any]) -> None:
        self._offsets = array.array("Q")
        self._lines = array.array("Q")
        for e in entries:
            if isinstance(e, IndexEntry):
                self._offsets.append(e.offset)
                self._lines.append(e.line)
            elif isinstance(e, (tuple, list)):
                self._offsets.append(e[0])
                self._lines.append(e[1])
            else:
                self._offsets.append(getattr(e, "offset", 0))
                self._lines.append(getattr(e, "line", 0))
        if not self._offsets:
            self._offsets.append(0)
            self._lines.append(0)

    def __enter__(self) -> LineIndexer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self.close()

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
                self._offsets = array.array("Q", [0])
                self._lines = array.array("Q", [0])
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
            self._offsets = array.array("Q", [0])
            self._lines = array.array("Q", [0])
            self.line_count = 0
            self._last_indexed_offset = 0
            return

        results.sort(key=lambda x: x[2])
        total_lines = 0
        full_offsets = array.array("Q", [0])
        full_lines = array.array("Q", [0])
        for line_count, index_entries, _chunk_id in results:
            line_offset = total_lines
            for offset, local_line in index_entries:
                if offset > full_offsets[-1]:
                    full_offsets.append(offset)
                    full_lines.append(line_offset + local_line)
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

        self._offsets = full_offsets
        self._lines = full_lines
        self.line_count = total_lines
        self._last_indexed_offset = full_offsets[-1] if full_offsets else 0

    def _decode_raw_line(self, raw: bytes) -> str:
        """Dekoduje surowe bajty linii tekstu i usuwa znaki nowej linii."""
        raw = raw.rstrip(b"\r\n")
        try:
            return raw.decode(self.encoding, errors="replace")
        except (UnicodeError, LookupError, ValueError, TypeError):
            return repr(raw)

    def _consume_chunk_index_entries(
        self,
        chunk: bytes | bytearray,
        base_offset: int,
        base_line: int,
        last_indexed_offset: int,
        chunk_len: int | None = None,
    ) -> tuple[int, int]:
        """Wyszukuje granice indeksowania w chunku i dodaje nowe pozycje do tablic indeksu.

        Zwraca krotkę (nowy_last_indexed_offset, total_chunk_newlines).
        Zoptymalizowane pod kątem alokacji pamięci: bezpośrednie dodawanie do
        array.array("Q") bez tworzenia obiektów pośrednich.
        """
        return _collect_chunk_index_entries(
            chunk,
            base_offset,
            base_line,
            last_indexed_offset,
            self.index_interval_bytes,
            offsets=self._offsets,
            lines=self._lines,
            chunk_len=chunk_len,
        )

    def _build_single(self) -> None:
        """Implementacja single-thread — fallback i dla małych plików."""
        line_num = 0
        last_indexed_offset = 0
        bytes_read = 0
        last_byte = b""
        chunk_size = INDEX_CHUNK_BYTES if self.is_compressed else _SINGLE_THREAD_READ_CHUNK_SIZE
        buf = bytearray(chunk_size)
        with open_maybe_compressed(str(self.path), "rb") as f:
            readinto_fn: Callable[[Any], int] | None = getattr(f, "readinto", None)
            while True:
                if self._cancel_event is not None and self._cancel_event.is_set():
                    self._offsets = array.array("Q", [0])
                    self._lines = array.array("Q", [0])
                    self.line_count = 0
                    self._last_indexed_offset = 0
                    return
                if readinto_fn is not None:
                    n = readinto_fn(buf)
                    if not n:
                        break
                    chunk_len = n
                    chunk_to_use: bytes | bytearray = buf
                    last_byte = bytes(buf[n - 1 : n])
                else:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    chunk_len = len(chunk)
                    chunk_to_use = chunk
                    last_byte = chunk[-1:]

                last_indexed_offset, nl_count = self._consume_chunk_index_entries(
                    chunk_to_use, bytes_read, line_num, last_indexed_offset, chunk_len=chunk_len
                )

                line_num += nl_count
                bytes_read += chunk_len
                if self._progress_cb and self.size > 0:
                    raw_pct = bytes_read / self.size * 100.0
                    self._progress_cb(min(99.9, raw_pct) if self.is_compressed else min(100.0, raw_pct))

        if bytes_read > 0 and last_byte != b"\n":
            line_num += 1
            self.has_trailing_newline = False
        else:
            self.has_trailing_newline = True

        if self.is_compressed:
            self.size = bytes_read

        if self._progress_cb and self.size > 0:
            self._progress_cb(100.0)

        self.line_count = line_num
        self._last_indexed_offset = last_indexed_offset

    def update_from(self, new_size: int, progress_cb: Callable[[float], None] | None = None) -> int:
        """Inkrementalna aktualizacja indeksu. Dla skompresowanych zwraca 0."""
        if new_size <= self.size:
            return 0
        if self.is_compressed:
            return 0

        with self._file_lock:
            old_size = self.size
            if new_size <= old_size:
                return 0
            bytes_to_read = new_size - old_size
            line_num = self.line_count
            last_indexed_offset = self._last_indexed_offset
            bytes_read = 0
            total_new_nls = 0
            last_byte = b""
            had_trailing_nl = self.has_trailing_newline
            base_line = line_num if had_trailing_nl else max(0, line_num - 1)

            f = self._get_file()
            f.seek(old_size)
            buf = bytearray(INDEX_CHUNK_BYTES)
            readinto_fn: Callable[[Any], int] | None = getattr(f, "readinto", None)
            while True:
                if readinto_fn is not None:
                    n = readinto_fn(buf)
                else:
                    chunk = f.read(INDEX_CHUNK_BYTES)
                    n = len(chunk)
                    buf[:n] = chunk
                if not n:
                    break
                chunk_len = n
                last_byte = bytes(buf[n - 1 : n])

                base = old_size + bytes_read
                last_indexed_offset, nl_count = self._consume_chunk_index_entries(
                    buf, base, base_line, last_indexed_offset, chunk_len=chunk_len
                )
                total_new_nls += nl_count

                base_line += nl_count
                bytes_read += chunk_len
                if progress_cb and bytes_to_read > 0:
                    progress_cb(min(100.0, bytes_read / bytes_to_read * 100.0))

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

    advance_lines = _advance_lines

    def _seek_to_line(self, f: typing.IO[bytes], target_line: int) -> bool:
        """Ustawia wskaźnik otwartego pliku na początek `target_line`.

        Fast-Path optymalizacja:
          1. Jeśli kursor znajduje się już dokładnie na target_line, natychmiast kończy sukcesem.
          2. Jeśli kursor znajduje się tuż przed target_line (do 128 linii w przód), wznawia odczyt
             bez bisekcji w indeksie i bez cofania się do punktu bazowego.
          3. W pozostałych przypadkach (skok wstecz lub daleki skok) wykonuje binarne wyszukiwanie
             w indeksie i skacze do najbliższego punktu indeksu.
        """
        if self._cursor_pos is not None:
            cur_line, cur_off = self._cursor_pos
            if cur_line == target_line:
                if f.tell() == cur_off:
                    return True
                f.seek(cur_off)
                return True
            if 0 < target_line - cur_line <= 128:
                if f.tell() != cur_off:
                    f.seek(cur_off)
                needed = target_line - cur_line
                if not self._advance_lines(f, needed):
                    self._cursor_pos = None
                    return False
                self._cursor_pos = (target_line, f.tell())
                return True

        idx = max(0, bisect.bisect_right(self._lines, target_line) - 1)
        start_offset = self._offsets[idx]
        start_line = self._lines[idx]

        if self._cursor_pos is not None and start_line <= self._cursor_pos[0] <= target_line:
            cur_line, cur_off = self._cursor_pos
            if f.tell() != cur_off:
                f.seek(cur_off)
            needed = target_line - cur_line
        else:
            f.seek(start_offset)
            needed = target_line - start_line

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

    def read_raw_lines(self, start_line: int, count: int) -> list[tuple[int, bytes]]:
        """Zwraca liste krotek (numer_linii, surowe_bajty) bez dekodowania do str."""
        if start_line < 0:
            start_line = 0
        if start_line >= self.line_count or count <= 0:
            return []

        out: list[tuple[int, bytes]] = []
        with self._file_lock:
            f = self._get_file()
            if not self._seek_to_line(f, start_line):
                return []
            for i, raw in enumerate(itertools.islice(f, count)):
                out.append((start_line + i, raw.rstrip(b"\r\n")))
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

        idx = max(0, bisect.bisect_right(self._offsets, byte_offset) - 1)
        start_offset = self._offsets[idx]
        start_line = self._lines[idx]

        to_read = byte_offset - start_offset
        nl_cnt = 0
        last_nl_global = -1
        bytes_left = to_read
        chunk_size = 512 * 1024

        with self._file_lock:
            f = self._get_file()
            f.seek(start_offset)
            read_pos = start_offset
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
            self._cursor_pos = None

        curr_line = start_line + nl_cnt
        curr_offset = last_nl_global + 1 if last_nl_global != -1 else start_offset
        if 0 < self.line_count <= curr_line:
            curr_line = self.line_count - 1
        return curr_line, curr_offset

    def read_tail(self, max_lines: int) -> list[tuple[int, str]]:
        if self.line_count == 0:
            return []
        start_line = max(0, self.line_count - max_lines)
        return self.read_lines(start_line, max_lines)
