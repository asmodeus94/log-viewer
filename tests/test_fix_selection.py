import pytest
from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication
import sys
import os

def test_highlight_and_scroll_does_not_select_text():
    os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts.warning=false"
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    from log_reader.log_tab import LogTab

    main_window_mock = MagicMock()
    main_window_mock.encoding = "utf-8"
    main_window_mock.window_size_lines = 100
    main_window_mock.max_display_lines = 1000
    main_window_mock.max_display_line_length = 500
    main_window_mock.index_interval_bytes = 1024
    main_window_mock.font_family = "Monospace"
    main_window_mock.font_size = 10
    main_window_mock.theme = {"highlight": "#ffff00", "bg_main": "#ffffff", "minimap_error": "#ff0000", "minimap_warn": "#ffaa00", "minimap_info": "#00ff00", "minimap_debug": "#0000ff", "minimap_bg": "#ffffff", "minimap_viewport": "#000000", "context": "#aaaaaa", "bookmark": "#00ff00", "edited": "#ff8800", "current_line": "#f0f0f0"}
    main_window_mock.t.return_value = "Test"

    tab = LogTab(main_window_mock)
    tab.text.setPlainText("Line 1\nLine 2\nLine 3")
    tab.line_map = [0, 1, 2]
    tab._highlight_and_scroll(1)

    # Cursor should not have selection
    assert tab.text.textCursor().hasSelection() is False

    # But an extra selection should exist and it should have selection
    assert tab._search_extra_sel is not None
    assert tab._search_extra_sel.cursor.hasSelection() is True
