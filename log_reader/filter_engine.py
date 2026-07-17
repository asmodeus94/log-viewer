"""FilterEngine — skanowanie pliku w tle z możliwością anulowania."""

from __future__ import annotations

import re
import threading
from typing import Callable, List, Optional, Tuple

from .helpers import open_maybe_compressed
from .indexer import LineIndexer


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

    def _run(self, session, pattern, use_regex, case_sensitive, negate, on_progress, on_done):
        results: List[Tuple[int, int, str]] = []
        error: Optional[str] = None
        matcher = None
        matcher_bytes = None  # regex na surowych bajtach (optymalizacja #10)
        try:
            if use_regex:
                flags = 0 if case_sensitive else re.IGNORECASE
                matcher = re.compile(pattern, flags)
                # Skompiluj też na bajtach — ~2x szybsze (bez dekodowania)
                try:
                    pattern_bytes = pattern.encode(self.indexer.encoding, errors="replace")
                    matcher_bytes = re.compile(pattern_bytes, flags)
                except Exception:
                    pass  # fallback na regex na str
            else:
                needle = pattern if case_sensitive else pattern.lower()
        except re.error as e:
            if self._is_current_session(session) and not self._cancel.is_set():
                try:
                    on_done([], str(e))
                except Exception:
                    pass
            return

        try:
            size = self.indexer.size
            encoding = self.indexer.encoding
            bytes_read = 0
            line_no = 0

            # Optymalizacja: dla plain text (bez regex) przeszukujemy surowe bajty.
            if matcher is None and not negate:
                needle_bytes = needle.encode(encoding, errors="replace")
                if not case_sensitive:
                    needle_bytes_lower = needle_bytes.lower()
                else:
                    needle_bytes_lower = needle_bytes

                with open_maybe_compressed(self.path, "rb") as f:
                    CHUNK = 4 * 1024 * 1024  # 4 MB
                    carry = b""
                    chunk_count = 0
                    eof = False
                    while not eof:
                        if chunk_count % 10 == 0:
                            if self._cancel.is_set() or not self._is_current_session(session):
                                return
                        chunk_count += 1

                        chunk = f.read(CHUNK)
                        if not chunk:
                            eof = True
                        data = carry + chunk
                        if not data:
                            break
                        bytes_read += len(chunk)

                        # Podziel na linie — ostatnia może być niekompletna
                        if eof:
                            # Ostatni chunk — wszystkie dane są kompletne
                            complete_data = data
                            carry = b""
                        else:
                            last_nl = data.rfind(b"\n")
                            if last_nl != -1:
                                complete_data = data[:last_nl + 1]
                                carry = data[last_nl + 1:]
                            else:
                                # Cały chunk to jedna długa linia bez \n — zachowaj jako carry
                                complete_data = b""
                                carry = data

                        if complete_data:
                            lines = complete_data.split(b"\n")
                            if lines and lines[-1] == b"":
                                lines.pop()
                            for line_bytes in lines:
                                if not case_sensitive:
                                    if needle_bytes_lower in line_bytes.lower():
                                        try:
                                            text = line_bytes.decode(encoding, errors="replace")
                                        except Exception:
                                            text = repr(line_bytes)
                                        results.append((line_no, 0, text.rstrip("\r\n")))
                                else:
                                    if needle_bytes_lower in line_bytes:
                                        try:
                                            text = line_bytes.decode(encoding, errors="replace")
                                        except Exception:
                                            text = repr(line_bytes)
                                        results.append((line_no, 0, text.rstrip("\r\n")))
                                line_no += 1

                        if self._is_current_session(session) and not self._cancel.is_set():
                            pct = (bytes_read / size * 100.0) if size else 0.0
                            try:
                                on_progress(pct, len(results))
                            except Exception:
                                pass
            else:
                # Tryb regex lub negacja — używaj regex na bajtach gdy możliwe
                # (optymalizacja #10: ~2x szybsze bez dekodowania każdej linii)
                use_bytes_regex = matcher_bytes is not None
                needle_bytes = needle.encode(encoding, errors="replace") if matcher is None else None
                if needle_bytes and not case_sensitive:
                    needle_bytes_lower = needle_bytes.lower()
                elif needle_bytes:
                    needle_bytes_lower = needle_bytes
                else:
                    needle_bytes_lower = None

                with open_maybe_compressed(self.path, "rb") as f:
                    for raw in f:
                        if line_no % 1000 == 0:
                            if self._cancel.is_set() or not self._is_current_session(session):
                                return
                        bytes_read += len(raw)

                        # Najpierw sprawdź na surowych bajtach (szybkie)
                        if use_bytes_regex:
                            matched = matcher_bytes.search(raw) is not None
                        elif needle_bytes_lower is not None:
                            if not case_sensitive:
                                matched = needle_bytes_lower in raw.lower()
                            else:
                                matched = needle_bytes_lower in raw
                        else:
                            # Fallback: dekoduj i sprawdź na str
                            try:
                                text = raw.decode(encoding, errors="replace")
                            except Exception:
                                text = repr(raw)
                            if matcher is not None:
                                matched = matcher.search(text) is not None
                            else:
                                matched = (needle in text) if case_sensitive else (needle in text.lower())

                        if negate:
                            matched = not matched
                        if matched:
                            # Dekoduj tylko pasujące linie
                            try:
                                text = raw.decode(encoding, errors="replace")
                            except Exception:
                                text = repr(raw)
                            results.append((line_no, bytes_read - len(raw), text.rstrip("\r\n")))
                        line_no += 1
                        if line_no % 5000 == 0:
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
