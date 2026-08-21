#!/usr/bin/env python3
"""scripts/compile_ui.py — Inkrementalna kompilacja plików .ui do formatu Python (PySide6)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    ui_dir = repo_root / "log_viewer" / "ui"

    if not ui_dir.is_dir():
        print(f"Error: UI directory not found at {ui_dir}")
        sys.exit(1)

    ui_files = [f for f in ui_dir.iterdir() if f.is_file() and f.suffix == ".ui"]
    if not ui_files:
        print("No .ui files found.")
        sys.exit(0)

    compiled_any = False
    for ui_path in sorted(ui_files):
        output_name = f"ui_{ui_path.stem}.py"
        output_path = ui_dir / output_name

        # Sprawdzenie czasów modyfikacji - kompilacja przyrostowa
        # Kompiluj jeśli wygenerowany plik nie istnieje lub jeśli plik .ui jest nowszy
        needs_compile = True
        if output_path.exists():
            input_mtime = ui_path.stat().st_mtime
            output_mtime = output_path.stat().st_mtime
            if input_mtime <= output_mtime:
                needs_compile = False

        if needs_compile:
            print(f"Compiling {ui_path.name} -> {output_name}")
            try:
                exec_dir = Path(sys.executable).resolve().parent
                uic_cmd = exec_dir / ("pyside6-uic.exe" if os.name == "nt" else "pyside6-uic")
                uic_cmd_str = str(uic_cmd) if uic_cmd.is_file() else "pyside6-uic"

                subprocess.run([uic_cmd_str, str(ui_path), "-o", str(output_path)], check=True)
                compiled_any = True
            except subprocess.CalledProcessError as e:
                print(f"Error compiling {ui_path.name}: {e}")
                sys.exit(1)
            except FileNotFoundError:
                print("Error: pyside6-uic not found. Please make sure PySide6 is installed.")
                sys.exit(1)

    if compiled_any:
        print("UI compilation complete.")
    else:
        print("All UI files are up to date.")


if __name__ == "__main__":
    main()
