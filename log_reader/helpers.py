"""Funkcje pomocnicze — formatowanie, przycinanie, DnD, kompresja."""

from __future__ import annotations

import os
import sys
import gzip
import bz2
import lzma
from typing import List, Tuple
from urllib.parse import unquote


# Stałe konfiguracyjne (wartości domyślne — mogą być nadpisane przez UserConfig)
INDEX_INTERVAL_BYTES = 1 * 1024 * 1024
INDEX_CHUNK_BYTES = 1 * 1024 * 1024
WINDOW_SIZE_LINES = 5000
MAX_DISPLAY_LINES = 20000
MAX_DISPLAY_LINE_LENGTH = 10000
FOLLOW_POLL_MS = 600
FILTER_PROGRESS_MS = 100
DEFAULT_ENCODING = "utf-8"
CONFIG_FILE_PATH = "~/.logreader.json"

# Tagi Text widgeta
TAG_HIGHLIGHT = "highlight"
TAG_BOOKMARK = "bookmark"
TAG_EDITED = "edited"
TAG_TRUNCATED = "truncated"
TAG_ERROR = "lvl_error"
TAG_WARN = "lvl_warn"
TAG_INFO = "lvl_info"
TAG_DEBUG = "lvl_debug"
TAG_TIMESTAMP = "ts"
TAG_CONTEXT = "context"  # linia kontekstu przy filtrowaniu (N linii po trafieniu)

TRUNCATION_SUFFIX = " ... [truncated {n} chars]"

SUPPORTED_ENCODINGS = [
    ("utf-8", "UTF-8 (default)"),
    ("utf-8-sig", "UTF-8 with BOM"),
    ("latin-1", "Latin-1 (ISO 8859-1)"),
    ("cp1250", "CP1250 (Windows Central European)"),
    ("cp1252", "CP1252 (Windows Western)"),
    ("iso-8859-2", "ISO-8859-2 (Central European)"),
    ("ascii", "ASCII"),
]

# =============================================================================
# Motywy — Dark (VS Code Dark+) i Light (VS Code Light+)
# Aplikacja wykrywa motyw systemowy i wybiera odpowiedni.
# =============================================================================

THEME_DARK = {
    # Tła
    "bg_main": "#1e1e1e",
    "bg_panel": "#252526",
    "bg_statusbar": "#2d2d2d",
    "bg_input": "#3c3c3c",
    "bg_hover": "#2a2d2e",
    "bg_selected": "#094771",
    "bg_alt": "#2d2d2d",
    # Tekst
    "fg_main": "#d4d4d4",
    "fg_dim": "#858585",
    "fg_bright": "#ffffff",
    # Obramowania
    "border": "#3c3c3c",
    "border_light": "#505050",
    # Poziomy logu
    "error": "#f44747",
    "warn": "#cca700",
    "info": "#569cd6",
    "debug": "#c586c0",
    # Akcenty
    "accent": "#0e639c",
    "accent_hover": "#1177bb",
    "highlight": "#fff176",
    "bookmark": "#6a9955",
    "edited": "#ce9178",
    "truncated": "#6a6a6a",
    "current_line": "#2a2d2e",
    "context": "#3a3d3a",
    # Mini-map
    "minimap_bg": "#1e1e1e",
    "minimap_error": "#f44747",
    "minimap_warn": "#cca700",
    "minimap_info": "#569cd6",
    "minimap_debug": "#c586c0",
    "minimap_viewport": "#ffffff44",
}

THEME_LIGHT = {
    # Tła
    "bg_main": "#ffffff",
    "bg_panel": "#f3f3f3",
    "bg_statusbar": "#007acc",
    "bg_input": "#ffffff",
    "bg_hover": "#e8e8e8",
    "bg_selected": "#cde7ff",
    "bg_alt": "#f8f8f8",
    # Tekst
    "fg_main": "#1e1e1e",
    "fg_dim": "#858585",
    "fg_bright": "#000000",
    # Obramowania
    "border": "#cccccc",
    "border_light": "#d4d4d4",
    # Poziomy logu
    "error": "#d73a49",
    "warn": "#b08800",
    "info": "#005cc5",
    "debug": "#6f42c1",
    # Akcenty
    "accent": "#0e639c",
    "accent_hover": "#1177bb",
    "highlight": "#ffeb3b",
    "bookmark": "#28a745",
    "edited": "#b08800",
    "truncated": "#9e9e9e",
    "current_line": "#f0f0f0",
    "context": "#e8f5e9",
    # Mini-map
    "minimap_bg": "#ffffff",
    "minimap_error": "#d73a49",
    "minimap_warn": "#b08800",
    "minimap_info": "#005cc5",
    "minimap_debug": "#6f42c1",
    "minimap_viewport": "#007acc33",
}

# Kompatybilność wsteczna — domyślnie dark
THEME = THEME_DARK

OPEN_FILETYPES = (
    "Log files (*.log);;Log gzip (*.log.gz *.gz);;"
    "Log bzip2 (*.log.bz2 *.bz2);;Log xz (*.log.xz *.xz);;"
    "Text files (*.txt);;Text gzip (*.txt.gz);;All files (*)"
)


def fmt_size(n: int) -> str:
    """Formatuje rozmiar bajtów w czytelny sposób."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def truncate_for_display(text: str, max_length: int = MAX_DISPLAY_LINE_LENGTH) -> Tuple[str, bool]:
    """Przycina tekst do max_length znaków z sufiksem informacyjnym."""
    if len(text) <= max_length:
        return text, False
    suffix = TRUNCATION_SUFFIX.format(n=len(text) - max_length)
    keep = max(0, max_length - len(suffix))
    return text[:keep] + suffix, True


def parse_dnd_files(dnd_data: str) -> List[str]:
    """Parsuje dane z DnD eventa — format zależy od platformy."""
    if not dnd_data:
        return []
    paths: List[str] = []
    s = dnd_data.strip()
    i = 0
    while i < len(s):
        if s[i] == "{":
            end = s.find("}", i)
            if end == -1:
                break
            paths.append(s[i + 1:end])
            i = end + 1
            while i < len(s) and s[i] in " \t":
                i += 1
        elif s[i] in " \t":
            i += 1
        else:
            end = i
            while end < len(s) and s[end] not in " \t":
                end += 1
            paths.append(s[i:end])
            i = end
    result: List[str] = []
    for p in paths:
        if p.startswith("file://"):
            p = p[7:]
            if p.startswith("localhost/"):
                p = p[9:]
        if "%" in p:
            try:
                p = unquote(p)
            except Exception:
                pass
        if sys.platform == "win32":
            p = p.replace("/", "\\")
        result.append(p)
    return result


def dnd_files_to_open(dnd_data: str) -> List[str]:
    """Jak parse_dnd_files, ale filtruje tylko istniejące pliki."""
    return [p for p in parse_dnd_files(dnd_data) if os.path.isfile(p)]


def is_compressed(path: str) -> bool:
    """Zwraca True jeśli ścieżka ma rozszerzenie kompresji."""
    p = path.lower()
    return p.endswith(".gz") or p.endswith(".gzip") \
        or p.endswith(".bz2") or p.endswith(".bzip2") \
        or p.endswith(".xz") or p.endswith(".lzma") or p.endswith(".lz")


def open_maybe_compressed(path: str, mode: str = "rb"):
    """Otwiera plik, transparentnie dekompresując na podstawie rozszerzenia."""
    p = path.lower()
    if p.endswith(".gz") or p.endswith(".gzip"):
        return gzip.open(path, mode)
    if p.endswith(".bz2") or p.endswith(".bzip2"):
        return bz2.open(path, mode)
    if p.endswith(".xz") or p.endswith(".lzma") or p.endswith(".lz"):
        return lzma.open(path, mode)
    return open(path, mode)

def get_resource_path(relative_path: str) -> str:
    """Zwraca bezwzględną ścieżkę do zasobu, działa zarówno w środowisku deweloperskim jak i po spakowaniu PyInstallerem."""
    try:
        # PyInstaller tworzy folder tymczasowy i zapisuje ścieżkę do niego w _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        # Jeśli nie spakowane, root to folder nadrzędny wobec log_reader
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)
