"""Testy filter_engine.py — FilterEngine, session isolation, cancel."""
import os
import time
import threading
import pytest
from log_reader.indexer import LineIndexer
from log_reader.filter_engine import FilterEngine


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
        from log_reader.indexer import LineIndexer
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
        from log_reader.indexer import LineIndexer
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
        from log_reader.indexer import LineIndexer
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
        from log_reader.indexer import LineIndexer
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
        lines1 = [r[0] for r in results1]
        lines2 = [r[0] for r in results2]
        assert lines1 == lines2
        idx.close()
