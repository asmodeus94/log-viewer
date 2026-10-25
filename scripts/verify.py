#!/usr/bin/env python3
"""scripts/verify.py — Centralna bramka jakości kodu (Quality Gate).

Sprawdza kod pod kątem:
1. Kompilacji plików interfejsu .ui (compile_ui.py)
2. Formatowania i analizy statycznej linterem (ruff format & ruff check)
3. Zgodności typów (mypy: core, scripts oraz tests)
4. Analizy AST pod kątem duplikacji kodu, nadpisywanych zmiennych i konwencji testów (ast_check.py)
5. Automatycznej walidacji raportów SARIF (*.sarif, *.sarif.json)
6. Poprawności działania testów jednostkowych (pytest)
7. Opcjonalnych testów wydajnościowych / benchmarków (--benchmark)

Użycie:
    python scripts/verify.py                # Pełna weryfikacja
    python scripts/verify.py --fix          # Automatyczna naprawa formatowania i importów + weryfikacja
    python scripts/verify.py --quick        # Szybka weryfikacja (UI + Lint + MyPy + AST Inspect, bez pytest)
    python scripts/verify.py --benchmark    # Uruchomienie benchmarków wydajnościowych (pytest-benchmark)
    python scripts/verify.py --step ui      # Tylko wybrany krok: ui, lint, mypy, inspect, sarif, test, benchmark
    python scripts/verify.py --sarif        # Ręczna walidacja raportów SARIF w repozytorium
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ast_check import run_ast_checks

# Kolory ANSI do czytelnego formatowania w terminalu
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def enable_windows_ansi() -> None:
    """Włącza obsługę sekwencji ANSI w konsoli Windows bez uruchamiania podprocesu powłoki."""
    if os.name == "nt" and hasattr(ctypes, "windll"):
        kernel32: Any = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


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


def cleanup_benchmarks(repo_root: Path) -> None:
    """Usuwa ewentualne pozostałości po pytest-benchmark."""
    for pattern in (".benchmarks", ".benchmark"):
        for p in repo_root.rglob(pattern):
            if p.is_dir() and ".venv" not in p.parts:
                shutil.rmtree(p, ignore_errors=True)


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
    """Krok 3: Analiza typów statycznych (mypy dla log_viewer, scripts oraz tests)."""
    # 3a: Kod główny i skrypty weryfikacyjne
    targets_core = ["log_viewer", "scripts"]
    cmd_core = [py_exe, "-m", "mypy"] + targets_core
    code_core, _ = run_command(cmd_core, repo_root, "Step 3a: Static type check - core & scripts (mypy)")
    if code_core != 0:
        return False

    # 3b: Pakiet testów (z włączonym check_untyped_defs i wyłączonym wymogiem sygnatur typów funkcji)
    cmd_tests = [
        py_exe,
        "-m",
        "mypy",
        "tests",
        "--check-untyped-defs",
        "--disable-error-code",
        "no-untyped-def",
        "--disable-error-code",
        "unreachable",
        "--disable-error-code",
        "unused-ignore",
        "--disable-error-code",
        "assignment",
    ]
    code_tests, _ = run_command(cmd_tests, repo_root, "Step 3b: Static type check - test suite (mypy)")
    return code_tests == 0


def step_inspect(repo_root: Path) -> bool:
    """Krok 4: Analiza AST (DuplicatedCode, nadpisywane przypisania PyUnusedLocal oraz konwencje testów)."""
    print(
        f"{CYAN}{BOLD}--> Step 4: AST Code Quality Inspection (DuplicatedCode, Dead Stores & Test Conventions){RESET}"
    )
    issues = run_ast_checks(repo_root)
    if issues:
        for issue in issues:
            print(f"    {YELLOW}{issue}{RESET}")
        print(f"\n{RED}{BOLD}AST inspection failed: found {len(issues)} issue(s).{RESET}")
        return False
    print(
        f"    {GREEN}All AST quality checks passed (0 duplicate blocks, 0 dead stores, 0 convention violations).{RESET}"
    )
    return True


def _resolve_sarif_file_path(uri: str, repo_root: Path) -> Path | None:
    """Rozwiązuje ścieżkę pliku z formatu SARIF (URI / ścieżka względna) na obiekt Path."""
    clean = uri.replace("\\", "/")
    if "file:/" in clean:
        idx = clean.find("file:/")
        path_str = clean[idx + 6 :]
        if path_str.startswith("//"):
            path_str = path_str[2:]
        if path_str.startswith("/") and len(path_str) > 2 and path_str[2] == ":":
            path_str = path_str[1:]
        p = Path(path_str)
    else:
        p = repo_root / uri

    if p.is_file():
        return p

    # Sprawdzenie relatywnie do repo_root
    try:
        cand = repo_root / Path(clean).name
        if cand.is_file():
            return cand
    except Exception:
        pass

    filename = Path(clean).name
    matches = list(repo_root.rglob(filename))
    if matches and matches[0].is_file():
        return matches[0]

    return None


def _is_sarif_issue_active(file_path: Path, start_line: int | None, snippet: str | None) -> bool:
    """Sprawdza, czy problem z raportu SARIF nadal występuje w kodzie źródłowym.

    Jeśli fragment kodu (snippet) nie występuje już w pliku w okolicach linii
    lub w ogóle w pliku, problem uznaje się za rozwiązany (stale SARIF report).
    """
    if not snippet or not snippet.strip():
        # Brak fragmentu w raporcie — uznajemy zgłoszenie za aktywne
        return True

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return True

    needle = snippet.strip()

    # Jeśli linia jest znana, sprawdź najpierw w oknie ±5 linii
    if start_line is not None and 1 <= start_line <= len(lines):
        start_idx = max(0, start_line - 6)
        end_idx = min(len(lines), start_line + 5)
        for i in range(start_idx, end_idx):
            if needle in lines[i]:
                return True

    # Jeśli nie ma w oknie, sprawdź czy występuje w całym pliku
    return any(needle in line for line in lines)


def step_sarif(repo_root: Path, target_path: str | None = None) -> bool:
    """Krok weryfikacji raportów SARIF (np. z inspekcji IDE / Qodana).

    Automatycznie weryfikuje, czy zaraportowane fragmenty kodu nadal występują w plikach
    źródłowych, odróżniając aktywne problemy od już naprawionych.
    """
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

    active_issues = 0
    resolved_count = 0

    for sarif_file in sarif_files:
        rel_name = sarif_file.relative_to(repo_root) if sarif_file.is_relative_to(repo_root) else sarif_file.name
        print(f"    Scanning {YELLOW}{rel_name}{RESET}...")
        try:
            with open(sarif_file, encoding="utf-8") as sarif_fh:
                data = json.load(sarif_fh)
        except (OSError, ValueError) as e:
            print(f"    {RED}Error reading SARIF file {rel_name}: {e}{RESET}")
            active_issues += 1
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
                    snippet_text: str | None = None
                    start_line: int | None = None
                    file_path: Path | None = None

                    if locations:
                        phys = locations[0].get("physicalLocation", {})
                        art = phys.get("artifactLocation", {}).get("uri", "")
                        region = phys.get("region", {})
                        start_line = region.get("startLine")
                        snippet_dict = region.get("snippet", {})
                        if isinstance(snippet_dict, dict):
                            snippet_text = snippet_dict.get("text")
                        loc_str = f" in {art}" + (f":{start_line}" if start_line else "")
                        if art:
                            file_path = _resolve_sarif_file_path(art, repo_root)

                    # Sprawdź czy problem jest wciąż aktywny w kodzie
                    is_active = True
                    if file_path is not None and file_path.is_file():
                        is_active = _is_sarif_issue_active(file_path, start_line, snippet_text)

                    if not is_active:
                        resolved_count += 1
                        continue

                    color = RED if level == "error" else YELLOW
                    print(f"    {color}[{tool_name} / {rule_id}] ({level}){loc_str}: {msg}{RESET}")
                    active_issues += 1

    if resolved_count > 0:
        print(f"    {GREEN}({resolved_count} previously reported issue(s) confirmed as resolved in code){RESET}")

    if active_issues > 0:
        print(f"\n{RED}{BOLD}SARIF validation failed: found {active_issues} active issue(s).{RESET}")
        return False

    print(f"    {GREEN}All scanned SARIF reports are clean (0 active issues).{RESET}")
    return True


def step_tests(repo_root: Path, py_exe: str) -> bool:
    """Krok 5: Testy jednostkowe (pytest)."""
    cmd = [py_exe, "-m", "pytest", "tests/", "--benchmark-disable"]
    code, _ = run_command(cmd, repo_root, "Step 5: Unit and GUI tests (pytest)")
    cleanup_benchmarks(repo_root)
    return code == 0


def step_benchmark(repo_root: Path, py_exe: str) -> bool:
    """Krok benchmarku: testy wydajnościowe (pytest-benchmark)."""
    cmd = [py_exe, "-m", "pytest", "tests/test_benchmarks.py", "--benchmark-enable"]
    code, _ = run_command(cmd, repo_root, "Benchmark Step: Performance tests (pytest-benchmark)")
    cleanup_benchmarks(repo_root)
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
        "--quick", "-q", action="store_true", help="Quick verification (UI + Lint + MyPy + Inspect, skips pytest)"
    )
    parser.add_argument(
        "--benchmark",
        "-b",
        action="store_true",
        help="Run performance benchmark tests with statistics (pytest-benchmark)",
    )
    parser.add_argument(
        "--step",
        choices=["ui", "lint", "mypy", "inspect", "sarif", "test", "benchmark"],
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

    cleanup_benchmarks(repo_root)

    if args.fix:
        if not step_format_and_fix(repo_root, py_exe):
            print(f"\n{RED}{BOLD}[ERROR] Auto-fix failed.{RESET}")
            return 1
        print(f"{GREEN}Auto-fix and formatting completed successfully.{RESET}\n")

    has_sarif_files = any(
        f.is_file() and (f.name.endswith(".sarif") or f.name.endswith(".sarif.json"))
        for f in repo_root.glob("*.sarif*")
    )

    steps_to_run = []
    if args.step:
        steps_to_run.append(args.step)
    elif args.quick:
        steps_to_run = ["ui", "lint", "mypy", "inspect"]
        if args.sarif or has_sarif_files:
            steps_to_run.append("sarif")
    else:
        steps_to_run = ["ui", "lint", "mypy", "inspect"]
        if args.sarif or has_sarif_files:
            steps_to_run.append("sarif")
        steps_to_run.append("test")
        if args.benchmark:
            steps_to_run.append("benchmark")

    for step in steps_to_run:
        success = False
        if step == "ui":
            success = step_compile_ui(repo_root, py_exe)
        elif step == "lint":
            success = step_lint(repo_root, py_exe)
        elif step == "mypy":
            success = step_typecheck(repo_root, py_exe)
        elif step == "inspect":
            success = step_inspect(repo_root)
        elif step == "sarif":
            success = step_sarif(repo_root, args.sarif)
        elif step == "test":
            success = step_tests(repo_root, py_exe)
        elif step == "benchmark":
            success = step_benchmark(repo_root, py_exe)

        if not success:
            print(f"\n{RED}{BOLD}[QUALITY GATE ERROR] Step '{step}' failed.{RESET}")
            cleanup_benchmarks(repo_root)
            return 1
        print(f"{GREEN}[OK] Step '{step}' passed successfully.{RESET}\n")

    cleanup_benchmarks(repo_root)
    print(f"{GREEN}{BOLD}=== SUCCESS: All quality checks passed successfully! ==={RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
