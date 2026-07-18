"""Fixtures współdzielone między testami."""
import os
import sys
import tempfile
import pytest

# Dodaj katalog nadrzędny do path by móc importować pakiet log_reader
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def temp_log_file():
    """Tworzy tymczasowy plik logu. Zwraca ścieżkę. Czyści po teście."""
    paths = []
    def _create(num_lines=1000, line_len=120, suffix=".log", content=None):
        path = tempfile.mktemp(suffix=suffix)
        paths.append(path)
        if content:
            with open(path, "wb") as f:
                f.write(content)
        else:
            levels = ["INFO", "WARN", "ERROR", "DEBUG"]
            with open(path, "wb") as f:
                for i in range(num_lines):
                    level = levels[i % 4]
                    ts = f"2026-07-04 10:{i // 60:02d}:{i % 60:02d}"
                    msg = f"{ts} [{level}] line{i:>8d} - " + ("x" * (line_len - 60)) + "\n"
                    f.write(msg.encode("utf-8"))
        return path
    yield _create
    # Cleanup
    for p in paths:
        if os.path.exists(p):
            try:
                os.unlink(p)
            except PermissionError:
                pass


@pytest.fixture
def temp_config_path():
    """Zwraca ścieżkę do tymczasowego pliku konfiguracyjnego."""
    path = tempfile.mktemp(suffix=".json")
    yield path
    if os.path.exists(path):
        try:
            os.unlink(path)
        except PermissionError:
            pass
