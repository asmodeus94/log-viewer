#!/usr/bin/env python3
"""
Główny skrypt uruchomieniowy.
Automatycznie kompiluje zmienione pliki .ui (kompilacja przyrostowa)
a następnie uruchamia aplikację Log Viewer.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    # Krok 1: Skompiluj pliki UI przyrostowo
    repo_root = Path(__file__).resolve().parent

    # Dodajemy ścieżkę do sys.path aby zaimportować moduł
    scripts_dir = repo_root / "scripts"
    scripts_dir_str = str(scripts_dir)
    if scripts_dir_str not in sys.path:
        sys.path.insert(0, scripts_dir_str)

    try:
        import compile_ui

        # Przechwytujemy sys.exit jeśli pojawiłby się w skrypcie (np. brak PySide6)
        try:
            compile_ui.main()
        except SystemExit as e:
            if e.code != 0:
                print(f"UI compilation failed (exit code {e.code}). Application will not be started.")
                sys.exit(e.code)
    except ImportError:
        print("Error: Could not import scripts/compile_ui.py.")
        sys.exit(1)

    # Krok 2: Uruchom aplikację log_viewer
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    try:
        from log_viewer.main import main as log_viewer_main

        log_viewer_main()
    except ImportError as e:
        print(f"Error: Could not start log_viewer. ({e})")
        sys.exit(1)


if __name__ == "__main__":
    main()
