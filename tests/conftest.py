"""Fixtures współdzielone między testami."""

import os
import sys
from pathlib import Path

# noinspection PyPackageRequirements
import pytest

# Dodaj katalog nadrzędny do path by móc importować pakiet log_viewer
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Konfiguracja platformy Qt — w środowiskach testowych/headless (zwłaszcza macOS i CI)
# wymuszamy offscreen, aby uniknąć problemów z brakiem aktywnego serwera okien (Cocoa/X11)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Wycisz ostrzeżenia z qt.qpa.fonts (np. brak czcionki "Sans Serif" na macOS)
os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts.warning=false"


@pytest.fixture
def temp_log_file(tmp_path: Path):
    """Tworzy tymczasowy plik logu. Zwraca ścieżkę. Czyści po teście."""
    paths: list[str] = []
    counter = 0

    def _create(num_lines: int = 1000, line_len: int = 120, suffix: str = ".log", content: bytes | None = None) -> str:
        nonlocal counter
        counter += 1
        path = str(tmp_path / f"temp_log_{counter}{suffix}")
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
def temp_config_path(tmp_path: Path):
    """Zwraca ścieżkę do tymczasowego pliku konfiguracyjnego."""
    path = str(tmp_path / "temp_config.json")
    yield path
    if os.path.exists(path):
        try:
            os.unlink(path)
        except PermissionError:
            pass
