#!/usr/bin/env python3
"""
scripts/verify.py — Centralna bramka jakości kodu (Quality Gate).

Sprawdza kod pod kątem:
1. Kompilacji plików interfejsu .ui (compile_ui.py)
2. Formatowania i analizy statycznej linterem (ruff format & ruff check)
3. Zgodności typów (mypy)
4. Poprawności działania testów jednostkowych (pytest)
5. Opcjonalnej walidacji raportów SARIF (--sarif)

Użycie:
    python scripts/verify.py            # Pełna weryfikacja
    python scripts/verify.py --fix      # Automatyczna naprawa formatowania i importów + weryfikacja
    python scripts/verify.py --quick    # Szybka weryfikacja (UI + Lint + MyPy, bez pytest)
    python scripts/verify.py --step ui  # Tylko wybrany krok: ui, lint, mypy, sarif, test
    python scripts/verify.py --sarif    # Walidacja raportów SARIF w repozytorium
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Kolory ANSI do czytelnego formatowania w terminalu
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def enable_windows_ansi() -> None:
    """Włącza obsługę sekwencji ANSI w konsoli Windows."""
    if os.name == "nt":
        os.system("")


def get_repo_root() -> Path:
    """Zwraca ścieżkę do głównego katalogu repozytorium."""
    return Path(__file__).resolve().parent.parent


def get_python_executable(repo_root: Path) -> str:
    """Zwraca ścieżkę do interpretera w wirtualnym środowisku .venv lub sys.executable."""
    if os.name == "nt":
        venv_py = repo_root / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = repo_root / ".venv" / "bin" / "python"

    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def run_command(cmd: list[str], cwd: Path, description: str) -> tuple[int, str]:
    """Uruchamia polecenie i zwraca kod wyjścia oraz przechwycone wyjście."""
    print(f"{CYAN}{BOLD}--> {description}{RESET}")
    print(f"    {YELLOW}$ {' '.join(cmd)}{RESET}")

    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=False,
        check=False,
    )
    return proc.returncode, ""


def step_compile_ui(repo_root: Path, py_exe: str) -> bool:
    """Krok 1: Kompilacja plików interfejsu .ui."""
    compile_script = repo_root / "scripts" / "compile_ui.py"
    code, _ = run_command([py_exe, str(compile_script)], repo_root, "Step 1: UI Compilation (compile_ui.py)")
    return code == 0


def step_format_and_fix(repo_root: Path, py_exe: str) -> bool:
    """Automatyczne formatowanie i naprawa importów za pomocą ruff."""
    targets = ["log_viewer", "tests", "scripts", "dev-scripts", "run.py"]
    print(f"{CYAN}{BOLD}--> Auto-fixing linter and formatting (ruff)...{RESET}")
    cmd_fmt = [py_exe, "-m", "ruff", "format"] + targets
    code1, _ = run_command(cmd_fmt, repo_root, "Code formatting (ruff format)")

    cmd_fix = [py_exe, "-m", "ruff", "check", "--fix"] + targets
    code2, _ = run_command(cmd_fix, repo_root, "Fixing linter rules (ruff check --fix)")
    return code1 == 0 and code2 == 0


def step_lint(repo_root: Path, py_exe: str) -> bool:
    """Krok 2: Analiza statyczna i formatowanie (ruff)."""
    targets = ["log_viewer", "tests", "scripts", "dev-scripts", "run.py"]

    # 1. Sprawdzenie formatowania
    cmd_fmt_check = [py_exe, "-m", "ruff", "format", "--check"] + targets
    code_fmt, _ = run_command(cmd_fmt_check, repo_root, "Step 2a: Format check (ruff format --check)")
    if code_fmt != 0:
        print(f"{YELLOW}Hint: Run 'python scripts/verify.py --fix' to automatically format code.{RESET}")
        return False

    # 2. Sprawdzenie reguł lintera
    cmd_check = [py_exe, "-m", "ruff", "check"] + targets
    code_check, _ = run_command(cmd_check, repo_root, "Step 2b: Linter rules check (ruff check)")
    if code_check != 0:
        print(f"{YELLOW}Hint: Run 'python scripts/verify.py --fix' to automatically fix safe rules.{RESET}")
        return False

    return True


def step_typecheck(repo_root: Path, py_exe: str) -> bool:
    """Krok 3: Analiza typów statycznych (mypy)."""
    targets = ["log_viewer", "scripts"]
    cmd = [py_exe, "-m", "mypy"] + targets
    code, _ = run_command(cmd, repo_root, "Step 3: Static type check (mypy)")
    return code == 0


def step_sarif(repo_root: Path, target_path: str | None = None) -> bool:
    """Krok weryfikacji raportów SARIF (np. z inspekcji IDE / Qodana)."""
    print(f"{CYAN}{BOLD}--> Step: SARIF Reports Validation{RESET}")

    sarif_files: list[Path] = []
    if target_path and target_path != "auto":
        p = Path(target_path)
        if not p.is_absolute():
            p = repo_root / p
        if not p.exists():
            print(f"    {RED}Error: SARIF file not found: {p}{RESET}")
            return False
        sarif_files.append(p)
    else:
        for sarif_candidate in sorted(repo_root.glob("*.sarif*")):
            if sarif_candidate.is_file() and (
                sarif_candidate.name.endswith(".sarif") or sarif_candidate.name.endswith(".sarif.json")
            ):
                sarif_files.append(sarif_candidate)

    if not sarif_files:
        print(f"    {GREEN}No SARIF report files found to validate.{RESET}")
        return True

    total_issues = 0
    for sarif_file in sarif_files:
        rel_name = sarif_file.relative_to(repo_root) if sarif_file.is_relative_to(repo_root) else sarif_file.name
        print(f"    Scanning {YELLOW}{rel_name}{RESET}...")
        try:
            with open(sarif_file, encoding="utf-8") as sarif_fh:
                data = json.load(sarif_fh)
        except Exception as e:
            print(f"    {RED}Error reading SARIF file {rel_name}: {e}{RESET}")
            total_issues += 1
            continue

        runs = data.get("runs", [])
        for run in runs:
            tool_info = run.get("tool", {}).get("driver", {})
            tool_name = tool_info.get("name", "Analyzer")
            results = run.get("results", [])
            for res in results:
                level = res.get("level", "warning")
                kind = res.get("kind", "")
                if level in ("error", "warning") or kind == "fail":
                    rule_id = res.get("ruleId", "Rule")
                    msg = res.get("message", {}).get("text", "No message")
                    locations = res.get("locations", [])
                    loc_str = ""
                    if locations:
                        phys = locations[0].get("physicalLocation", {})
                        art = phys.get("artifactLocation", {}).get("uri", "")
                        region = phys.get("region", {})
                        line_no = region.get("startLine", "")
                        loc_str = f" in {art}" + (f":{line_no}" if line_no else "")

                    color = RED if level == "error" else YELLOW
                    print(f"    {color}[{tool_name} / {rule_id}] ({level}){loc_str}: {msg}{RESET}")
                    total_issues += 1

    if total_issues > 0:
        print(f"\n{RED}{BOLD}SARIF validation failed: found {total_issues} issue(s).{RESET}")
        return False

    print(f"    {GREEN}All scanned SARIF reports are clean (0 issues).{RESET}")
    return True


def step_tests(repo_root: Path, py_exe: str) -> bool:
    """Krok 4: Testy jednostkowe (pytest)."""
    cmd = [py_exe, "-m", "pytest", "tests/"]
    code, _ = run_command(cmd, repo_root, "Step 4: Unit and GUI tests (pytest)")
    return code == 0


def main() -> int:
    enable_windows_ansi()

    parser = argparse.ArgumentParser(description="Log Viewer Quality Gate")
    parser.add_argument(
        "--fix",
        "-f",
        action="store_true",
        help="Automatically format code and fix safe linter errors before verification",
    )
    parser.add_argument(
        "--quick", "-q", action="store_true", help="Quick verification (UI + Lint + MyPy, skips pytest)"
    )
    parser.add_argument(
        "--step",
        choices=["ui", "lint", "mypy", "sarif", "test"],
        help="Run only specific verification step",
    )
    parser.add_argument(
        "--sarif",
        nargs="?",
        const="auto",
        default=None,
        help="Validate SARIF report file(s) (optional path to specific file, default: scan repo root)",
    )

    args = parser.parse_args()
    repo_root = get_repo_root()
    py_exe = get_python_executable(repo_root)

    print(f"{BOLD}=== QUALITY GATE ==={RESET}")
    print(f"Working directory: {repo_root}")
    print(f"Interpreter:       {py_exe}\n")

    if args.fix:
        if not step_format_and_fix(repo_root, py_exe):
            print(f"\n{RED}{BOLD}[ERROR] Auto-fix failed.{RESET}")
            return 1
        print(f"{GREEN}Auto-fix and formatting completed successfully.{RESET}\n")

    steps_to_run = []
    if args.step:
        steps_to_run.append(args.step)
    elif args.quick:
        steps_to_run = ["ui", "lint", "mypy"]
        if args.sarif:
            steps_to_run.append("sarif")
    else:
        steps_to_run = ["ui", "lint", "mypy"]
        if args.sarif:
            steps_to_run.append("sarif")
        steps_to_run.append("test")

    for step in steps_to_run:
        success = False
        if step == "ui":
            success = step_compile_ui(repo_root, py_exe)
        elif step == "lint":
            success = step_lint(repo_root, py_exe)
        elif step == "mypy":
            success = step_typecheck(repo_root, py_exe)
        elif step == "sarif":
            success = step_sarif(repo_root, args.sarif)
        elif step == "test":
            success = step_tests(repo_root, py_exe)

        if not success:
            print(f"\n{RED}{BOLD}[QUALITY GATE ERROR] Step '{step}' failed.{RESET}")
            return 1
        print(f"{GREEN}[OK] Step '{step}' passed successfully.{RESET}\n")

    print(f"{GREEN}{BOLD}=== SUCCESS: All quality checks passed successfully! ==={RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
