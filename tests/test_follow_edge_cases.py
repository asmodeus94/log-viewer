import array
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from log_viewer.bitset import Bitset
from log_viewer.config import UserConfig
from log_viewer.indexer import LineIndexer
from log_viewer.log_tab import LogTab
from log_viewer.main_window import LogViewerWindow
from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import QCoreApplication


def _pump_events(iterations: int = 50, sleep_sec: float = 0.01) -> None:
    for _ in range(iterations):
        QCoreApplication.processEvents()
        if sleep_sec > 0:
            time.sleep(sleep_sec)


# noinspection PyProtectedMember
def _setup_tab_with_manual_indexer(window: LogViewerWindow, file_path: str) -> tuple[LogTab, LineIndexer]:
    with patch("log_viewer.controllers.tab_file_controller.FileController.open_file"):
        tab = window.open_file_in_tab(file_path)
    assert tab is not None
    indexer = LineIndexer(file_path, progress_cb=None, encoding="utf-8", index_interval_bytes=1024)
    tab.indexer = indexer
    tab.file_path = file_path
    tab._last_file_size = indexer.size
    tab._load_window(at_line=0)
    return tab, indexer


# noinspection PyProtectedMember
def _append_and_trigger_follow(tab: LogTab, indexer: LineIndexer, file_path: str, payload: bytes) -> int:
    with open(file_path, "ab") as f:
        f.write(payload)
    new_lines_count = indexer.update_from(os.stat(file_path).st_size)
    tab.file_controller._on_follow_new_lines(new_line_count=new_lines_count, mtime_str="now", ctime_str="now")
    return new_lines_count


def _cleanup_window(window: LogViewerWindow | None) -> None:
    with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.StandardButton.No):
        if window is not None:
            window.close()
        _pump_events(iterations=20, sleep_sec=0.0)


def test_incremental_follow_with_filter_edge_cases(tmp_path):
    """Test sprawdza poprawny proces uaktualniania filtra w trybie Follow (Tail)
    oraz asynchronicznego przemieszczania suwaka unikając Thread Leak'ów i zawieszeń na macOS/Linux/Windows.
    """
    _ = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=str(tmp_path / "follow_edge_1.json"))
    window = LogViewerWindow(config=cfg)

    tf_path = tmp_path / "follow_edge_1.log"
    initial_lines = [b"Line 0 - secret initial\n" if i == 0 else f"Line {i} - basic text\n".encode() for i in range(10)]
    tf_path.write_bytes(b"".join(initial_lines))
    file_path = str(tf_path)

    try:
        with (
            patch.object(QtWidgets.QMessageBox, "information"),
            patch.object(QtWidgets.QMessageBox, "warning"),
            patch.object(QtWidgets.QMessageBox, "critical"),
            patch.object(QtWidgets.QMessageBox, "question"),
        ):
            tab, indexer = _setup_tab_with_manual_indexer(window, file_path)

            # Inicjujemy filtry na karcie używając wzorca "secret"
            window.filter_entry.setText("secret")
            window.cmd_apply_filter()

            # Przetwarzamy kolejkę zdarzeń aby FilterWorker zakończył działanie
            for _ in range(50):
                QCoreApplication.processEvents()
                if tab.filter_active and tab.filter_results is not None and len(tab.filter_results) > 0:
                    break
                time.sleep(0.01)

            assert tab.filter_active is True
            filter_res_init = tab.filter_results
            assert filter_res_init is not None
            initial_hits = len(filter_res_init)
            assert initial_hits == 1

            # Zwijamy scrollbar do 0 by sprawdzić timer przewijania
            tab.text.verticalScrollBar().setValue(0)

            # Aktywujemy Follow
            tab.cmd_toggle_follow()
            assert tab.follow_active is True

            # Symulujemy napływ zdarzeń (10 kolejnych linii z "secret incoming")
            incoming_payload = b"".join(f"Line {i} - secret incoming\n".encode() for i in range(10, 20))
            new_lines_count = _append_and_trigger_follow(tab, indexer, file_path, incoming_payload)
            assert new_lines_count == 10

            # Czekamy na przetworzenie pętli (IncrementalFilterWorker odeśle zmergowane wyniki)
            for _ in range(50):
                QCoreApplication.processEvents()
                if tab.filter_results is not None and len(tab.filter_results) > initial_hits:
                    break
                time.sleep(0.01)

            filter_res = tab.filter_results
            assert filter_res is not None
            assert len(filter_res) == 11, f"Expected 11 hits, got {len(filter_res)}"

            # Przetwarzamy zdarzenia timera przewijania (QTimer.singleShot(0, _scroll_down))
            _pump_events(iterations=20, sleep_sec=0.0)

            max_val = tab.text.verticalScrollBar().maximum()
            assert tab.text.verticalScrollBar().value() == max_val, (
                "Scrollbar did not reach the bottom after Timer execution"
            )

            # Drugi napływ danych (linia pusta / bez wzorca filtra)
            new_lines_count_2 = _append_and_trigger_follow(tab, indexer, file_path, b"Line 20 - empty\n")
            assert new_lines_count_2 == 1

            # Czekamy na przetworzenie zdarzeń drugiego workera w event loopie
            _pump_events(iterations=50, sleep_sec=0.01)

            # noinspection PyProtectedMember
            assert getattr(tab, "_inc_pending_lines", 0) == 0
            filter_res_2 = tab.filter_results
            assert filter_res_2 is not None
            assert len(filter_res_2) == 11
            assert tab.file_controller is not None
    finally:
        _cleanup_window(window)


def test_follow_incremental_context_and_highlighting(tmp_path):
    """Weryfikuje, że w trybie Follow przy aktywnym filtrze z kontekstem:
    1. Linia z błędem (trafienie) ma żółte tło (highlight).
    2. Kolejne dopisane linie kontekstu mają szare tło (context), a NIE żółte.
    3. ExtraSelections są poprawnie aplikowane od razu bez konieczności klikania w okno.
    """
    _ = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=str(tmp_path / "follow_edge_2.json"))
    window = LogViewerWindow(config=cfg)

    tf_path = tmp_path / "follow_edge_2.log"
    initial_lines = [
        b"Line 0 - [ERROR] ERR-000 initial\n" if i == 0 else f"Line {i} - standard log\n".encode() for i in range(10)
    ]
    tf_path.write_bytes(b"".join(initial_lines))
    file_path = str(tf_path)

    try:
        with (
            patch.object(QtWidgets.QMessageBox, "information"),
            patch.object(QtWidgets.QMessageBox, "warning"),
            patch.object(QtWidgets.QMessageBox, "critical"),
            patch.object(QtWidgets.QMessageBox, "question"),
        ):
            tab, indexer = _setup_tab_with_manual_indexer(window, file_path)

            # Ustawiamy filtr na "ERR-" z kontekstem 5
            window.filter_entry.setText("ERR-")
            window.filter_context_spin.setValue(5)
            window.cmd_apply_filter()

            _pump_events(iterations=30, sleep_sec=0.01)

            # Włączamy tryb Follow
            tab.cmd_toggle_follow()
            assert tab.follow_active is True

            # Dopisujemy linię z błędem ERR-102 (linia 10)
            new_lines = _append_and_trigger_follow(
                tab, indexer, file_path, b"Line 10 - [ERROR] Restarting after ERR-102\n"
            )
            assert new_lines == 1
            _pump_events(iterations=50, sleep_sec=0.01)

            # Dopisujemy kolejne 3 linie bez błędu (linie 11, 12, 13) - powinny wejść jako kontekst
            ctx_payload = b"".join(f"Line {i} - [INFO] Background task {i} OK\n".encode() for i in range(11, 14))
            new_lines_2 = _append_and_trigger_follow(tab, indexer, file_path, ctx_payload)
            assert new_lines_2 == 3
            _pump_events(iterations=50, sleep_sec=0.01)

            # Sprawdzamy czy linie 10, 11, 12, 13 są w filter_all_lines
            all_lines = tab.filter_all_lines
            assert all_lines is not None
            for line_no in (10, 11, 12, 13):
                assert line_no in all_lines

            # Tylko linia 10 powinna być w filter_results (trafienie)
            results = tab.filter_results
            assert results is not None
            assert 10 in results
            for line_no in (11, 12, 13):
                assert line_no not in results

            # Weryfikujemy ExtraSelections na kontrolce edytora
            sels = tab.text.extraSelections()
            assert len(sels) >= 4, "Wszystkie 4 linie (trafienie + 3 linie kontekstu) powinny mieć ExtraSelections"

            # noinspection PyProtectedMember
            color_highlight = tab._theme_colors.get("highlight", QtGui.QColor("#fff176"))
            # noinspection PyProtectedMember
            color_context = tab._theme_colors.get("context", QtGui.QColor("#3a3d3a"))

            hit_sels = [s for s in sels if s.format.background().color() == color_highlight]
            context_sels = [s for s in sels if s.format.background().color() == color_context]

            assert len(hit_sels) == 2, (
                f"Linie 0 i 10 (ERR-000 i ERR-102) powinny mieć żółte tło, znaleziono: {len(hit_sels)}"
            )
            assert len(context_sels) >= 3, (
                f"Linie 11..13 powinny mieć szare tło kontekstu, znaleziono: {len(context_sels)}"
            )
    finally:
        _cleanup_window(window)


def test_follow_rotation_resets_search_and_reapplies_filter(tmp_path):
    """Po rotacji pliku w trybie Follow stare wyniki wyszukiwania muszą zostać wyczyszczone,
    a aktywny filtr przeliczony dla nowej zawartości (nawet gdy nowy plik ma 0 trafień)."""
    _ = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=str(tmp_path / "follow_rot.json"))
    window = LogViewerWindow(config=cfg)

    tf_path = tmp_path / "follow_rot.log"
    initial_lines = [
        f"Line {i} - ERROR initial\n".encode() if i % 10 == 0 else f"Line {i} - INFO ok\n".encode() for i in range(100)
    ]
    tf_path.write_bytes(b"".join(initial_lines))
    file_path = str(tf_path)

    try:
        with (
            patch.object(QtWidgets.QMessageBox, "information") as mock_info,
            patch.object(QtWidgets.QMessageBox, "warning"),
            patch.object(QtWidgets.QMessageBox, "critical"),
            patch.object(QtWidgets.QMessageBox, "question"),
        ):
            tab, _ = _setup_tab_with_manual_indexer(window, file_path)
            window.filter_entry.setText("ERROR")
            window.cmd_apply_filter()
            _pump_events(iterations=50, sleep_sec=0.01)

            assert tab.filter_active is True
            assert tab.filter_results is not None
            assert len(tab.filter_results) == 10

            # Zasymuluj poprzednie wyniki wyszukiwania
            tab.search_results_all = Bitset.from_indices(array.array("Q", [0, 50, 90]), 100)
            tab.search_results = tab.search_results_all
            if tab.search_model is not None:
                tab.search_model.set_results(tab.search_results_all, indexer=tab.indexer)

            tab.cmd_toggle_follow()
            assert tab.follow_active is True

            # Rotacja do nowego, mniejszego pliku z 2 trafieniami (linie 2 i 7)
            rotated_payload = (
                b"Line 0 - INFO ok\n"
                b"Line 1 - INFO ok\n"
                b"Line 2 - ERROR rotated\n"
                b"Line 3 - INFO ok\n"
                b"Line 4 - INFO ok\n"
                b"Line 5 - INFO ok\n"
                b"Line 6 - INFO ok\n"
                b"Line 7 - ERROR rotated\n"
            )
            tf_path.write_bytes(rotated_payload)
            new_idx = LineIndexer(file_path, encoding="utf-8", index_interval_bytes=1024)
            tab.file_controller._on_follow_reindex(new_idx, len(rotated_payload), 0)
            _pump_events(iterations=50, sleep_sec=0.01)

            assert len(tab.search_results_all) == 0
            if tab.search_model is not None:
                assert tab.search_model.rowCount() == 0
            assert tab.filter_active is True
            assert tab.filter_results is not None
            assert list(tab.filter_results) == [2, 7]
            assert tab.line_map == [2, 7]

            # Druga rotacja — tym razem 0 trafień; brak modalnego okienka QMessageBox, filtr nadal aktywny
            tf_path.write_bytes(b"Line 0 - INFO only\nLine 1 - INFO only\n")
            empty_match_idx = LineIndexer(file_path, encoding="utf-8", index_interval_bytes=1024)
            tab.file_controller._on_follow_reindex(empty_match_idx, tf_path.stat().st_size, 0)
            _pump_events(iterations=50, sleep_sec=0.01)

            assert tab.filter_active is True
            assert tab.filter_results is not None
            assert len(tab.filter_results) == 0
            assert tab.line_map == []
            mock_info.assert_not_called()
    finally:
        _cleanup_window(window)


def test_reindex_after_save_resets_search_and_reapplies_filter(tmp_path):
    """Po zapisie edycji i reindeksowaniu wyniki wyszukiwania są czyszczone, a aktywny filtr przeliczany."""
    _ = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=str(tmp_path / "save_reindex.json"))
    window = LogViewerWindow(config=cfg)

    tf_path = tmp_path / "save_reindex.log"
    tf_path.write_bytes(b"Line 0 - ERROR a\nLine 1 - INFO b\nLine 2 - ERROR c\n")
    file_path = str(tf_path)

    try:
        with (
            patch.object(QtWidgets.QMessageBox, "information"),
            patch.object(QtWidgets.QMessageBox, "warning"),
            patch.object(QtWidgets.QMessageBox, "critical"),
            patch.object(QtWidgets.QMessageBox, "question"),
        ):
            tab, _ = _setup_tab_with_manual_indexer(window, file_path)
            window.filter_entry.setText("ERROR")
            window.cmd_apply_filter()
            _pump_events(iterations=50, sleep_sec=0.01)

            assert tab.filter_results is not None
            assert list(tab.filter_results) == [0, 2]

            tab.search_results_all = Bitset.from_indices(array.array("Q", [0, 2]), 3)
            if tab.search_model is not None:
                tab.search_model.set_results(tab.search_results_all, indexer=tab.indexer)

            # Nadpisz plik tak, aby linia 0 przestała pasować do filtra, a linia 1 zaczęła
            tf_path.write_bytes(b"Line 0 - INFO fixed\nLine 1 - ERROR new\nLine 2 - ERROR c\n")
            new_idx = LineIndexer(file_path, encoding="utf-8", index_interval_bytes=1024)
            tab.file_controller._on_reindex_after_save(new_idx, 1)
            _pump_events(iterations=50, sleep_sec=0.01)

            assert len(tab.search_results_all) == 0
            if tab.search_model is not None:
                assert tab.search_model.rowCount() == 0
            assert tab.filter_active is True
            assert tab.filter_results is not None
            assert list(tab.filter_results) == [1, 2]
            assert tab.line_map == [1, 2]
    finally:
        _cleanup_window(window)
