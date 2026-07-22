import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets
from log_reader.app import LogViewerWindow
from log_reader.config import UserConfig

def test_follow_toggles_and_scrolls_to_bottom(temp_log_file):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path = temp_log_file(num_lines=100)
    tab = window.open_file_in_tab(path)

    # Przewijamy gdzies na gore
    tab.text.verticalScrollBar().setValue(0)
    assert tab.text.verticalScrollBar().value() == 0

    from log_reader.indexer import LineIndexer

    # Ręcznie tworzymy instancję indexera, tak abyśmy nie musieli czekać na
    # QThread z `open_file()`, który nie nadąża w tym środowisku testowym.
    indexer = LineIndexer(path, progress_cb=None, encoding="utf-8", index_interval_bytes=1024 * 1024)
    tab.indexer = indexer
    tab.file_path = path

    # Wlaczamy follow - powinnismy od razu zjechac na sam dol
    tab.cmd_toggle_follow()
    assert tab.follow_active is True

    # Skoro nie mamy zmockowanego GUI event loop, sprawdzamy po prostu czy
    # funkcja setowana jest do maksymalnej wartosci
    max_val = tab.text.verticalScrollBar().maximum()
    assert tab.text.verticalScrollBar().value() == max_val

def test_follow_new_lines_scrolls_to_bottom(temp_log_file):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path = temp_log_file(num_lines=100)
    tab = window.open_file_in_tab(path)

    from log_reader.indexer import LineIndexer
    indexer = LineIndexer(path, progress_cb=None, encoding="utf-8", index_interval_bytes=1024 * 1024)
    tab.indexer = indexer
    tab.file_path = path

    # Ustawiamy stan recznie, wlaczajac follow, i dajac troche w gore by pokazac roznice
    tab.follow_active = True
    tab.text.verticalScrollBar().setValue(0)

    # Symulujemy dodanie nowych linii z wewnetrznej metody
    with patch.object(tab, "_status"):
        tab._on_follow_new_lines(new_line_count=5)

    max_val = tab.text.verticalScrollBar().maximum()
    assert tab.text.verticalScrollBar().value() == max_val
