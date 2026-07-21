"""Testy integracyjne dla log-viewer (otwieranie, wyszukiwanie, edycja, zapis)."""
import os
import time
import threading

from log_reader.edit_buffer import EditBuffer
from log_reader.indexer import LineIndexer
from log_reader.filter_engine import FilterEngine


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
