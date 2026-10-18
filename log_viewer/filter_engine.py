from __future__ import annotations

import array
import bisect
import itertools
import multiprocessing
import multiprocessing.sharedctypes
import re
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from log_viewer.bitset import Bitset
from log_viewer.helpers import open_maybe_compressed
from log_viewer.indexer import LineIndexer

# Próg rozmiaru pliku (w bajtach), od którego aktywowany jest tryb równoległy.
# Dla plików poniżej tego progu single-thread jest szybszy (brak narzutu na Pool).
_PARALLEL_SEARCH_THRESHOLD = 50 * 1024 * 1024  # 50 MB
_WORKER_READ_CHUNK_SIZE = 32 * 1024 * 1024  # 32 MB — większy chunk = lepsza lokalność cache OS
_FILE_READ_CHUNK_SIZE = 32 * 1024 * 1024  # 32 MB

# Maksymalna liczba linii z filtra, które bezwarunkowo opłaca się przeszukiwać pojedynczo (Fast-Path).
# Powyżej tego progu decyzja podejmowana jest adaptacyjnie na podstawie gęstości i rozpiętości filtra.
_FAST_FILTERED_LINE_THRESHOLD = 2000

# Współdzielone liczniki postępu — globalne w kontekście procesu roboczego.
_shared_filter_progress_bytes: multiprocessing.sharedctypes.Synchronized[int] | Any = None
_shared_filter_hits: multiprocessing.sharedctypes.Synchronized[int] | Any = None


def _filter_init_worker(
    progress_val: multiprocessing.sharedctypes.Synchronized[int] | Any,
    hits_val: multiprocessing.sharedctypes.Synchronized[int] | Any,
) -> None:
    """Inicjalizator procesu roboczego — ustawia globalne liczniki postępu."""
    global _shared_filter_progress_bytes, _shared_filter_hits
    _shared_filter_progress_bytes = progress_val
    _shared_filter_hits = hits_val


def _filter_worker_chunk(
    args: tuple[int, int, int, int, str, str, bool, bool, bool, str],
) -> tuple[int, int, array.array[int], int]:
    """Worker dla multiprocessing — przeszukuje przydzielony zakres bajtów pliku.

    Parametry (args):
        chunk_id      – identyfikator kawałka (do scalenia w kolejności)
        start_offset  – byte offset początku zakresu (zawsze na granicy linii)
        end_offset    – byte offset końca zakresu (wyłączny, na granicy linii)
        start_line    – numer pierwszej linii w tym zakresie (0-indeksowany)
        path          – ścieżka do pliku
        pattern       – wzorzec wyszukiwania
        use_regex     – czy wzorzec jest wyrażeniem regularnym
        case_sensitive– czy rozróżniać wielkość liter
        negate        – czy negować wynik dopasowania
        encoding      – kodowanie pliku

    Zwraca krotkę (chunk_id, base_word, words, hit_count).
    """
    (chunk_id, start_offset, end_offset, start_line, path, pattern, use_regex, case_sensitive, negate, encoding) = args

    results: array.array[int] = array.array("Q")

    # Utwórz strategię wewnątrz workera — obiekty re nie są picklowalne
    try:
        if use_regex:
            strategy: FilterStrategy = RegexStrategy(pattern, case_sensitive, negate, encoding)
        else:
            strategy = PlainTextStrategy(pattern, case_sensitive, negate, encoding)
    except (re.error, UnicodeError, ValueError, OSError):
        return chunk_id, 0, array.array("Q"), 0

    line_no = start_line
    carry: bytes = b""
    total_range = end_offset - start_offset
    bytes_processed = 0

    try:
        with open(path, "rb", buffering=4 * 1024 * 1024) as f:
            f.seek(start_offset)

            while bytes_processed < total_range:
                to_read = min(_WORKER_READ_CHUNK_SIZE, total_range - bytes_processed)
                chunk: bytes = f.read(to_read)
                if not chunk:
                    break

                chunk_len = len(chunk)
                bytes_processed += chunk_len

                # Aktualizuj współdzielony licznik postępu
                if _shared_filter_progress_bytes is not None:
                    with _shared_filter_progress_bytes.get_lock():
                        _shared_filter_progress_bytes.value += chunk_len

                is_last_read = bytes_processed >= total_range
                data: bytes = carry + chunk if carry else chunk

                if is_last_read:
                    # Ostatni odczyt — przetwórz wszystko (nawet bez końcowego \n)
                    complete_data = data
                    carry = b""
                else:
                    # Zostaw niepełną ostatnią linię jako carry do następnego chunku
                    last_nl = data.rfind(b"\n")
                    if last_nl != -1:
                        complete_data = data[: last_nl + 1]
                        carry = data[last_nl + 1 :]
                    else:
                        # Cały blok to jedna długa linia — czekaj na więcej danych
                        carry = data
                        continue

                local_hits_list = strategy.match_chunk(complete_data, line_no)
                results.extend(local_hits_list)
                local_hits = len(local_hits_list)

                lines_count = complete_data.count(b"\n")
                if complete_data and not complete_data.endswith(b"\n"):
                    lines_count += 1
                line_no += lines_count

                if local_hits > 0 and _shared_filter_hits is not None:
                    with _shared_filter_hits.get_lock():
                        _shared_filter_hits.value += local_hits

        # Obsłuż ewentualny carry na samym końcu zakresu (linia bez \n)
        if carry:
            carry_hits = strategy.match_chunk(carry, line_no)
            results.extend(carry_hits)
            if _shared_filter_hits is not None and carry_hits:
                with _shared_filter_hits.get_lock():
                    _shared_filter_hits.value += len(carry_hits)

    except OSError:
        pass

    hit_count = len(results)
    if hit_count == 0:
        return chunk_id, 0, array.array("Q"), 0

    min_idx = results[0]
    max_idx = results[-1]

    base_word = min_idx // 64
    end_word = max_idx // 64

    words = array.array("Q", [0] * (end_word - base_word + 1))

    for idx in results:
        words[idx // 64 - base_word] |= 1 << (idx % 64)

    return chunk_id, base_word, words, hit_count


class FilterStrategy(ABC):
    """Abstrakcyjna klasa bazowa dla strategii filtrowania (Wzorzec Strategii).

    Definiuje wspólny interfejs sprawdzania czy dana linia pasuje do wzorca.
    """

    def __init__(self, negate: bool, encoding: str) -> None:
        self.negate = negate
        self.encoding = encoding

    @abstractmethod
    def match(self, line_bytes: bytes) -> bool:
        """Sprawdza, czy przekazane bajty linii pasują do wzorca strategii."""
        pass

    def match_chunk(self, chunk: bytes, start_line: int) -> list[int]:
        """Domyślna implementacja chunkowa – dzieli na linie i wywołuje match()."""
        lines = chunk.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()

        hits: list[int] = []
        for line_bytes in lines:
            if self.match(line_bytes):
                hits.append(start_line)
            start_line += 1
        return hits


class PlainTextStrategy(FilterStrategy):
    """Strategia dla zwykłego wyszukiwania tekstu (bez wyrażeń regularnych)."""

    def __init__(self, needle: str, case_sensitive: bool, negate: bool, encoding: str) -> None:
        super().__init__(negate, encoding)
        self.case_sensitive = case_sensitive
        self.needle_bytes: bytes = needle.encode(encoding, errors="replace")
        if not case_sensitive:
            self.needle_bytes = self.needle_bytes.lower()

    def match(self, line_bytes: bytes) -> bool:
        if not self.case_sensitive:
            matched = self.needle_bytes in line_bytes.lower()
        else:
            matched = self.needle_bytes in line_bytes

        return not matched if self.negate else matched

    def match_chunk(self, chunk: bytes, start_line: int) -> list[int]:
        if not self.needle_bytes:
            return (
                []
                if self.negate
                else list(
                    range(
                        start_line, start_line + chunk.count(b"\n") + (1 if chunk and not chunk.endswith(b"\n") else 0)
                    )
                )
            )

        haystack = chunk.lower() if not self.case_sensitive else chunk
        hits: list[int] = []

        if self.negate:
            lines = haystack.split(b"\n")
            if lines and lines[-1] == b"":
                lines.pop()

            for line_bytes in lines:
                if self.needle_bytes not in line_bytes:
                    hits.append(start_line)
                start_line += 1
            return hits

        pos = 0
        lines_counted = start_line
        last_nl_pos = -1

        while True:
            hit_pos = haystack.find(self.needle_bytes, pos)
            if hit_pos == -1:
                break

            nl_count = haystack.count(b"\n", last_nl_pos + 1, hit_pos)
            lines_counted += nl_count
            # Unikaj dodawania tej samej linii wielokrotnie, jeśli jest na niej wiele dopasowań
            if not hits or hits[-1] != lines_counted:
                hits.append(lines_counted)

            next_nl = haystack.find(b"\n", hit_pos)
            if next_nl == -1:
                break

            last_nl_pos = next_nl
            lines_counted += 1
            pos = next_nl + 1

        return hits


class RegexStrategy(FilterStrategy):
    """Strategia dla wyrażeń regularnych z optymalizacją pod surowe bajty."""

    def __init__(self, pattern: str, case_sensitive: bool, negate: bool, encoding: str) -> None:
        super().__init__(negate, encoding)
        self.pattern = pattern
        self.flags = re.MULTILINE if case_sensitive else (re.IGNORECASE | re.MULTILINE)
        self.matcher_str: re.Pattern[str] = re.compile(pattern, self.flags)

        self.matcher_bytes: re.Pattern[bytes] | None = None
        try:
            pattern_bytes = pattern.encode(encoding, errors="replace")
            self.matcher_bytes = re.compile(pattern_bytes, self.flags)
        except (re.error, UnicodeError, ValueError):
            pass  # Fallback na sprawdzanie łańcuchów znaków

    def match(self, line_bytes: bytes) -> bool:
        # Najpierw sprawdź na surowych bajtach (szybkie)
        if self.matcher_bytes is not None:
            matched = self.matcher_bytes.search(line_bytes) is not None
        else:
            # Fallback: dekoduj i sprawdź na str
            try:
                text = line_bytes.decode(self.encoding, errors="replace")
            except (UnicodeError, LookupError, ValueError, TypeError):
                text = repr(line_bytes)
            matched = self.matcher_str.search(text) is not None

        return not matched if self.negate else matched

    def match_chunk(self, chunk: bytes, start_line: int) -> list[int]:
        if self.negate:
            return super().match_chunk(chunk, start_line)
        if self.matcher_bytes is not None:
            return self._match_chunk_bytes(chunk, start_line)
        return self._match_chunk_str(chunk, start_line)

    def _match_chunk_bytes(self, chunk: bytes, start_line: int) -> list[int]:
        assert self.matcher_bytes is not None
        hits: list[int] = []
        last_nl_pos = -1
        lines_counted = start_line

        for match in self.matcher_bytes.finditer(chunk):
            hit_pos = match.start()
            if hit_pos > last_nl_pos:
                nl_count = chunk.count(b"\n", last_nl_pos + 1, hit_pos)
                lines_counted += nl_count

                if not hits or hits[-1] != lines_counted:
                    hits.append(lines_counted)
                last_nl_pos = hit_pos

        return hits

    def _match_chunk_str(self, chunk: bytes, start_line: int) -> list[int]:
        try:
            target = chunk.decode(self.encoding, errors="replace")
        except (UnicodeError, LookupError, ValueError, TypeError):
            target = repr(chunk)

        hits: list[int] = []
        last_nl_pos = -1
        lines_counted = start_line

        for match in self.matcher_str.finditer(target):
            hit_pos = match.start()
            if hit_pos > last_nl_pos:
                nl_count = target.count("\n", last_nl_pos + 1, hit_pos)
                lines_counted += nl_count

                if not hits or hits[-1] != lines_counted:
                    hits.append(lines_counted)
                last_nl_pos = hit_pos

        return hits


def read_file_chunks(path: str, chunk_size: int = _FILE_READ_CHUNK_SIZE) -> Iterator[bytes]:
    """Generator odczytujący plik partiami (chunking) po zadanym rozmiarze."""
    with open_maybe_compressed(path, "rb") as f:
        carry: bytes = b""
        eof: bool = False
        while not eof:
            chunk: bytes = f.read(chunk_size)
            if not chunk:
                eof = True

            data: bytes = carry + chunk
            if not data:
                break

            complete_data: bytes
            if eof:
                complete_data = data
                carry = b""
            else:
                last_nl = data.rfind(b"\n")
                if last_nl != -1:
                    complete_data = data[: last_nl + 1]
                    carry = data[last_nl + 1 :]
                else:
                    # Cały chunk to jedna długa linia bez \n
                    complete_data = b""
                    carry = data

            if complete_data:
                yield complete_data


def _notify_progress(
    callback: Callable[..., None] | None,
    pct: float,
    hits: int,
    state: str = "filtering",
    partial_results: tuple[int, array.array[int]] | None = None,
) -> None:
    """Bezpieczne wywołanie callbacka postępu obsługujące funkcje 2- lub 4-argumentowe."""
    if callback is None:
        return
    try:
        callback(pct, hits, state, partial_results)
    except TypeError:
        try:
            callback(pct, hits)
        except (ValueError, RuntimeError, AttributeError, TypeError):
            pass
    except (ValueError, RuntimeError, AttributeError):
        pass


class FilterEngine:
    """Przeszukuje plik w tle. Każde wywołanie start() dostaje nowy _session_id.

    Bezpieczeństwo wątkowe: start() czeka na zakończenie poprzedniego wątku.

    Dla plików >= 50 MB (nieskompresowanych) automatycznie używa multiprocessing
    (tyle workerów ile rdzeni CPU) dla drastycznie wyższej wydajności.
    Dla plików mniejszych i skompresowanych stosuje tryb single-thread.
    """

    def __init__(self, path: str, indexer: LineIndexer) -> None:
        self.path = path
        self.indexer = indexer
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._session_id = 0
        self._session_lock = threading.Lock()

    def cancel(self, timeout: float = 5.0) -> None:
        with self._session_lock:
            self._cancel.set()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        pattern: str,
        use_regex: bool,
        case_sensitive: bool,
        negate: bool,
        on_progress: Callable[..., None],
        on_done: Callable[[Bitset, str | None], None],
        search_in_filter: bool = False,
        filtered_lines: Bitset | None = None,
    ) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._cancel.set()
            self._thread.join(timeout=5.0)
        with self._session_lock:
            self._session_id += 1
            session = self._session_id
            self._cancel.clear()
            thread = threading.Thread(
                target=self._run,
                args=(
                    session,
                    pattern,
                    use_regex,
                    case_sensitive,
                    negate,
                    on_progress,
                    on_done,
                    search_in_filter,
                    filtered_lines,
                ),
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def _is_current_session(self, session: int) -> bool:
        with self._session_lock:
            return session == self._session_id

    def _create_strategy(self, pattern: str, use_regex: bool, case_sensitive: bool, negate: bool) -> FilterStrategy:
        """Fabryka do tworzenia odpowiedniej strategii wyszukiwania."""
        if use_regex:
            return RegexStrategy(pattern, case_sensitive, negate, self.indexer.encoding)
        return PlainTextStrategy(pattern, case_sensitive, negate, self.indexer.encoding)

    def _should_use_filtered_search(self, filtered_lines: Bitset) -> bool:
        """Adaptacyjna ocena opłacalności trybu _run_filtered vs skanowanie chunków (Optymalizacja 1.2).

        Zamiast sztywnego progu 2000 linii, silnik analizuje:
        1. Całkowitą liczbę linii w filtrze (flen).
        2. Rozpiętość (span) filtra w pliku (od pierwszej do ostatniej linii).
        3. Rozmiar pliku i gęstość w oknie filtra.
        """
        flen = len(filtered_lines)
        if flen == 0:
            return False
        # Do 2000 linii bezwzględnie fast-path: natychmiastowy odczyt (< 100 ms) bez narzutu na procesy
        if flen <= _FAST_FILTERED_LINE_THRESHOLD:
            return True

        # Powyżej 50 000 linii skan strumieniowy/równoległy w C jest zawsze szybszy
        if flen > 50_000:
            return False

        total_lines = self.indexer.line_count if self.indexer else 0
        if total_lines <= 0:
            return True

        first = filtered_lines.first_set_index()
        last = filtered_lines.last_set_index()
        if first is None or last is None:
            return False

        line_span = last - first + 1
        span_ratio = line_span / total_lines

        # Jeśli filtr jest skupiony (np. linie z danego przedziału czasowego obejmujące <= 30% pliku
        # lub do 100 000 kolejnych linii), bezpośredni odczyt klastra jest do 10-15x szybszy
        if span_ratio <= 0.30 or line_span <= 100_000:
            return True

        # Jeśli rozkład jest szeroki, ale linijki są stosunkowo rzadkie (do 3000)
        return flen <= 3000

    def _compute_search_ranges(self, n_workers: int) -> list[tuple[int, int, int]]:
        """Wyznacza zakresy (start_offset, end_offset, start_line) dla workerów.

        Korzysta z istniejącego rzadkiego indeksu LineIndexer — zero dodatkowego I/O.
        Każdy zakres zaczyna i kończy się na granicy linii (gwarantowane przez indeks).
        """
        offsets = self.indexer.offsets
        lines = self.indexer.lines
        n_entries = len(offsets)
        file_size = self.indexer.size

        if n_entries < 2:
            # Za mało wpisów w indeksie — cały plik jako jeden zakres
            return [(0, file_size, 0)]

        # Wybierz co K-ty wpis jako punkt podziału, maksymalnie n_workers zakresów
        step = max(1, (n_entries - 1) // n_workers)
        split_indices = list(range(0, n_entries, step))[:n_workers]

        ranges: list[tuple[int, int, int]] = []
        for i, idx in enumerate(split_indices):
            start_offset = offsets[idx]
            start_line = lines[idx]

            if i + 1 < len(split_indices):
                end_offset = offsets[split_indices[i + 1]]
            else:
                end_offset = file_size

            if end_offset > start_offset:
                ranges.append((start_offset, end_offset, start_line))

        return ranges if ranges else [(0, file_size, 0)]

    def _run(
        self,
        session: int,
        pattern: str,
        use_regex: bool,
        case_sensitive: bool,
        negate: bool,
        on_progress: Callable[..., None],
        on_done: Callable[[Bitset, str | None], None],
        search_in_filter: bool = False,
        filtered_lines: Bitset | None = None,
    ) -> None:
        """Dyspozytor — wybiera tryb równoległy lub single-thread i deleguje pracę.

        Tryb równoległy (multiprocessing) dla plików >= 50 MB nieskompresowanych.
        Tryb single-thread dla małych plików i plików skompresowanych.
        """
        # Wstępna walidacja wyrażenia regularnego przed uruchomieniem workerów
        if use_regex:
            try:
                re.compile(pattern, re.MULTILINE if case_sensitive else (re.IGNORECASE | re.MULTILINE))
            except re.error as e:
                if self._is_current_session(session) and not self._cancel.is_set():
                    try:
                        on_done(Bitset(0), str(e))
                    except (TypeError, ValueError, RuntimeError, AttributeError):
                        pass
                return

        # Jeśli wyszukujemy w przefiltrowanych liniach:
        if search_in_filter and filtered_lines is not None:
            flen = len(filtered_lines)
            if flen == 0:
                if self._is_current_session(session) and not self._cancel.is_set():
                    _notify_progress(on_progress, 100.0, 0, "filtering", None)
                    try:
                        on_done(Bitset(self.indexer.line_count if self.indexer else 0), None)
                    except (TypeError, ValueError, RuntimeError, AttributeError):
                        pass
                return

            # Optymalizacja 1.2: Adaptacyjna ocena gęstości i rozpiętości filtra
            if self._should_use_filtered_search(filtered_lines):
                self._run_filtered(
                    session,
                    pattern,
                    use_regex,
                    case_sensitive,
                    negate,
                    on_progress,
                    on_done,
                    filtered_lines,
                )
                return

        # Wybór trybu na podstawie rozmiaru pliku i jego typu
        use_parallel = (
            not self.indexer.is_compressed
            and self.indexer.size >= _PARALLEL_SEARCH_THRESHOLD
            and len(self.indexer.offsets) >= 4
        )

        if use_parallel:
            try:
                self._run_parallel(
                    session,
                    pattern,
                    use_regex,
                    case_sensitive,
                    negate,
                    on_progress,
                    on_done,
                    search_in_filter,
                    filtered_lines,
                )
                return
            except (OSError, RuntimeError, ValueError) as e:
                # Fallback do single-thread przy błędzie multiprocessing
                print(f"Warning: parallel search failed ({e}), falling back to single-thread", file=sys.stderr)

        self._run_single(
            session, pattern, use_regex, case_sensitive, negate, on_progress, on_done, search_in_filter, filtered_lines
        )

    @staticmethod
    def _get_clusters(
        targets: Bitset | Sequence[int],
        max_gap: int = 32,
        max_span: int = 256,
    ) -> Iterator[list[int]]:
        """Grupuje linie w klastry."""
        cluster: list[int] = []
        first_t = -1
        for t in targets:
            if first_t == -1:
                cluster = [t]
                first_t = t
            elif (t - cluster[-1] <= max_gap) and (t - first_t + 1 <= max_span):
                cluster.append(t)
            else:
                yield cluster
                cluster = [t]
                first_t = t
        if cluster:
            yield cluster

    def _run_filtered(
        self,
        session: int,
        pattern: str,
        use_regex: bool,
        case_sensitive: bool,
        negate: bool,
        on_progress: Callable[..., None],
        on_done: Callable[[Bitset, str | None], None],
        filtered_lines: Bitset | Sequence[int],
    ) -> None:
        """Błyskawiczne przeszukiwanie wyłącznie linii z podzbioru filtered_lines.

        Zamiast skanować cały plik, odczytuje bezpośrednio tylko linie przefiltrowane,
        używając rzadkiego indeksu LineIndexer do przeskakiwania dużych odstępów.
        """
        universe_size = self.indexer.line_count if self.indexer else 0
        total_lines = len(filtered_lines)

        if total_lines == 0:
            if self._is_current_session(session) and not self._cancel.is_set():
                _notify_progress(on_progress, 100.0, 0, "filtering", None)
                try:
                    on_done(Bitset(universe_size), None)
                except (TypeError, ValueError, RuntimeError, AttributeError):
                    pass
            return

        try:
            strategy = self._create_strategy(pattern, use_regex, case_sensitive, negate)
        except re.error as e:
            if self._is_current_session(session) and not self._cancel.is_set():
                try:
                    on_done(Bitset(0), str(e))
                except (TypeError, ValueError, RuntimeError, AttributeError):
                    pass
            return

        merged_bitset = Bitset(universe_size)
        total_hits = 0
        batch_hits: list[int] = []
        last_progress_time = time.time()
        processed_count = 0

        try:
            with open_maybe_compressed(str(self.path), "rb", buffering=256 * 1024) as f:
                cur_line = 0
                offsets = self.indexer.offsets
                lines = self.indexer.lines
                is_compressed = self.indexer.is_compressed

                for cluster in self._get_clusters(filtered_lines):
                    if self._cancel.is_set() or not self._is_current_session(session):
                        return

                    start_target = cluster[0]
                    end_target = cluster[-1]
                    span = end_target - start_target + 1

                    # Pozycjonowanie w pliku do start_target
                    if cur_line != start_target:
                        if cur_line < start_target:
                            if not is_compressed and len(lines) > 1:
                                idx = max(0, bisect.bisect_right(lines, start_target) - 1)
                                start_line = lines[idx]
                                start_offset = offsets[idx]
                                if start_line > cur_line:
                                    f.seek(start_offset)
                                    cur_line = start_line

                            needed = start_target - cur_line
                            if needed > 0:
                                if not self.indexer.advance_lines(f, needed):
                                    break
                                cur_line = start_target
                        else:
                            idx = max(0, bisect.bisect_right(lines, start_target) - 1)
                            start_line = lines[idx]
                            start_offset = offsets[idx]
                            f.seek(start_offset)
                            needed = start_target - start_line
                            if not self.indexer.advance_lines(f, needed):
                                break
                            cur_line = start_target

                    # Odczyt klastra linii z buforowaniem
                    if span == 1:
                        raw_line = f.readline()
                        if not raw_line:
                            break
                        cur_line = start_target + 1
                        processed_count += 1
                        if strategy.match(raw_line):
                            batch_hits.append(start_target)
                            total_hits += 1
                    elif span == len(cluster):
                        # Wszystkie linie w klastrze wystepuja ciagle jedna po drugiej
                        for target_line, raw_line in zip(cluster, itertools.islice(f, span), strict=False):
                            processed_count += 1
                            if strategy.match(raw_line):
                                batch_hits.append(target_line)
                                total_hits += 1
                        cur_line = start_target + span
                    else:
                        # Linie w klastrze maja niewielkie odstepy (<= max_gap)
                        lines_chunk = list(itertools.islice(f, span))
                        if not lines_chunk:
                            break
                        cur_line = start_target + len(lines_chunk)
                        chunk_len = len(lines_chunk)
                        for target_line in cluster:
                            offset = target_line - start_target
                            if offset < chunk_len:
                                raw_line = lines_chunk[offset]
                                processed_count += 1
                                if strategy.match(raw_line):
                                    batch_hits.append(target_line)
                                    total_hits += 1

                    now = time.time()
                    if now - last_progress_time >= 0.1 or len(batch_hits) >= 2000:
                        if self._cancel.is_set() or not self._is_current_session(session):
                            return
                        pct = (processed_count / total_lines) * 100.0
                        partial_res = None
                        if batch_hits:
                            min_idx = batch_hits[0]
                            max_idx = batch_hits[-1]
                            base_w = min_idx // 64
                            end_w = max_idx // 64
                            words = array.array("Q", [0] * (end_w - base_w + 1))
                            for h in batch_hits:
                                words[h // 64 - base_w] |= 1 << (h % 64)
                            merged_bitset.update_indices(batch_hits)
                            partial_res = (base_w, words)
                            batch_hits = []
                        _notify_progress(on_progress, min(99.9, pct), total_hits, "filtering", partial_res)
                        last_progress_time = now

            if batch_hits:
                merged_bitset.update_indices(batch_hits)

        except (OSError, UnicodeError) as e:
            if self._is_current_session(session) and not self._cancel.is_set():
                try:
                    on_done(Bitset(0), str(e))
                except (TypeError, ValueError, RuntimeError, AttributeError):
                    pass
            return

        if self._cancel.is_set() or not self._is_current_session(session):
            return

        _notify_progress(on_progress, 100.0, total_hits, "filtering", None)
        if self._is_current_session(session) and not self._cancel.is_set():
            try:
                on_done(merged_bitset, None)
            except (TypeError, ValueError, RuntimeError, AttributeError):
                pass

    def _run_parallel(
        self,
        session: int,
        pattern: str,
        use_regex: bool,
        case_sensitive: bool,
        negate: bool,
        on_progress: Callable[..., None],
        on_done: Callable[[Bitset, str | None], None],
        search_in_filter: bool = False,
        filtered_lines: Bitset | None = None,
    ) -> None:
        """Równoległe przeszukiwanie pliku z użyciem multiprocessing.Pool.

        Plik dzielony jest na N zakresów wzdłuż granic linii (z istniejącego indeksu).
        Każdy rdzeń CPU dostaje swój zakres i zwraca pasujące numery linii.

        Używa imap_unordered zamiast map_async, dzięki czemu wyniki każdego
        chunku są odbierane natychmiast po jego zakończeniu — licznik trafień
        jest aktualizowany na bieżąco przez on_progress.
        Wyniki są scalane w kolejności (po chunk_id), zachowując porządek linii.
        """
        n_workers = max(2, multiprocessing.cpu_count())
        ranges = self._compute_search_ranges(n_workers)

        # Optymalizacja 1.1: Pomijanie pustych chunków w search_in_filter (Chunk Skipping via Bitset.any_in_range)
        active_ranges: list[tuple[int, int, int, int]] = []
        skipped_bytes = 0

        for i, (start_off, end_off, start_ln) in enumerate(ranges):
            end_ln = (
                ranges[i + 1][2] if i + 1 < len(ranges) else (self.indexer.line_count if self.indexer else start_ln)
            )
            if search_in_filter and filtered_lines is not None and not filtered_lines.any_in_range(start_ln, end_ln):
                skipped_bytes += end_off - start_off
                continue
            active_ranges.append((i, start_off, end_off, start_ln))

        if search_in_filter and filtered_lines is not None and not active_ranges:
            if self._is_current_session(session) and not self._cancel.is_set():
                _notify_progress(on_progress, 100.0, 0, "filtering", None)
                try:
                    on_done(Bitset(self.indexer.line_count if self.indexer else 0), None)
                except (TypeError, ValueError, RuntimeError, AttributeError):
                    pass
            return

        pool_workers = min(n_workers, max(1, len(active_ranges)))

        args_list = [
            (
                chunk_id,
                start_off,
                end_off,
                start_ln,
                self.path,
                pattern,
                use_regex,
                case_sensitive,
                False,
                self.indexer.encoding,
            )
            for (chunk_id, start_off, end_off, start_ln) in active_ranges
        ]

        shared_bytes = multiprocessing.Value("Q", skipped_bytes)
        shared_hits = multiprocessing.Value("Q", 0)
        # Słownik: chunk_id → (base_word, words)
        chunk_results: dict[int, tuple[int, array.array[int]]] = {}

        try:
            with multiprocessing.Pool(
                pool_workers, initializer=_filter_init_worker, initargs=(shared_bytes, shared_hits)
            ) as pool:
                # imap_unordered — iterator dostarczający wyniki chunk po chunku,
                # w kolejności ich ukończenia (nie koniecznie wg chunk_id).
                imap_iter = pool.imap_unordered(_filter_worker_chunk, args_list)

                while True:
                    if self._cancel.is_set() or not self._is_current_session(session):
                        pool.terminate()
                        return

                    try:
                        # Czekaj na kolejny wynik z limitem 0.1 s — pozwala
                        # sprawdzać anulowanie bez blokowania wątku na stałe.
                        chunk_id, base_word, words, hit_count = imap_iter.next(timeout=0.1)
                    except multiprocessing.TimeoutError:
                        # Żaden chunk jeszcze nie skończył — raportuj postęp
                        # na podstawie licznika bajtów ze współdzielonej pamięci.
                        bytes_done = shared_bytes.value
                        current_hits = shared_hits.value
                        pct = (bytes_done / self.indexer.size * 100.0) if self.indexer.size else 0.0
                        _notify_progress(on_progress, min(pct, 99.0), current_hits)
                        continue
                    except StopIteration:
                        # Wszystkie chunki zostały odebrane — kończymy pętlę.
                        break

                    # Odbieramy wynik chunku: dodajemy do wyników
                    if hit_count > 0:
                        chunk_results[chunk_id] = (base_word, words)

                    bytes_done = shared_bytes.value
                    current_hits = shared_hits.value
                    pct = (bytes_done / self.indexer.size * 100.0) if self.indexer.size else 0.0
                    _notify_progress(
                        on_progress,
                        min(pct, 99.0),
                        current_hits,
                        "filtering",
                        (base_word, words) if hit_count > 0 else None,
                    )

        except (OSError, RuntimeError, ValueError) as e:
            if self._is_current_session(session) and not self._cancel.is_set():
                try:
                    on_done(Bitset(0), str(e))
                except (TypeError, ValueError, RuntimeError, AttributeError):
                    pass
            return

        # Sprawdź anulowanie przed scaleniem
        if self._cancel.is_set() or not self._is_current_session(session):
            return

        merged_bitset = Bitset(self.indexer.line_count if self.indexer else 0)

        for _chunk_id, (base_word, words) in sorted(chunk_results.items(), key=lambda x: x[0]):
            merged_bitset.merge_chunk_words(base_word, words)

        if negate:
            merged_bitset = ~merged_bitset

        if search_in_filter and filtered_lines is not None:
            merged_bitset = merged_bitset & filtered_lines

        if self._is_current_session(session) and not self._cancel.is_set():
            try:
                on_done(merged_bitset, None)
            except (TypeError, ValueError, RuntimeError, AttributeError):
                pass

    def _run_single(
        self,
        session: int,
        pattern: str,
        use_regex: bool,
        case_sensitive: bool,
        negate: bool,
        on_progress: Callable[..., None],
        on_done: Callable[[Bitset, str | None], None],
        search_in_filter: bool = False,
        filtered_lines: Bitset | None = None,
    ) -> None:
        """Sekwencyjne przeszukiwanie pliku w jednym wątku.

        Używane dla małych plików (< 50 MB) oraz plików skompresowanych,
        gdzie multiprocessing przyniósłby zbyt duży narzut.
        """
        merged_bitset = Bitset(self.indexer.line_count if self.indexer else 0)
        total_hits = 0
        error: str | None = None

        try:
            strategy = self._create_strategy(pattern, use_regex, case_sensitive, False)
        except re.error as e:
            if self._is_current_session(session) and not self._cancel.is_set():
                try:
                    on_done(Bitset(0), str(e))
                except (TypeError, ValueError, RuntimeError, AttributeError):
                    pass
            return

        try:
            size = self.indexer.size
            bytes_read = 0
            line_no = 0
            for chunk_count, block in enumerate(read_file_chunks(self.path)):
                if chunk_count % 10 == 0 and (self._cancel.is_set() or not self._is_current_session(session)):
                    return
                bytes_read += len(block)

                lines_count = block.count(b"\n")
                if block and not block.endswith(b"\n"):
                    lines_count += 1
                chunk_end_line = line_no + lines_count

                # Optymalizacja 1.1: Pomijanie match_chunk dla bloków bez linii w filtrze
                if (
                    search_in_filter
                    and filtered_lines is not None
                    and not filtered_lines.any_in_range(line_no, chunk_end_line)
                ):
                    line_no = chunk_end_line
                    if self._is_current_session(session) and not self._cancel.is_set():
                        pct = (bytes_read / size * 100.0) if size else 0.0
                        _notify_progress(
                            on_progress,
                            pct,
                            total_hits,
                            "filtering",
                            None,
                        )
                    continue

                chunk_hits = strategy.match_chunk(block, line_no)
                partial_res = None

                if chunk_hits:
                    min_idx = chunk_hits[0]
                    max_idx = chunk_hits[-1]
                    base_word = min_idx // 64
                    end_word = max_idx // 64
                    words = array.array("Q", [0] * (end_word - base_word + 1))
                    for idx in chunk_hits:
                        words[idx // 64 - base_word] |= 1 << (idx % 64)

                    merged_bitset.merge_chunk_words(base_word, words)
                    total_hits += len(chunk_hits)
                    partial_res = (base_word, words)

                line_no = chunk_end_line

                if self._is_current_session(session) and not self._cancel.is_set():
                    pct = (bytes_read / size * 100.0) if size else 0.0
                    _notify_progress(
                        on_progress,
                        pct,
                        total_hits,
                        "filtering",
                        partial_res,
                    )

        except (OSError, UnicodeError) as e:
            error = str(e)

        if negate and error is None:
            merged_bitset = ~merged_bitset

        if error is None and search_in_filter and filtered_lines is not None:
            merged_bitset = merged_bitset & filtered_lines

        if self._is_current_session(session) and not self._cancel.is_set():
            try:
                on_done(merged_bitset, error)
            except (TypeError, ValueError, RuntimeError, AttributeError):
                pass
