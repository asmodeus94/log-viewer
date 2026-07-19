"""
Czytnik Logów / Log Reader — wersja PySide6
============================================

Aplikacja okienkowa (PySide6/Qt6) do przeglądania bardzo dużych plików logów.

Moduły:
  - exceptions: FileChangedError, CompressedSaveError
  - helpers: fmt_size, truncate_for_display, parse_dnd_files, is_compressed, open_maybe_compressed
  - i18n: słownik PL/EN
  - config: UserConfig (~/.logreader.json)
  - indexer: LineIndexer (rzadki indeks co 1 MB, multiprocessing)
  - filter_engine: FilterEngine (skanowanie w tle, session isolation)
  - edit_buffer: EditBuffer (edycja in-place, walidacja mtime)
  - workers: QThread workers (IndexerWorker, FilterWorker, SaveWorker)
  - widgets: LineNumberArea, LogPlainTextEdit, SettingsDialog
  - main_window: LogViewerWindow (główna aplikacja PySide6 - widżet z zakładkami)
  - log_tab: LogTab (pojedyncza zakładka z logiem)
  - app: Fasada w celu utrzymania kompatybilności wstecznej

Użycie:
    python -m log_reader [plik.log]

Licencja: MIT
"""

from .exceptions import FileChangedError, CompressedSaveError
from .helpers import (
    fmt_size, truncate_for_display, parse_dnd_files, dnd_files_to_open,
    is_compressed, open_maybe_compressed,
)
from .indexer import LineIndexer, IndexEntry
from .filter_engine import FilterEngine
from .edit_buffer import EditBuffer
from .config import UserConfig
from .i18n import I18N

__version__ = "1.0"
__all__ = [
    "FileChangedError", "CompressedSaveError",
    "fmt_size", "truncate_for_display", "parse_dnd_files", "dnd_files_to_open",
    "is_compressed", "open_maybe_compressed",
    "LineIndexer", "IndexEntry",
    "FilterEngine",
    "EditBuffer",
    "UserConfig",
    "I18N",
]
