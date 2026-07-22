import os
import sys
import tempfile
import pytest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
libegl = os.path.expanduser("~/.local/lib/libEGL.so.1")
if os.path.exists(libegl):
    os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/lib") + ":" + os.environ.get("LD_LIBRARY_PATH", "")

from PySide6 import QtWidgets
from log_reader.app import LogViewerWindow
from log_reader.config import UserConfig

def test_duplicate_file_tab_names(temp_log_file):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path = temp_log_file(num_lines=10)
    base_name = os.path.basename(path)

    # 1. otwarcie
    with patch("log_reader.log_tab.LogTab.open_file"):
        t1 = window.open_file_in_tab(path)
        t1.file_path = path  # Must set this because open_file is mocked
        assert window.tabs.count() == 1
        assert window.tabs.tabText(0) == base_name

    # 2. otwarcie
    with patch("log_reader.log_tab.LogTab.open_file"):
        t2 = window.open_file_in_tab(path)
        t2.file_path = path
        assert window.tabs.count() == 2
        assert window.tabs.tabText(1) == f"{base_name} [A]"

    # 3. otwarcie
    with patch("log_reader.log_tab.LogTab.open_file"):
        t3 = window.open_file_in_tab(path)
        t3.file_path = path
        assert window.tabs.count() == 3
        assert window.tabs.tabText(2) == f"{base_name} [B]"

def test_cmd_reload_clears_edits_if_accepted(temp_log_file):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path = temp_log_file(num_lines=10)

    with patch("log_reader.log_tab.LogTab.open_file"):
        tab = window.open_file_in_tab(path)

    tab.file_path = path
    tab.edit_buffer.set(0, "Zmieniona linia")
    assert len(tab.edit_buffer) == 1

    with patch("PySide6.QtWidgets.QMessageBox.question", return_value=QtWidgets.QMessageBox.Yes):
        with patch.object(tab, "open_file", side_effect=lambda *args, **kwargs: tab.edit_buffer.clear()):
            window.cmd_reload()

    assert len(tab.edit_buffer) == 0
