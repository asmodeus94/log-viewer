#!/usr/bin/env python3
"""
scripts/verify_hook.py — Adapter Lifecycle Hooka 'Stop' dla Antigravity CLI.

Gdy agent AI próbuje zakończyć turę (model_stop), hook:
1. Sprawdza, czy zmodyfikowano pliki źródłowe (.py, .ui).
2. Jeśli tak, uruchamia weryfikację jakości (scripts/verify.py).
3. W przypadku błędów, blokuje zatrzymanie (decision: continue) i wstrzykuje komunikat o błędach.
4. W przypadku powodzenia lub braku modyfikacji kodu, pozwala na zakończenie (decision: allow).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_python_executable(repo_root: Path) -> str:
    if os.name == "nt":
        venv_py = repo_root / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = repo_root / ".venv" / "bin" / "python"

    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def has_modified_source_files(repo_root: Path) -> bool:
    """Sprawdza czy w repozytorium zmodyfikowano pliki źródłowe Python/UI."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return False

        lines = res.stdout.strip().splitlines()
        for line in lines:
            # Format: ' M log_viewer/widgets.py' lub '?? new_file.py'
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                file_path = parts[1]
                if file_path.endswith((".py", ".ui", ".toml")):
                    return True
        return False
    except Exception:
        return False


def main() -> None:
    repo_root = get_repo_root()
    py_exe = get_python_executable(repo_root)

    # Jeśli nie ma zmodyfikowanych plików źródłowych, nie blokujemy
    if not has_modified_source_files(repo_root):
        print(json.dumps({"decision": "allow"}))
        return

    # Uruchamiamy weryfikację bramki jakości (UI + Lint + MyPy + Test)
    verify_script = repo_root / "scripts" / "verify.py"
    proc = subprocess.run(
        [py_exe, str(verify_script)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )

    if proc.returncode == 0:
        # Sukces - zezwalamy na zatrzymanie
        print(json.dumps({"decision": "allow"}))
    else:
        # Błąd - wymuszamy kontynuację i przekazujemy szczegóły błędów
        output = (proc.stdout + "\n" + proc.stderr).strip()
        # Przycinamy długość wyjścia w razie potrzeby
        if len(output) > 4000:
            output = output[-4000:]

        reason = (
            "BRAMKA JAKOŚCI (Antigravity Quality Gate): Wykryto błędy lintera, typowania lub testów "
            "w zmodyfikowanym kodzie. Zanim zakończysz odpowiedź, napraw błędy i doprowadź scripts/verify.py "
            f"do kodu wyjścia 0.\n\nWynik weryfikacji:\n{output}"
        )
        print(json.dumps({"decision": "continue", "reason": reason}))


if __name__ == "__main__":
    main()
