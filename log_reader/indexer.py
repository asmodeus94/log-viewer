"""LineIndexer — rzadki indeks (byte_offset, line_number) co ~1 MB."""

from __future__ import annotations

import os
import sys
import threading
import multiprocessing
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .helpers import (
    INDEX_CHUNK_BYTES, INDEX_INTERVAL_BYTES, DEFAULT_ENCODING,
    is_compressed, open_maybe_compressed,
)


@dataclass
class IndexEntry:
    __slots__ = ("offset", "line")
    offset: int   # byte offset początku tej linii (0-indexed)
    line: int     # numer linii (0-indexed)


def _indexer_worker_chunk(args):
    """
    Worker function dla multiprocessing — indeksuje fragment pliku.

    Optymalizacje wydajności (vs stara wersja z pętlą find()):
      1. **bytes.count(b"\\n")** do liczenia newline'ów — C-level, ~10-50x
         szybsze niż pętla find() w Pythonie. Dla chunku 16 MB z liniami ~80B
         = 200 000 newline'ów policzonych jednym wywołaniem C.
      2. **READ_CHUNK = 16 MB** (było 4 MB) — lepsza lokalność cache'u CPU,
         mniej overheadu na wywołania read(). Dla 25 GB = ~1600 chunków
         zamiast 6250.
      3. **find() tylko gdy minął interval** (1 MB) od ostatniego index entry.
         Dla 16 MB chunku = ~16 wywołań find() zamiast ~200 000.
      4. **buffering=1 MB** w open() — zmniejsza overhead syscalli read().

    Liczy newline'e w swoim zakresie [start, end) — każdy worker liczy
    newline'e które wpadają do jego bajtów. Łączna suma = wszystkie newline'e
    w pliku (bez podwójnego liczenia, bo zakresy się nie nakładają).
    """
    start, end, path, interval, chunk_id = args
    try:
        line_count = 0
        index_entries: List[Tuple[int, int]] = []
        last_idx = start  # ostatni offset gdzie zapisaliśmy index entry
        local_line = 0
        READ_CHUNK = 16 * 1024 * 1024  # 16 MB — większy = lepsza lokalność
        carry = b""
        bytes_processed = 0  # ile bajtów z [start, end) przetworzono

        with open(path, "rb", buffering=1024 * 1024) as f:
            f.seek(start)
            while bytes_processed < (end - start):
                to_read = min(READ_CHUNK, (end - start) - bytes_processed)
                chunk = f.read(to_read)
                if not chunk:
                    break
                chunk_len = len(chunk)
                bytes_processed += chunk_len

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
        import sys
        print(f"Warning: indexer worker {chunk_id} failed: {e}", file=sys.stderr)
        return (0, [], chunk_id)


class LineIndexer:
    """
    Buduje rzadki indeks pliku: co ~1 MB zapisuje (byte_offset, line_number).
    Pozwala na O(log N) skok do dowolnej linii bez wczytywania całego pliku.

    Utrzymuje jeden otwarty deskryptor pliku (z blokadą).
    Wspiera pliki skompresowane (.gz/.bz2/.xz).
    Wspiera konfigurowalne kodowanie.
    """

    def __init__(self, path: str, progress_cb: Optional[Callable[[float], None]] = None,
                 encoding: str = DEFAULT_ENCODING,
                 index_interval_bytes: Optional[int] = None,
                 cancel_event: Optional["threading.Event"] = None):
        self.path = path
        self.encoding = encoding
        self.is_compressed = is_compressed(path)
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

    def _get_file(self):
        if self._file_cache is None:
            self._file_cache = open_maybe_compressed(self.path, "rb")
        return self._file_cache

    def _build(self) -> None:
        try:
            self.size = os.path.getsize(self.path)
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

        Emituje postęp w trakcie — dzieli plik na dużo mniejsze chunki (~256 MB)
        niż liczba workerów, dzięki czemu postęp aktualizuje się płynnie
        (co ~2-3 sekundy przy 100 MB/s dysku), a nie skokami co 12.5% na 8 CPU.
        Pool automatycznie kolejkuje chunki między workerami.
        """
        n_workers = max(2, multiprocessing.cpu_count())
        # Podziel na chunki ~256 MB — więcej chunków niż workerów = płynny postęp.
        # Dla 25 GB → ~100 chunków, postęp co ~2.5s. Dla 1 GB → 4 chunki.
        CHUNK_BYTES = 256 * 1024 * 1024
        n_chunks = max(n_workers, (self.size + CHUNK_BYTES - 1) // CHUNK_BYTES)
        chunk_size = self.size // n_chunks
        ranges = []
        for i in range(n_chunks):
            start = i * chunk_size
            end = (i + 1) * chunk_size if i < n_chunks - 1 else self.size
            ranges.append((start, end, self.path, self.index_interval_bytes, i))

        # imap_unordered — zwraca wyniki gdy tylko są gotowe (nie czeka na
        # wszystkie). Pozwala emitować postęp w trakcie.
        # chunksize=1 — każdy chunk jest duży (256 MB), więc nie opłaca się
        # grupować ich dalej. Lepsze dla płynnego postępu.
        results = []
        cancelled = False
        with multiprocessing.Pool(n_workers) as pool:
            for i, result in enumerate(pool.imap_unordered(_indexer_worker_chunk, ranges, chunksize=1)):
                # Sprawdź anulowanie — jeśli user wcisnął Anuluj, przerwij.
                if self._cancel_event is not None and self._cancel_event.is_set():
                    cancelled = True
                    break
                results.append(result)
                # Emituj postęp proporcjonalnie do ukończonych chunków.
                if self._progress_cb:
                    pct = (i + 1) / len(ranges) * 100.0
                    self._progress_cb(pct)
            # pool.terminate() automatycznie przy __exit__, ale jeśli
            # anulowano, wymuś natychmiastowe zamknięcie.
            if cancelled:
                pool.terminate()

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
        with open_maybe_compressed(self.path, "rb") as f:
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
        with open(self.path, "rb") as f:
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
        lo, hi = 0, len(self.index) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.index[mid].line <= target_line:
                lo = mid
            else:
                hi = mid - 1
        start = self.index[lo]
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
        lo, hi = 0, len(self.index) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.index[mid].offset <= byte_offset:
                lo = mid
            else:
                hi = mid - 1
        start = self.index[lo]
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
