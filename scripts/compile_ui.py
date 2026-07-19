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

    for ui_file in ui_files:
        input_path = os.path.join(ui_dir, ui_file)
        output_name = f"ui_{os.path.splitext(ui_file)[0]}.py"
        output_path = os.path.join(ui_dir, output_name)

        print(f"Compiling {ui_file} -> {output_name}")
        try:
            # We use pyside6-uic to generate python code
            subprocess.run(
                ["pyside6-uic", input_path, "-o", output_path],
                check=True
            )
        except subprocess.CalledProcessError as e:
            print(f"Error compiling {ui_file}: {e}")
            sys.exit(1)
        except FileNotFoundError:
            print("Error: pyside6-uic not found. Please make sure PySide6 is installed.")
            sys.exit(1)

    print("UI compilation complete.")

if __name__ == "__main__":
    main()
