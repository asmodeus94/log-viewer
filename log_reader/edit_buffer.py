"""EditBuffer — bufor edycji (line_no -> new_text) + zapis przez temp-file."""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Callable, Dict, Optional

from .exceptions import FileChangedError, CompressedSaveError
from .helpers import is_compressed


class EditBuffer:
    """Przechowuje zmiany line_no -> new_text. Bez modyfikacji pliku do czasu save()."""

    def __init__(self):
        self._edits: Dict[int, str] = {}

    def __len__(self) -> int:
        return len(self._edits)

    def has(self, line_no: int) -> bool:
        return line_no in self._edits

    def get(self, line_no: int) -> Optional[str]:
        return self._edits.get(line_no)

    def set(self, line_no: int, new_text: str) -> None:
        self._edits[line_no] = new_text

    def discard(self, line_no: int) -> None:
        self._edits.pop(line_no, None)

    def clear(self) -> None:
        self._edits.clear()

    def items(self):
        return self._edits.items()

    def save_to_file(self, src_path: str, progress_cb: Optional[Callable[[float], None]] = None,
                     expected_mtime: Optional[float] = None,
                     expected_size: Optional[int] = None,
                     encoding: str = "utf-8") -> str:
        """
        Tworzy backup src_path.bak, zapisuje nową treść do src_path.
        Walidacja mtime/size. Zachowuje uprawnienia. Blokuje dla skompresowanych.

        Naprawy (uwagi od Gemini Pro):
          1. encoding: używa podanego kodowania zamiast hardcoded UTF-8.
          2. Race condition (TOCTOU): odczyt pliku JEDEN RAZ — strumień src jest
             kopiowany do backup i jednocześnie do tmp (z edycjami). Nie ma drugiego
             otwarcia pliku, więc inny proces nie może dopisać między odczytami.
          3. Tolerancja mtime: dokładne porównanie (!=), nie tolerancja 1s.
          4. copystat: błędy są logowane do stderr, nie cicho ignorowane.
        """
        if not self._edits:
            return ""
        if is_compressed(src_path):
            raise CompressedSaveError(
                f"Cannot save edits in-place to compressed file '{src_path}'. "
                f"Use 'Save As' to write to a new uncompressed file instead."
            )
        # Walidacja przed zapisem — sprawdź czy plik się nie zmienił
        try:
            current_stat = os.stat(src_path)
        except OSError as e:
            raise FileChangedError(f"Cannot stat source file: {e}")
        if expected_size is not None and current_stat.st_size != expected_size:
            raise FileChangedError(
                f"File size changed: expected {expected_size}, got {current_stat.st_size}"
            )
        # Porównanie mtime używając st_mtime_ns (nanosekundy jako int) —
        # eliminuje problemy precyzji zmiennoprzecinkowej (st_mtime jest float).
        # Uwaga: niektóre filesystemy (ext4, tmpfs) zaokrągglają nanosekundy
        # między kolejnymi wywołaniami os.stat() — tolerancja 1 ms (1_000_000 ns).
        # expected_mtime może być float (z poprzednich wersji) lub int (st_mtime_ns).
        if expected_mtime is not None:
            current_mtime_ns = current_stat.st_mtime_ns
            # Konwertuj expected_mtime na ns jeśli jest float
            if isinstance(expected_mtime, float):
                expected_mtime_ns = int(expected_mtime * 1_000_000_000)
            else:
                expected_mtime_ns = expected_mtime
            # Tolerancja 1 ms — filesystemy mogą zaokrąglać ns
            if abs(current_mtime_ns - expected_mtime_ns) > 1_000_000:
                raise FileChangedError(
                    f"File mtime changed: expected {expected_mtime_ns}, got {current_mtime_ns}"
                )

        backup_path = src_path + ".bak"
        size = current_stat.st_size

        # #2: Race condition fix — otwórz plik źródłowy JEDEN RAZ.
        # Z tego samego strumienia kopiujemy do backup i jednocześnie
        # zapisujemy edytowane linie do tmp. Nie ma drugiego odczytu pliku,
        # więc inny proces nie może dopisać danych między odczytami.
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(src_path)) or ".",
            prefix=".logreader_tmp_",
        )
        bytes_written = 0

        def secure_opener(path, flags):
            return os.open(path, flags, 0o600)

        try:
            with open(src_path, "rb") as src, \
                 open(backup_path, "wb", opener=secure_opener) as bak, \
                 os.fdopen(tmp_fd, "wb") as out:
                line_no = 0
                for raw in src:
                    # Kopiuj do backup (oryginalna treść)
                    bak.write(raw)
                    # Zapisz do tmp (z edycjami)
                    if line_no in self._edits:
                        new_text = self._edits[line_no]
                        # #1: Użyj kodowania pliku, nie hardcoded UTF-8
                        out.write(new_text.encode(encoding, errors="replace"))
                        if not new_text.endswith("\n"):
                            out.write(b"\n")
                    else:
                        out.write(raw)
                    bytes_written += len(raw)
                    if progress_cb and size:
                        progress_cb(bytes_written / size * 100.0)
                    line_no += 1

            # #4: Błędy copystat logujemy, ale nie crashujemy — plik już jest zapisany
            try:
                shutil.copystat(src_path, backup_path)
            except OSError as e:
                import sys
                print(f"Warning: could not copy metadata to backup ({e})", file=sys.stderr)
            try:
                shutil.copystat(src_path, tmp_path)
            except OSError as e:
                import sys
                print(f"Warning: could not copy metadata to output ({e})", file=sys.stderr)

            # Atomic replace
            os.replace(tmp_path, src_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return backup_path
