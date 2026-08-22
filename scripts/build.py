#!/usr/bin/env python3
"""
Skrypt budujący aplikacje projektu (log-viewer oraz opcjonalnie generate-logs) za pomocą PyInstallera.

Automatycznie:
1. Przetwarza argumenty wiersza poleceń (--target, --clean).
2. Domyślnie buduje wyłącznie główną aplikację 'log-viewer' (chyba że wskazano inaczej).
3. Kompiluje pliki UI (.ui -> .py) przed budowaniem aplikacji głównej.
4. Konwertuje ikonę assets/icon.png na format natywny (.icns na macOS, .ico na Windows).
5. Tworzy plik startowy run_frozen.py dla aplikacji okienkowej.
6. Wywołuje PyInstallera z wykorzystaniem zoptymalizowanych plików spec.
7. Na macOS opcjonalnie tworzy skompresowane archiwum dystrybucyjne .zip obok katalogu .app.
8. Wyświetla podsumowanie rozmiarów wygenerowanych artefaktów w dist/.
"""

from __future__ import annotations

import argparse
import importlib
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


def clean_build_dirs(repo_root: Path) -> None:
    """Czyści katalogi wyjściowe dist/ i build/."""
    print(">>> Cleaning build and dist directories...")
    for folder_name in ("dist", "build"):
        folder_path = repo_root / folder_name
        if folder_path.exists():
            try:
                shutil.rmtree(folder_path)
                print(f"    Removed: {folder_path}")
            except OSError as e:
                print(f"    Warning: Could not remove {folder_path}: {e}")


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
        pil_image = importlib.import_module("PIL.Image")
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
        img = pil_image.open(icon_png_path)
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
    # noinspection PyDeprecation
    upx_path = shutil.which("upx")
    if upx_path:
        print(f">>> Detected UPX: {upx_path} (binary compression active)")
        return True
    print(">>> UPX is not installed in PATH (building without UPX compression)")
    return False


def create_macos_archive(dist_dir: Path, app_name: str) -> Path | None:
    """Tworzy skompresowane archiwum zip dla paczki .app na macOS."""
    app_path = dist_dir / f"{app_name}.app"
    if not app_path.exists():
        return None

    zip_path = dist_dir / f"{app_name}-macos.zip"
    print(f">>> Creating distribution archive: {zip_path.name}...")

    # Używamy natywnego ditto na macOS dla zachowania uprawnień i symlinków Mach-O,
    # a jako fallback modułu zipfile
    # noinspection PyDeprecation
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


def print_build_summary(dist_dir: Path, target_names: list[str] | None = None) -> None:
    """Wyświetla podsumowanie wygenerowanych plików i ich rozmiarów."""
    print("\n==================================================")
    print("             BUILD ARTIFACTS SUMMARY              ")
    print("==================================================")
    if not dist_dir.exists():
        print("No dist directory found.")
        return

    found_artifacts = False
    for item in sorted(dist_dir.iterdir()):
        if target_names is not None and not any(item.name.startswith(name) for name in target_names):
            continue

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
        print("No generated files found for current target in dist/.")
    print("==================================================\n")


def create_frozen_entrypoint(repo_root: Path) -> Path:
    """Tworzy plik startowy run_frozen.py dla aplikacji log-viewer."""
    frozen_main = repo_root / "run_frozen.py"
    with open(frozen_main, "w", encoding="utf-8") as f:
        f.write(
            "import multiprocessing\n"
            "from log_viewer.main import main\n\n"
            "if __name__ == '__main__':\n"
            "    multiprocessing.freeze_support()\n"
            "    main()\n"
        )
    return frozen_main


def build_single_target(
    repo_root: Path,
    target_name: str,
    spec_filename: str,
    dist_dir: Path,
    build_dir: Path,
    system_name: str,
) -> None:
    """Buduje pojedynczy cel za pomocą PyInstallera."""
    spec_file = repo_root / spec_filename
    if not spec_file.exists():
        print(f"Error: Spec file not found: {spec_file}")
        sys.exit(1)

    print(f"\n>>> Executing PyInstaller build for '{target_name}' (Spec: {spec_filename})...")

    pyinstaller_args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir / target_name),
        str(spec_file),
    ]

    try:
        subprocess.run(pyinstaller_args, check=True)
        print(f">>> PyInstaller build for '{target_name}' completed successfully!")

        if system_name == "Darwin":
            create_macos_archive(dist_dir, target_name)
    except subprocess.CalledProcessError as e:
        print(f"\nError during build of '{target_name}': {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("\nError: 'PyInstaller' module not found.")
        print("Install dev dependencies: pip install -r requirements-dev.txt")
        sys.exit(1)


def build_app(target: str = "viewer", clean: bool = False) -> None:
    """Główna procedura budowania wybranych celów aplikacji."""
    repo_root = get_repo_root()
    os.chdir(repo_root)

    # 1. Opcjonalne czyszczenie
    if clean:
        clean_build_dirs(repo_root)

    # 2. Ustalenie listy celów
    target_normalized = target.lower()
    targets_to_build: list[str] = []
    if target_normalized in ("viewer", "log-viewer"):
        targets_to_build = ["viewer"]
    elif target_normalized in ("generator", "generate-logs", "log-generator"):
        targets_to_build = ["generator"]
    elif target_normalized == "all":
        targets_to_build = ["viewer", "generator"]
    else:
        print(f"Error: Unknown build target '{target}'. Allowed: viewer, generator, all.")
        sys.exit(1)

    # 3. Przygotowanie ikony
    system_name = platform.system()
    icon_ext = ".icns" if system_name == "Darwin" else ".ico"
    convert_icon(repo_root, icon_ext)

    # 4. Sprawdzenie UPX
    check_upx_available()

    dist_dir = repo_root / "dist"
    build_dir = repo_root / "build"

    # 5. Budowanie celów
    built_target_names: list[str] = []
    for current_target in targets_to_build:
        if current_target == "viewer":
            target_name = "log-viewer"
            built_target_names.append(target_name)
            compile_ui_files(repo_root)
            create_frozen_entrypoint(repo_root)
            build_single_target(
                repo_root=repo_root,
                target_name=target_name,
                spec_filename="log-viewer.spec",
                dist_dir=dist_dir,
                build_dir=build_dir,
                system_name=system_name,
            )
        elif current_target == "generator":
            target_name = "generate-logs"
            built_target_names.append(target_name)
            build_single_target(
                repo_root=repo_root,
                target_name=target_name,
                spec_filename="generate-logs.spec",
                dist_dir=dist_dir,
                build_dir=build_dir,
                system_name=system_name,
            )

    # 6. Podsumowanie
    print_build_summary(dist_dir, built_target_names)
    print(f">>> You can find the executables in the folder: {dist_dir}")


def main() -> None:
    """Punkt wejściowy skryptu z obsługą argumentów CLI."""
    parser = argparse.ArgumentParser(
        description="Skrypt budujący pliki wykonywalne aplikacji Log Viewer i narzędzi pomocniczych za pomocą PyInstallera."
    )
    parser.add_argument(
        "--target",
        "-t",
        choices=["viewer", "generator", "all", "log-viewer", "generate-logs"],
        default="viewer",
        help="Cel budowania: 'viewer' (domyślny, aplikacja Log Viewer), 'generator' (narzędzie Generate Logs) lub 'all' (obie aplikacje).",
    )
    parser.add_argument(
        "--clean",
        "-c",
        action="store_true",
        help="Wyczyść katalogi wyjściowe dist/ i build/ przed rozpoczęciem budowania.",
    )

    args = parser.parse_args()
    build_app(target=args.target, clean=args.clean)


if __name__ == "__main__":
    main()
