"""Testy podświetlania podciągów wyszukiwania oraz przewijania poziomego w oknie głównym."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.config import UserConfig
from log_viewer.log_tab import LogTab
from log_viewer.main_window import LogViewerWindow
from PySide6.QtWidgets import QApplication


# noinspection PyProtectedMember
def _create_tab(tmp_path: Path) -> tuple[LogViewerWindow, LogTab]:
    if QApplication.instance() is None:
        QApplication(sys.argv)

    cfg = UserConfig(config_path=str(tmp_path / "search_sub_cfg.json"))
    win = LogViewerWindow(config=cfg)
    tab = win._new_tab()
    return win, tab


# noinspection PyProtectedMember
class TestSearchSubstringHighlight:
    def test_plain_text_multiple_matches_in_line(self, tmp_path: Path):
        win, tab = _create_tab(tmp_path)
        tab.text.setPlainText("2026-11-01 INFO alpha beta\n2026-11-01 ERROR foo bar ERROR baz\n2026-11-01 DEBUG end")
        tab.line_map = [0, 1, 2]
        tab.search_pattern = "error"
        tab.last_search_regex = False
        tab.last_search_case = False
        tab.last_search_negate = False

        tab._highlight_and_scroll(1)

        assert tab.text.textCursor().hasSelection() is False
        assert len(tab.search_match_extra_sels) == 2
        assert tab.search_match_extra_sels[0].cursor.selectedText() == "ERROR"
        assert tab.search_match_extra_sels[1].cursor.selectedText() == "ERROR"
        # Kursor powinien być ustawiony na początku pierwszego dopasowania dla przewijania poziomego
        block_pos = tab.text.document().findBlockByNumber(1).position()
        assert tab.text.textCursor().position() == block_pos + 11
        win.close()

    def test_case_sensitive_plain_text_match(self, tmp_path: Path):
        win, tab = _create_tab(tmp_path)
        tab.text.setPlainText("error in lowercase and ERROR in uppercase")
        tab.line_map = [0]
        tab.search_pattern = "ERROR"
        tab.last_search_regex = False
        tab.last_search_case = True
        tab.last_search_negate = False

        tab._highlight_and_scroll(0)

        assert len(tab.search_match_extra_sels) == 1
        assert tab.search_match_extra_sels[0].cursor.selectedText() == "ERROR"
        win.close()

    def test_regex_and_zero_length_safety(self, tmp_path: Path):
        win, tab = _create_tab(tmp_path)
        tab.text.setPlainText("prefix ID=12345 suffix")
        tab.line_map = [0]
        tab.search_pattern = r"ID=\d+"
        tab.search_compiled = re.compile(r"ID=\d+")
        tab.last_search_regex = True
        tab.last_search_case = True
        tab.last_search_negate = False

        tab._highlight_and_scroll(0)
        assert len(tab.search_match_extra_sels) == 1
        assert tab.search_match_extra_sels[0].cursor.selectedText() == "ID=12345"

        # Regex dopasowujący pusty ciąg (np. ^) nie może zawiesić pętli ani tworzyć pustych ExtraSelection
        tab.search_pattern = r"^"
        tab.search_compiled = re.compile(r"^")
        tab._highlight_and_scroll(0)
        assert len(tab.search_match_extra_sels) == 0
        win.close()

    def test_negate_search_does_not_highlight_substring(self, tmp_path: Path):
        win, tab = _create_tab(tmp_path)
        tab.text.setPlainText("Line without match but containing word")
        tab.line_map = [0]
        tab.search_pattern = "word"
        tab.last_search_regex = False
        tab.last_search_case = False
        tab.last_search_negate = True

        tab._highlight_and_scroll(0)
        assert tab.search_extra_sel is not None
        assert len(tab.search_match_extra_sels) == 0
        win.close()

    def test_clear_search_removes_substring_selections(self, tmp_path: Path):
        win, tab = _create_tab(tmp_path)
        tab.text.setPlainText("Line with target word")
        tab.line_map = [0]
        tab.search_pattern = "target"
        tab.last_search_regex = False
        tab.last_search_case = False
        tab.last_search_negate = False

        tab._highlight_and_scroll(0)
        assert len(tab.search_match_extra_sels) == 1

        tab.cmd_clear_search()
        assert len(tab.search_match_extra_sels) == 0
        assert tab.search_extra_sel is None
        win.close()
