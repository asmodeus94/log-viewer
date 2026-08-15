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
        print("Error: .git directory not found. Make sure you are in a Git repository.")
        return 1

    hooks_dir.mkdir(parents=True, exist_ok=True)
    pre_commit_file = hooks_dir / "pre-commit"

    # Skrypt pre-commit w powłoce sh (obsługiwany przez Git na Windowsie przez Git Bash oraz na Linux/macOS)
    hook_content = """#!/bin/sh
# Git Pre-commit Hook — Log Viewer Quality Gate
echo "[PRE-COMMIT] Running Quality Gate (scripts/verify.py)..."

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
    echo "[PRE-COMMIT ERROR] Quality Gate failed (exit code: $EXIT_CODE)."
    echo "Commit was blocked."
    echo "Fix the errors or run 'python scripts/verify.py --fix'."
    exit $EXIT_CODE
fi

echo "[PRE-COMMIT] Quality Gate passed successfully. Proceeding with commit..."
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

    print(f"Successfully installed Git pre-commit hook in: {pre_commit_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
