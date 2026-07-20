#!/usr/bin/env python3
import os
import subprocess
import sys

def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ui_dir = os.path.join(repo_root, "log_reader", "ui")

    if not os.path.isdir(ui_dir):
        print(f"Error: UI directory not found at {ui_dir}")
        sys.exit(1)

    ui_files = [f for f in os.listdir(ui_dir) if f.endswith(".ui")]
    if not ui_files:
        print("No .ui files found.")
        sys.exit(0)

    compiled_any = False
    for ui_file in ui_files:
        input_path = os.path.join(ui_dir, ui_file)
        output_name = f"ui_{os.path.splitext(ui_file)[0]}.py"
        output_path = os.path.join(ui_dir, output_name)

        # Sprawdzenie czasów modyfikacji - kompilacja przyrostowa
        # Kompiluj jeśli wygenerowany plik nie istnieje lub jeśli plik .ui jest nowszy
        needs_compile = True
        if os.path.exists(output_path):
            input_mtime = os.path.getmtime(input_path)
            output_mtime = os.path.getmtime(output_path)
            if input_mtime <= output_mtime:
                needs_compile = False

        if needs_compile:
            print(f"Compiling {ui_file} -> {output_name}")
            try:
                # We use pyside6-uic to generate python code
                subprocess.run(
                    ["pyside6-uic", input_path, "-o", output_path],
                    check=True
                )
                compiled_any = True
            except subprocess.CalledProcessError as e:
                print(f"Error compiling {ui_file}: {e}")
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
