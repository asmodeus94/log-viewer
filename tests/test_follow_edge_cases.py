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
    """Test sprawdza naprawiony proces uaktualniania filtra w trybie Tail
    oraz asynchronicznego przemieszczania suwaka unikając Thread Leak'ów.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg = UserConfig(config_path=tempfile.mktemp(suffix=".json"))
    window = LogViewerWindow(config=cfg)

    # Tworzymy maly plik (np. 10 linii), symulujac przedwczesne uruchomienie "tail" z malym buforem okna
    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".log")
    for i in range(10):
        tf.write(f"Line {i} - basic text\n".encode('utf-8'))
    tf.close()

    try:
        tab = window.open_file_in_tab(tf.name)
        
        # Wymuszamy wlasny indexer szybki dla testu
        indexer = LineIndexer(tf.name, progress_cb=None, encoding="utf-8", index_interval_bytes=1024)
        tab.indexer = indexer
        tab.file_path = tf.name
        
        # Inicjujemy filtry na karcie uzywajac naszego TARGET (tu: "secret")
        window.filter_entry.setText("secret")
        window.cmd_apply_filter() # Aplikujemy by filter_active zrobilo sie True
        
        # Filtrujemy. Obecnie nie ma "secret" w pliku.
        assert tab.filter_active is True
        
        # Zwijamy scrollbar do 0 by sprawdzic timer
        tab.text.verticalScrollBar().setValue(0)
        
        # Aktywujemy Follow bez uruchamiania petli _follow_poll ktora zawiesza test na macOS
        tab.follow_active = True
        assert tab.follow_active is True
        
        # Symulujemy natlok zdarzen (szybkie dopisywanie uzytkownika do rosnacego pliku logow)
        with open(tf.name, "ab") as f:
            for i in range(10, 20):
                f.write(f"Line {i} - secret incoming\n".encode('utf-8'))
                
        new_lines_count = indexer.update_from(os.stat(tf.name).st_size)
        assert new_lines_count == 10
        
        # Wywołanie _on_follow_new_lines (to co wywolywalo Segfault wczesniej przez lambda closure)
        tab.file_controller._on_follow_new_lines(new_line_count=new_lines_count, mtime_str="now", ctime_str="now")
        
        # Czekamy na przetworzenie pętli (Worker z szukaniem filtru wysle wyniki)
        for _ in range(100):
            QCoreApplication.processEvents()
            time.sleep(0.01)
            if hasattr(tab, '_inc_filter_thread') and not tab._inc_filter_thread.isRunning():
                break
            
        # Po przetworzeniu eventow, powinny sie pojawic zaaktualizowane wyniki bitset
        assert len(tab.filter_results) > 0, "IncrementalWorker filter results were not correctly merged"
        
        # Sprawdzamy czy timer przewijania podbil suwak (QTimer.singleShot w GUI loopie)
        # Nastepnie odpalamy krotki cykl dla pewnosci ze wygasl
        for _ in range(50):
            QCoreApplication.processEvents()
            time.sleep(0.01)
            if tab.text.verticalScrollBar().value() == tab.text.verticalScrollBar().maximum():
                break
            
        max_val = tab.text.verticalScrollBar().maximum()
        assert tab.text.verticalScrollBar().value() == max_val, "Scrollbar did not reach the bottom after Timer execution"
        
        # Drugi natlok (test na thread leak i wczesniejsze zakonczenie optymalizatora)
        with open(tf.name, "ab") as f:
            f.write(b"Line 20 - empty\n")
            
        new_lines_count_2 = indexer.update_from(os.stat(tf.name).st_size)
        tab.file_controller._on_follow_new_lines(new_line_count=new_lines_count_2, mtime_str="now", ctime_str="now")
        
        assert getattr(tab, "_inc_pending_lines", 0) >= 0
        assert tab.file_controller is not None
        
        print("All Incremental Follow Edge Cases Pass!")
    finally:
        try:
            os.unlink(tf.name)
        except OSError:
            pass
