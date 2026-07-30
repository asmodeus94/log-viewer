"""Fasada zachowująca wsteczną kompatybilność ze starszą strukturą modułów.
Importuje główną klasę okna z `main_window` oraz logikę zakładek z `log_tab`.
"""

from .log_tab import LogTab
from .main_window import LogViewerWindow

__all__ = [
    "LogTab",
    "LogViewerWindow",
]
