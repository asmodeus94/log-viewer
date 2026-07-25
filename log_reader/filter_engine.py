import re
import array
import threading
from typing import Callable, List, Optional, Tuple, Iterator
from abc import ABC, abstractmethod

from log_reader.helpers import open_maybe_compressed
from log_reader.indexer import LineIndexer

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

def read_file_chunks(path: str, chunk_size: int = 4 * 1024 * 1024) -> Iterator[bytes]:
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

    def _run(self, session, pattern, use_regex, case_sensitive, negate, on_progress, on_done):
        results = array.array('Q')
        error: Optional[str] = None

        try:
            strategy = self._create_strategy(pattern, use_regex, case_sensitive, negate)
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

                lines = block.split(b"\n")
                if lines and lines[-1] == b"":
                    lines.pop()

                for line_bytes in lines:
                    if strategy.match(line_bytes):
                        results.append(line_no)
                    line_no += 1

                if self._is_current_session(session) and not self._cancel.is_set():
                    pct = (bytes_read / size * 100.0) if size else 0.0
                    try:
                        on_progress(pct, len(results))
                    except Exception:
                        pass

        except Exception as e:
            error = str(e)

        if self._is_current_session(session) and not self._cancel.is_set():
            try:
                on_done(results, error)
            except Exception:
                pass
