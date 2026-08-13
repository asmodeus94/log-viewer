import os
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets
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
