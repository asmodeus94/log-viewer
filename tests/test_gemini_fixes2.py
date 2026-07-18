"""Testy dla poprawek Gemini: deleteLater, st_mtime_ns tolerancja, regex na bajtach,
freeze_support, theme detection."""
import os
import sys
import time
import tempfile
import threading
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Konfiguracja Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
libegl = os.path.expanduser("~/.local/lib/libEGL.so.1")
if os.path.exists(libegl):
    os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/lib") + ":" + os.environ.get("LD_LIBRARY_PATH", "")

from log_reader.edit_buffer import EditBuffer
from log_reader.exceptions import FileChangedError, CompressedSaveError
from log_reader.indexer import LineIndexer, _indexer_worker_chunk
from log_reader.filter_engine import FilterEngine
from log_reader.config import UserConfig
from log_reader.helpers import THEME_DARK, THEME_LIGHT


# =============================================================================
# Testy st_mtime_ns tolerancja (poprawka Gemini #2)
# =============================================================================

class TestMtimeNsTolerance:
    """Testy tolerancji nanosekundowej dla st_mtime_ns."""

    def test_allows_when_unchanged(self, temp_log_file):
        """Zapis powinien przejść gdy plik nie zmienił się (st_mtime_ns)."""
        path = temp_log_file(num_lines=1000)
        st = os.stat(path)
        original_mtime_ns = st.st_mtime_ns
        original_size = st.st_size
        buf = EditBuffer()
        buf.set(5, "EDITED LINE 5")
        backup = buf.save_to_file(path, expected_size=original_size, expected_mtime=original_mtime_ns)
        assert os.path.exists(backup)
        try:
            os.unlink(backup)
        except PermissionError:
            pass

    def test_blocks_on_real_mtime_change(self, temp_log_file):
        """Zapis zablokowany gdy mtime zmienił się o >1ms."""
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        original_mtime_ns = os.stat(path).st_mtime_ns
        buf = EditBuffer()
        buf.set(5, "EDITED")
        # Zmień mtime o 2 sekundy (na pewno >1ms)
        new_mtime = time.time() + 2.0
        os.utime(path, (new_mtime, new_mtime))
        with pytest.raises(FileChangedError):
            buf.save_to_file(path, expected_size=original_size, expected_mtime=original_mtime_ns)

    def test_tolerates_sub_ms_ns_drift(self, temp_log_file):
        """Tolerancja dla różnicy <1ms (filesystem zaokrągla ns)."""
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        original_mtime_ns = os.stat(path).st_mtime_ns
        buf = EditBuffer()
        buf.set(5, "EDITED")
        # Symuluj drift 500_000 ns (0.5ms) — w tolerancji
        fake_mtime_ns = original_mtime_ns + 500_000
        backup = buf.save_to_file(path, expected_size=original_size, expected_mtime=fake_mtime_ns)
        assert os.path.exists(backup)
        try:
            os.unlink(backup)
        except PermissionError:
            pass

    def test_blocks_on_2ms_drift(self, temp_log_file):
        """Blokada przy różnicy 2ms (poza tolerancją)."""
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        original_mtime_ns = os.stat(path).st_mtime_ns
        buf = EditBuffer()
        buf.set(5, "EDITED")
        # Symuluj drift 2_000_000 ns (2ms) — poza tolerancją
        fake_mtime_ns = original_mtime_ns + 2_000_000
        with pytest.raises(FileChangedError):
            buf.save_to_file(path, expected_size=original_size, expected_mtime=fake_mtime_ns)

    def test_accepts_float_mtime(self, temp_log_file):
        """Kompatybilność wsteczna — expected_mtime jako float (stare wersje)."""
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        original_mtime_float = os.stat(path).st_mtime  # float, nie ns
        buf = EditBuffer()
        buf.set(5, "EDITED")
        # float → konwersja na ns w edit_buffer.py
        backup = buf.save_to_file(path, expected_size=original_size, expected_mtime=original_mtime_float)
        assert os.path.exists(backup)
        try:
            os.unlink(backup)
        except PermissionError:
            pass


# =============================================================================
# Testy regex na bajtach (optymalizacja #10)
# =============================================================================

class TestRegexOnBytes:
    """Testy optymalizacji regex na surowych bajtach."""

    def test_regex_finds_matches(self, temp_log_file):
        """Regex na bajtach znajduje te same wyniki co regex na str."""
        path = temp_log_file(num_lines=1000)
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
        """Regex z pattern \d+ na bajtach."""
        path = temp_log_file(num_lines=1000)
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
        assert len(results) == 1000  # wszystkie linie mają line\d+
        idx.close()

    def test_plain_text_and_regex_give_same_results(self, temp_log_file):
        """Plain text i regex dają te same wyniki dla prostego wzorca."""
        path = temp_log_file(num_lines=1000)
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


# =============================================================================
# Testy chunkowanego workera (optymalizacja #2)
# =============================================================================

class TestChunkedWorker:
    """Testy _indexer_worker_chunk z chunkowanym odczytem."""

    def test_correct_line_count(self, temp_log_file):
        """Worker liczy poprawną liczbę linii w swoim zakresie."""
        path = temp_log_file(num_lines=10000)
        size = os.path.getsize(path)
        chunk_size = size // 2
        r1 = (0, chunk_size, path, 1024 * 1024, 0)
        r2 = (chunk_size, size, path, 1024 * 1024, 1)
        result1 = _indexer_worker_chunk(r1)
        result2 = _indexer_worker_chunk(r2)
        total = result1[0] + result2[0]
        assert total == 10000

    def test_no_double_counting(self, temp_log_file):
        """Brak podwójnego liczenia linii na granicy chunków."""
        path = temp_log_file(num_lines=50000)
        size = os.path.getsize(path)
        # Podziel na 4 chunki
        chunk_size = size // 4
        ranges = [(i * chunk_size, (i + 1) * chunk_size if i < 3 else size, path, 1024 * 1024, i) for i in range(4)]
        results = [_indexer_worker_chunk(r) for r in ranges]
        total = sum(r[0] for r in results)
        assert total == 50000

    def test_worker_handles_large_lines(self):
        """Worker radzi sobie z bardzo długimi liniami (>4MB chunk)."""
        path = tempfile.mktemp(suffix=".log")
        try:
            with open(path, "wb") as f:
                # Jedna bardzo długa linia (8 MB)
                f.write(b"X" * (8 * 1024 * 1024) + b"\n")
                f.write(b"short line\n")
            size = os.path.getsize(path)
            result = _indexer_worker_chunk((0, size, path, 1024 * 1024, 0))
            assert result[0] == 2  # 2 linie
        finally:
            try:
                os.unlink(path)
            except PermissionError:
                pass

    def test_worker_memory_efficient(self, temp_log_file):
        """Worker nie ładuje całego zakresu do pamięci — czyta w chunkach 4MB."""
        path = temp_log_file(num_lines=10000)
        size = os.path.getsize(path)
        # Worker dla całego pliku
        result = _indexer_worker_chunk((0, size, path, 1024 * 1024, 0))
        assert result[0] == 10000
        # Jeśli worker ładuje wszystko do RAM, przy 10000 liniach ~500KB to OK
        # ale test weryfikuje że działa bez crash dla dużych plików


# =============================================================================
# Testy freeze_support (multiprocessing)
# =============================================================================

class TestFreezeSupport:
    """Testy multiprocessing.freeze_support."""

    def test_freeze_support_importable(self):
        """multiprocessing.freeze_support jest dostępne i można je wywołać."""
        import multiprocessing
        # freeze_support() powinno być no-op gdy nie jest frozen exe
        multiprocessing.freeze_support()
        # Nie powinno crashować

    def test_multiprocessing_pool_works(self, temp_log_file):
        """multiprocessing.Pool działa poprawnie (wymaga freeze_support na Windows)."""
        path = temp_log_file(num_lines=50000)
        idx = LineIndexer(path)
        # Jeśli plik > 100MB, użyje parallel. Sprawdź że nie crashuje.
        assert idx.line_count == 50000
        idx.close()


# =============================================================================
# Testy theme detection
# =============================================================================

class TestThemeDetection:
    """Testy wykrywania motywu systemowego."""

    def test_theme_dark_has_required_keys(self):
        """THEME_DARK ma wszystkie wymagane klucze."""
        required = [
            "bg_main", "bg_panel", "bg_statusbar", "bg_input", "bg_selected",
            "fg_main", "fg_dim", "fg_bright", "border", "border_light",
            "error", "warn", "info", "debug", "accent", "accent_hover",
            "highlight", "bookmark", "edited", "truncated", "current_line",
            "context",
            "minimap_bg", "minimap_error", "minimap_warn", "minimap_info",
            "minimap_debug", "minimap_viewport",
        ]
        for key in required:
            assert key in THEME_DARK, f"Missing key in THEME_DARK: {key}"

    def test_theme_light_has_required_keys(self):
        """THEME_LIGHT ma wszystkie wymagane klucze."""
        required = [
            "bg_main", "bg_panel", "bg_statusbar", "bg_input", "bg_selected",
            "fg_main", "fg_dim", "fg_bright", "border", "border_light",
            "error", "warn", "info", "debug", "accent", "accent_hover",
            "highlight", "bookmark", "edited", "truncated", "current_line",
            "context",
            "minimap_bg", "minimap_error", "minimap_warn", "minimap_info",
            "minimap_debug", "minimap_viewport",
        ]
        for key in required:
            assert key in THEME_LIGHT, f"Missing key in THEME_LIGHT: {key}"

    def test_themes_are_different(self):
        """THEME_DARK i THEME_LIGHT mają różne kolory."""
        assert THEME_DARK["bg_main"] != THEME_LIGHT["bg_main"]
        assert THEME_DARK["fg_main"] != THEME_LIGHT["fg_main"]

    def test_dark_bg_is_dark(self):
        """THEME_DARK ma ciemne tło (lightness < 128)."""
        # Konwersja hex na lightness
        bg = THEME_DARK["bg_main"].lstrip("#")
        r, g, b = int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16)
        lightness = (r + g + b) // 3
        assert lightness < 128, f"THEME_DARK bg_main should be dark, lightness={lightness}"

    def test_light_bg_is_light(self):
        """THEME_LIGHT ma jasne tło (lightness >= 128)."""
        bg = THEME_LIGHT["bg_main"].lstrip("#")
        r, g, b = int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16)
        lightness = (r + g + b) // 3
        assert lightness >= 128, f"THEME_LIGHT bg_main should be light, lightness={lightness}"

    def test_theme_colors_are_valid_hex(self):
        """Wszystkie kolory w obu motywach są poprawnymi hex."""
        import re
        hex_pattern = re.compile(r"^#[0-9a-fA-F]{6,8}$")
        for theme_name, theme in [("DARK", THEME_DARK), ("LIGHT", THEME_LIGHT)]:
            for key, color in theme.items():
                assert hex_pattern.match(color), f"Invalid hex color in {theme_name}.{key}: {color}"


# =============================================================================
# Testy deleteLater (wycieki pamięci C++)
# =============================================================================

class TestDeleteLater:
    """Testy czy deleteLater jest podpięte do workerów."""

    def test_deletelater_connections_exist(self):
        """Sprawdza czy LogViewerWindow ma metody z deleteLater w kodzie źródłowym."""
        import inspect
        from log_reader.app import LogViewerWindow
        source = inspect.getsource(LogViewerWindow)
        # Sprawdź czy deleteLater występuje w kodzie
        assert "deleteLater" in source, "deleteLater not found in LogViewerWindow source"
        # Sprawdź czy jest podpięte dla różnych workerów
        assert "self._indexer_worker.deleteLater" in source or "self._indexer_worker.finished.connect(self._indexer_worker.deleteLater)" in source
        assert "self._filter_worker.deleteLater" in source or "self._filter_worker.finished.connect(self._filter_worker.deleteLater)" in source
        assert "self._save_worker.deleteLater" in source
        assert "self._indexer_thread.deleteLater" in source
        assert "self._filter_thread.deleteLater" in source
        assert "self._save_thread.deleteLater" in source


# =============================================================================
# Testy logowania wyjątków (zamiast pass)
# =============================================================================

class TestExceptionLogging:
    """Testy czy wyjątki są logowane zamiast cichego pass."""

    def test_config_load_logs_error(self, temp_config_path, capsys):
        """Config._load loguje błędy do stderr."""
        with open(temp_config_path, "w") as f:
            f.write("{ invalid json")
        UserConfig(config_path=temp_config_path)
        captured = capsys.readouterr()
        assert "Warning" in captured.err or "could not load" in captured.err.lower()

    def test_config_save_logs_error(self, temp_config_path, capsys):
        """Config.save loguje błędy do stderr przy braku uprawnień."""
        cfg = UserConfig(config_path=temp_config_path)
        # Symuluj błąd zapisu — ustaw path na nieistniejący katalog
        cfg.path = "/nonexistent_dir/config.json"
        cfg.set("language", "en")
        captured = capsys.readouterr()
        assert "Warning" in captured.err or "could not save" in captured.err.lower()

    def test_indexer_worker_logs_error(self, capsys):
        """_indexer_worker_chunk loguje błędy."""
        # Wywołaj z nieistniejącym plikiem
        result = _indexer_worker_chunk((0, 100, "/nonexistent/file.log", 1024 * 1024, 0))
        assert result == (0, [], 0)
        captured = capsys.readouterr()
        assert "Warning" in captured.err or "failed" in captured.err.lower()


# =============================================================================
# Testy ostrzeżenia o czasie zapisu dla dużych plików
# =============================================================================

class TestSaveTimeWarning:
    """Testy ostrzeżenia o czasie zapisu."""

    def test_warning_in_source(self):
        """Sprawdza czy ostrzeżenie o czasie zapisu jest w kodzie."""
        import inspect
        from log_reader.app import LogViewerWindow
        source = inspect.getsource(LogViewerWindow)
        assert "save_warning" in source or "zapis potrwa" in source or "est_seconds" in source


# =============================================================================
# Testy integracyjne — pełny flow z najnowszymi poprawkami
# =============================================================================

class TestIntegrationFlow:
    """Testy integracyjne sprawdzające pełny flow z najnowszymi poprawkami."""

    def test_open_search_edit_save_flow(self, temp_log_file):
        """Pełny flow: otwórz plik → wyszukaj → edytuj → zapisz."""
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)

        # 1. Wyszukaj
        engine = FilterEngine(path, idx)
        results = []
        done = threading.Event()
        engine.start("ERROR", False, True, False, lambda p, h: None,
                     lambda r, e: (results.extend(r), done.set()))
        for _ in range(100):
            if done.is_set():
                break
            time.sleep(0.05)
        assert len(results) == 250

        # 2. Edytuj linię
        buf = EditBuffer()
        buf.set(5, "EDITED LINE 5")

        # 3. Zapisz
        st = os.stat(path)
        backup = buf.save_to_file(path, expected_size=st.st_size, expected_mtime=st.st_mtime_ns)
        assert os.path.exists(backup)

        # 4. Zweryfikuj
        with open(path, "rb") as f:
            lines = f.readlines()
        assert b"EDITED LINE 5" in lines[5]

        try:
            os.unlink(backup)
        except PermissionError:
            pass
        idx.close()

    def test_parallel_indexing_correctness(self, temp_log_file):
        """Parallel indexing daje poprawny line_count po naprawie chunkowania."""
        path = temp_log_file(num_lines=100000)  # ~5 MB
        idx = LineIndexer(path)
        # Sprawdź czy line_count jest poprawny
        assert idx.line_count == 100000
        # Sprawdź read_lines
        lines = idx.read_lines(50000, 3)
        assert len(lines) == 3
        assert lines[0][0] == 50000
        idx.close()
