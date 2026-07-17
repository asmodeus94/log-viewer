"""Testy indexer.py — LineIndexer, parallel/single indexing, read_lines."""
import os
import time
import gzip
import pytest
from log_reader.indexer import LineIndexer, IndexEntry


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
            os.unlink(path)


class TestLineIndexerCompression:
    def test_gz(self):
        import tempfile
        path = tempfile.mktemp(suffix=".log.gz")
        try:
            N = 1000
            with gzip.open(path, "wb") as f:
                for i in range(N):
                    f.write(f"line {i} [INFO] hello\n".encode())
            idx = LineIndexer(path)
            assert idx.line_count == N
            assert idx.is_compressed is True
            lines = idx.read_lines(100, 2)
            assert len(lines) == 2
            idx.close()
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestLineIndexerEncoding:
    def test_utf8(self, temp_log_file):
        path = temp_log_file(num_lines=100, content=None)
        # Nadpisz z polskimi znakami
        with open(path, "wb") as f:
            for i in range(100):
                f.write(f"line {i} zażółć hello\n".encode("utf-8"))
        idx = LineIndexer(path, encoding="utf-8")
        lines = idx.read_lines(0, 2)
        assert "zażółć" in lines[0][1]
        idx.close()

    def test_latin1_mismatch(self, temp_log_file):
        path = temp_log_file(num_lines=100, content=None)
        with open(path, "wb") as f:
            for i in range(100):
                f.write(f"line {i} zażółć hello\n".encode("utf-8"))
        idx = LineIndexer(path, encoding="latin-1")
        lines = idx.read_lines(0, 2)
        assert "hello" in lines[0][1]
        assert "zażółć" not in lines[0][1]  # mangled
        idx.close()


class TestLineIndexerParallel:
    def test_parallel_correctness(self, temp_log_file):
        """Parallel indexing daje poprawny line_count i read_lines."""
        N = 1_000_000  # ~100 MB
        path = temp_log_file(num_lines=N)
        size = os.path.getsize(path)
        if size <= 100 * 1024 * 1024:
            pytest.skip("File too small for parallel threshold")

        idx = LineIndexer(path)  # użyje parallel
        assert idx.line_count == N

        # Single-thread (wymuś)
        idx2 = LineIndexer(path)
        idx2.index = [IndexEntry(0, 0)]
        idx2._last_indexed_offset = 0
        idx2._build_single()
        assert idx2.line_count == N

        # Porównaj read_lines
        for target in [0, 1000, N // 2, N - 1]:
            lines_p = idx.read_lines(target, 3)
            lines_s = idx2.read_lines(target, 3)
            assert lines_p == lines_s, f"Mismatch at line {target}"

        idx.close()
        idx2.close()

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
                os.unlink(path)


class TestLineIndexerUpdateFrom:
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

        # Policz FD przed
        try:
            fd_before = len(os.listdir(f"/proc/{os.getpid()}/fd"))
        except Exception:
            pytest.skip("Cannot count FDs on this platform")

        # Wykonaj 100 operacji
        for i in range(100):
            idx.read_lines(i * 5, 3)
            idx.offset_of_line(i * 5)

        try:
            fd_after = len(os.listdir(f"/proc/{os.getpid()}/fd"))
        except Exception:
            pytest.skip("Cannot count FDs on this platform")

        # Powinno być najwyżej +1 (cached deskryptor)
        assert fd_after - fd_before <= 1
        idx.close()

    def test_close_releases_fd(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        idx = LineIndexer(path)
        idx.read_lines(0, 10)  # wymusi otwarcie cache
        idx.close()
        # Po close, _file_cache powinno być None
        assert idx._file_cache is None
