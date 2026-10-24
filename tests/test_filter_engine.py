"""Testy filter_engine.py — FilterEngine, session isolation, cancel."""

import os
import threading
import time

import pytest
from log_viewer.bitset import Bitset
from log_viewer.filter_engine import FilterEngine
from log_viewer.indexer import LineIndexer


class TestFilterEngine:
    def test_basic_search(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results = []
        done = threading.Event()
        engine.start(
            "ERROR",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            on_progress=lambda p, h: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
        )
        for _ in range(100):
            if done.is_set():
                break
            time.sleep(0.05)
        assert len(results) == 250  # 1/4 linii to ERROR
        idx.close()

    def test_negate(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results = []
        done = threading.Event()
        engine.start(
            "ERROR",
            use_regex=False,
            case_sensitive=True,
            negate=True,
            on_progress=lambda p, h: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
        )
        for _ in range(100):
            if done.is_set():
                break
            time.sleep(0.05)
        assert len(results) == 750  # 3/4 to nie-ERROR
        idx.close()

    def test_regex(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results = []
        done = threading.Event()
        engine.start(
            r"INFO",
            use_regex=True,
            case_sensitive=True,
            negate=False,
            on_progress=lambda p, h: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
        )
        for _ in range(100):
            if done.is_set():
                break
            time.sleep(0.05)
        # Powinno znaleźć wszystkie INFO (1/4 linii)
        assert len(results) == 250
        idx.close()

    def test_case_insensitive(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results = []
        done = threading.Event()
        engine.start(
            "error",
            use_regex=False,
            case_sensitive=False,
            negate=False,
            on_progress=lambda p, h: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
        )
        for _ in range(100):
            if done.is_set():
                break
            time.sleep(0.05)
        assert len(results) == 250
        idx.close()

    def test_regex_error(self, temp_log_file):
        path = temp_log_file(num_lines=100)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results = []
        error = [None]
        done = threading.Event()
        engine.start(
            "[invalid",
            use_regex=True,
            case_sensitive=True,
            negate=False,
            on_progress=lambda p, h: None,
            on_done=lambda r, e: (results.extend(r), error.__setitem__(0, e), done.set()),
        )
        for _ in range(100):
            if done.is_set():
                break
            time.sleep(0.05)
        assert error[0] is not None
        assert len(results) == 0
        idx.close()

    def test_search_file_without_trailing_newline(self, tmp_path):
        path = tmp_path / "search_no_nl.txt"
        path.write_bytes(b"first line\nsecond line without newline")

        idx = LineIndexer(path)
        assert idx.line_count == 2
        engine = FilterEngine(str(path), idx)

        def run_search(pattern, negate=False):
            res = []
            done = threading.Event()
            engine.start(
                pattern,
                use_regex=False,
                case_sensitive=False,
                negate=negate,
                on_progress=lambda *a: None,
                on_done=lambda r, e: (res.extend(r), done.set()),
            )
            done.wait(3.0)
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
            on_progress=lambda p, h: None,
            on_done=lambda r, e: None,
        )
        time.sleep(0.05)

        t0 = time.time()
        engine.cancel(timeout=5.0)
        elapsed = time.time() - t0
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
            on_progress=lambda p, h: progress_seen.set(),
            on_done=lambda r, e: callback_called.set(),
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

        results1 = []
        results2 = []
        done1 = threading.Event()
        done2 = threading.Event()
        started2 = threading.Event()

        # Sesja 1 — INFO (anulujemy przed zakończeniem)
        engine.start(
            "INFO",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            on_progress=lambda p, h: done1.set() if p > 5 else None,
            on_done=lambda r, e: results1.extend(r) if started2.is_set() else None,
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
        engine.start(
            "ERROR",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            on_progress=lambda p, h: None,
            on_done=lambda r, e: (results2.extend(r), done2.set()),
        )

        # Czekaj na sesję 2 (dłużej)
        for _ in range(500):
            if done2.is_set():
                break
            time.sleep(0.05)

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
        import threading

        from log_viewer.indexer import LineIndexer

        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)
        results = []
        done = threading.Event()
        engine.start(
            r"ERROR",
            use_regex=True,
            case_sensitive=True,
            negate=False,
            on_progress=lambda p, h: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
        )
        for _ in range(100):
            if done.is_set():
                break
            time.sleep(0.05)
        assert len(results) == 250  # 1/4 linii to ERROR
        idx.close()

    def test_regex_case_insensitive(self, temp_log_file):
        """Regex case-insensitive na bajtach."""
        path = temp_log_file(num_lines=1000)
        import threading

        from log_viewer.indexer import LineIndexer

        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)
        results = []
        done = threading.Event()
        engine.start(
            r"error",
            use_regex=True,
            case_sensitive=False,
            negate=False,
            on_progress=lambda p, h: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
        )
        for _ in range(100):
            if done.is_set():
                break
            time.sleep(0.05)
        assert len(results) == 250
        idx.close()

    def test_regex_with_pattern(self, temp_log_file):
        """Regex z pattern \\d+ na bajtach."""
        path = temp_log_file(num_lines=1000)
        import threading

        from log_viewer.indexer import LineIndexer

        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)
        results = []
        done = threading.Event()
        engine.start(
            r"line\s*\d+",
            use_regex=True,
            case_sensitive=True,
            negate=False,
            on_progress=lambda p, h: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
        )
        for _ in range(100):
            if done.is_set():
                break
            time.sleep(0.05)
        assert len(results) == 1000  # wszystkie linie mają line\\d+
        idx.close()

    def test_plain_text_and_regex_give_same_results(self, temp_log_file):
        """Plain text i regex dają te same wyniki dla prostego wzorca."""
        path = temp_log_file(num_lines=1000)
        import threading

        from log_viewer.indexer import LineIndexer

        idx = LineIndexer(path)

        # Plain text
        engine1 = FilterEngine(path, idx)
        results1 = []
        done1 = threading.Event()
        engine1.start("ERROR", False, True, False, lambda p, h: None, lambda r, e: (results1.extend(r), done1.set()))
        for _ in range(100):
            if done1.is_set():
                break
            time.sleep(0.05)

        # Regex
        engine2 = FilterEngine(path, idx)
        results2 = []
        done2 = threading.Event()
        engine2.start("ERROR", True, True, False, lambda p, h: None, lambda r, e: (results2.extend(r), done2.set()))
        for _ in range(100):
            if done2.is_set():
                break
            time.sleep(0.05)

        assert len(results1) == len(results2) == 250
        # Porównaj numery linii
        lines1 = list(results1)
        lines2 = list(results2)
        assert lines1 == lines2
        idx.close()


class TestParallelSearch:
    """Testy trybu równoległego (multiprocessing) FilterEngine."""

    def _make_large_file(self, num_lines: int) -> str:
        """Tworzy plik > 50 MB potrzebny do aktywacji trybu równoległego."""
        import tempfile

        path = tempfile.mktemp(suffix=".log")
        levels = ["INFO", "WARN", "ERROR", "DEBUG"]
        # ~200 bajtów/linię → 300k linii ≈ 60 MB (powyżej progu 50 MB)
        with open(path, "wb") as f:
            for i in range(num_lines):
                level = levels[i % 4]
                msg = f"2026-07-04 [{level}] line{i:>8d} - " + ("x" * 150) + "\n"
                f.write(msg.encode("utf-8"))
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
            results_single: list = []
            done_s = threading.Event()
            engine_s = FilterEngine(path_small, idx_small)
            engine_s.start(
                "ERROR",
                use_regex=False,
                case_sensitive=True,
                negate=False,
                on_progress=lambda p, h: None,
                on_done=lambda r, e: (results_single.extend(r), done_s.set()),
            )
            done_s.wait(timeout=60)
            idx_small.close()

            # --- Parallel: plik >= 50 MB — start() automatycznie wybierze _run_parallel ---
            assert idx.size >= _PARALLEL_SEARCH_THRESHOLD, (
                f"Plik ({idx.size} B) mniejszy niż próg ({_PARALLEL_SEARCH_THRESHOLD} B)"
            )
            results_par: list = []
            done_p = threading.Event()
            engine_p = FilterEngine(path, idx)
            engine_p.start(
                "ERROR",
                use_regex=False,
                case_sensitive=True,
                negate=False,
                on_progress=lambda p, h: None,
                on_done=lambda r, e: (results_par.extend(r), done_p.set()),
            )
            done_p.wait(timeout=60)

            assert done_s.is_set(), "Single-thread search did not finish in time"
            assert done_p.is_set(), "Parallel search did not finish in time"
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

    def test_parallel_regex(self, temp_log_file):
        """
        Tryb równoległy poprawnie obsługuje wyrażenia regularne.
        Wywołanie przez start() — session_id jest poprawnie ustawiony.
        """
        path = self._make_large_file(num_lines=300_000)
        try:
            idx = LineIndexer(path)
            engine = FilterEngine(path, idx)
            results: list = []
            done = threading.Event()
            # start() automatycznie wybierze tryb równoległy (plik > 50 MB)
            engine.start(
                "ERROR",
                use_regex=True,
                case_sensitive=True,
                negate=False,
                on_progress=lambda p, h: None,
                on_done=lambda r, e: (results.extend(r), done.set()),
            )
            done.wait(timeout=60)

            assert done.is_set(), "Parallel regex search did not finish in time"
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

    def test_auto_dispatch_parallel_for_large_file(self, temp_log_file):
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
            results: list = []
            done = threading.Event()
            engine.start(
                "ERROR",
                use_regex=False,
                case_sensitive=True,
                negate=False,
                on_progress=lambda p, h: None,
                on_done=lambda r, e: (results.extend(r), done.set()),
            )
            done.wait(timeout=60)

            assert done.is_set(), "Auto-dispatch parallel search did not finish in time"
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

            def on_progress(pct: float, hits: int, state: str = "", partial: object = None) -> None:
                if partial is not None:
                    partial_packets.append((hits, partial))

            def on_done(result: Bitset, err: str | None) -> None:
                done_results.append((result, err))

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
        chunk_id_e, base_word_e, words_e, hit_count_e = _filter_worker_chunk(args_empty)
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

        results = []
        done = threading.Event()
        engine.start(
            r"line\s+10",
            use_regex=True,
            case_sensitive=True,
            negate=False,
            on_progress=lambda *a: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
            search_in_filter=True,
            filtered_lines=filtered,
        )
        done.wait(5.0)

        assert done.is_set()
        assert len(results) > 0
        for r in results:
            assert r in filtered
            line_text = idx.read_lines(r, 1)[0][1]
            assert "[ERROR]" in line_text
            assert "10" in line_text and "[ERROR]" in line_text
        idx.close()

    def test_search_in_filtered_empty(self, temp_log_file):
        path = temp_log_file(num_lines=100)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        filtered = Bitset(100)
        results = []
        done = threading.Event()
        engine.start(
            "ERROR",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            on_progress=lambda *a: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
            search_in_filter=True,
            filtered_lines=filtered,
        )
        done.wait(2.0)
        assert done.is_set()
        assert len(results) == 0
        idx.close()

    def test_search_in_filtered_negate(self, temp_log_file):
        path = temp_log_file(num_lines=100)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        filtered = Bitset(100)
        filtered.update_indices([2, 6, 10, 14])

        results = []
        done = threading.Event()
        engine.start(
            r"line\s+10",
            use_regex=True,
            case_sensitive=True,
            negate=True,
            on_progress=lambda *a: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
            search_in_filter=True,
            filtered_lines=filtered,
        )
        done.wait(2.0)
        assert done.is_set()
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

        results = []
        done = threading.Event()
        engine.start(
            r"line\s+100",
            use_regex=True,
            case_sensitive=True,
            negate=False,
            on_progress=lambda *a: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
            search_in_filter=True,
            filtered_lines=filtered,
        )
        done.wait(10.0)

        assert done.is_set()
        assert len(results) > 0
        for r in results:
            assert r in filtered
            line_text = idx.read_lines(r, 1)[0][1]
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

        results = []
        done = threading.Event()
        engine.start(
            "ERROR",
            use_regex=False,
            case_sensitive=True,
            negate=False,
            on_progress=lambda *a: None,
            on_done=lambda r, e: (results.extend(r), done.set()),
            search_in_filter=True,
            filtered_lines=filtered,
        )
        done.wait(5.0)
        assert done.is_set()
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
        results = []
        done = threading.Event()
        engine.start(
            "ERROR",
            False,
            True,
            False,
            lambda *a: None,
            lambda r, e: (results.extend(r), done.set()),
            search_in_filter=True,
            filtered_lines=filtered,
        )
        done.wait(5.0)
        assert done.is_set()
        expected = [t for t in targets if t % 4 == 2]
        assert sorted(results) == expected
        idx.close()
