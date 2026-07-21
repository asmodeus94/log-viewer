#!/usr/bin/env python3
"""
Skrypt budujący aplikację log-viewer za pomocą PyInstallera.
Automatycznie:
1. Kompiluje pliki UI (.ui -> .py).
2. Konwertuje ikonę assets/icon.png na format natywny (.icns na macOS, .ico na Windows).
3. Wywołuje PyInstallera z odpowiednimi argumentami (w tym --windowed).
"""

import os
import sys
import platform
import subprocess

def get_repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def compile_ui_files(repo_root):
    print(">>> Kompilowanie plików UI...")
    compile_script = os.path.join(repo_root, "scripts", "compile_ui.py")
    try:
        subprocess.run([sys.executable, compile_script], check=True)
    except subprocess.CalledProcessError as e:
        print("Błąd kompilacji plików UI. Przerywam budowanie.")
        sys.exit(1)

def convert_icon(repo_root, target_format):
    print(f">>> Konwertowanie ikony do formatu {target_format}...")
    try:
        from PIL import Image
    except ImportError:
        print("Błąd: Biblioteka Pillow nie jest zainstalowana. Konwersja ikony nie powiedzie się.")
        print("Zainstaluj zależności dev: pip install -r requirements-dev.txt")
        sys.exit(1)

    icon_png_path = os.path.join(repo_root, "assets", "icon.png")
    if not os.path.exists(icon_png_path):
        print(f"Błąd: Nie znaleziono pliku ikony pod ścieżką: {icon_png_path}")
        sys.exit(1)

    icon_out_path = os.path.join(repo_root, "assets", f"icon{target_format}")
    try:
        img = Image.open(icon_png_path)
        if target_format == ".icns":
            img.save(icon_out_path, format="ICNS")
        elif target_format == ".ico":
            img.save(icon_out_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        print(f"Pomyślnie wygenerowano {icon_out_path}")
        return icon_out_path
    except Exception as e:
        print(f"Błąd podczas konwersji ikony: {e}")
        if platform.system() == "Linux":
            return icon_png_path
        sys.exit(1)

def build_app():
    repo_root = get_repo_root()
    os.chdir(repo_root)

    # 1. Kompilacja plików UI
    compile_ui_files(repo_root)

    # 2. Ustalanie platformy i konwersja ikony
    system_name = platform.system()
    icon_ext = ""
    if system_name == "Darwin":
        icon_ext = ".icns"
    elif system_name == "Windows":
        icon_ext = ".ico"

    if icon_ext:
        icon_path = convert_icon(repo_root, icon_ext)
    else:
        icon_path = os.path.join(repo_root, "assets", "icon.png")

    print(f">>> Wykonywanie budowania przez PyInstaller (System: {system_name})...")

    # 3. Argumenty PyInstaller
    # Generujemy w locie mały plik startowy, żeby uniknąć rzucania ImportError (relative import)
    # gdy próbujemy odpalić paczkę używając log_reader/main.py jako bezpośredniego script-file'a.
    frozen_main = os.path.join(repo_root, "run_frozen.py")
    with open(frozen_main, "w") as f:
        f.write("import multiprocessing\nfrom log_reader.main import main\n\nif __name__ == '__main__':\n    multiprocessing.freeze_support()\n    main()\n")

    main_script = frozen_main

    # Separator dla --add-data różni się w zależności od systemu
    path_separator = ";" if system_name == "Windows" else ":"
    add_data_arg = f"assets{path_separator}assets"

    pyinstaller_args = [
        "pyinstaller",
        "--noconfirm",
        "--windowed",
        "--name", "log-viewer",
        f"--add-data={add_data_arg}",
    ]

    if os.path.exists(icon_path):
        pyinstaller_args.extend(["--icon", icon_path])

    pyinstaller_args.append(main_script)

    try:
        subprocess.run(pyinstaller_args, check=True)
        print("\n>>> Budowanie zakończone pomyślnie!")
        print(f">>> Plik wykonywalny znajdziesz w folderze: {os.path.join(repo_root, 'dist')}")
    except subprocess.CalledProcessError as e:
        print(f"\nBłąd podczas budowania: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("\nBłąd: Nie znaleziono polecenia 'pyinstaller'.")
        print("Zainstaluj zależności dev: pip install -r requirements-dev.txt")
        sys.exit(1)

if __name__ == "__main__":
    build_app()
