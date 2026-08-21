#!/usr/bin/env python3
"""
Skrypt budujący aplikację log-viewer za pomocą PyInstallera.
Automatycznie:
1. Kompiluje pliki UI (.ui -> .py).
2. Konwertuje ikonę assets/icon.png na format natywny (.icns na macOS, .ico na Windows).
3. Tworzy plik startowy run_frozen.py (obsługa multiprocessing.freeze_support).
4. Wywołuje PyInstallera z wykorzystaniem zoptymalizowanego pliku spec (wykluczenie zbędnych modułów Qt).
5. Na macOS opcjonalnie tworzy skompresowane archiwum dystrybucyjne .zip obok katalogu .app.
6. Wyświetla podsumowanie rozmiarów wygenerowanych artefaktów.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def get_repo_root() -> Path:
    """Zwraca główny katalog repozytorium."""
    return Path(__file__).resolve().parent.parent


def compile_ui_files(repo_root: Path) -> None:
    """Kompiluje pliki szablonów interfejsu .ui do kodu Pythona."""
    print(">>> Compiling UI files...")
    compile_script = repo_root / "scripts" / "compile_ui.py"
    try:
        subprocess.run([sys.executable, str(compile_script)], check=True)
    except subprocess.CalledProcessError:
        print("Error compiling UI files. Aborting build.")
        sys.exit(1)


def convert_icon(repo_root: Path, target_format: str) -> Path | None:
    """Konwertuje ikonę PNG na format ICO (Windows) lub ICNS (macOS)."""
    print(f">>> Converting icon to {target_format} format...")
    try:
        from PIL import Image
    except ImportError:
        print("Warning: Pillow library is not installed. Icon conversion skipped.")
        print("Install dev dependencies: pip install -r requirements-dev.txt")
        return None

    icon_png_path = repo_root / "assets" / "icon.png"
    if not icon_png_path.exists():
        print(f"Error: Icon file not found at: {icon_png_path}")
        sys.exit(1)

    icon_out_path = repo_root / "assets" / f"icon{target_format}"
    try:
        img = Image.open(icon_png_path)
        if target_format == ".icns":
            img.save(str(icon_out_path), format="ICNS")
        elif target_format == ".ico":
            img.save(
                str(icon_out_path),
                format="ICO",
                sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
            )
        print(f"Successfully generated: {icon_out_path}")
        return icon_out_path
    except Exception as e:
        print(f"Error during icon conversion: {e}")
        return None


def check_upx_available() -> bool:
    """Sprawdza, czy kompresor UPX jest dostępny w ścieżce systemowej."""
    upx_path = shutil.which("upx")
    if upx_path:
        print(f">>> Detected UPX: {upx_path} (binary compression active)")
        return True
    print(">>> UPX is not installed in PATH (building without UPX compression)")
    return False


def create_macos_archive(dist_dir: Path) -> Path | None:
    """Tworzy skompresowane archiwum zip dla paczki .app na macOS."""
    app_path = dist_dir / "log-viewer.app"
    if not app_path.exists():
        return None

    zip_path = dist_dir / "log-viewer-macos.zip"
    print(f">>> Creating distribution archive: {zip_path.name}...")

    # Używamy natywnego ditto na macOS dla zachowania uprawnień i symlinków Mach-O,
    # a jako fallback modułu zipfile
    if shutil.which("ditto"):
        cmd = ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app_path), str(zip_path)]
        res = subprocess.run(cmd, check=False)
        if res.returncode == 0:
            return zip_path

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_p in app_path.rglob("*"):
                if file_p.is_file():
                    arcname = file_p.relative_to(dist_dir)
                    zf.write(file_p, arcname)
        return zip_path
    except Exception as e:
        print(f"Warning: Could not create zip archive: {e}")
        return None


def print_build_summary(dist_dir: Path) -> None:
    """Wyświetla podsumowanie wygenerowanych plików i ich rozmiarów."""
    print("\n==================================================")
    print("             BUILD ARTIFACTS SUMMARY              ")
    print("==================================================")
    if not dist_dir.exists():
        print("No dist directory found.")
        return

    found_artifacts = False
    for item in sorted(dist_dir.iterdir()):
        if item.is_file():
            size_mb = item.stat().st_size / (1024 * 1024)
            print(f" - {item.name:<30} {size_mb:>8.2f} MB")
            found_artifacts = True
        elif item.is_dir() and item.name.endswith(".app"):
            total_size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
            size_mb = total_size / (1024 * 1024)
            print(f" - {item.name + ' (bundle)':<30} {size_mb:>8.2f} MB")
            found_artifacts = True

    if not found_artifacts:
        print("No generated files found in dist/.")
    print("==================================================\n")


def build_app() -> None:
    """Główna procedura budowania aplikacji."""
    repo_root = get_repo_root()
    os.chdir(repo_root)

    # 1. Kompilacja plików UI
    compile_ui_files(repo_root)

    # 2. Ustalenie platformy i przygotowanie ikony
    system_name = platform.system()
    icon_ext = ".icns" if system_name == "Darwin" else ".ico"
    convert_icon(repo_root, icon_ext)

    # 3. Sprawdzenie UPX
    check_upx_available()

    # 4. Generowanie pliku startowego run_frozen.py
    frozen_main = repo_root / "run_frozen.py"
    with open(frozen_main, "w", encoding="utf-8") as f:
        f.write(
            "import multiprocessing\n"
            "from log_viewer.main import main\n\n"
            "if __name__ == '__main__':\n"
            "    multiprocessing.freeze_support()\n"
            "    main()\n"
        )

    spec_file = repo_root / "log-viewer.spec"
    dist_dir = repo_root / "dist"
    build_dir = repo_root / "build"

    print(f">>> Executing PyInstaller build (System: {system_name}, Spec: {spec_file.name})...")

    pyinstaller_args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        str(spec_file),
    ]

    try:
        subprocess.run(pyinstaller_args, check=True)
        print("\n>>> PyInstaller build completed successfully!")

        if system_name == "Darwin":
            create_macos_archive(dist_dir)

        print_build_summary(dist_dir)
        print(f">>> You can find the executables in the folder: {dist_dir}")
    except subprocess.CalledProcessError as e:
        print(f"\nError during build: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("\nError: 'PyInstaller' module not found.")
        print("Install dev dependencies: pip install -r requirements-dev.txt")
        sys.exit(1)


if __name__ == "__main__":
    build_app()
