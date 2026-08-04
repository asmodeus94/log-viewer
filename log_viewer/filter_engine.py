import re
import array
import threading
import multiprocessing
import sys
from typing import Callable, List, Optional, Tuple, Iterator
from abc import ABC, abstractmethod

from log_viewer.helpers import open_maybe_compressed
from log_viewer.indexer import LineIndexer

# Próg rozmiaru pliku (w bajtach), od którego aktywowany jest tryb równoległy.
# Dla plików poniżej tego progu single-thread jest szybszy (brak narzutu na Pool).
_PARALLEL_SEARCH_THRESHOLD = 50 * 1024 * 1024  # 50 MB

# Współdzielone liczniki postępu — globalne w kontekście procesu roboczego.
_shared_filter_progress_bytes = None
_shared_filter_hits = None


def _filter_init_worker(progress_val: "multiprocessing.Value", hits_val: "multiprocessing.Value") -> None:
    """Inicjalizator procesu roboczego — ustawia globalne liczniki postępu."""
    global _shared_filter_progress_bytes, _shared_filter_hits
    _shared_filter_progress_bytes = progress_val
    _shared_filter_hits = hits_val


def _filter_worker_chunk(
    args: Tuple[int, int, int, int, str, str, bool, bool, bool, str]
) -> Tuple[int, int, "array.array[int]", int]:
    """
    Worker dla multiprocessing — przeszukuje przydzielony zakres bajtów pliku.

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

    Zwraca krotkę (chunk_id, array numerów linii pasujących do wzorca).
    """
    (chunk_id, start_offset, end_offset, start_line,
     path, pattern, use_regex, case_sensitive, negate, encoding) = args

    results: "array.array[int]" = array.array('Q')

    # Utwórz strategię wewnątrz workera — obiekty re nie są picklowalne
    try:
        if use_regex:
            strategy: FilterStrategy = RegexStrategy(pattern, case_sensitive, negate, encoding)
        else:
            strategy = PlainTextStrategy(pattern, case_sensitive, negate, encoding)
    except Exception:
        return (chunk_id, 0, array.array('Q'), 0)

    line_no = start_line
    READ_CHUNK = 32 * 1024 * 1024  # 32 MB — większy chunk = lepsza lokalność cache OS
    carry = b""
    total_range = end_offset - start_offset
    bytes_processed = 0

    try:
        with open(path, "rb", buffering=4 * 1024 * 1024) as f:
            f.seek(start_offset)

            while bytes_processed < total_range:
                to_read = min(READ_CHUNK, total_range - bytes_processed)
                chunk = f.read(to_read)
                if not chunk:
                    break

                chunk_len = len(chunk)
                bytes_processed += chunk_len

                # Aktualizuj współdzielony licznik postępu
                global _shared_filter_progress_bytes
                if _shared_filter_progress_bytes is not None:
                    with _shared_filter_progress_bytes.get_lock():
                        _shared_filter_progress_bytes.value += chunk_len

                is_last_read = (bytes_processed >= total_range)
                data = carry + chunk if carry else chunk

                if is_last_read:
                    # Ostatni odczyt — przetwórz wszystko (nawet bez końcowego \n)
                    complete_data = data
                    carry = b""
                else:
                    # Zostaw niepełną ostatnią linię jako carry do następnego chunku
                    last_nl = data.rfind(b"\n")
                    if last_nl != -1:
                        complete_data = data[:last_nl + 1]
                        carry = data[last_nl + 1:]
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

                global _shared_filter_hits
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

    except Exception:
        pass

    hit_count = len(results)
    if hit_count == 0:
        return (chunk_id, 0, array.array('Q'), 0)

    min_idx = results[0]
    max_idx = results[-1]
    
    base_word = min_idx // 64
    end_word = max_idx // 64
    
    words = array.array('Q', [0] * (end_word - base_word + 1))
    
    for idx in results:
        words[idx // 64 - base_word] |= (1 << (idx % 64))

    return (chunk_id, base_word, words, hit_count)


class FilterStrategy(ABC):
    """
    Abstrakcyjna klasa bazowa dla strategii filtrowania (Wzorzec Strategii).
    Definiuje wspólny interfejs sprawdzania czy dana linia pasuje do wzorca.
    """
    def __init__(self, negate: bool, encoding: str):
        self.negate = negate
        self.encoding = encoding

    @abstractmethod
    def match(self, line_bytes: bytes) -> bool:
        """Sprawdza, czy przekazane bajty linii pasują do wzorca strategii."""
        pass

    def match_chunk(self, chunk: bytes, start_line: int) -> List[int]:
        """Domyślna implementacja chunkowa – dzieli na linie i wywołuje match()."""
        lines = chunk.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()

        hits = []
        for line_bytes in lines:
            if self.match(line_bytes):
                hits.append(start_line)
            start_line += 1
        return hits


class PlainTextStrategy(FilterStrategy):
    """Strategia dla zwykłego wyszukiwania tekstu (bez wyrażeń regularnych)."""
    def __init__(self, needle: str, case_sensitive: bool, negate: bool, encoding: str):
        super().__init__(negate, encoding)
        self.case_sensitive = case_sensitive
        self.needle_bytes = needle.encode(encoding, errors="replace")
        if not case_sensitive:
            self.needle_bytes = self.needle_bytes.lower()

    def match(self, line_bytes: bytes) -> bool:
        if not self.case_sensitive:
            matched = self.needle_bytes in line_bytes.lower()
        else:
            matched = self.needle_bytes in line_bytes

        return not matched if self.negate else matched

    def match_chunk(self, chunk: bytes, start_line: int) -> List[int]:
        if not self.needle_bytes:
            return [] if self.negate else list(range(start_line, start_line + chunk.count(b"\n") + (1 if chunk and not chunk.endswith(b"\n") else 0)))

        haystack = chunk.lower() if not self.case_sensitive else chunk
        hits = []

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
    def __init__(self, pattern: str, case_sensitive: bool, negate: bool, encoding: str):
        super().__init__(negate, encoding)
        self.pattern = pattern
        self.flags = 0 if case_sensitive else re.IGNORECASE
        self.matcher_str = re.compile(pattern, self.flags)

        self.matcher_bytes = None
        try:
            pattern_bytes = pattern.encode(encoding, errors="replace")
            self.matcher_bytes = re.compile(pattern_bytes, self.flags)
        except Exception:
            pass  # Fallback na sprawdzanie łańcuchów znaków

    def match(self, line_bytes: bytes) -> bool:
        # Najpierw sprawdź na surowych bajtach (szybkie)
        if self.matcher_bytes is not None:
            matched = self.matcher_bytes.search(line_bytes) is not None
        else:
            # Fallback: dekoduj i sprawdź na str
            try:
                text = line_bytes.decode(self.encoding, errors="replace")
            except Exception:
                text = repr(line_bytes)
            matched = self.matcher_str.search(text) is not None

        return not matched if self.negate else matched

    def match_chunk(self, chunk: bytes, start_line: int) -> List[int]:
        if self.matcher_bytes is not None:
            matcher = self.matcher_bytes
            target = chunk
            nl = b"\n"
        else:
            matcher = self.matcher_str
            try:
                target = chunk.decode(self.encoding, errors="replace")
            except Exception:
                target = repr(chunk)
            nl = "\n"

        hits = []
        last_nl_pos = -1
        lines_counted = start_line

        for match in matcher.finditer(target):
            hit_pos = match.start()
            if hit_pos > last_nl_pos:
                nl_count = target.count(nl, last_nl_pos + 1, hit_pos)
                lines_counted += nl_count
                
                if not hits or hits[-1] != lines_counted:
                    hits.append(lines_counted)
                last_nl_pos = hit_pos

        return hits

def read_file_chunks(path: str, chunk_size: int = 32 * 1024 * 1024) -> Iterator[bytes]:
    """Generator odczytujący plik partiami (chunking) po zadanym rozmiarze."""
    with open_maybe_compressed(path, "rb") as f:
        carry = b""
        eof = False
        while not eof:
            chunk = f.read(chunk_size)
            if not chunk:
                eof = True

            data = carry + chunk
            if not data:
                break

            if eof:
                complete_data = data
                carry = b""
            else:
                last_nl = data.rfind(b"\n")
                if last_nl != -1:
                    complete_data = data[:last_nl + 1]
                    carry = data[last_nl + 1:]
                else:
                    # Cały chunk to jedna długa linia bez \n
                    complete_data = b""
                    carry = data

            if complete_data:
                yield complete_data


class FilterEngine:
    """
    Przeszukuje plik w tle. Każde wywołanie start() dostaje nowy _session_id.
    Bezpieczeństwo wątkowe: start() czeka na zakończenie poprzedniego wątku.

    Dla plików >= 50 MB (nieskompresowanych) automatycznie używa multiprocessing
    (tyle workerów ile rdzeni CPU) dla drastycznie wyższej wydajności.
    Dla plików mniejszych i skompresowanych stosuje tryb single-thread.
    """

    def __init__(self, path: str, indexer: LineIndexer):
        self.path = path
        self.indexer = indexer
        self._thread: Optional[threading.Thread] = None
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

    def start(self,
              pattern: str,
              use_regex: bool,
              case_sensitive: bool,
              negate: bool,
              on_progress: Callable[[float, int], None],
              on_done: Callable[[List[Tuple[int, int, str]], Optional[str]], None]) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._cancel.set()
            self._thread.join(timeout=5.0)
        with self._session_lock:
            self._session_id += 1
            session = self._session_id
            self._cancel.clear()
            self._thread = threading.Thread(
                target=self._run,
                args=(session, pattern, use_regex, case_sensitive, negate, on_progress, on_done),
                daemon=True,
            )
            self._thread.start()

    def _is_current_session(self, session: int) -> bool:
        with self._session_lock:
            return session == self._session_id

    def _create_strategy(self, pattern: str, use_regex: bool, case_sensitive: bool, negate: bool) -> FilterStrategy:
        """Fabryka do tworzenia odpowiedniej strategii wyszukiwania."""
        if use_regex:
            return RegexStrategy(pattern, case_sensitive, negate, self.indexer.encoding)
        return PlainTextStrategy(pattern, case_sensitive, negate, self.indexer.encoding)

    def _compute_search_ranges(self, n_workers: int) -> List[Tuple[int, int, int]]:
        """
        Wyznacza zakresy (start_offset, end_offset, start_line) dla workerów.

        Korzysta z istniejącego rzadkiego indeksu LineIndexer — zero dodatkowego I/O.
        Każdy zakres zaczyna i kończy się na granicy linii (gwarantowane przez indeks).
        """
        index = self.indexer.index
        n_entries = len(index)
        file_size = self.indexer.size

        if n_entries < 2:
            # Za mało wpisów w indeksie — cały plik jako jeden zakres
            return [(0, file_size, 0)]

        # Wybierz co K-ty wpis jako punkt podziału, maksymalnie n_workers zakresów
        step = max(1, (n_entries - 1) // n_workers)
        split_indices = list(range(0, n_entries, step))[:n_workers]

        ranges: List[Tuple[int, int, int]] = []
        for i, idx in enumerate(split_indices):
            entry = index[idx]
            start_offset = entry.offset
            start_line = entry.line

            if i + 1 < len(split_indices):
                end_offset = index[split_indices[i + 1]].offset
            else:
                end_offset = file_size

            if end_offset > start_offset:
                ranges.append((start_offset, end_offset, start_line))

        return ranges if ranges else [(0, file_size, 0)]

    def _run(self, session: int, pattern: str, use_regex: bool,
             case_sensitive: bool, negate: bool,
             on_progress: Callable[[float, int], None],
             on_done: Callable) -> None:
        """
        Dyspozytor — wybiera tryb równoległy lub single-thread i deleguje pracę.

        Tryb równoległy (multiprocessing) dla plików >= 50 MB nieskompresowanych.
        Tryb single-thread dla małych plików i plików skompresowanych.
        """
        # Wstępna walidacja wyrażenia regularnego przed uruchomieniem workerów
        if use_regex:
            try:
                re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
            except re.error as e:
                if self._is_current_session(session) and not self._cancel.is_set():
                    try:
                        from log_viewer.bitset import Bitset
                        on_done(Bitset(0), str(e))
                    except Exception:
                        pass
                return

        # Wybór trybu na podstawie rozmiaru pliku i jego typu
        use_parallel = (
            not self.indexer.is_compressed
            and self.indexer.size >= _PARALLEL_SEARCH_THRESHOLD
            and len(self.indexer.index) >= 4
        )

        if use_parallel:
            try:
                self._run_parallel(session, pattern, use_regex, case_sensitive,
                                   negate, on_progress, on_done)
                return
            except Exception as e:
                # Fallback do single-thread przy błędzie multiprocessing
                print(f"Warning: parallel search failed ({e}), falling back to single-thread",
                      file=sys.stderr)

        self._run_single(session, pattern, use_regex, case_sensitive,
                         negate, on_progress, on_done)

    def _run_parallel(self, session: int, pattern: str, use_regex: bool,
                      case_sensitive: bool, negate: bool,
                      on_progress: Callable[[float, int], None],
                      on_done: Callable) -> None:
        """
        Równoległe przeszukiwanie pliku z użyciem multiprocessing.Pool.

        Plik dzielony jest na N zakresów wzdłuż granic linii (z istniejącego indeksu).
        Każdy rdzeń CPU dostaje swój zakres i zwraca pasujące numery linii.

        Używa imap_unordered zamiast map_async, dzięki czemu wyniki każdego
        chunku są odbierane natychmiast po jego zakończeniu — licznik trafień
        jest aktualizowany na bieżąco przez on_progress.
        Wyniki są scalane w kolejności (po chunk_id), zachowując porządek linii.
        """
        n_workers = max(2, multiprocessing.cpu_count())
        ranges = self._compute_search_ranges(n_workers)

        args_list = [
            (i, start_off, end_off, start_ln,
             self.path, pattern, use_regex, case_sensitive, False,
             self.indexer.encoding)
            for i, (start_off, end_off, start_ln) in enumerate(ranges)
        ]

        shared_bytes = multiprocessing.Value('Q', 0)
        shared_hits = multiprocessing.Value('Q', 0)
        # Słownik: chunk_id → (base_word, words)
        chunk_results: dict[int, Tuple[int, "array.array[int]"]] = {}

        try:
            with multiprocessing.Pool(
                n_workers,
                initializer=_filter_init_worker,
                initargs=(shared_bytes, shared_hits)
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
                        try:
                            on_progress(min(pct, 99.0), current_hits)
                        except Exception:
                            pass
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
                    try:
                        # Przekazujemy wyniki bieżącego chunku — GUI może dołączyć je do listy
                        on_progress(min(pct, 99.0), current_hits, "filtering", (base_word, words) if hit_count > 0 else None)
                    except Exception:
                        pass

        except Exception as e:
            if self._is_current_session(session) and not self._cancel.is_set():
                try:
                    from log_viewer.bitset import Bitset
                    on_done(Bitset(0), str(e))
                except Exception:
                    pass
            return

        # Sprawdź anulowanie przed scaleniem
        if self._cancel.is_set() or not self._is_current_session(session):
            return

        from log_viewer.bitset import Bitset
        merged_bitset = Bitset(self.indexer.line_count if self.indexer else 0)
        global_words = merged_bitset._words
        
        for _chunk_id, (base_word, words) in sorted(chunk_results.items(), key=lambda x: x[0]):
            if len(words) == 1:
                global_words[base_word] |= words[0]
            elif len(words) > 1:
                global_words[base_word] |= words[0]
                global_words[base_word + len(words) - 1] |= words[-1]
                if len(words) > 2:
                    global_words[base_word + 1 : base_word + len(words) - 1] = words[1:-1]
                    
        merged_bitset._counts = None
        merged_bitset._total_count = shared_hits.value

        if negate:
            merged_bitset = ~merged_bitset

        if self._is_current_session(session) and not self._cancel.is_set():
            try:
                on_done(merged_bitset, None)
            except Exception:
                pass

    def _run_single(self, session: int, pattern: str, use_regex: bool,
                    case_sensitive: bool, negate: bool,
                    on_progress: Callable[[float, int], None],
                    on_done: Callable) -> None:
        """
        Sekwencyjne przeszukiwanie pliku w jednym wątku.

        Używane dla małych plików (< 50 MB) oraz plików skompresowanych,
        gdzie multiprocessing przyniósłby zbyt duży narzut.
        """
        from log_viewer.bitset import Bitset
        merged_bitset = Bitset(self.indexer.line_count if self.indexer else 0)
        total_hits = 0
        error: Optional[str] = None

        try:
            strategy = self._create_strategy(pattern, use_regex, case_sensitive, False)
        except re.error as e:
            if self._is_current_session(session) and not self._cancel.is_set():
                try:
                    on_done(array.array('Q'), str(e))
                except Exception:
                    pass
            return

        try:
            size = self.indexer.size
            bytes_read = 0
            line_no = 0
            chunk_count = 0

            for block in read_file_chunks(self.path):
                if chunk_count % 10 == 0:
                    if self._cancel.is_set() or not self._is_current_session(session):
                        return
                chunk_count += 1
                bytes_read += len(block)

                chunk_hits = strategy.match_chunk(block, line_no)
                
                if chunk_hits:
                    min_idx = chunk_hits[0]
                    max_idx = chunk_hits[-1]
                    base_word = min_idx // 64
                    end_word = max_idx // 64
                    words = array.array('Q', [0] * (end_word - base_word + 1))
                    for idx in chunk_hits:
                        words[idx // 64 - base_word] |= (1 << (idx % 64))
                    
                    global_words = merged_bitset._words
                    if len(words) == 1:
                        global_words[base_word] |= words[0]
                    elif len(words) > 1:
                        global_words[base_word] |= words[0]
                        global_words[base_word + len(words) - 1] |= words[-1]
                        if len(words) > 2:
                            global_words[base_word + 1 : base_word + len(words) - 1] = words[1:-1]
                            
                    merged_bitset._counts = None
                    total_hits += len(chunk_hits)
                    merged_bitset._total_count = total_hits

                lines_count = block.count(b"\n")
                if block and not block.endswith(b"\n"):
                    lines_count += 1
                line_no += lines_count

                if self._is_current_session(session) and not self._cancel.is_set():
                    pct = (bytes_read / size * 100.0) if size else 0.0
                    try:
                        on_progress(pct, total_hits, "filtering", (base_word, words) if chunk_hits else None)
                    except Exception:
                        pass

        except Exception as e:
            error = str(e)

        if negate and error is None:
            merged_bitset = ~merged_bitset

        if self._is_current_session(session) and not self._cancel.is_set():
            try:
                on_done(merged_bitset, error)
            except Exception:
                pass
