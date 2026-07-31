"""LineIndexer — rzadki indeks (byte_offset, line_number) co ~1 MB."""

from __future__ import annotations

import sys
import bisect
import threading
import time
import multiprocessing
import typing
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple, Union

from .helpers import (
    INDEX_CHUNK_BYTES, INDEX_INTERVAL_BYTES, DEFAULT_ENCODING,
    is_compressed, open_maybe_compressed,
)


@dataclass
class IndexEntry:
    __slots__ = ("offset", "line")
    offset: int   # byte offset początku tej linii (0-indexed)
    line: int     # numer linii (0-indexed)


_shared_progress_bytes = None


def _init_worker(progress_val):
    global _shared_progress_bytes
    _shared_progress_bytes = progress_val


def _indexer_worker_chunk(args: Tuple[int, int, str, int, int]) -> Tuple[int, List[Tuple[int, int]], int]:
    """
    Funkcja robocza (worker) dla modułu multiprocessing — indeksuje fragment pliku.

    Optymalizacje wydajności:
      1. Użycie `bytes.count(b"\\n")` do szybkiego liczenia znaków nowej linii w kodzie maszynowym (C).
      2. Wykorzystanie większych fragmentów odczytu (`READ_CHUNK` = 32 MB) dla lepszej lokalności pamięci podręcznej.
      3. Wywoływanie operacji `find()` jedynie w momencie, gdy minął wymagany `interval` bajtów.
      4. Zastosowanie dużego bufora wejściowego przy wywoływaniu `open()`.

    Liczy znaki nowej linii w przydzielonym zakresie `[start, end)`. Każdy proces
    przetwarza wyłącznie bajty zadeklarowane w swoim wycinku.
    """
    start, end, path_str, interval, chunk_id = args
    try:
        global _shared_progress_bytes
        line_count = 0
        index_entries: List[Tuple[int, int]] = []
        last_idx = start  # ostatni offset gdzie zapisaliśmy index entry
        local_line = 0
        READ_CHUNK = 32 * 1024 * 1024  # 32 MB — większy = lepsza lokalność, wykorzystuje cache OS
        carry = b""
        bytes_processed = 0  # ile bajtów z [start, end) przetworzono

        with open(path_str, "rb", buffering=1024 * 1024) as f:
            f.seek(start)
            while bytes_processed < (end - start):
                to_read = min(READ_CHUNK, (end - start) - bytes_processed)
                chunk = f.read(to_read)
                if not chunk:
                    break
                chunk_len = len(chunk)
                bytes_processed += chunk_len

                # Aktualizuj postęp płynnie
                if _shared_progress_bytes is not None:
                    with _shared_progress_bytes.get_lock():
                        _shared_progress_bytes.value += chunk_len

                # Połącz carry z poprzedniego chunka z nowym chunkiem.
                # carry to niepełna ostatnia linia z poprzedniego chunku TEGO
                # workera. Operujemy na `data` = carry + chunk, ale pamiętaj
                # że bajty z carry były już policzone w poprzednim chunku —
                # więc liczymy newline'e TYLKO w nowych bajtach (chunk).
                if carry:
                    data = carry + chunk
                else:
                    data = chunk
                data_len = len(data)
                carry_len = data_len - chunk_len  # ile bajtów to carry

                # === OPTYMALIZACJA 1: count() zamiast pętli find() ===
                # Policz newline'e TYLKO w nowych bajtach (chunk, nie carry).
                # carry był już policzony w poprzednim chunku.
                # Ale uwaga: newline na granicy carry|chunk należy do chunk
                # (bo liczymy go gdy wpada do nowego zakresu).
                nl_count = chunk.count(b"\n")

                line_count += nl_count
                local_line += nl_count

                # === OPTYMALIZACJA 2: find() tylko dla index_entries ===
                current_end_offset = start + bytes_processed
                while current_end_offset - last_idx >= interval:
                    target_offset = last_idx + interval
                    target_in_chunk = target_offset - (start + bytes_processed - data_len)

                    if target_in_chunk < carry_len:
                        target_in_chunk = carry_len

                    nl = data.find(b"\n", target_in_chunk)
                    if nl == -1:
                        break

                    offset = start + bytes_processed - data_len + nl + 1
                    nls_before = data[carry_len:nl].count(b"\n")
                    entry_local_line = (local_line - nl_count) + nls_before + 1

                    index_entries.append((offset, entry_local_line))
                    last_idx = offset

                # Zachowaj niepełną ostatnią linię jako carry dla następnego chunku.
                last_nl = data.rfind(b"\n")
                if last_nl != -1:
                    carry = data[last_nl + 1:]
                else:
                    carry = data

        return (line_count, index_entries, chunk_id)
    except Exception as e:
        print(f"Warning: indexer worker {chunk_id} failed: {e}", file=sys.stderr)
        return (0, [], chunk_id)


class _IndexLineProxy:
    """Klasa pomocnicza dla modułu bisect symulująca listę samych linii (Python 3.8+ kompatybilność)."""
    __slots__ = ("_data",)

    def __init__(self, data: List[IndexEntry]):
        self._data = data

    def __getitem__(self, idx: int) -> int:
        return self._data[idx].line

    def __len__(self) -> int:
        return len(self._data)


class _IndexOffsetProxy:
    """Klasa pomocnicza dla modułu bisect symulująca listę samych przesunięć (Python 3.8+ kompatybilność)."""
    __slots__ = ("_data",)

    def __init__(self, data: List[IndexEntry]):
        self._data = data

    def __getitem__(self, idx: int) -> int:
        return self._data[idx].offset

    def __len__(self) -> int:
        return len(self._data)


class LineIndexer:
    """
    Buduje rzadki indeks pliku: co ~1 MB zapisuje (byte_offset, line_number).
    Pozwala na O(log N) skok do dowolnej linii bez wczytywania całego pliku.

    Utrzymuje jeden otwarty deskryptor pliku (z blokadą).
    Wspiera pliki skompresowane (.gz/.bz2/.xz).
    Wspiera konfigurowalne kodowanie.
    """

    def __init__(self, path: Union[str, Path], progress_cb: Optional[Callable[[float], None]] = None,
                 encoding: str = DEFAULT_ENCODING,
                 index_interval_bytes: Optional[int] = None,
                 cancel_event: Optional["threading.Event"] = None) -> None:
        self.path: Path = Path(path)
        self.encoding: str = encoding
        self.is_compressed: bool = is_compressed(str(self.path))
        self.index_interval_bytes = index_interval_bytes if index_interval_bytes is not None else INDEX_INTERVAL_BYTES
        self.size: int = 0
        self.line_count: int = 0
        self.index: List[IndexEntry] = [IndexEntry(0, 0)]
        self._progress_cb = progress_cb
        self._cancel_event = cancel_event  # None = nie można anulować
        self._file_cache: Optional[object] = None
        self._file_lock = threading.Lock()
        self._last_indexed_offset = 0
        self._build()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def close(self) -> None:
        with self._file_lock:
            if self._file_cache is not None:
                try:
                    self._file_cache.close()
                except Exception:
                    pass
                self._file_cache = None

    def _get_file(self) -> "typing.IO[bytes]":
        if self._file_cache is None:
            self._file_cache = open_maybe_compressed(str(self.path), "rb")
        return self._file_cache

    def _build(self) -> None:
        try:
            self.size = self.path.stat().st_size
        except OSError:
            self.size = 0
        # Dla dużych plików użyj multiprocessing — znacznie szybsze na multicore.
        if not self.is_compressed and self.size > 100 * 1024 * 1024:
            try:
                self._build_parallel()
                return
            except Exception as e:
                print(f"Warning: parallel indexing failed ({e}), falling back to single-thread", file=sys.stderr)
                self.index = [IndexEntry(0, 0)]
                self._last_indexed_offset = 0
        self._build_single()

    def _build_parallel(self) -> None:
        """Indeksowanie równoległe z multiprocessing.

        Emituje postęp niezwykle płynnie przy użyciu współdzielonego licznika.
        Dzieli plik na chunki dla workerów proporcjonalnie do ich ilości.
        """
        n_workers = max(2, multiprocessing.cpu_count())
        # Mniej chunków przyspiesza wykonanie bez narzutu na inicjalizację workerów,
        # bo płynny postęp jest teraz raportowany z wnętrza chunka na bieżąco.
        # Minimalna liczba chunków = n_workers * 2 aby równo rozłożyć pracę.
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

        shared_progress = multiprocessing.Value('Q', 0)
        results = []
        cancelled = False

        with multiprocessing.Pool(n_workers, initializer=_init_worker, initargs=(shared_progress,)) as pool:
            # Użyj map_async zamiast imap_unordered, by móc aktywnie monitorować postęp
            result_async = pool.map_async(_indexer_worker_chunk, ranges)

            # Pętla nasłuchująca w czasie rzeczywistym
            while not result_async.ready():
                if self._cancel_event is not None and self._cancel_event.is_set():
                    cancelled = True
                    pool.terminate()
                    break

                if self._progress_cb:
                    bytes_done = shared_progress.value
                    pct = (bytes_done / self.size) * 100.0
                    if pct > 99.9:
                        pct = 99.9
                    self._progress_cb(pct)

                time.sleep(0.05)  # Częstotliwość odświeżania paska: 20 FPS

            if not cancelled:
                results = result_async.get()

        if cancelled:
            # Nie buduj indeksu — wróć z pustym. Worker sprawdzi cancel_event
            # i wyemituje error("cancelled").
            self.index = [IndexEntry(0, 0)]
            self.line_count = 0
            self._last_indexed_offset = 0
            return

        results.sort(key=lambda x: x[2])
        total_lines = 0
        full_index = [IndexEntry(0, 0)]
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

        self.index = full_index
        self.line_count = total_lines
        self._last_indexed_offset = last_indexed_offset

    def _build_single(self) -> None:
        """Implementacja single-thread — fallback i dla małych plików."""
        line_num = 0
        last_indexed_offset = 0
        bytes_read = 0
        interval = self.index_interval_bytes
        with open_maybe_compressed(str(self.path), "rb") as f:
            while True:
                chunk = f.read(INDEX_CHUNK_BYTES)
                if not chunk:
                    break
                chunk_len = len(chunk)
                nl_count = chunk.count(b"\n")

                current_end_offset = bytes_read + chunk_len
                while current_end_offset - last_indexed_offset >= interval:
                    target_offset = last_indexed_offset + interval
                    target_in_chunk = target_offset - bytes_read
                    if target_in_chunk < 0:
                        target_in_chunk = 0

                    nl = chunk.find(b"\n", target_in_chunk)
                    if nl == -1:
                        break

                    offset = bytes_read + nl + 1
                    nls_before = chunk[:nl].count(b"\n")
                    entry_line = line_num + nls_before + 1
                    self.index.append(IndexEntry(offset, entry_line))
                    last_indexed_offset = offset

                line_num += nl_count
                bytes_read += chunk_len
                if self._progress_cb and self.size > 0:
                    self._progress_cb(bytes_read / self.size * 100.0)
        self.line_count = line_num
        self._last_indexed_offset = last_indexed_offset

    def update_from(self, new_size: int,
                    progress_cb: Optional[Callable[[float], None]] = None) -> int:
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
        interval = self.index_interval_bytes
        with open(str(self.path), "rb") as f:
            f.seek(old_size)
            while True:
                chunk = f.read(INDEX_CHUNK_BYTES)
                if not chunk:
                    break
                chunk_len = len(chunk)
                nl_count = chunk.count(b"\n")

                base = old_size + bytes_read
                current_end_offset = base + chunk_len
                while current_end_offset - last_indexed_offset >= interval:
                    target_offset = last_indexed_offset + interval
                    target_in_chunk = target_offset - base
                    if target_in_chunk < 0:
                        target_in_chunk = 0

                    nl = chunk.find(b"\n", target_in_chunk)
                    if nl == -1:
                        break

                    offset = base + nl + 1
                    nls_before = chunk[:nl].count(b"\n")
                    entry_line = line_num + nls_before + 1
                    self.index.append(IndexEntry(offset, entry_line))
                    last_indexed_offset = offset

                line_num += nl_count
                bytes_read += chunk_len
                if progress_cb and bytes_to_read > 0:
                    progress_cb(bytes_read / bytes_to_read * 100.0)
        with self._file_lock:
            if self._file_cache is not None:
                try:
                    self._file_cache.close()
                except Exception:
                    pass
                self._file_cache = None
        new_lines = line_num - self.line_count
        self.line_count = line_num
        self._last_indexed_offset = last_indexed_offset
        self.size = new_size
        return new_lines

    def offset_of_line(self, target_line: int) -> Optional[int]:
        if target_line < 0:
            target_line = 0
        if target_line >= self.line_count:
            return None
        proxy = _IndexLineProxy(self.index)
        idx = bisect.bisect_right(proxy, target_line) - 1
        start = self.index[max(0, idx)]

        with self._file_lock:
            f = self._get_file()
            f.seek(start.offset)
            current = start.line
            while current < target_line:
                line = f.readline()
                if not line:
                    return None
                current += 1
            return f.tell()

    def read_specific_lines(self, target_lines: List[int]) -> List[Tuple[int, str]]:
        """
        Zoptymalizowana metoda do wczytywania wielu konkretnych (potencjalnie rzadkich) linii naraz.
        Zamiast szukać offsetu i przewijać plik dla każdej małej grupy linii z osobna (co psuje wydajność),
        utrzymuje pozycję odczytu i przechodzi sekwencyjnie między liniami, lub skacze tylko gdy to opłacalne.
        """
        if not target_lines:
            return []

        # Usunięcie duplikatów i posortowanie ułatwia sekwencyjny odczyt
        targets = sorted(list(set(target_lines)))
        out: List[Tuple[int, str]] = []
        proxy = _IndexLineProxy(self.index)

        with self._file_lock:
            f = self._get_file()
            current_line = -1

            for target_line in targets:
                if target_line < 0 or target_line >= self.line_count:
                    continue

                # Znajdź najbliższy wpis w indeksie przed pożądaną linią
                idx = bisect.bisect_right(proxy, target_line) - 1
                start = self.index[max(0, idx)]

                # Jeśli nasza aktualna pozycja (current_line) jest już bliżej celu niż znacznik z indeksu,
                # to nie cofamy się (nie robimy f.seek), tylko kontynuujemy czytanie do przodu.
                if current_line != -1 and start.line <= current_line <= target_line:
                    pass
                else:
                    # Skaczemy, bo znacznik z indeksu jest bliżej (albo byliśmy za daleko / jeszcze nie zaczęliśmy)
                    f.seek(start.offset)
                    current_line = start.line

                # Przewijaj linie do przodu dopóki nie trafisz w target_line
                while current_line < target_line:
                    line = f.readline()
                    if not line:
                        break
                    current_line += 1

                if current_line == target_line:
                    raw = f.readline()
                    if not raw:
                        break
                    try:
                        text = raw.decode(self.encoding, errors="replace")
                    except Exception:
                        text = repr(raw)
                    if text.endswith("\r\n"):
                        text = text[:-2]
                    elif text.endswith("\n") or text.endswith("\r"):
                        text = text[:-1]
                    out.append((target_line, text))
                    current_line += 1

        return out

    def read_lines(self, start_line: int, count: int) -> List[Tuple[int, str]]:
        if start_line < 0:
            start_line = 0
        if start_line >= self.line_count:
            return []
        offset = self.offset_of_line(start_line)
        if offset is None:
            return []
        out: List[Tuple[int, str]] = []
        with self._file_lock:
            f = self._get_file()
            f.seek(offset)
            for i in range(count):
                raw = f.readline()
                if not raw:
                    break
                try:
                    text = raw.decode(self.encoding, errors="replace")
                except Exception:
                    text = repr(raw)
                if text.endswith("\r\n"):
                    text = text[:-2]
                elif text.endswith("\n") or text.endswith("\r"):
                    text = text[:-1]
                out.append((start_line + i, text))
        return out

    def line_at_byte_offset(self, byte_offset: int) -> Tuple[int, int]:
        if byte_offset < 0:
            byte_offset = 0
        if byte_offset > self.size:
            byte_offset = self.size
        proxy = _IndexOffsetProxy(self.index)
        idx = bisect.bisect_right(proxy, byte_offset) - 1
        start = self.index[max(0, idx)]

        with self._file_lock:
            f = self._get_file()
            f.seek(start.offset)
            current_offset = start.offset
            current_line = start.line
            while current_offset <= byte_offset:
                line = f.readline()
                if not line:
                    break
                if current_offset <= byte_offset < current_offset + len(line):
                    return (current_line, current_offset)
                current_offset += len(line)
                current_line += 1
            return (current_line, current_offset)

    def read_tail(self, max_lines: int) -> List[Tuple[int, str]]:
        if self.line_count == 0:
            return []
        start_line = max(0, self.line_count - max_lines)
        return self.read_lines(start_line, max_lines)
