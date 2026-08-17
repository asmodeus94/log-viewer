import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.config import UserConfig
from log_viewer.main_window import LogViewerWindow
from PySide6 import QtWidgets


def test_follow_toggles_and_scrolls_to_bottom(temp_log_file):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path = temp_log_file(num_lines=100)
    tab = window.open_file_in_tab(path)

    # Przewijamy gdzies na gore
    tab.text.verticalScrollBar().setValue(0)
    assert tab.text.verticalScrollBar().value() == 0

    from log_viewer.indexer import LineIndexer

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

    from log_viewer.indexer import LineIndexer

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


def test_follow_background_tab_defers_render(temp_log_file):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path1 = temp_log_file(num_lines=50)
    path2 = temp_log_file(num_lines=50)

    tab1 = window.open_file_in_tab(path1)
    tab2 = window.open_file_in_tab(path2)

    from log_viewer.indexer import LineIndexer

    tab1.indexer = LineIndexer(path1, progress_cb=None, encoding="utf-8", index_interval_bytes=1024 * 1024)
    tab2.indexer = LineIndexer(path2, progress_cb=None, encoding="utf-8", index_interval_bytes=1024 * 1024)
    tab1.file_path = path1
    tab2.file_path = path2

    # Aktywna jest tab2 (druga otwarta karta)
    assert window.tabs.currentWidget() == tab2

    # Włączamy follow na karcie 1 (która jest w tle)
    tab1.follow_active = True
    tab1.needs_follow_refresh = False

    # Symulujemy przybycie nowych linii na karcie 1 (w tle)
    with patch.object(tab1, "_status"):
        tab1.file_controller.apply_follow_new_lines("mtime", "ctime", force_reload=True)

    # Karta w tle powinna odłożyć renderowanie i ustawić flagę needs_follow_refresh
    assert tab1.needs_follow_refresh is True

    # Przełączamy na kartę 1 w UI
    window.tabs.setCurrentWidget(tab1)
    # Flaga needs_follow_refresh powinna zostać skonsumowana
    assert tab1.needs_follow_refresh is False
