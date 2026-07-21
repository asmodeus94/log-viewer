"""Testy helpers.py — fmt_size, truncate_for_display, parse_dnd_files, is_compressed, open_maybe_compressed."""
import os
import gzip
import tempfile
import pytest
from log_reader.helpers import (
    THEME_DARK, THEME_LIGHT,
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

    def test_very_short_max_length(self):
        # Kiedy max_length jest mniejsze niż długość generowanego sufiksu.
        # Np. dla text = "A" * 50 i max_length = 10
        # Sufiks " ... [truncated 40 chars]" ma 25 znaków.
        # W tym przypadku funkcja powinna zwrócić sam sufiks (mimo że przekracza on max_length).
        text = "A" * 50
        result, was_truncated = truncate_for_display(text, max_length=10)
        assert was_truncated is True
        assert result == " ... [truncated 40 chars]"

    @pytest.mark.parametrize("length, max_length, should_truncate", [
        (99, 100, False),   # Krótszy niż limit
        (100, 100, False),  # Dokładnie równy limitowi
        (101, 100, True),   # O jeden znak dłuższy niż limit
    ])
    def test_boundary_lengths(self, length, max_length, should_truncate):
        text = "x" * length
        result, was_truncated = truncate_for_display(text, max_length=max_length)

        assert was_truncated is should_truncate

        if should_truncate:
            assert len(result) == max_length
            assert "truncated" in result
        else:
            assert result == text
            assert len(result) == length


class TestParseDndFiles:
    def test_simple_unix_path(self):
        assert parse_dnd_files("/tmp/test.log") == [os.path.normpath("/tmp/test.log")]

    def test_path_with_spaces_braces(self):
        assert parse_dnd_files("{C:/My Documents/a.log}") == [os.path.normpath("C:/My Documents/a.log")]

    def test_file_uri(self):
        assert parse_dnd_files("file:///tmp/test.log") == [os.path.normpath("/tmp/test.log")]

    def test_file_uri_with_spaces(self):
        assert parse_dnd_files("file:///tmp/with%20space.log") == [os.path.normpath("/tmp/with space.log")]

    def test_multiple_paths(self):
        result = parse_dnd_files("/tmp/a.log /tmp/b.log /tmp/c.log")
        assert result == [os.path.normpath("/tmp/a.log"), os.path.normpath("/tmp/b.log"), os.path.normpath("/tmp/c.log")]

    def test_multiple_paths_with_braces(self):
        result = parse_dnd_files("{/tmp/with space.log} /tmp/no-space.log {/tmp/another space.log}")
        assert result == [os.path.normpath("/tmp/with space.log"), os.path.normpath("/tmp/no-space.log"), os.path.normpath("/tmp/another space.log")]

    def test_empty_input(self):
        assert parse_dnd_files("") == []
        assert parse_dnd_files("   ") == []
        assert parse_dnd_files(None) == []

    def test_file_uri_localhost(self):
        assert parse_dnd_files("file://localhost/tmp/test.log") == [os.path.normpath("/tmp/test.log")]

    def test_polish_chars(self):
        encoded = "file:///tmp/za%C5%BC%C3%B3%C5%82%C4%87.log"
        assert parse_dnd_files(encoded) == [os.path.normpath("/tmp/zażółć.log")]


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
