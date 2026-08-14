"""
log-viewer / log-viewer — wersja PySide6
============================================

Aplikacja okienkowa (PySide6/Qt6) do przeglądania bardzo dużych plików logów.

Moduły:
  - exceptions: FileChangedError, CompressedSaveError
  - helpers: fmt_size, truncate_for_display, parse_dnd_files, is_compressed, open_maybe_compressed
  - i18n: słownik PL/EN
  - config: UserConfig (~/.log-viewer.json)
  - indexer: LineIndexer (rzadki indeks co 1 MB, multiprocessing)
  - filter_engine: FilterEngine (skanowanie w tle, session isolation)
  - edit_buffer: EditBuffer (edycja in-place, walidacja mtime)
  - workers: QThread workers (IndexerWorker, FilterWorker, SaveWorker)
  - widgets: LineNumberArea, LogPlainTextEdit, SettingsDialog
  - main_window: LogViewerWindow (główna aplikacja PySide6 - widżet z zakładkami)
  - log_tab: LogTab (pojedyncza zakładka z logiem)

Użycie:
    python -m log_viewer [plik.log]

Licencja: MIT
"""

from .config import UserConfig
from .edit_buffer import EditBuffer
from .exceptions import CompressedSaveError, FileChangedError
from .filter_engine import FilterEngine
from .helpers import (
    dnd_files_to_open,
    fmt_size,
    is_compressed,
    open_maybe_compressed,
    parse_dnd_files,
    truncate_for_display,
)
from .i18n import I18N
from .indexer import IndexEntry, LineIndexer

__version__ = "1.0"
__all__ = [
    "FileChangedError",
    "CompressedSaveError",
    "fmt_size",
    "truncate_for_display",
    "parse_dnd_files",
    "dnd_files_to_open",
    "is_compressed",
    "open_maybe_compressed",
    "LineIndexer",
    "IndexEntry",
    "FilterEngine",
    "EditBuffer",
    "UserConfig",
    "I18N",
]
