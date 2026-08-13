import os
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets, QtGui
from PySide6.QtCore import QCoreApplication, QTimer
from log_viewer.main_window import LogViewerWindow
from log_viewer.config import UserConfig
from log_viewer.indexer import LineIndexer


def test_incremental_follow_with_filter_edge_cases():
    """Test sprawdza poprawny proces uaktualniania filtra w trybie Follow (Tail)
    oraz asynchronicznego przemieszczania suwaka unikając Thread Leak'ów i zawieszeń na macOS/Linux/Windows.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    # Tworzymy plik początkowy (10 linii), w tym jedną linię ze słowem "secret", by filtr był aktywny
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    for i in range(10):
        if i == 0:
            tf.write(f"Line {i} - secret initial\n".encode("utf-8"))
        else:
            tf.write(f"Line {i} - basic text\n".encode("utf-8"))
    tf.close()

    try:
        # Patchujemy QMessageBox, aby żadne okna modalne nie blokowały pętli zdarzeń (zwłaszcza na macOS)
        with patch.object(QtWidgets.QMessageBox, "information"), \
             patch.object(QtWidgets.QMessageBox, "warning"), \
             patch.object(QtWidgets.QMessageBox, "critical"), \
             patch.object(QtWidgets.QMessageBox, "question"):

            # Blokujemy asynchroniczny open_file wewnątrz open_file_in_tab, aby kontrolować indexer synchronicznie
            with patch("log_viewer.controllers.tab_file_controller.FileController.open_file"):
                tab = window.open_file_in_tab(tf.name)

            indexer = LineIndexer(tf.name, progress_cb=None, encoding="utf-8", index_interval_bytes=1024)
            tab.indexer = indexer
            tab.file_path = tf.name
            tab._last_file_size = indexer.size
            tab._load_window(at_line=0)

            # Inicjujemy filtry na karcie używając wzorca "secret"
            window.filter_entry.setText("secret")
            window.cmd_apply_filter()

            # Przetwarzamy kolejkę zdarzeń aby FilterWorker zakończył działanie
            for _ in range(50):
                QCoreApplication.processEvents()
                if tab.filter_active and getattr(tab, "filter_results", None) is not None and len(tab.filter_results) > 0:
                    break
                time.sleep(0.01)

            assert tab.filter_active is True
            initial_hits = len(tab.filter_results)
            assert initial_hits == 1

            # Zwijamy scrollbar do 0 by sprawdzić timer przewijania
            tab.text.verticalScrollBar().setValue(0)

            # Aktywujemy Follow
            tab.cmd_toggle_follow()
            assert tab.follow_active is True

            # Symulujemy napływ zdarzeń (10 kolejnych linii z "secret incoming")
            with open(tf.name, "ab") as f:
                for i in range(10, 20):
                    f.write(f"Line {i} - secret incoming\n".encode("utf-8"))

            new_lines_count = indexer.update_from(os.stat(tf.name).st_size)
            assert new_lines_count == 10

            # Wywołanie _on_follow_new_lines (uruchamia IncrementalFilterWorker)
            tab.file_controller._on_follow_new_lines(new_line_count=new_lines_count, mtime_str="now", ctime_str="now")

            # Czekamy na przetworzenie pętli (IncrementalFilterWorker odeśle zmergowane wyniki)
            for _ in range(50):
                QCoreApplication.processEvents()
                if len(tab.filter_results) > initial_hits:
                    break
                time.sleep(0.01)

            # Wyniki bitset powinny być powiększone o 10 nowych trafień (łącznie 11)
            assert len(tab.filter_results) == 11, f"Expected 11 hits, got {len(tab.filter_results)}"

            # Przetwarzamy zdarzenia timera przewijania (QTimer.singleShot(0, _scroll_down))
            for _ in range(20):
                QCoreApplication.processEvents()

            max_val = tab.text.verticalScrollBar().maximum()
            assert tab.text.verticalScrollBar().value() == max_val, "Scrollbar did not reach the bottom after Timer execution"

            # Drugi napływ danych (linia pusta / bez wzorca filtra)
            with open(tf.name, "ab") as f:
                f.write(b"Line 20 - empty\n")

            new_lines_count_2 = indexer.update_from(os.stat(tf.name).st_size)
            assert new_lines_count_2 == 1
            tab.file_controller._on_follow_new_lines(new_line_count=new_lines_count_2, mtime_str="now", ctime_str="now")

            # Czekamy na przetworzenie zdarzeń drugiego workera w event loopie
            for _ in range(50):
                QCoreApplication.processEvents()
                time.sleep(0.01)

            assert getattr(tab, "_inc_pending_lines", 0) == 0
            assert len(tab.filter_results) == 11
            assert tab.file_controller is not None

            print("All Incremental Follow Edge Cases Pass!")
    finally:
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.No):
            if 'window' in locals() and window is not None:
                window.close()
            for _ in range(20):
                QCoreApplication.processEvents()
            try:
                os.unlink(tf.name)
            except OSError:
                pass


def test_follow_incremental_context_and_highlighting():
    """Weryfikuje, że w trybie Follow przy aktywnym filtrze z kontekstem:
    1. Linia z błędem (trafienie) ma żółte tło (highlight).
    2. Kolejne dopisane linie kontekstu mają szare tło (context), a NIE żółte.
    3. ExtraSelections są poprawnie aplikowane od razu bez konieczności klikania w okno.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    for i in range(10):
        if i == 0:
            tf.write(b"Line 0 - [ERROR] ERR-000 initial\n")
        else:
            tf.write(f"Line {i} - standard log\n".encode("utf-8"))
    tf.close()

    try:
        with patch.object(QtWidgets.QMessageBox, "information"), \
             patch.object(QtWidgets.QMessageBox, "warning"), \
             patch.object(QtWidgets.QMessageBox, "critical"), \
             patch.object(QtWidgets.QMessageBox, "question"):

            with patch("log_viewer.controllers.tab_file_controller.FileController.open_file"):
                tab = window.open_file_in_tab(tf.name)

            indexer = LineIndexer(tf.name, progress_cb=None, encoding="utf-8", index_interval_bytes=1024)
            tab.indexer = indexer
            tab.file_path = tf.name
            tab._last_file_size = indexer.size
            tab._load_window(at_line=0)

            # Ustawiamy filtr na "ERR-" z kontekstem 5
            window.filter_entry.setText("ERR-")
            window.filter_context_spin.setValue(5)
            window.cmd_apply_filter()

            for _ in range(30):
                QCoreApplication.processEvents()
                time.sleep(0.01)

            # Włączamy tryb Follow
            tab.cmd_toggle_follow()
            assert tab.follow_active is True

            # Dopisujemy linię z błędem ERR-102 (linia 10)
            with open(tf.name, "ab") as f:
                f.write(b"Line 10 - [ERROR] Restarting after ERR-102\n")

            new_lines = indexer.update_from(os.stat(tf.name).st_size)
            assert new_lines == 1
            tab.file_controller._on_follow_new_lines(new_line_count=new_lines, mtime_str="now", ctime_str="now")

            for _ in range(50):
                QCoreApplication.processEvents()
                time.sleep(0.01)

            # Dopisujemy kolejne 3 linie bez błędu (linie 11, 12, 13) - powinny wejść jako kontekst
            with open(tf.name, "ab") as f:
                for i in range(11, 14):
                    f.write(f"Line {i} - [INFO] Background task {i} OK\n".encode("utf-8"))

            new_lines_2 = indexer.update_from(os.stat(tf.name).st_size)
            assert new_lines_2 == 3
            tab.file_controller._on_follow_new_lines(new_line_count=new_lines_2, mtime_str="now", ctime_str="now")

            for _ in range(50):
                QCoreApplication.processEvents()
                time.sleep(0.01)

            # Sprawdzamy czy linie 10, 11, 12, 13 są w _filter_all_lines
            assert 10 in tab._filter_all_lines
            assert 11 in tab._filter_all_lines
            assert 12 in tab._filter_all_lines
            assert 13 in tab._filter_all_lines

            # Tylko linia 10 powinna być w filter_results (trafienie)
            assert 10 in tab.filter_results
            assert 11 not in tab.filter_results
            assert 12 not in tab.filter_results
            assert 13 not in tab.filter_results

            # Weryfikujemy ExtraSelections na kontrolce edytora
            sels = tab.text.extraSelections()
            assert len(sels) >= 4, "Wszystkie 4 linie (trafienie + 3 linie kontekstu) powinny mieć ExtraSelections"

            # Sprawdzamy kolory: linia 10 musi mieć kolor highlight (#fff176), a linie 11..13 kolor context (#3a3d3a)
            color_highlight = tab._theme_colors.get("highlight", QtGui.QColor("#fff176"))
            color_context = tab._theme_colors.get("context", QtGui.QColor("#3a3d3a"))

            hit_sels = [s for s in sels if s.format.background().color() == color_highlight]
            context_sels = [s for s in sels if s.format.background().color() == color_context]

            assert len(hit_sels) == 2, f"Linie 0 i 10 (ERR-000 i ERR-102) powinny mieć żółte tło, znaleziono: {len(hit_sels)}"
            assert len(context_sels) >= 3, f"Linie 11..13 powinny mieć szare tło kontekstu, znaleziono: {len(context_sels)}"

            print("Follow incremental context and highlighting test passed successfully!")
    finally:
        with patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.No):
            if 'window' in locals() and window is not None:
                window.close()
            for _ in range(20):
                QCoreApplication.processEvents()
            try:
                os.unlink(tf.name)
            except OSError:
                pass
