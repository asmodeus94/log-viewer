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

def test_window_title_updates_on_tab_change(temp_log_file):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path1 = temp_log_file(num_lines=10)
    base_name1 = os.path.basename(path1)

    path2 = temp_log_file(num_lines=10)
    base_name2 = os.path.basename(path2)

    app_title = window.t("app_title")

    # Powinno pokazywać domyślny tytuł jeśli nie ma zakładek
    assert window.windowTitle() == app_title

    # Otwarcie pierwszego pliku
    with patch("log_reader.log_tab.LogTab.open_file"):
        t1 = window.open_file_in_tab(path1)
        t1.file_path = path1
        # Musimy ręcznie zmienić tytuł, bo open_file jest zmockowane
        window._on_tab_title_changed(t1, base_name1)
        window.tabs.setCurrentWidget(t1)
        assert window.windowTitle() == f"{base_name1} - {app_title}"

    # Otwarcie drugiego pliku
    with patch("log_reader.log_tab.LogTab.open_file"):
        t2 = window.open_file_in_tab(path2)
        t2.file_path = path2
        window._on_tab_title_changed(t2, base_name2)
        window.tabs.setCurrentWidget(t2)
        assert window.windowTitle() == f"{base_name2} - {app_title}"

    # Zamknięcie pierwszego pliku
    window.cmd_close_tab() # Zamyka obecny czyli path2

    assert window.windowTitle() == f"{base_name1} - {app_title}"

    # Zamknięcie drugiego pliku
    window.cmd_close_tab() # Zamyka obecny czyli path1

    assert window.windowTitle() == app_title

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


import gc
import time
from log_reader.log_tab import _running_tasks

def test_qthread_survives_tab_closure(temp_log_file):
    """Weryfikuje, że w czasie działania wątku usunięcie zakładki nie powoduje utraty referencji i błędu (GC)."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    # Tworzymy duży plik by wymusić dłuższe działanie IndexerWorker
    path = temp_log_file(num_lines=100000)

    # Rejestrujemy stan działających wątków przed dodaniem
    initial_tasks = len(_running_tasks)

    # Dodajemy zakładkę (startuje indexer_thread)
    tab = window.open_file_in_tab(path)

    # Symulujemy zamknięcie zakładki po 10ms (podczas gdy wciąż działa)
    # Wywołujemy manualnie zdarzenie tak by usunęło Tab, ale wątek może zostać
    # przerwany poleceniem cancel (co robi close). W testach worker kończy od razu,
    # ale upewnijmy się, że rejestr działa.

    thread = tab._indexer_thread
    assert thread is not None

    # Upewniamy się, że został dodany do rejestru
    found = any(t == thread for t, w in _running_tasks)
    assert found is True

    index = window.tabs.indexOf(tab)
    window._on_tab_close_requested(index)

    # Wymuszamy Garbage Collection aby sprawdzić czy wątek zginie zanim zostanie usunięty
    del tab
    gc.collect()

    # Wątek mógł już zostać przerwany i zakończony przez Qt, co usunie go z _running_tasks
    # Zatem pomyślne zakończenie testu polega na tym, że aplikacja nie zgłosiła błędu Abort (Crash).
    # Proces w test-suite przechodzi dalej gładko.
