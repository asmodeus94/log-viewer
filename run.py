#!/usr/bin/env python3
"""
Główny skrypt uruchomieniowy.
Automatycznie kompiluje zmienione pliki .ui (kompilacja przyrostowa)
a następnie uruchamia aplikację Log Viewer.
"""
import sys
import os

def main():
    # Krok 1: Skompiluj pliki UI przyrostowo
    repo_root = os.path.dirname(os.path.abspath(__file__))
    compile_script = os.path.join(repo_root, "scripts", "compile_ui.py")

    # Dodajemy ścieżkę do sys.path aby zaimportować moduł
    scripts_dir = os.path.join(repo_root, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

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

    # Krok 2: Uruchom aplikację log_reader
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    try:
        from log_reader.main import main as log_reader_main
        log_reader_main()
    except ImportError as e:
        print(f"Error: Could not start log_reader. ({e})")
        sys.exit(1)

if __name__ == "__main__":
    main()
