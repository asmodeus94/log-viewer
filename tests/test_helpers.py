"""Testy helpers.py — fmt_size, truncate_for_display, parse_dnd_files, is_compressed, open_maybe_compressed."""
import os
import gzip
import tempfile
import pytest
from log_reader.helpers import (
    fmt_size, truncate_for_display, parse_dnd_files, dnd_files_to_open,
    is_compressed, open_maybe_compressed,
    MAX_DISPLAY_LINE_LENGTH,
)


class TestFmtSize:
    def test_bytes(self):
        assert fmt_size(0) == "0 B"
        assert fmt_size(1023) == "1023 B"

    def test_kb(self):
        assert "KB" in fmt_size(2048)

    def test_mb(self):
        assert "MB" in fmt_size(10 * 1024 * 1024)

    def test_gb(self):
        assert "GB" in fmt_size(5 * 1024 * 1024 * 1024)


class TestTruncateForDisplay:
    def test_short_text(self):
        result, was_truncated = truncate_for_display("short")
        assert result == "short"
        assert was_truncated is False

    def test_exact_length(self):
        text = "x" * 100
        result, was_truncated = truncate_for_display(text, max_length=100)
        assert result == text
        assert was_truncated is False

    def test_long_text(self):
        text = "A" * 50000
        result, was_truncated = truncate_for_display(text, max_length=100)
        assert was_truncated is True
        assert len(result) == 100
        assert "truncated" in result

    def test_default_limit(self):
        text = "B" * 15000
        result, was_truncated = truncate_for_display(text)
        assert was_truncated is True
        assert len(result) == MAX_DISPLAY_LINE_LENGTH

    def test_unicode_preservation(self):
        text = "ż" * 100 + "A" * 50
        result, was_truncated = truncate_for_display(text, max_length=100)
        assert was_truncated is True
        assert len(result) == 100  # code points, nie bajty


class TestParseDndFiles:
    def test_simple_unix_path(self):
        assert parse_dnd_files("/tmp/test.log") == ["/tmp/test.log"]

    def test_path_with_spaces_braces(self):
        assert parse_dnd_files("{C:/My Documents/a.log}") == ["C:/My Documents/a.log"]

    def test_file_uri(self):
        assert parse_dnd_files("file:///tmp/test.log") == ["/tmp/test.log"]

    def test_file_uri_with_spaces(self):
        assert parse_dnd_files("file:///tmp/with%20space.log") == ["/tmp/with space.log"]

    def test_multiple_paths(self):
        result = parse_dnd_files("/tmp/a.log /tmp/b.log /tmp/c.log")
        assert result == ["/tmp/a.log", "/tmp/b.log", "/tmp/c.log"]

    def test_multiple_paths_with_braces(self):
        result = parse_dnd_files("{/tmp/with space.log} /tmp/no-space.log {/tmp/another space.log}")
        assert result == ["/tmp/with space.log", "/tmp/no-space.log", "/tmp/another space.log"]

    def test_empty_input(self):
        assert parse_dnd_files("") == []
        assert parse_dnd_files("   ") == []
        assert parse_dnd_files(None) == []

    def test_file_uri_localhost(self):
        assert parse_dnd_files("file://localhost/tmp/test.log") == ["/tmp/test.log"]

    def test_polish_chars(self):
        encoded = "file:///tmp/za%C5%BC%C3%B3%C5%82%C4%87.log"
        assert parse_dnd_files(encoded) == ["/tmp/zażółć.log"]


class TestDndFilesToOpen:
    def test_filters_nonexistent(self, temp_log_file):
        real = temp_log_file(num_lines=10)
        result = dnd_files_to_open(f"{real} /nonexistent/file.log")
        assert result == [real]

    def test_multiple_existing(self, temp_log_file):
        f1 = temp_log_file(num_lines=10)
        f2 = temp_log_file(num_lines=10)
        result = dnd_files_to_open(f"{f1} {f2}")
        assert result == [f1, f2]

    def test_empty(self):
        assert dnd_files_to_open("") == []
        assert dnd_files_to_open("/nonexistent") == []


class TestIsCompressed:
    def test_gz(self):
        assert is_compressed("/tmp/test.log.gz") is True
        assert is_compressed("/tmp/test.LOG.GZ") is True
        assert is_compressed("/tmp/test.gzip") is True

    def test_bz2(self):
        assert is_compressed("/tmp/test.log.bz2") is True
        assert is_compressed("/tmp/test.bzip2") is True

    def test_xz(self):
        assert is_compressed("/tmp/test.log.xz") is True
        assert is_compressed("/tmp/test.lzma") is True
        assert is_compressed("/tmp/test.lz") is True

    def test_plain(self):
        assert is_compressed("/tmp/test.log") is False
        assert is_compressed("/tmp/test.txt") is False
        assert is_compressed("/tmp/test") is False


class TestOpenMaybeCompressed:
    def test_gz(self, temp_log_file):
        path = temp_log_file(suffix=".log.gz", content=b"")
        with gzip.open(path, "wb") as f:
            for i in range(100):
                f.write(f"line {i}\n".encode())
        with open_maybe_compressed(path, "rb") as f:
            content = f.read()
        assert b"line 0" in content
        assert b"line 99" in content

    def test_plain(self, temp_log_file):
        path = temp_log_file(num_lines=10)
        with open_maybe_compressed(path, "rb") as f:
            content = f.read()
        assert b"line" in content
