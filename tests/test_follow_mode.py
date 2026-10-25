import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.config import UserConfig
from log_viewer.indexer import LineIndexer
from log_viewer.log_tab import LogTab
from log_viewer.main_window import LogViewerWindow
from PySide6 import QtWidgets


def _ensure_app() -> None:
    if QtWidgets.QApplication.instance() is None:
        QtWidgets.QApplication(sys.argv)


def _create_window(tmp_path: Path, name: str = "cfg.json") -> LogViewerWindow:
    _ensure_app()
    cfg = UserConfig(config_path=str(tmp_path / name))
    return LogViewerWindow(config=cfg)


def _open_tab_with_indexer(window: LogViewerWindow, path: str) -> LogTab:
    with patch("log_viewer.controllers.tab_file_controller.FileController.open_file"):
        tab = window.open_file_in_tab(path)
    assert tab is not None
    tab.indexer = LineIndexer(path, progress_cb=None, encoding="utf-8", index_interval_bytes=1024 * 1024)
    tab.file_path = path
    return tab


def test_follow_toggles_and_scrolls_to_bottom(temp_log_file, tmp_path: Path):
    window = _create_window(tmp_path, "cfg1.json")
    try:
        path = temp_log_file(num_lines=100)
        tab = _open_tab_with_indexer(window, path)

        # Przewijamy gdzies na gore
        tab.text.verticalScrollBar().setValue(0)
        assert tab.text.verticalScrollBar().value() == 0

        # Wlaczamy follow - powinnismy od razu zjechac na sam dol
        tab.cmd_toggle_follow()
        assert tab.follow_active is True

        # Skoro nie mamy zmockowanego GUI event loop, sprawdzamy po prostu czy
        # funkcja setowana jest do maksymalnej wartosci
        max_val = tab.text.verticalScrollBar().maximum()
        assert tab.text.verticalScrollBar().value() == max_val
    finally:
        window.close()


def test_follow_new_lines_scrolls_to_bottom(temp_log_file, tmp_path: Path):
    window = _create_window(tmp_path, "cfg2.json")
    try:
        path = temp_log_file(num_lines=100)
        tab = _open_tab_with_indexer(window, path)

        # Ustawiamy stan recznie, wlaczajac follow, i dajac troche w gore by pokazac roznice
        tab.follow_active = True
        tab.text.verticalScrollBar().setValue(0)

        # Symulujemy dodanie nowych linii z wewnetrznej metody
        with patch.object(tab, "_status"):
            tab._on_follow_new_lines(new_line_count=5)

        max_val = tab.text.verticalScrollBar().maximum()
        assert tab.text.verticalScrollBar().value() == max_val
    finally:
        window.close()


def test_follow_background_tab_defers_render(temp_log_file, tmp_path: Path):
    window = _create_window(tmp_path, "cfg3.json")
    try:
        path1 = temp_log_file(num_lines=50)
        path2 = temp_log_file(num_lines=50)

        tab1 = _open_tab_with_indexer(window, path1)
        tab2 = _open_tab_with_indexer(window, path2)

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
    finally:
        window.close()
