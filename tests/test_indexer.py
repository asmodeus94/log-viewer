"""Testy indexer.py — LineIndexer, parallel/single indexing, read_lines."""

import gzip
import os
import time
from unittest.mock import patch

import pytest
from log_viewer.indexer import IndexEntry, LineIndexer, _indexer_worker_chunk, open_maybe_compressed


class TestLineIndexerBasic:
    def test_basic_indexing(self, temp_log_file):
        path = temp_log_file(num_lines=10000)
        idx = LineIndexer(path)
        assert idx.line_count == 10000
        assert idx.size > 0
        assert len(idx.index) >= 1
        idx.close()

    def test_offset_of_line(self, temp_log_file):
        path = temp_log_file(num_lines=10000)
        idx = LineIndexer(path)
        off = idx.offset_of_line(5000)
        assert off is not None and off > 0
        idx.close()

    def test_read_lines(self, temp_log_file):
        path = temp_log_file(num_lines=10000)
        idx = LineIndexer(path)
        lines = idx.read_lines(5000, 3)
        assert len(lines) == 3
        assert lines[0][0] == 5000
        assert "line" in lines[0][1] and "5000" in lines[0][1]
        idx.close()

    def test_line_at_byte_offset(self, temp_log_file):
        path = temp_log_file(num_lines=10000)
        idx = LineIndexer(path)
        line_no, _off = idx.line_at_byte_offset(idx.size // 2)
        assert 0 <= line_no < 10000
        idx.close()

    def test_read_tail(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        tail = idx.read_tail(100)
        assert len(tail) == 100
        assert tail[-1][0] == 999
        idx.close()

    def test_empty_file(self):
        import tempfile

        path = tempfile.mktemp(suffix=".log")
        with open(path, "wb"):
            pass
        try:
            idx = LineIndexer(path)
            assert idx.line_count == 0
            assert idx.read_lines(0, 10) == []
            idx.close()
        finally:
            try:
                os.unlink(path)
            except PermissionError:
                pass


class TestLineIndexerCompression:
    def test_gz(self):
        import tempfile

        path = tempfile.mktemp(suffix=".log.gz")
        try:
            n_lines = 1000
            with gzip.open(path, "wb") as f:
                for i in range(n_lines):
                    f.write(f"line {i} [INFO] hello\n".encode())
            progress_vals = []
            with LineIndexer(path, progress_cb=progress_vals.append) as idx:
                assert idx.line_count == n_lines
                assert idx.is_compressed is True
                assert idx.size > os.path.getsize(path)
                lines = idx.read_lines(100, 2)
                assert len(lines) == 2
                line_no, _ = idx.line_at_byte_offset(idx.size - 5)
                assert line_no == n_lines - 1
                if progress_vals:
                    assert all(pv <= 100.0 for pv in progress_vals)
        finally:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except PermissionError:
                    pass

    def test_bz2(self, tmp_path):
        import bz2

        path = tmp_path / "test.log.bz2"
        with bz2.open(path, "wb") as f:
            for i in range(50):
                f.write(f"bz2 line {i}\n".encode())
        with LineIndexer(path) as idx:
            assert idx.line_count == 50
            assert idx.is_compressed is True
            assert idx.size > path.stat().st_size
            lines = idx.read_lines(10, 2)
            assert len(lines) == 2
            assert "bz2 line 10" in lines[0][1]

    def test_empty_gz(self, tmp_path):
        path = tmp_path / "empty.log.gz"
        with gzip.open(path, "wb") as f:
            pass
        with LineIndexer(path) as idx:
            assert idx.line_count == 0
            assert idx.size == 0
            assert idx.read_lines(0, 10) == []


class TestLineIndexerEncoding:
    def test_utf8(self, temp_log_file):
        path = temp_log_file(num_lines=100, content=None)
        # Nadpisz z polskimi znakami
        with open(path, "wb") as f:
            for i in range(100):
                f.write(f"line {i} zażółć hello\n".encode())
        idx = LineIndexer(path, encoding="utf-8")
        lines = idx.read_lines(0, 2)
        assert "zażółć" in lines[0][1]
        idx.close()

    def test_latin1_mismatch(self, temp_log_file):
        path = temp_log_file(num_lines=100, content=None)
        with open(path, "wb") as f:
            for i in range(100):
                f.write(f"line {i} zażółć hello\n".encode())
        idx = LineIndexer(path, encoding="latin-1")
        lines = idx.read_lines(0, 2)
        assert "hello" in lines[0][1]
        assert "zażółć" not in lines[0][1]  # mangled
        idx.close()


class TestLineIndexerParallel:
    def test_parallel_correctness(self, temp_log_file):
        """Parallel indexing daje poprawny line_count i read_lines."""
        n_lines = 1_000_000  # ~100 MB
        path = temp_log_file(num_lines=n_lines)
        size = os.path.getsize(path)
        if size <= 100 * 1024 * 1024:
            pytest.skip("File too small for parallel threshold")

        idx = LineIndexer(path, parallel_threshold_bytes=50 * 1024 * 1024)  # użyje parallel
        assert idx.line_count == n_lines

        # Single-thread (wymuś)
        idx2 = LineIndexer(path)
        idx2.index = [IndexEntry(0, 0)]
        idx2._last_indexed_offset = 0
        idx2._build_single()
        assert idx2.line_count == n_lines

        # Porównaj read_lines
        for target in [0, 1000, n_lines // 2, n_lines - 1]:
            lines_p = idx.read_lines(target, 3)
            lines_s = idx2.read_lines(target, 3)
            assert lines_p == lines_s, f"Mismatch at line {target}"

        idx.close()
        idx2.close()

    def test_parallel_threshold_config(self, temp_log_file):
        """LineIndexer respektuje domyślny i przekazany próg parallel_threshold_bytes."""
        path = temp_log_file(num_lines=10)
        idx = LineIndexer(path)
        assert idx.parallel_threshold_bytes == 300 * 1024 * 1024
        idx.close()

        idx_custom = LineIndexer(path, parallel_threshold_bytes=100)
        assert idx_custom.parallel_threshold_bytes == 100
        idx_custom.close()

    def test_single_thread_cancellation(self, temp_log_file):
        """Single-thread indexing przerywa natychmiast gdy cancel_event jest ustawiony."""
        import threading

        path = temp_log_file(num_lines=5000)
        cancel = threading.Event()
        cancel.set()
        idx = LineIndexer(path, cancel_event=cancel)
        assert idx.line_count == 0
        assert len(idx.index) == 1
        idx.close()

    def test_small_file_uses_single(self, temp_log_file):
        """Małe pliki (<100MB) używają single-thread."""
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        assert idx.line_count == 1000
        idx.close()

    def test_compressed_uses_single(self):
        """Skompresowane pliki używają single-thread."""
        import tempfile

        path = tempfile.mktemp(suffix=".log.gz")
        try:
            with gzip.open(path, "wb") as f:
                for i in range(1000):
                    f.write(f"line {i}\n".encode())
            idx = LineIndexer(path)
            assert idx.line_count == 1000
            assert idx.is_compressed is True
            idx.close()
        finally:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except PermissionError:
                    pass


class TestLineIndexerUpdateFrom:
    def test_update_from_concurrent_growth_consistency(self, tmp_path):
        p = tmp_path / "growing.log"
        p.write_bytes(b"line 0\nline 1\n")
        idx = LineIndexer(p)
        assert idx.size == len(b"line 0\nline 1\n")
        assert idx.line_count == 2

        with open(p, "ab") as f:
            f.write(b"line 2\nline 3\nline 4\n")
        actual_size = p.stat().st_size
        stale_new_size = idx.size + 7

        new_lines = idx.update_from(stale_new_size)
        assert new_lines == 3
        assert idx.line_count == 5
        assert idx.size == actual_size

        # Kolejny tick follow mode nie powinien powtórnie zliczać linii
        assert idx.update_from(actual_size) == 0
        assert idx.line_count == 5

        # Weryfikacja integralności odczytanych linii z indeksu
        lines = idx.read_lines(0, 5)
        assert [t for _, t in lines] == [f"line {i}" for i in range(5)]

        idx.close()

    def test_incremental_update(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        original_size = idx.size
        original_count = idx.line_count

        # Dopisz 100 linii
        with open(path, "ab") as f:
            for i in range(1000, 1100):
                f.write(f"2026-07-04 10:00:{i:02d} [INFO] line{i:>8d} - new tail\n".encode())
        new_size = os.path.getsize(path)

        new_lines = idx.update_from(new_size)
        assert new_lines == 100
        assert idx.line_count == original_count + 100
        assert idx.size == new_size
        idx.close()

    def test_no_op_when_smaller(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        result = idx.update_from(idx.size - 100)
        assert result == 0
        idx.close()


class TestLineIndexerFileDescriptorCache:
    def test_cache_reuses_fd(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)

        with patch("log_viewer.indexer.open_maybe_compressed", wraps=open_maybe_compressed) as mock_open:
            # Wykonaj 100 operacji
            for i in range(100):
                idx.read_lines(i * 5, 3)
                idx.offset_of_line(i * 5)

            # Deskryptor powinien zostać zbuforowany i ponownie użyty,
            # więc open powinno zostać wywołane najwyżej raz
            assert mock_open.call_count <= 1

        idx.close()

    def test_close_releases_fd(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        idx.read_lines(0, 10)  # wymusi otwarcie cache
        idx.close()
        # Po close, _file_cache powinno być None
        assert idx._file_cache is None


class TestChunkedWorker:
    """Testy _indexer_worker_chunk z chunkowanym odczytem."""

    def test_correct_line_count(self, temp_log_file):
        """Worker liczy poprawną liczbę linii w swoim zakresie."""
        path = temp_log_file(num_lines=10000)
        size = os.path.getsize(path)
        chunk_size = size // 2
        r1 = (0, chunk_size, str(path), 1024 * 1024, 0)
        r2 = (chunk_size, size, str(path), 1024 * 1024, 1)
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
        ranges = [
            (i * chunk_size, (i + 1) * chunk_size if i < 3 else size, str(path), 1024 * 1024, i) for i in range(4)
        ]
        results = [_indexer_worker_chunk(r) for r in ranges]
        total = sum(r[0] for r in results)
        assert total == 50000

    def test_worker_handles_large_lines(self):
        """Worker radzi sobie z bardzo długimi liniami (>4MB chunk)."""
        import tempfile

        path = tempfile.mktemp(suffix=".log")
        try:
            with open(path, "wb") as f:
                # Jedna bardzo długa linia (8 MB)
                f.write(b"X" * (8 * 1024 * 1024) + b"\n")
                f.write(b"short line\n")
            size = os.path.getsize(path)
            result = _indexer_worker_chunk((0, size, str(path), 1024 * 1024, 0))
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
        result = _indexer_worker_chunk((0, size, str(path), 1024 * 1024, 0))
        assert result[0] == 10000
        # Jeśli worker ładuje wszystko do RAM, przy 10000 liniach ~500KB to OK
        # ale test weryfikuje że działa bez crash dla dużych plików

    def test_indexer_worker_logs_error(self, capsys):
        """_indexer_worker_chunk loguje błędy."""
        # Wywołaj z nieistniejącym plikiem
        result = _indexer_worker_chunk((0, 100, "/nonexistent/file.log", 1024 * 1024, 0))
        assert result == (0, [], 0)
        captured = capsys.readouterr()
        assert "Warning" in captured.err or "failed" in captured.err.lower()


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


class TestReadSpecificLines:
    def test_read_specific_lines_sparse(self, temp_log_file):
        """Metoda read_specific_lines poprawnie odczytuje oddalone od siebie linie."""
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)

        targets = [10, 500, 990]
        lines = idx.read_specific_lines(targets)

        assert len(lines) == 3
        assert lines[0][0] == 10
        assert "line      10" in lines[0][1]
        assert lines[1][0] == 500
        assert "line     500" in lines[1][1]
        assert lines[2][0] == 990
        assert "line     990" in lines[2][1]

        idx.close()

    def test_read_specific_lines_continuous(self, temp_log_file):
        """Metoda read_specific_lines poprawnie odczytuje blok ciągłych linii."""
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)

        targets = [100, 101, 102, 103, 104]
        lines = idx.read_specific_lines(targets)

        assert len(lines) == 5
        for i, (ln, text) in enumerate(lines):
            assert ln == 100 + i
            assert f"line     {100 + i}" in text

        idx.close()

    def test_read_specific_lines_unordered_and_duplicates(self, temp_log_file):
        """Metoda read_specific_lines ignoruje duplikaty i radzi sobie z listą nieposortowaną."""
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)

        targets = [500, 10, 500, 990, 10]
        lines = idx.read_specific_lines(targets)

        assert len(lines) == 3
        assert lines[0][0] == 10
        assert lines[1][0] == 500
        assert lines[2][0] == 990

        idx.close()

    def test_read_specific_lines_out_of_bounds(self, temp_log_file):
        """Metoda read_specific_lines bezpiecznie ignoruje indeksy poza zakresem."""
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)

        targets = [-5, 10, 2000, 5000]
        lines = idx.read_specific_lines(targets)

        assert len(lines) == 1
        assert lines[0][0] == 10

        idx.close()

    def test_read_specific_lines_empty(self, temp_log_file):
        """Metoda read_specific_lines zwraca pustą listę dla pustej wejściowej."""
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)

        lines = idx.read_specific_lines([])
        assert len(lines) == 0

        idx.close()

    def test_read_specific_lines_performance(self, temp_log_file):
        """Test wydajnościowy dla read_specific_lines sprawdzający limit czasu wykonywania dla bardzo rozproszonych danych."""

        path = temp_log_file(num_lines=100000)
        idx = LineIndexer(path)

        # Wybieramy co 10. linię - bardzo rozproszony układ (najgorszy przypadek)
        targets = list(range(0, 100000, 10))

        start_time = time.time()
        lines = idx.read_specific_lines(targets)
        end_time = time.time()

        # Nawet na wolnych maszynach ciągłe odczytywanie 10 tys. rozproszonych linii
        # w jednym przejściu (bez setek seeków) powinno trwać znacznie poniżej sekundy
        assert (end_time - start_time) < 1.0, f"Performance test failed, took {end_time - start_time} seconds"
        assert len(lines) == 10000

        idx.close()


class TestLineIndexerNoTrailingNewline:
    """Testy dla plików bez końcowego znaku nowej linii (np. 1-liniowe pliki)."""

    def test_single_line_without_newline(self, tmp_path):
        path = tmp_path / "single_line.txt"
        path.write_bytes(b"sad a asd as")

        idx = LineIndexer(path)
        assert idx.line_count == 1
        assert idx.has_trailing_newline is False
        assert idx.offset_of_line(0) == 0
        assert idx.read_lines(0, 10) == [(0, "sad a asd as")]
        assert idx.read_tail(10) == [(0, "sad a asd as")]
        idx.close()

    def test_multiple_lines_without_trailing_newline(self, tmp_path):
        path = tmp_path / "multi_no_nl.txt"
        path.write_bytes(b"line 0\nline 1\nline 2")

        idx = LineIndexer(path)
        assert idx.line_count == 3
        assert idx.has_trailing_newline is False
        assert idx.offset_of_line(0) == 0
        assert idx.offset_of_line(2) == len(b"line 0\nline 1\n")
        assert idx.read_lines(0, 10) == [(0, "line 0"), (1, "line 1"), (2, "line 2")]
        assert idx.read_lines(2, 1) == [(2, "line 2")]
        idx.close()

    def test_incremental_update_without_trailing_newline(self, tmp_path):
        path = tmp_path / "follow_no_nl.txt"
        path.write_bytes(b"first")

        idx = LineIndexer(path)
        assert idx.line_count == 1
        assert idx.has_trailing_newline is False

        # Dopisanie do tej samej linii (brak \n)
        with open(path, "ab") as f:
            f.write(b" line continued")
        nl = idx.update_from(path.stat().st_size)
        assert nl == 0
        assert idx.line_count == 1
        assert idx.has_trailing_newline is False
        assert idx.read_lines(0, 10) == [(0, "first line continued")]

        # Dopisanie nowej linii
        with open(path, "ab") as f:
            f.write(b"\nsecond line")
        nl = idx.update_from(path.stat().st_size)
        assert nl == 1
        assert idx.line_count == 2
        assert idx.has_trailing_newline is False
        assert idx.read_lines(0, 10) == [(0, "first line continued"), (1, "second line")]

        # Domknięcie nowej linii
        with open(path, "ab") as f:
            f.write(b"\n")
        nl = idx.update_from(path.stat().st_size)
        assert nl == 0
        assert idx.line_count == 2
        assert idx.has_trailing_newline is True
        idx.close()


class TestLineIndexerByteOffset:
    """Testy dla zoptymalizowanej metody line_at_byte_offset."""

    def test_byte_offset_various_positions(self, tmp_path):
        # 3 linie:
        # "line 0\n" -> 7 bajtów (offset 0..6, \n na 6)
        # "line 1\n" -> 7 bajtów (offset 7..13, \n na 13)
        # "line 2\n" -> 7 bajtów (offset 14..20, \n na 20)
        # size = 21, line_count = 3
        path = tmp_path / "offset_test.log"
        path.write_bytes(b"line 0\nline 1\nline 2\n")

        idx = LineIndexer(path)
        assert idx.size == 21
        assert idx.line_count == 3

        # Początek linii 0
        assert idx.line_at_byte_offset(0) == (0, 0)
        # Środek linii 0
        assert idx.line_at_byte_offset(3) == (0, 0)
        # Znak \n linii 0
        assert idx.line_at_byte_offset(6) == (0, 0)

        # Początek linii 1
        assert idx.line_at_byte_offset(7) == (1, 7)
        # Środek linii 1
        assert idx.line_at_byte_offset(10) == (1, 7)
        # Znak \n linii 1
        assert idx.line_at_byte_offset(13) == (1, 7)

        # Początek linii 2
        assert idx.line_at_byte_offset(14) == (2, 14)
        # Znak \n linii 2
        assert idx.line_at_byte_offset(20) == (2, 14)

        # Dokładnie koniec pliku (size)
        assert idx.line_at_byte_offset(21) == (2, 21)
        # Poza plikiem
        assert idx.line_at_byte_offset(999) == (2, 21)
        # Ujemny offset
        assert idx.line_at_byte_offset(-10) == (0, 0)

        idx.close()

    def test_byte_offset_no_trailing_newline(self, tmp_path):
        # "line 0\nline 1" -> 13 bajtów (offset 0..6: line 0, 7..12: line 1)
        path = tmp_path / "offset_no_nl.log"
        path.write_bytes(b"line 0\nline 1")

        idx = LineIndexer(path)
        assert idx.size == 13
        assert idx.line_count == 2
        assert idx.has_trailing_newline is False

        assert idx.line_at_byte_offset(0) == (0, 0)
        assert idx.line_at_byte_offset(6) == (0, 0)
        assert idx.line_at_byte_offset(7) == (1, 7)
        assert idx.line_at_byte_offset(12) == (1, 7)
        assert idx.line_at_byte_offset(13) == (1, 13)

        idx.close()

    def test_byte_offset_multiple_sparse_index_entries(self, tmp_path):
        path = tmp_path / "sparse_offsets.log"
        lines = [f"2026-07-04 line {i:04d} payload text\n".encode() for i in range(200)]
        path.write_bytes(b"".join(lines))

        idx = LineIndexer(path, index_interval_bytes=256)
        assert len(idx.index) > 3

        running_off = 0
        for i, line_b in enumerate(lines):
            res_line, res_off = idx.line_at_byte_offset(running_off)
            assert (res_line, res_off) == (i, running_off)

            res_mid_line, res_mid_off = idx.line_at_byte_offset(running_off + len(line_b) // 2)
            assert (res_mid_line, res_mid_off) == (i, running_off)

            running_off += len(line_b)

        idx.close()


class TestConsumeChunkIndexEntries:
    """Testy dla bezalokacyjnej metody _consume_chunk_index_entries."""

    def test_consume_multiple_entries_in_one_chunk(self, tmp_path):
        path = tmp_path / "multi_entries_chunk.log"
        lines = [f"line {i:02d} with padding data here\n".encode() for i in range(20)]
        path.write_bytes(b"".join(lines))

        idx = LineIndexer(path, index_interval_bytes=60)
        assert len(idx.index) >= 5
        for entry in idx.index:
            if entry.line < idx.line_count:
                assert idx.offset_of_line(entry.line) == entry.offset
            else:
                assert entry.offset == idx.size and entry.line == idx.line_count
        idx.close()

    def test_consume_zero_copy_equivalence(self, tmp_path):
        path = tmp_path / "zero_copy_equiv.log"
        lines = [f"2026-07-04 {i:05d} [INFO] sample log entry message\n".encode() for i in range(1000)]
        path.write_bytes(b"".join(lines))

        idx = LineIndexer(path, index_interval_bytes=512)
        assert len(idx.index) > 10

        for entry in idx.index:
            if entry.line < idx.line_count:
                assert entry.offset == idx.offset_of_line(entry.line)
            else:
                assert entry.offset == idx.size and entry.line == idx.line_count

        idx.close()


class TestIndexerWorkerChunk:
    """Testy dla funkcji roboczej multiprocessing _indexer_worker_chunk i _collect_chunk_index_entries."""

    def test_worker_chunk_direct(self, tmp_path):
        from log_viewer.indexer import _indexer_worker_chunk

        path = tmp_path / "worker_direct.log"
        raw_lines = [f"line {i:04d} data\n".encode() for i in range(100)]
        path.write_bytes(b"".join(raw_lines))
        size = path.stat().st_size

        # Podziel na 2 równe połówki
        half = size // 2
        res1 = _indexer_worker_chunk((0, half, str(path), 128, 0))
        res2 = _indexer_worker_chunk((half, size, str(path), 128, 1))

        assert res1[2] == 0
        assert res2[2] == 1
        total_lines = res1[0] + res2[0]
        assert total_lines == 100

    def test_worker_chunk_invalid_file(self):
        from log_viewer.indexer import _indexer_worker_chunk

        res = _indexer_worker_chunk((0, 1000, "nonexistent_file_path_123.log", 128, 99))
        assert res == (0, [], 99)

    def test_collect_chunk_index_entries_direct(self):
        from log_viewer.indexer import _collect_chunk_index_entries

        data = b"line 1\nline 2\nline 3\nline 4\nline 5\n"
        entries = []
        last_off, nl_cnt = _collect_chunk_index_entries(
            chunk=data,
            base_offset=0,
            base_line=0,
            last_indexed_offset=0,
            interval=10,
            entries=entries,
        )
        assert nl_cnt == 5
        assert len(entries) > 0
        for off, line_no in entries:
            assert data[off - 1 : off] == b"\n"
            assert 0 <= line_no <= 5

    def test_collect_chunk_index_entries_no_newlines(self):
        from log_viewer.indexer import _collect_chunk_index_entries

        data = b"no newlines here at all"
        entries = []
        last_off, nl_cnt = _collect_chunk_index_entries(
            chunk=data,
            base_offset=0,
            base_line=0,
            last_indexed_offset=0,
            interval=5,
            entries=entries,
        )
        assert nl_cnt == 0
        assert entries == []
        assert last_off == 0

    def test_collect_chunk_index_entries_zero_entries_with_newlines(self):
        from log_viewer.indexer import _collect_chunk_index_entries

        data = b"line 1\nline 2\nline 3\n"
        entries = []
        last_off, nl_cnt = _collect_chunk_index_entries(
            chunk=data,
            base_offset=500,
            base_line=42,
            last_indexed_offset=500,
            interval=10000,
            entries=entries,
        )
        assert nl_cnt == 3
        assert entries == []
        assert last_off == 500


class TestLineIndexerContextManager:
    def test_context_manager_opens_and_closes(self, temp_log_file):
        path = temp_log_file(num_lines=100)
        with LineIndexer(path) as idx:
            assert idx.line_count == 100
            assert idx._file_cache is None
            idx.read_lines(0, 10)
            assert idx._file_cache is not None

        assert idx._file_cache is None
        assert idx._cursor_pos is None

    def test_context_manager_exception_closes(self, temp_log_file):
        path = temp_log_file(num_lines=100)
        idx_ref = None
        try:
            with LineIndexer(path) as idx:
                idx_ref = idx
                idx.read_lines(0, 10)
                raise RuntimeError("Simulated error inside with")
        except RuntimeError:
            pass

        assert idx_ref is not None
        assert idx_ref._file_cache is None


class TestLineIndexerCursorTracking:
    def test_cursor_tracking_sequential_and_random_access(self, temp_log_file):
        path = temp_log_file(num_lines=2000)
        idx = LineIndexer(path, index_interval_bytes=512)

        # 1. Kursor początkowo None
        assert idx._cursor_pos is None

        # 2. Odczyt linii 10..15 ustawia kursor na linii 15
        lines = idx.read_lines(10, 5)
        assert len(lines) == 5
        assert idx._cursor_pos is not None
        assert idx._cursor_pos[0] == 15

        # 3. Sekwencyjny odczyt linii 15..20 wznawia z kursora (bez f.seek wstecz)
        lines2 = idx.read_lines(15, 5)
        assert len(lines2) == 5
        assert lines2[0][0] == 15
        assert idx._cursor_pos[0] == 20

        # 4. Skok w tył (do linii 5) cofa pozycję i prawidłowo pobiera dane
        lines_back = idx.read_lines(5, 3)
        assert len(lines_back) == 3
        assert lines_back[0][0] == 5
        assert idx._cursor_pos[0] == 8

        # 4b. Daleki skok w przod (indeks blizej niz kursor)
        lines_far = idx.read_lines(1500, 2)
        assert len(lines_far) == 2
        assert lines_far[0][0] == 1500
        assert idx._cursor_pos[0] == 1502

        # 5. offset_of_line aktualizuje kursor
        off = idx.offset_of_line(50)
        assert off is not None
        assert idx._cursor_pos is not None
        assert idx._cursor_pos[0] == 50

        # 6. Zamknięcie resetuje kursor
        idx.close()
        assert idx._cursor_pos is None
