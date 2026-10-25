import gc
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import log_viewer.log_tab as log_tab_mod
from log_viewer.config import UserConfig
from log_viewer.controllers.tab_file_controller import FileController
from log_viewer.log_tab import LogTab
from log_viewer.main_window import LogViewerWindow
from PySide6 import QtWidgets


def _create_window(tmp_path: Path, name: str = "cfg.json") -> LogViewerWindow:
    _ = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=str(tmp_path / name))
    return LogViewerWindow(config=cfg)


def _open_mocked_tab(window: LogViewerWindow, path: str) -> LogTab:
    with patch("log_viewer.log_tab.LogTab.open_file"):
        tab = window.open_file_in_tab(path)
    assert tab is not None
    tab.file_path = path
    return tab


def test_duplicate_file_tab_names(temp_log_file, tmp_path):
    window = _create_window(tmp_path, "dup_tabs.json")
    path = temp_log_file(num_lines=10)
    base_name = Path(path).name

    # 1. otwarcie
    _open_mocked_tab(window, path)
    assert window.tabs.count() == 1
    assert window.tabs.tabText(0) == base_name

    # 2. otwarcie
    _open_mocked_tab(window, path)
    assert window.tabs.count() == 2
    assert window.tabs.tabText(1) == f"{base_name} [A]"

    # 3. otwarcie
    _open_mocked_tab(window, path)
    assert window.tabs.count() == 3
    assert window.tabs.tabText(2) == f"{base_name} [B]"


def test_window_title_updates_on_tab_change(temp_log_file, tmp_path):
    window = _create_window(tmp_path, "title_tabs.json")

    path1 = temp_log_file(num_lines=10)
    base_name1: str = Path(path1).name

    path2 = temp_log_file(num_lines=10)
    base_name2: str = Path(path2).name

    app_title = window.t("app_title")

    # Powinno pokazywać domyślny tytuł jeśli nie ma zakładek
    assert window.windowTitle() == app_title

    # Otwarcie pierwszego pliku
    t1 = _open_mocked_tab(window, path1)
    # noinspection PyProtectedMember
    window._on_tab_title_changed(t1, base_name1)
    window.tabs.setCurrentWidget(t1)
    assert window.windowTitle() == f"{base_name1} - {app_title}"

    # Otwarcie drugiego pliku
    t2 = _open_mocked_tab(window, path2)
    # noinspection PyProtectedMember
    window._on_tab_title_changed(t2, base_name2)
    window.tabs.setCurrentWidget(t2)
    assert window.windowTitle() == f"{base_name2} - {app_title}"

    # Zamknięcie pierwszego pliku
    window.cmd_close_tab()  # Zamyka obecny czyli path2
    assert window.windowTitle() == f"{base_name1} - {app_title}"

    # Zamknięcie drugiego pliku
    window.cmd_close_tab()  # Zamyka obecny czyli path1
    assert window.windowTitle() == app_title


def test_cmd_reload_clears_edits_if_accepted(temp_log_file, tmp_path):
    window = _create_window(tmp_path, "reload_tabs.json")
    path = temp_log_file(num_lines=10)

    with patch("log_viewer.controllers.tab_file_controller.FileController.open_file"):
        tab = window.open_file_in_tab(path)
    assert tab is not None

    tab.file_path = path
    # noinspection PyProtectedMember
    tab._assigned_title = "mocked_title"
    tab.edit_buffer.set(0, "Zmieniona linia")
    assert len(tab.edit_buffer) == 1

    controller: FileController = tab.file_controller
    edit_buf = tab.edit_buffer
    with (
        patch(
            "PySide6.QtWidgets.QMessageBox.question",
            return_value=QtWidgets.QMessageBox.StandardButton.Yes,
        ),
        patch.object(controller, "open_file", side_effect=lambda *_a, **_kw: edit_buf.clear()),
    ):
        window.cmd_reload()

    assert len(tab.edit_buffer) == 0


def test_qthread_survives_tab_closure(temp_log_file, tmp_path):
    """Weryfikuje, że w czasie działania wątku usunięcie zakładki nie powoduje utraty referencji i błędu (GC)."""
    window = _create_window(tmp_path, "qthread_tabs.json")

    # Tworzymy duży plik by wymusić dłuższe działanie IndexerWorker
    path = temp_log_file(num_lines=100000)

    # Dodajemy zakładkę (startuje indexer_thread)
    tab = window.open_file_in_tab(path)
    assert tab is not None

    # noinspection PyProtectedMember
    thread = tab._indexer_thread
    assert thread is not None

    # Upewniamy się, że został dodany do rejestru
    # noinspection PyProtectedMember
    running_tasks = log_tab_mod._running_tasks
    found = any(t == thread for t, _w in running_tasks)
    assert found is True

    index = window.tabs.indexOf(tab)
    # noinspection PyProtectedMember
    window._on_tab_close_requested(index)

    # Wymuszamy Garbage Collection aby sprawdzić czy wątek zginie zanim zostanie usunięty
    del tab
    gc.collect()


def test_tab_middle_click_closes_tab(temp_log_file, tmp_path):
    """Weryfikuje, że kliknięcie środkowego przycisku myszy (kółka) na karcie zamyka tę kartę."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    window = _create_window(tmp_path, "middle_click.json")
    path = temp_log_file(num_lines=10)
    _open_mocked_tab(window, path)

    assert window.tabs.count() == 1

    # Przycisk zamykania zakładki powinien być usunięty (None)
    assert window.tabs.tabBar().tabButton(0, QtWidgets.QTabBar.ButtonPosition.RightSide) is None

    # Symulacja kliknięcia środkowym przyciskiem myszy (kółkiem) na karcie 0
    rect = window.tabs.tabBar().tabRect(0)
    click_pos = rect.center()

    mouse_event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(click_pos),
        Qt.MouseButton.MiddleButton,
        Qt.MouseButton.MiddleButton,
        Qt.KeyboardModifier.NoModifier,
    )

    # Wywołanie eventFilter na tabBar
    window.eventFilter(window.tabs.tabBar(), mouse_event)

    # Karta powinna zostać zamknięta
    assert window.tabs.count() == 0


def test_per_tab_filter_isolation(tmp_path, qtbot):
    """Weryfikuje, że każda karta utrzymuje w pełni niezależny stan filtra i parametrów."""
    window = _create_window(tmp_path, "config.json")

    file1 = tmp_path / "app1.log"
    file2 = tmp_path / "app2.log"

    lines1 = [f"F1 Line {i} ERR-error" if i % 10 == 0 else f"F1 Line {i} info" for i in range(50)]
    lines2 = [f"F2 Line {i} WARN-warning" if i % 5 == 0 else f"F2 Line {i} info" for i in range(50)]

    file1.write_text("\n".join(lines1) + "\n", encoding="utf-8")
    file2.write_text("\n".join(lines2) + "\n", encoding="utf-8")

    tab1 = window.open_file_in_tab(str(file1))
    tab2 = window.open_file_in_tab(str(file2))

    assert tab1 is not None
    assert tab2 is not None
    t1: LogTab = tab1
    t2: LogTab = tab2

    # Poczekaj na załadowanie obu plików
    qtbot.waitUntil(lambda: t1.indexer is not None and t2.indexer is not None, timeout=3000)

    def _apply_filter_and_wait(tab: LogTab, pattern: str, context: int) -> None:
        window.tabs.setCurrentWidget(tab)
        window.filter_entry.setText(pattern)
        window.filter_context_spin.setValue(context)
        tab.filter_controller.cmd_apply_filter()
        qtbot.waitUntil(
            lambda: tab.filter_active and tab.filter_results is not None and len(tab.filter_results) > 0,
            timeout=3000,
        )

    # 1. Zastosuj filtr ERR- na tab1 z kontekstem 2
    _apply_filter_and_wait(t1, "ERR-", 2)
    assert t1.filter_pattern == "ERR-"
    assert t1.filter_context_after == 2
    assert t1.filter_results is not None
    assert len(t1.filter_results) == 5  # linie: 0, 10, 20, 30, 40

    # 2. Zastosuj filtr WARN- na tab2 z kontekstem 0
    _apply_filter_and_wait(t2, "WARN-", 0)
    assert t2.filter_pattern == "WARN-"
    assert t2.filter_context_after == 0
    assert t2.filter_results is not None
    assert len(t2.filter_results) == 10  # linie: 0, 5, 10, 15, 20, 25, 30, 35, 40, 45

    # 3. Przełącz na tab1 — sprawdź toolbar i stan tab1
    window.tabs.setCurrentWidget(t1)
    assert window.filter_entry.text() == "ERR-"
    assert window.filter_context_spin.value() == 2
    assert t1.filter_active is True
    assert t1.filter_pattern == "ERR-"
    assert t1.filter_results is not None
    assert len(t1.filter_results) == 5

    # 4. Przełącz na tab2 — sprawdź toolbar i stan tab2
    window.tabs.setCurrentWidget(t2)
    assert window.filter_entry.text() == "WARN-"
    assert window.filter_context_spin.value() == 0
    assert t2.filter_active is True
    assert t2.filter_pattern == "WARN-"
    assert t2.filter_results is not None
    assert len(t2.filter_results) == 10

    # 5. Wyczyść filtr na tab2 — tab1 powinien pozostać przefiltrowany
    t2.filter_controller.cmd_clear_filter()
    assert t2.filter_active is False
    assert window.filter_entry.text() == ""

    window.tabs.setCurrentWidget(t1)
    assert t1.filter_active is True
    assert t1.filter_pattern == "ERR-"
    assert t1.filter_results is not None
    assert len(t1.filter_results) == 5
    assert window.filter_entry.text() == "ERR-"
