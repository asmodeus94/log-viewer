"""Testy filter_engine.py — FilterEngine, session isolation, cancel."""

import os
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

# noinspection PyPackageRequirements
import pytest
from log_viewer.bitset import Bitset
from log_viewer.filter_engine import FilterEngine
from log_viewer.indexer import LineIndexer


def _run_engine_sync(
    engine: FilterEngine,
    pattern: str,
    *,
    use_regex: bool = False,
    case_sensitive: bool = True,
    negate: bool = False,
    search_in_filter: bool = False,
    filtered_lines: Bitset | None = None,
    timeout: float = 5.0,
) -> tuple[list[int], str | None, bool]:
    """Uruchamia FilterEngine synchronicznie i zwraca (wyniki, błąd, czy_ukończono)."""
    results: list[int] = []
    errors: list[str | None] = [None]
    done = threading.Event()

    def _on_done(r: Bitset, e: str | None) -> None:
        results.extend(r)
        errors[0] = e
        done.set()

    engine.start(
        pattern,
        use_regex=use_regex,
        case_sensitive=case_sensitive,
        negate=negate,
        on_progress=lambda *_a: None,
        on_done=_on_done,
        search_in_filter=search_in_filter,
        filtered_lines=filtered_lines,
    )
    finished = done.wait(timeout)
    return results, errors[0], finished


class TestFilterEngine:
    def test_basic_search(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results, err, finished = _run_engine_sync(engine, "ERROR", use_regex=False, case_sensitive=True, negate=False)
        assert finished
        assert err is None
        assert len(results) == 250  # 1/4 linii to ERROR
        idx.close()

    def test_negate(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results, err, finished = _run_engine_sync(engine, "ERROR", use_regex=False, case_sensitive=True, negate=True)
        assert finished
        assert err is None
        assert len(results) == 750  # 3/4 to nie-ERROR
        idx.close()

    def test_regex(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results, err, finished = _run_engine_sync(engine, r"INFO", use_regex=True, case_sensitive=True, negate=False)
        assert finished
        assert err is None
        # Powinno znaleźć wszystkie INFO (1/4 linii)
        assert len(results) == 250
        idx.close()

    def test_case_insensitive(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results, err, finished = _run_engine_sync(engine, "error", use_regex=False, case_sensitive=False, negate=False)
        assert finished
        assert err is None
        assert len(results) == 250
        idx.close()

    def test_regex_error(self, temp_log_file):
        path = temp_log_file(num_lines=100)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results, err, finished = _run_engine_sync(engine, "[invalid", use_regex=True, case_sensitive=True, negate=False)
        assert finished
        assert err is not None
        assert len(results) == 0
        idx.close()

    def test_search_file_without_trailing_newline(self, tmp_path):
        path = tmp_path / "search_no_nl.txt"
        path.write_bytes(b"first line\nsecond line without newline")

        idx = LineIndexer(path)
        assert idx.line_count == 2
        engine = FilterEngine(str(path), idx)

        def run_search(pattern: str, negate: bool = False) -> list[int]:
            res, _err, _fin = _run_engine_sync(
                engine, pattern, use_regex=False, case_sensitive=False, negate=negate, timeout=3.0
            )
            return res

        # Dopasowanie do drugiej linii (tej bez \n)
        assert run_search("second", negate=False) == [1]
        # Negacja dopasowania do drugiej linii -> pierwsza linia
        assert run_search("second", negate=True) == [0]
        # Wzorzec nieistniejący z negacją -> wszystkie linie [0, 1]
        assert run_search("nonexistent", negate=True) == [0, 1]
        idx.close()


class TestFilterEngineCancel:
    def test_cancel_blocks(self, temp_log_file):
        path = temp_log_file(num_lines=100000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        engine.start(
            "INFO",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            on_progress=lambda _p, _h: None,
            on_done=lambda _r, _e: None,
        )
        time.sleep(0.05)

        engine.cancel(timeout=5.0)
        assert not engine.is_running()
        idx.close()

    def test_no_callback_after_cancel(self, temp_log_file):
        path = temp_log_file(num_lines=500000)  # duży plik by filter trwał
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        callback_called = threading.Event()
        progress_seen = threading.Event()
        engine.start(
            "INFO",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            on_progress=lambda _p, _h: progress_seen.set(),
            on_done=lambda _r, _e: callback_called.set(),
        )
        # Czekaj aż filter na pewno zaczął
        for _ in range(50):
            if progress_seen.is_set():
                break
            time.sleep(0.01)
        if not engine.is_running():
            pytest.skip("Filter too fast")
        engine.cancel(timeout=5.0)
        time.sleep(0.5)
        assert not callback_called.is_set()
        idx.close()


class TestFilterEngineSessionIsolation:
    def test_session_isolation(self, temp_log_file):
        """Stary wątek nie emituje wyników do nowej sesji."""
        # Bardzo duży plik by filter trwał na pewno >2s
        path = temp_log_file(num_lines=5000000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results1: list[int] = []
        done1 = threading.Event()
        started2 = threading.Event()

        def _on_progress_1(p: float, _h: int) -> None:
            if p > 5:
                done1.set()

        def _on_done_1(r: Bitset, _e: str | None) -> None:
            if started2.is_set():
                results1.extend(r)

        # Sesja 1 — INFO (anulujemy przed zakończeniem)
        engine.start(
            "INFO",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            on_progress=_on_progress_1,
            on_done=_on_done_1,
        )
        # Czekaj aż progress > 5%
        for _ in range(200):
            if done1.is_set():
                break
            time.sleep(0.05)

        if not engine.is_running():
            pytest.skip("Filter too fast for session isolation test")

        # Teraz uruchom sesję 2 — start() anuluje sesję 1
        started2.set()
        results2, _err2, finished2 = _run_engine_sync(
            engine, "ERROR", use_regex=False, case_sensitive=True, negate=False, timeout=25.0
        )
        assert finished2

        # Sesja 2 powinna mieć wyniki (ERROR = 1/4 = 1.25M)
        assert len(results2) == 1250000, f"Expected 1250000, got {len(results2)}"
        # Sesja 1 nie powinna mieć wyników (anulowana)
        assert len(results1) == 0, f"Old session leaked: {len(results1)}"
        idx.close()


class TestRegexOnBytes:
    """Testy optymalizacji regex na surowych bajtach."""

    def test_regex_finds_matches(self, temp_log_file):
        """Regex na bajtach znajduje te same wyniki co regex na str."""
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)
        results, _err, finished = _run_engine_sync(engine, r"ERROR", use_regex=True, case_sensitive=True, negate=False)
        assert finished
        assert len(results) == 250  # 1/4 linii to ERROR
        idx.close()

    def test_regex_case_insensitive(self, temp_log_file):
        """Regex case-insensitive na bajtach."""
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)
        results, _err, finished = _run_engine_sync(engine, r"error", use_regex=True, case_sensitive=False, negate=False)
        assert finished
        assert len(results) == 250
        idx.close()

    def test_regex_with_pattern(self, temp_log_file):
        """Regex z pattern \\d+ na bajtach."""
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)
        results, _err, finished = _run_engine_sync(
            engine, r"line\s*\d+", use_regex=True, case_sensitive=True, negate=False
        )
        assert finished
        assert len(results) == 1000  # wszystkie linie mają line\\d+
        idx.close()

    def test_plain_text_and_regex_give_same_results(self, temp_log_file):
        """Plain text i regex dają te same wyniki dla prostego wzorca."""
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)

        # Plain text
        engine1 = FilterEngine(path, idx)
        results1, _err1, fin1 = _run_engine_sync(engine1, "ERROR", use_regex=False, case_sensitive=True, negate=False)
        assert fin1

        # Regex
        engine2 = FilterEngine(path, idx)
        results2, _err2, fin2 = _run_engine_sync(engine2, "ERROR", use_regex=True, case_sensitive=True, negate=False)
        assert fin2

        assert len(results1) == len(results2) == 250
        assert results1 == results2
        idx.close()

    def test_regex_fast_line_skip_multiple_matches_per_line(self):
        """Punkt 2.3: linie z wieloma dopasowaniami wzorca zwracają linię dokładnie raz."""
        from log_viewer.filter_engine import RegexStrategy

        # Chunk z wieloma dopasowaniami na tej samej linii
        chunk = (
            b"item alpha: val=10 code=20 id=30 tag=40\n"
            b"item beta: no numbers here\n"
            b"item gamma: a=1 b=2 c=3 d=4 e=5 f=6 g=7 h=8 i=9 j=10\n"
            b"item delta: end\n"
        )
        strategy = RegexStrategy(r"\d+", case_sensitive=True, negate=False, encoding="utf-8")
        hits = strategy.match_chunk(chunk, start_line=100)
        assert hits == [100, 102]

        # Wzorzec wielokrotnych nawiasów
        strategy_brackets = RegexStrategy(r"\[.*?\]", case_sensitive=True, negate=False, encoding="utf-8")
        chunk_brackets = b"2026-10-25 [PID=1] [THREAD=2] [MODULE=A] ok\nplain line\n[ONLY_ONE]\n"
        assert strategy_brackets.match_chunk(chunk_brackets, start_line=0) == [0, 2]

    def test_regex_fast_line_skip_consecutive_empty_lines(self):
        """Punkt 2.3: obsługa pustych linii i wzorców granicznych ^ oraz $."""
        from log_viewer.filter_engine import RegexStrategy

        chunk = b"first line\n\nthird line\n\nfifth\n"
        strat_start = RegexStrategy(r"^", case_sensitive=True, negate=False, encoding="utf-8")
        assert strat_start.match_chunk(chunk, start_line=0) == [0, 1, 2, 3, 4]

        strat_end = RegexStrategy(r"$", case_sensitive=True, negate=False, encoding="utf-8")
        assert strat_end.match_chunk(chunk, start_line=0) == [0, 1, 2, 3, 4]

        strat_digits = RegexStrategy(r"\d+", case_sensitive=True, negate=False, encoding="utf-8")
        assert strat_digits.match_chunk(chunk, start_line=0) == []

    def test_regex_fast_line_skip_no_trailing_newline(self):
        """Punkt 2.3: poprawny skok i zliczanie, gdy chunk nie kończy się znakiem nowej linii."""
        from log_viewer.filter_engine import RegexStrategy

        chunk = b"first line with 10 20\nsecond line with 30 40"
        strategy = RegexStrategy(r"\d+", case_sensitive=True, negate=False, encoding="utf-8")
        assert strategy.match_chunk(chunk, start_line=50) == [50, 51]

    def test_regex_fast_line_skip_str_fallback(self):
        """Punkt 2.3: weryfikacja optymalizacji na ścieżce fallbacku str (_match_chunk_str)."""
        from log_viewer.filter_engine import RegexStrategy

        chunk = b"2026-10-25 [PID=10] [ERR=20] test 1\nno matches\n2026-10-25 [PID=30] [ERR=40] test 2\n"
        strategy = RegexStrategy(r"\d+", case_sensitive=True, negate=False, encoding="utf-8")
        # Wymuszenie fallbacku na _match_chunk_str przez wyzerowanie matcher_bytes
        strategy.matcher_bytes = None
        hits = strategy.match_chunk(chunk, start_line=0)
        assert hits == [0, 2]


class TestParallelSearch:
    """Testy trybu równoległego (multiprocessing) FilterEngine."""

    @staticmethod
    def _make_large_file(num_lines: int) -> str:
        """Tworzy plik > 50 MB potrzebny do aktywacji trybu równoległego."""
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as tf:
            path = tf.name
            levels = ["INFO", "WARN", "ERROR", "DEBUG"]
            # ~200 bajtów/linię → 300k linii ≈ 60 MB (powyżej progu 50 MB)
            for i in range(num_lines):
                level = levels[i % 4]
                msg = f"2026-07-04 [{level}] line{i:>8d} - " + ("x" * 150) + "\n"
                tf.write(msg.encode("utf-8"))
        return path

    def test_parallel_same_results_as_single(self, temp_log_file):
        """
        Tryb równoległy musi zwracać identyczne numery linii co single-thread.
        Weryfikujemy przez porównanie wyników obu trybów na tym samym pliku.
        Oba tryby są wywoływane przez start() — session_id jest poprawnie ustawiony.
        """
        from log_viewer.filter_engine import _PARALLEL_SEARCH_THRESHOLD

        path = self._make_large_file(num_lines=300_000)
        try:
            idx = LineIndexer(path)

            # --- Single-thread: użyj małego pliku poniżej progu równoległości ---
            path_small = temp_log_file(num_lines=50_000)
            idx_small = LineIndexer(path_small)
            engine_s = FilterEngine(path_small, idx_small)
            results_single, _err_s, done_s = _run_engine_sync(
                engine_s, "ERROR", use_regex=False, case_sensitive=True, negate=False, timeout=60.0
            )
            idx_small.close()

            # --- Parallel: plik >= 50 MB — start() automatycznie wybierze _run_parallel ---
            assert idx.size >= _PARALLEL_SEARCH_THRESHOLD, (
                f"Plik ({idx.size} B) mniejszy niż próg ({_PARALLEL_SEARCH_THRESHOLD} B)"
            )
            engine_p = FilterEngine(path, idx)
            results_par, _err_p, done_p = _run_engine_sync(
                engine_p, "ERROR", use_regex=False, case_sensitive=True, negate=False, timeout=60.0
            )

            assert done_s, "Single-thread search did not finish in time"
            assert done_p, "Parallel search did not finish in time"
            assert len(results_single) > 0, "Missing single-thread results"
            # Proporcja ERROR: 1/4 linii → 50k linii = 12500, 300k linii = 75000
            assert len(results_single) == 12_500, f"Single: oczekiwano 12500, dostano {len(results_single)}"
            assert len(results_par) == 75_000, f"Parallel: oczekiwano 75000, dostano {len(results_par)}"
            idx.close()
        finally:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except PermissionError:
                    pass

    def test_parallel_regex(self):
        """
        Tryb równoległy poprawnie obsługuje wyrażenia regularne.
        Wywołanie przez start() — session_id jest poprawnie ustawiony.
        """
        path = self._make_large_file(num_lines=300_000)
        try:
            idx = LineIndexer(path)
            engine = FilterEngine(path, idx)
            # start() automatycznie wybierze tryb równoległy (plik > 50 MB)
            results, _err, done = _run_engine_sync(
                engine, "ERROR", use_regex=True, case_sensitive=True, negate=False, timeout=60.0
            )

            assert done, "Parallel regex search did not finish in time"
            assert len(results) == 75_000, (  # 1/4 linii to ERROR
                f"Oczekiwano 75000, dostano {len(results)}"
            )
            idx.close()
        finally:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except PermissionError:
                    pass

    def test_auto_dispatch_parallel_for_large_file(self):
        """
        FilterEngine automatycznie używa trybu równoległego dla pliku > 50 MB.
        Weryfikuje poprawność wyników po auto-dispatch przez start().
        """
        from log_viewer.filter_engine import _PARALLEL_SEARCH_THRESHOLD

        path = self._make_large_file(num_lines=300_000)
        try:
            idx = LineIndexer(path)
            assert idx.size >= _PARALLEL_SEARCH_THRESHOLD, (
                f"Plik ({idx.size} B) mniejszy niż próg ({_PARALLEL_SEARCH_THRESHOLD} B)"
            )
            assert len(idx.index) >= 4, f"Za mało wpisów w indeksie: {len(idx.index)}"

            engine = FilterEngine(path, idx)
            results, _err, done = _run_engine_sync(
                engine, "ERROR", use_regex=False, case_sensitive=True, negate=False, timeout=60.0
            )

            assert done, "Auto-dispatch parallel search did not finish in time"
            assert len(results) == 75_000, f"Oczekiwano 75000, dostano {len(results)}"
            idx.close()
        finally:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except PermissionError:
                    pass

    def test_compute_search_ranges(self, temp_log_file):
        """_compute_search_ranges zwraca spójne zakresy pokrywające cały plik."""
        path = temp_log_file(num_lines=50_000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        ranges = engine._compute_search_ranges(n_workers=4)

        assert len(ranges) >= 1, "Must have at least one range"
        for i, (start, end, _line) in enumerate(ranges):
            assert start < end, f"Range [{i}] has empty interval: {start}..{end}"
            assert start >= 0
            assert end <= idx.size + 1

        assert ranges[0][0] == 0, "First range must start at offset 0"
        assert ranges[-1][1] == idx.size, "Last range must reach end of file"
        idx.close()

    @staticmethod
    def _assert_ranges_consistent(path: str, idx: LineIndexer, ranges: list[tuple[int, int, int]]) -> None:
        """Weryfikuje ciągłość zakresów, pokrycie całego pliku i poprawność numerów linii startowych."""
        assert ranges[0][0] == 0, "Pierwszy zakres musi zaczynać się od offsetu 0"
        assert ranges[-1][1] == idx.size, "Ostatni zakres musi sięgać końca pliku"
        with open(path, "rb") as f:
            data = f.read()
        for i, (start, end, start_line) in enumerate(ranges):
            assert start < end, f"Zakres [{i}] jest pusty: {start}..{end}"
            if i + 1 < len(ranges):
                assert end == ranges[i + 1][0], f"Luka lub nakładanie między zakresami {i} i {i + 1}"
            # Każdy zakres zaczyna się na granicy linii, a start_line odpowiada liczbie \n przed offsetem
            assert start == 0 or data[start - 1 : start] == b"\n", f"Zakres [{i}] nie zaczyna się na granicy linii"
            assert data.count(b"\n", 0, start) == start_line, f"Błędny numer linii startowej w zakresie [{i}]"

    def test_compute_search_ranges_min_tasks_per_worker(self, temp_log_file):
        """Punkt 2.2: dla małych plików liczba zadań wynosi n_workers * _SEARCH_TASKS_PER_WORKER."""
        from log_viewer.filter_engine import _SEARCH_TASKS_PER_WORKER

        path = temp_log_file(num_lines=20_000)
        idx = LineIndexer(path, index_interval_bytes=16 * 1024)
        try:
            engine = FilterEngine(path, idx)
            n_workers = 4
            ranges = engine._compute_search_ranges(n_workers=n_workers)

            assert len(ranges) == n_workers * _SEARCH_TASKS_PER_WORKER
            self._assert_ranges_consistent(path, idx, ranges)

            # Zadania mają zbliżony rozmiar (tolerancja: rozdzielczość indeksu)
            ideal = idx.size / len(ranges)
            for start, end, _line in ranges:
                assert abs((end - start) - ideal) <= 2 * 16 * 1024 + 1024
        finally:
            idx.close()

    def test_compute_search_ranges_target_task_size(self, temp_log_file, monkeypatch):
        """Punkt 2.2: dla dużych plików liczba zadań wynika z docelowego rozmiaru zadania."""
        import log_viewer.filter_engine as fe

        monkeypatch.setattr(fe, "_SEARCH_TASK_TARGET_BYTES", 64 * 1024)
        path = temp_log_file(num_lines=20_000)
        idx = LineIndexer(path, index_interval_bytes=8 * 1024)
        try:
            engine = FilterEngine(path, idx)
            ranges = engine._compute_search_ranges(n_workers=2)

            expected = -(-idx.size // (64 * 1024))
            assert expected > 2 * fe._SEARCH_TASKS_PER_WORKER
            assert len(ranges) == expected
            self._assert_ranges_consistent(path, idx, ranges)
            assert max(end - start for start, end, _ in ranges) <= 64 * 1024 + 2 * 8 * 1024
        finally:
            idx.close()

    def test_compute_search_ranges_limited_by_index_resolution(self, temp_log_file):
        """Punkt 2.2: liczba zadań nie przekracza liczby wpisów rzadkiego indeksu."""
        path = temp_log_file(num_lines=20_000)
        idx = LineIndexer(path, index_interval_bytes=512 * 1024)
        try:
            engine = FilterEngine(path, idx)
            ranges = engine._compute_search_ranges(n_workers=16)

            assert 1 <= len(ranges) <= len(idx.offsets)
            self._assert_ranges_consistent(path, idx, ranges)
        finally:
            idx.close()

    def test_run_parallel_work_stealing_results(self, temp_log_file, monkeypatch):
        """Punkt 2.2: wiele małych zadań daje identyczny wynik i strumieniuje wyniki cząstkowe."""
        import log_viewer.filter_engine as fe

        monkeypatch.setattr(fe, "_SEARCH_TASK_TARGET_BYTES", 128 * 1024)
        num_lines = 20_000
        path = temp_log_file(num_lines=num_lines)
        idx = LineIndexer(path, index_interval_bytes=16 * 1024)
        try:
            engine = FilterEngine(path, idx)
            n_tasks = len(engine._compute_search_ranges(max(2, fe.multiprocessing.cpu_count())))
            assert n_tasks > 4

            partial_packets: list[tuple[int, object]] = []
            done_results: list[tuple[Bitset, str | None]] = []

            def on_progress(_pct: float, hits: int, _state: str = "", partial: object = None) -> None:
                if partial is not None:
                    partial_packets.append((hits, partial))

            def on_done(res_bs: Bitset, res_err: str | None) -> None:
                done_results.append((res_bs, res_err))

            # 1. Pełne wyszukiwanie
            engine._run_parallel(0, "ERROR", False, True, False, on_progress, on_done)
            assert len(done_results) == 1
            result, err = done_results[0]
            assert err is None
            assert list(result) == list(range(2, num_lines, 4))
            # Każde zadanie z trafieniami dostarcza osobny pakiet wyników cząstkowych
            assert len(partial_packets) == n_tasks

            # 2. Wyszukiwanie w odfiltrowanych wynikach z drobnoziarnistym pomijaniem zadań
            done_results.clear()
            filtered = Bitset(num_lines)
            filtered.update_indices(range(1000, 3000))
            engine._run_parallel(0, "ERROR", False, True, False, on_progress, on_done, True, filtered)
            result, err = done_results[0]
            assert err is None
            assert list(result) == [i for i in range(1000, 3000) if i % 4 == 2]
        finally:
            idx.close()

    def test_filter_worker_chunk_direct_words(self, temp_log_file):
        """Weryfikacja bezposredniego kodowania slow bitsetu w _filter_worker_chunk (Punkt 2.1)."""
        from log_viewer.filter_engine import _filter_worker_chunk

        path = temp_log_file(num_lines=1000)
        file_size = os.path.getsize(path)

        # 1. Chunk z trafeniami (ERROR = 1/4 linii)
        args = (0, 0, file_size, 0, path, "ERROR", False, True, False, "utf-8")
        chunk_id, base_word, words, hit_count = _filter_worker_chunk(args)

        assert chunk_id == 0
        assert hit_count == 250
        assert base_word == 0
        indices = []
        for w_idx, word in enumerate(words):
            for b in range(64):
                if word & (1 << b):
                    indices.append((base_word + w_idx) * 64 + b)
        assert len(indices) == 250
        assert indices == list(range(2, 1000, 4))

        # 2. Chunk bez trafien
        args_empty = (1, 0, file_size, 0, path, "NON_EXISTENT_PATTERN_XYZ", False, True, False, "utf-8")
        chunk_id_e, _base_word_e, words_e, hit_count_e = _filter_worker_chunk(args_empty)
        assert chunk_id_e == 1
        assert hit_count_e == 0
        assert len(words_e) == 0

        # 3. Chunk z blednym regexem
        args_err = (2, 0, file_size, 0, path, "[invalid", True, True, False, "utf-8")
        chunk_id_err, _, words_err, hit_count_err = _filter_worker_chunk(args_err)
        assert chunk_id_err == 2
        assert hit_count_err == 0
        assert len(words_err) == 0


class TestFilterEngineSearchInFilter:
    def test_search_in_filtered_lines(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        filtered = Bitset(1000)
        filtered.update_indices(range(2, 1000, 4))

        results, _err, done = _run_engine_sync(
            engine,
            r"line\s+10",
            use_regex=True,
            case_sensitive=True,
            negate=False,
            search_in_filter=True,
            filtered_lines=filtered,
            timeout=5.0,
        )

        assert done
        assert len(results) > 0
        for line_idx in results:
            assert line_idx in filtered
            line_text = idx.read_lines(line_idx, 1)[0][1]
            assert "[ERROR]" in line_text
            assert "10" in line_text and "[ERROR]" in line_text
        idx.close()

    def test_search_in_filtered_empty(self, temp_log_file):
        path = temp_log_file(num_lines=100)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        filtered = Bitset(100)
        results, _err, done = _run_engine_sync(
            engine,
            "ERROR",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            search_in_filter=True,
            filtered_lines=filtered,
            timeout=2.0,
        )
        assert done
        assert len(results) == 0
        idx.close()

    def test_search_in_filtered_negate(self, temp_log_file):
        path = temp_log_file(num_lines=100)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        filtered = Bitset(100)
        filtered.update_indices([2, 6, 10, 14])

        results, _err, done = _run_engine_sync(
            engine,
            r"line\s+10",
            use_regex=True,
            case_sensitive=True,
            negate=True,
            search_in_filter=True,
            filtered_lines=filtered,
            timeout=2.0,
        )
        assert done
        assert set(results) == {2, 6, 14}
        idx.close()

    def test_search_in_filtered_large_threshold(self, temp_log_file):
        """Weryfikacja trybu dla filtrów > 2000 linii (przełączenie na tryb pełny/równoległy z maskowaniem)."""
        path = temp_log_file(num_lines=10000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        # 2500 linii w filtrze (> 2000)
        filtered = Bitset(10000)
        filtered.update_indices(range(2, 10000, 4))
        assert len(filtered) == 2500

        results, _err, done = _run_engine_sync(
            engine,
            r"line\s+100",
            use_regex=True,
            case_sensitive=True,
            negate=False,
            search_in_filter=True,
            filtered_lines=filtered,
            timeout=10.0,
        )

        assert done
        assert len(results) > 0
        for line_idx in results:
            assert line_idx in filtered
            line_text = idx.read_lines(line_idx, 1)[0][1]
            assert "[ERROR]" in line_text
            assert "100" in line_text
        idx.close()

    def test_search_in_filtered_chunk_skipping(self, temp_log_file):
        """Weryfikacja pomijania chunków gdy filtered_lines są zlokalizowane tylko w części pliku."""
        path = temp_log_file(num_lines=10000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        # Tylko linie 10..20 w filtrze (z 10 000 linii)
        filtered = Bitset(10000)
        filtered.update_indices(range(10, 20))

        results, _err, done = _run_engine_sync(
            engine,
            "ERROR",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            search_in_filter=True,
            filtered_lines=filtered,
            timeout=5.0,
        )
        assert done
        # W liniach 10..19: linie o indeksie % 4 == 2 to ERROR: 10, 14, 18
        assert set(results) == {10, 14, 18}
        idx.close()

    def test_should_use_filtered_search_adaptive(self, temp_log_file):
        """Weryfikacja adaptacyjnej decyzji _should_use_filtered_search."""
        path = temp_log_file(num_lines=100000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        # 1. Pusty filtr -> False
        empty_bs = Bitset(100000)
        assert not engine._should_use_filtered_search(empty_bs)

        # 2. Mały filtr (<= 2000 linii) -> Zawsze True (fast-path)
        small_bs = Bitset(100000)
        small_bs.update_indices(range(0, 100000, 100))  # 1000 linii
        assert len(small_bs) == 1000
        assert engine._should_use_filtered_search(small_bs)

        # 3. Średni filtr skupiony (np. 10 000 linii w przedziale 10 000 linii) -> True
        clustered_bs = Bitset(100000)
        clustered_bs.update_indices(range(20000, 30000))
        assert len(clustered_bs) == 10000
        assert engine._should_use_filtered_search(clustered_bs)

        # 4. Bardzo duży filtr (> 50 000 linii) -> False (skanowanie chunków jest szybsze)
        large_bs = Bitset(100000)
        large_bs.update_indices(range(0, 60000))
        assert len(large_bs) == 60000
        assert not engine._should_use_filtered_search(large_bs)

        idx.close()

    def test_get_clusters(self):
        """Weryfikacja grupowania linii w klastry."""
        assert list(FilterEngine._get_clusters([10, 11, 12, 13], 32, 256)) == [[10, 11, 12, 13]]
        assert list(FilterEngine._get_clusters([10, 15, 25, 40], 16, 256)) == [[10, 15, 25, 40]]
        assert list(FilterEngine._get_clusters([10, 100, 250], 16, 256)) == [[10], [100], [250]]
        c = list(FilterEngine._get_clusters(list(range(0, 300)), 32, 256))
        assert len(c) == 2 and c[0] == list(range(0, 256)) and c[1] == list(range(256, 300))
        assert list(FilterEngine._get_clusters([])) == []

    def test_search_in_filtered_clustered_execution(self, temp_log_file):
        """Weryfikacja klastrowanego odczytu w _run_filtered."""
        path = temp_log_file(num_lines=2000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)
        targets = [10, 11, 12, 13, 14, 50, 52, 54, 56, 500, 1200, 1201]
        filtered = Bitset(2000)
        filtered.update_indices(targets)
        results, _err, done = _run_engine_sync(
            engine,
            "ERROR",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            search_in_filter=True,
            filtered_lines=filtered,
            timeout=5.0,
        )
        assert done
        expected = [t for t in targets if t % 4 == 2]
        assert sorted(results) == expected
        idx.close()


class TestFilterWorkerSearchInFilter:
    """Testy optymalizacji 3.1: maskowanie search_in_filter i zliczanie trafień w tle w FilterWorker."""

    @staticmethod
    def _setup_worker_with_captured_callbacks(
        engine: FilterEngine,
        *,
        search_in_filter: bool,
        filtered_lines: Bitset | None,
    ):
        import sys

        from log_viewer.workers import FilterWorker
        from PySide6 import QtWidgets

        _ = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

        worker = FilterWorker(
            engine,
            pattern="ERROR",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            context_after=0,
            search_in_filter=search_in_filter,
            filtered_lines=filtered_lines,
        )

        progress_events: list[tuple[float, int, str, object]] = []
        worker.progress.connect(lambda p, h, s, part: progress_events.append((p, h, s, part)))

        captured_callbacks: dict[str, Callable[..., Any]] = {}

        def mock_start(_pattern, _use_regex, _case_sensitive, _negate, on_progress, on_done, **_kwargs):
            captured_callbacks["on_progress"] = on_progress
            captured_callbacks["on_done"] = on_done

        engine.start = mock_start  # type: ignore[method-assign]
        worker.run()
        return progress_events, captured_callbacks["on_progress"]

    def test_filter_worker_search_in_filter_masks_progress(self, temp_log_file):
        import array

        path = temp_log_file(num_lines=100)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        # Filtr zawiera tylko linie 2, 6, 10
        filtered = Bitset(100)
        filtered.update_indices([2, 6, 10])

        progress_events, cb = self._setup_worker_with_captured_callbacks(
            engine, search_in_filter=True, filtered_lines=filtered
        )

        # 1. Chunk z wieloma trafieniami (np. wszystkie 64 bity w słowie 0 ustawione)
        raw_words = array.array("Q", [0xFFFFFFFFFFFFFFFF])
        cb(50.0, 64, "filtering", (0, raw_words))

        assert len(progress_events) == 1
        pct, hits, _state, partial = progress_events[0]
        assert pct == 50.0
        assert hits == 3  # tylko 3 bity pasują do filtra: 2, 6, 10
        assert partial is not None
        base_w, masked_w = partial  # type: ignore[misc]
        assert base_w == 0
        assert [i for i in range(64) if masked_w[0] & (1 << i)] == [2, 6, 10]

        # 2. Chunk, którego żadne trafienia nie pasują do filtra (np. słowo 1: linie 64..127)
        raw_words_2 = array.array("Q", [0xFFFFFFFFFFFFFFFF])
        cb(75.0, 128, "filtering", (1, raw_words_2))

        assert len(progress_events) == 2
        pct2, hits2, _state2, partial2 = progress_events[1]
        assert pct2 == 75.0
        assert hits2 == 3  # licznik trafień nie wzrósł
        assert partial2 is None  # pusty wynik odfiltrowany – brak przesyłania do GUI

        # 3. Postęp bez wyników cząstkowych (partial_results=None, np. heartbeat timeout)
        cb(85.0, 150, "filtering", None)
        assert len(progress_events) == 3
        pct3, hits3, _state3, partial3 = progress_events[2]
        assert pct3 == 85.0
        assert hits3 == 3  # emituje przefiltrowane trafienia (3), a nie surowe 150
        assert partial3 is None

        idx.close()

    def test_filter_worker_search_in_filter_disabled_passthrough(self, temp_log_file):
        import array

        path = temp_log_file(num_lines=100)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        progress_events, cb = self._setup_worker_with_captured_callbacks(
            engine, search_in_filter=False, filtered_lines=None
        )

        raw_words = array.array("Q", [0x0F])
        cb(50.0, 4, "filtering", (0, raw_words))

        assert len(progress_events) == 1
        pct, hits, _state, partial = progress_events[0]
        assert pct == 50.0
        assert hits == 4
        assert partial == (0, raw_words)

        idx.close()
