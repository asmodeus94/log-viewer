"""Konfiguracja użytkownika w ~/.log-viewer.json."""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any

from .helpers import (
    CONFIG_FILE_PATH,
    DEFAULT_ENCODING,
    INDEX_INTERVAL_BYTES,
    MAX_DISPLAY_LINE_LENGTH,
    MAX_DISPLAY_LINES,
    WINDOW_SIZE_LINES,
)


def secure_opener(path, flags):
    return os.open(path, flags, 0o600)


class UserConfig:
    """
    Trwała konfiguracja użytkownika — zapisywana w ~/.log-viewer.json.
    Ładowana przy starcie aplikacji, automatycznie zapisywana przy zmianach.

    Pola (z domyślnymi wartościami):
      language: "pl" lub "en"
      encoding: kodowanie znaków (domyślnie "utf-8")
      font_family: rodzina fontu (domyślnie None, co zależy od systemu)
      font_size: rozmiar fontu (domyślnie 10)
      window_size_lines: ile linii ładować do Text widgeta
      max_display_lines: górny limit linii w Text widgeta
      max_display_line_length: max znaków w jednej linii
      index_interval_bytes: gęstość indeksu (co ile bajtów)
    """

    DEFAULTS: dict[str, Any] = {
        "language": "pl",
        "encoding": DEFAULT_ENCODING,
        "font_family": None,
        "font_size": 10,
        "window_size_lines": WINDOW_SIZE_LINES,
        "max_display_lines": MAX_DISPLAY_LINES,
        "max_display_line_length": MAX_DISPLAY_LINE_LENGTH,
        "index_interval_bytes": INDEX_INTERVAL_BYTES,
    }

    def __init__(self, config_path: str | None = None):
        self.path = os.path.expanduser(config_path or CONFIG_FILE_PATH)
        self._data: dict[str, Any] = dict(self.DEFAULTS)
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in self.DEFAULTS.items():
                    if k in data and (v is None or isinstance(data[k], type(v)) or data[k] is None):
                        self._data[k] = data[k]
        except (OSError, json.JSONDecodeError, ValueError) as e:
            # Brak pliku przy pierwszym uruchomieniu jest normalny — nie loguj
            if isinstance(e, FileNotFoundError):
                pass
            else:
                print(f"Warning: could not load config from {self.path}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: unexpected error loading config: {e}", file=sys.stderr)

    def save(self) -> None:
        try:
            tmp_path = os.path.join(
                os.path.dirname(os.path.abspath(self.path)) or ".", f".log-viewer_cfg_{uuid.uuid4().hex}"
            )
            with open(tmp_path, "w", encoding="utf-8", opener=secure_opener) as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False, sort_keys=True)
            os.replace(tmp_path, self.path)
        except Exception as e:
            print(f"Warning: could not save config to {self.path}: {e}", file=sys.stderr)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default if default is not None else self.DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()
