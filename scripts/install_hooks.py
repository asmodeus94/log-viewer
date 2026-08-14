#!/usr/bin/env python3
"""
scripts/install_hooks.py — Instaluje Git hook pre-commit w repozytorium.

Uruchomienie:
    python scripts/install_hooks.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    hooks_dir = repo_root / ".git" / "hooks"

    if not hooks_dir.parent.exists():
        print("Błąd: Nie znaleziono katalogu .git. Upewnij się, że jesteś w repozytorium Git.")
        return 1

    hooks_dir.mkdir(parents=True, exist_ok=True)
    pre_commit_file = hooks_dir / "pre-commit"

    # Skrypt pre-commit w powłoce sh (obsługiwany przez Git na Windowsie przez Git Bash oraz na Linux/macOS)
    hook_content = """#!/bin/sh
# Git Pre-commit Hook — Log Viewer Quality Gate
echo "[PRE-COMMIT] Uruchamianie bramki jakości (scripts/verify.py)..."

if [ -f ".venv/Scripts/python.exe" ]; then
    PYTHON_EXE=".venv/Scripts/python.exe"
elif [ -f ".venv/bin/python" ]; then
    PYTHON_EXE=".venv/bin/python"
else
    PYTHON_EXE="python"
fi

$PYTHON_EXE scripts/verify.py
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "[BŁĄD PRE-COMMIT] Bramka jakości nie przeszła (kod wyjścia: $EXIT_CODE)."
    echo "Zatwierdzenie zmian (commit) zostało zablokowane."
    echo "Popraw błędy lub uruchom 'python scripts/verify.py --fix'."
    exit $EXIT_CODE
fi

echo "[PRE-COMMIT] Bramka jakości zakończona sukcesem. Zatwierdzanie commita..."
exit 0
"""

    with open(pre_commit_file, "w", encoding="utf-8", newline="\n") as f:
        f.write(hook_content)

    # Ustawienie uprawnień do wykonywania na systemach POSIX
    try:
        current_mode = os.stat(pre_commit_file).st_mode
        os.chmod(pre_commit_file, current_mode | 0o755)
    except Exception:
        pass

    print(f"Pomyślnie zainstalowano Git pre-commit hook w: {pre_commit_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
