import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
libegl = os.path.expanduser("~/.local/lib/libEGL.so.1")
if os.path.exists(libegl):
    os.environ["LD_LIBRARY_PATH"] = os.path.expanduser("~/.local/lib") + ":" + os.environ.get("LD_LIBRARY_PATH", "")

from log_viewer.config import UserConfig
from log_viewer.main_window import LogViewerWindow
from PySide6 import QtWidgets


def test_duplicate_file_tab_names(temp_log_file):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path = temp_log_file(num_lines=10)
    base_name = os.path.basename(path)

    # 1. otwarcie
    with patch("log_viewer.log_tab.LogTab.open_file"):
        t1 = window.open_file_in_tab(path)
        t1.file_path = path  # Must set this because open_file is mocked
        assert window.tabs.count() == 1
        assert window.tabs.tabText(0) == base_name

    # 2. otwarcie
    with patch("log_viewer.log_tab.LogTab.open_file"):
        t2 = window.open_file_in_tab(path)
        t2.file_path = path
        assert window.tabs.count() == 2
        assert window.tabs.tabText(1) == f"{base_name} [A]"

    # 3. otwarcie
    with patch("log_viewer.log_tab.LogTab.open_file"):
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
    with patch("log_viewer.log_tab.LogTab.open_file"):
        t1 = window.open_file_in_tab(path1)
        t1.file_path = path1
        # Musimy ręcznie zmienić tytuł, bo open_file jest zmockowane
        window._on_tab_title_changed(t1, base_name1)
        window.tabs.setCurrentWidget(t1)
        assert window.windowTitle() == f"{base_name1} - {app_title}"

    # Otwarcie drugiego pliku
    with patch("log_viewer.log_tab.LogTab.open_file"):
        t2 = window.open_file_in_tab(path2)
        t2.file_path = path2
        window._on_tab_title_changed(t2, base_name2)
        window.tabs.setCurrentWidget(t2)
        assert window.windowTitle() == f"{base_name2} - {app_title}"

    # Zamknięcie pierwszego pliku
    window.cmd_close_tab()  # Zamyka obecny czyli path2

    assert window.windowTitle() == f"{base_name1} - {app_title}"

    # Zamknięcie drugiego pliku
    window.cmd_close_tab()  # Zamyka obecny czyli path1

    assert window.windowTitle() == app_title


def test_cmd_reload_clears_edits_if_accepted(temp_log_file):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path = temp_log_file(num_lines=10)

    with patch("log_viewer.controllers.tab_file_controller.FileController.open_file"):
        tab = window.open_file_in_tab(path)

    tab.file_path = path
    tab._assigned_title = "mocked_title"
    tab.edit_buffer.set(0, "Zmieniona linia")
    assert len(tab.edit_buffer) == 1

    with (
        patch("PySide6.QtWidgets.QMessageBox.question", return_value=QtWidgets.QMessageBox.Yes),
        patch.object(tab.file_controller, "open_file", side_effect=lambda *args, **kwargs: tab.edit_buffer.clear()),
    ):
        window.cmd_reload()

    assert len(tab.edit_buffer) == 0


import gc

from log_viewer.log_tab import _running_tasks


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


def test_tab_middle_click_closes_tab(temp_log_file):
    """Weryfikuje, że kliknięcie środkowego przycisku myszy (kółka) na karcie zamyka tę kartę."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    path = temp_log_file(num_lines=10)
    with patch("log_viewer.log_tab.LogTab.open_file"):
        tab1 = window.open_file_in_tab(path)
        tab1.file_path = path

    assert window.tabs.count() == 1

    # Przycisk zamykania zakładki powinien być usunięty (None)
    assert window.tabs.tabBar().tabButton(0, QtWidgets.QTabBar.ButtonPosition.RightSide) is None

    # Symulacja kliknięcia środkowym przyciskiem myszy (kółkiem) na karcie 0
    rect = window.tabs.tabBar().tabRect(0)
    click_pos = rect.center()

    from PySide6.QtCore import QPointF

    mouse_event = QMouseEvent(
        QEvent.MouseButtonRelease, QPointF(click_pos), Qt.MiddleButton, Qt.MiddleButton, Qt.NoModifier
    )

    # Wywołanie eventFilter na tabBar
    window.eventFilter(window.tabs.tabBar(), mouse_event)

    # Karta powinna zostać zamknięta
    assert window.tabs.count() == 0


def test_per_tab_filter_isolation(tmp_path, qtbot):
    """Weryfikuje, że każda karta utrzymuje w pełni niezależny stan filtra i parametrów."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=str(tmp_path / "config.json"))
    window = LogViewerWindow(config=cfg)

    file1 = tmp_path / "app1.log"
    file2 = tmp_path / "app2.log"

    lines1 = [f"F1 Line {i} ERR-error" if i % 10 == 0 else f"F1 Line {i} info" for i in range(50)]
    lines2 = [f"F2 Line {i} WARN-warning" if i % 5 == 0 else f"F2 Line {i} info" for i in range(50)]

    file1.write_text("\n".join(lines1) + "\n", encoding="utf-8")
    file2.write_text("\n".join(lines2) + "\n", encoding="utf-8")

    tab1 = window.open_file_in_tab(str(file1))
    tab2 = window.open_file_in_tab(str(file2))

    assert tab1 is not None and tab2 is not None

    # Poczekaj na załadowanie obu plików
    qtbot.waitUntil(lambda: tab1.indexer is not None and tab2.indexer is not None, timeout=3000)

    # 1. Zastosuj filtr ERR- na tab1 z kontekstem 2
    window.tabs.setCurrentWidget(tab1)
    window.filter_entry.setText("ERR-")
    window.filter_context_spin.setValue(2)
    tab1.filter_controller.cmd_apply_filter()

    qtbot.waitUntil(
        lambda: tab1.filter_active and tab1.filter_results is not None and len(tab1.filter_results) > 0, timeout=3000
    )

    assert tab1.filter_pattern == "ERR-"
    assert tab1.filter_context_after == 2
    assert len(tab1.filter_results) == 5  # linie: 0, 10, 20, 30, 40

    # 2. Zastosuj filtr WARN- na tab2 z kontekstem 0
    window.tabs.setCurrentWidget(tab2)
    window.filter_entry.setText("WARN-")
    window.filter_context_spin.setValue(0)
    tab2.filter_controller.cmd_apply_filter()

    qtbot.waitUntil(
        lambda: tab2.filter_active and tab2.filter_results is not None and len(tab2.filter_results) > 0, timeout=3000
    )

    assert tab2.filter_pattern == "WARN-"
    assert tab2.filter_context_after == 0
    assert len(tab2.filter_results) == 10  # linie: 0, 5, 10, 15, 20, 25, 30, 35, 40, 45

    # 3. Przełącz na tab1 — sprawdź toolbar i stan tab1
    window.tabs.setCurrentWidget(tab1)
    assert window.filter_entry.text() == "ERR-"
    assert window.filter_context_spin.value() == 2
    assert tab1.filter_active is True
    assert tab1.filter_pattern == "ERR-"
    assert len(tab1.filter_results) == 5

    # 4. Przełącz na tab2 — sprawdź toolbar i stan tab2
    window.tabs.setCurrentWidget(tab2)
    assert window.filter_entry.text() == "WARN-"
    assert window.filter_context_spin.value() == 0
    assert tab2.filter_active is True
    assert tab2.filter_pattern == "WARN-"
    assert len(tab2.filter_results) == 10

    # 5. Wyczyść filtr na tab2 — tab1 powinien pozostać przefiltrowany
    tab2.filter_controller.cmd_clear_filter()
    assert tab2.filter_active is False
    assert window.filter_entry.text() == ""

    window.tabs.setCurrentWidget(tab1)
    assert tab1.filter_active is True
    assert tab1.filter_pattern == "ERR-"
    assert len(tab1.filter_results) == 5
    assert window.filter_entry.text() == "ERR-"
