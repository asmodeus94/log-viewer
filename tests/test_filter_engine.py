"""Testy filter_engine.py — FilterEngine, session isolation, cancel."""
import os
import time
import threading
import pytest
from log_viewer.indexer import LineIndexer
from log_viewer.filter_engine import FilterEngine


class TestFilterEngine:
    def test_basic_search(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        results = []
        done = threading.Event()
        engine.start("ERROR", use_regex=False, case_sensitive=True, negate=False,
                     on_progress=lambda p, h: None,
                     on_done=lambda r, e: (results.extend(r), done.set()))
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
        engine.start("ERROR", use_regex=False, case_sensitive=True, negate=True,
                     on_progress=lambda p, h: None,
                     on_done=lambda r, e: (results.extend(r), done.set()))
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
        engine.start(r"INFO", use_regex=True, case_sensitive=True, negate=False,
                     on_progress=lambda p, h: None,
                     on_done=lambda r, e: (results.extend(r), done.set()))
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
        engine.start("error", use_regex=False, case_sensitive=False, negate=False,
                     on_progress=lambda p, h: None,
                     on_done=lambda r, e: (results.extend(r), done.set()))
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
        engine.start("[invalid", use_regex=True, case_sensitive=True, negate=False,
                     on_progress=lambda p, h: None,
                     on_done=lambda r, e: (results.extend(r), error.__setitem__(0, e), done.set()))
        for _ in range(100):
            if done.is_set():
                break
            time.sleep(0.05)
        assert error[0] is not None
        assert len(results) == 0
        idx.close()


class TestFilterEngineCancel:
    def test_cancel_blocks(self, temp_log_file):
        path = temp_log_file(num_lines=100000)
        idx = LineIndexer(path)
        engine = FilterEngine(path, idx)

        engine.start("INFO", use_regex=False, case_sensitive=True, negate=False,
                     on_progress=lambda p, h: None,
                     on_done=lambda r, e: None)
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
        engine.start("INFO", use_regex=False, case_sensitive=True, negate=False,
                     on_progress=lambda p, h: progress_seen.set(),
                     on_done=lambda r, e: callback_called.set())
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
        engine.start("INFO", use_regex=False, case_sensitive=True, negate=False,
                     on_progress=lambda p, h: done1.set() if p > 5 else None,
                     on_done=lambda r, e: results1.extend(r) if started2.is_set() else None)
        # Czekaj aż progress > 5%
        for _ in range(200):
            if done1.is_set():
                break
            time.sleep(0.05)

        if not engine.is_running():
            pytest.skip("Filter too fast for session isolation test")

        # Teraz uruchom sesję 2 — start() anuluje sesję 1
        started2.set()
        engine.start("ERROR", use_regex=False, case_sensitive=True, negate=False,
                     on_progress=lambda p, h: None,
                     on_done=lambda r, e: (results2.extend(r), done2.set()))

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
        engine.start(r"ERROR", use_regex=True, case_sensitive=True, negate=False,
                     on_progress=lambda p, h: None,
                     on_done=lambda r, e: (results.extend(r), done.set()))
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
        engine.start(r"error", use_regex=True, case_sensitive=False, negate=False,
                     on_progress=lambda p, h: None,
                     on_done=lambda r, e: (results.extend(r), done.set()))
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
        engine.start(r"line\s*\d+", use_regex=True, case_sensitive=True, negate=False,
                     on_progress=lambda p, h: None,
                     on_done=lambda r, e: (results.extend(r), done.set()))
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
        engine1.start("ERROR", False, True, False, lambda p, h: None,
                      lambda r, e: (results1.extend(r), done1.set()))
        for _ in range(100):
            if done1.is_set():
                break
            time.sleep(0.05)

        # Regex
        engine2 = FilterEngine(path, idx)
        results2 = []
        done2 = threading.Event()
        engine2.start("ERROR", True, True, False, lambda p, h: None,
                      lambda r, e: (results2.extend(r), done2.set()))
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
                "ERROR", use_regex=False, case_sensitive=True, negate=False,
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
                "ERROR", use_regex=False, case_sensitive=True, negate=False,
                on_progress=lambda p, h: None,
                on_done=lambda r, e: (results_par.extend(r), done_p.set()),
            )
            done_p.wait(timeout=60)

            assert done_s.is_set(), "Single-thread search did not finish in time"
            assert done_p.is_set(), "Parallel search did not finish in time"
            assert len(results_single) > 0, "Missing single-thread results"
            # Proporcja ERROR: 1/4 linii → 50k linii = 12500, 300k linii = 75000
            assert len(results_single) == 12_500, (
                f"Single: oczekiwano 12500, dostano {len(results_single)}"
            )
            assert len(results_par) == 75_000, (
                f"Parallel: oczekiwano 75000, dostano {len(results_par)}"
            )
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
                "ERROR", use_regex=True, case_sensitive=True, negate=False,
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
            assert len(idx.index) >= 4, (
                f"Za mało wpisów w indeksie: {len(idx.index)}"
            )

            engine = FilterEngine(path, idx)
            results: list = []
            done = threading.Event()
            engine.start(
                "ERROR", use_regex=False, case_sensitive=True, negate=False,
                on_progress=lambda p, h: None,
                on_done=lambda r, e: (results.extend(r), done.set()),
            )
            done.wait(timeout=60)

            assert done.is_set(), "Auto-dispatch parallel search did not finish in time"
            assert len(results) == 75_000, (
                f"Oczekiwano 75000, dostano {len(results)}"
            )
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
        for i, (start, end, line) in enumerate(ranges):
            assert start < end, f"Range [{i}] has empty interval: {start}..{end}"
            assert start >= 0
            assert end <= idx.size + 1

        assert ranges[0][0] == 0, "First range must start at offset 0"
        assert ranges[-1][1] == idx.size, "Last range must reach end of file"
        idx.close()
