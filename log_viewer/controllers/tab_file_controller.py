from typing import Optional
from PySide6.QtCore import QObject, Qt, Slot, QThread, QTimer
from PySide6.QtWidgets import QMessageBox, QProgressDialog
import os
import time
import os
from log_viewer.helpers import fmt_size
from log_viewer.workers import IndexerWorker
from log_viewer.indexer import LineIndexer

class FileController(QObject):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab

    def open_file(self, path: str, title: Optional[str] = None, preserve_state: bool = False) -> None:
        if not os.path.isfile(path):
            QMessageBox.critical(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_no_file"))
            return

        # Bezpieczeństwo wątkowe: jeśli to np. Reload (lub Open po Open), zatrzymaj poprzednie instancje, 
        # by nie stworzyć zombiaków pożerających dysk i wywołujących segfault.
        if getattr(self.tab, '_indexer_worker', None) is not None:
            try:
                self.tab._indexer_worker.cancel()
            except Exception:
                pass
        for thread_name in ('_indexer_thread', '_filter_thread', '_save_thread', '_search_thread'):
            t = getattr(self.tab, thread_name, None)
            if t is not None:
                try:
                    if t.isRunning():
                        t.quit()
                        t.wait(1500)
                except RuntimeError:
                    pass
                setattr(self.tab, thread_name, None)

        if not preserve_state:
            self.tab.cmd_clear_filter(silent=True)
        if self.tab.follow_active:
            self.tab.cmd_toggle_follow()
        self.tab.file_path = path
        self.tab._assigned_title = title or os.path.basename(path)
        self.tab._status(self.tab.t("st_opening"))
        self.tab.title_changed.emit(self.tab._assigned_title)
        self.tab.window_start = 0
        self.tab.window_lines = []
        self.tab.line_map = []
        if not preserve_state:
            self.tab.edit_buffer.clear()
            self.tab.bookmarks.clear()
            self.tab._refresh_bookmarks_tree()
            self.tab._refresh_edits_tree()
            self.tab.pct_label.setText("0%")
            self.tab.text.setPlainText("")
        self.tab.text.set_line_map([])

        encoding = self.tab.encoding

        # QProgressDialog — pokazuje postęp indeksowania z przyciskiem Anuluj.
        # Dla małych plików (< 100 MB) dialog się nie pojawi (indeksowanie
        # trwa < 1s, Qt automatycznie ukrywa dialog jeśli minDuration nie upłynął).
        file_size = os.path.getsize(path)
        # Pokaż dialog tylko dla plików > 50 MB — dla mniejszych indeksowanie
        # jest błyskawiczne i dialog by tylko mig­nął.
        show_dialog = file_size > 50 * 1024 * 1024
        if show_dialog:
            self.tab._index_progress = QProgressDialog(
                self.tab._fmt("st_indexing", pct="0.0"),
                self.tab.t("btn_cancel"),
                0, 100, self.tab._main,
            )
            self.tab._index_progress.setWindowTitle(self.tab.t("dlg_index_title"))
            self.tab._index_progress.setMinimumDuration(500)  # pokaż po 500ms
            self.tab._index_progress.setAutoClose(True)
            self.tab._index_progress.setAutoReset(True)
            self.tab._index_progress.canceled.connect(self.tab._cancel_indexing)
        else:
            self.tab._index_progress = None

        self.tab._indexer_thread = QThread()
        self.tab._indexer_worker = IndexerWorker(path, encoding, self.tab.index_interval_bytes)
        self.tab._indexer_worker.moveToThread(self.tab._indexer_thread)
        self.tab._indexer_thread.started.connect(self.tab._indexer_worker.run)
        self.tab._register_thread_worker(self.tab._indexer_thread, self.tab._indexer_worker)

        # Używamy metod-slotów (nie closure) — Qt QueuedConnection wymaga
        # picklowalnych odbiorców, a closure nie jest picklowalne. To była
        # przyczyna błędu „Timers cannot be stopped from another thread" —
        #Qt nie mógł zakolejkować wywołania i wywoływał slot w worker thread.
        self.tab._indexer_worker.progress.connect(self.tab._on_index_progress, Qt.QueuedConnection)
        self.tab._indexer_worker.finished.connect(self.tab._on_index_done, Qt.QueuedConnection)
        self.tab._indexer_worker.error.connect(self.tab._on_index_error, Qt.QueuedConnection)
        self.tab._indexer_worker.finished.connect(self.tab._indexer_thread.quit, Qt.QueuedConnection)
        self.tab._indexer_worker.error.connect(self.tab._indexer_thread.quit, Qt.QueuedConnection)
        self.tab._indexer_worker.finished.connect(self.tab._indexer_worker.deleteLater, Qt.QueuedConnection)
        self.tab._indexer_worker.error.connect(self.tab._indexer_worker.deleteLater, Qt.QueuedConnection)
        self.tab._indexer_thread.finished.connect(self.tab._indexer_thread.deleteLater, Qt.QueuedConnection)
        # Cleanup dialog przy zakończeniu (sukces lub błąd).
        self.tab._indexer_worker.finished.connect(self.tab._close_index_progress, Qt.QueuedConnection)
        self.tab._indexer_worker.error.connect(self.tab._close_index_progress, Qt.QueuedConnection)
        self.tab._indexer_thread.start()

    @Slot(float)
    def _on_index_progress(self, p: float) -> None:
        """Slot dla sygnału progress z IndexerWorker. Aktualizuje status bar
        i dialog postępu. MUSI być metodą (nie closure) żeby Qt QueuedConnection
        działał poprawnie — closure nie jest picklowalne cross-thread."""
        self.tab._status(self.tab._fmt("st_indexing", pct=f"{p:.1f}"))
        if self.tab._index_progress is not None:
            self.tab._index_progress.setValue(int(p))
            self.tab._index_progress.setLabelText(self.tab._fmt("st_indexing", pct=f"{p:.1f}"))

    def _cancel_indexing(self) -> None:
        """Anuluje indeksowanie — ustawia flagę w workerze. Pool zostanie
        przerwany w _build_parallel."""
        if self.tab._indexer_worker is not None:
            self.tab._indexer_worker.cancel()
        self.tab._status(self.tab.t("st_cancelling"))

    def _close_index_progress(self) -> None:
        """Zamyka dialog postępu indeksowania (sukces, błąd, anulowanie)."""
        if self.tab._index_progress is not None:
            self.tab._index_progress.blockSignals(True)
            self.tab._index_progress.close()
            self.tab._index_progress = None

    @Slot(object)
    def _on_index_error(self, err: str) -> None:
        if err == "cancelled":
            # Anulowane przez usera — nie pokazuj jako błąd, tylko status.
            self.tab._status(self.tab.t("st_cancelled"))
            if self.tab.indexer is None:
                idx = self.tab._main.tabs.indexOf(self.tab)
                if idx >= 0:
                    self.tab._main._on_tab_close_requested(idx)
            return
        QMessageBox.critical(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_index_error").format(e=err))
        self.tab._status(self.tab.t("st_ready"))

    @Slot(object)
    def _on_index_done(self, idx: LineIndexer) -> None:
        if getattr(self.tab, "edit_buffer", None) is None:
            return
        if self.tab.indexer is not None:
            try:
                self.tab.indexer.close()
            except Exception:
                pass
            self.tab.indexer = None

        self.tab.line_map = None
        self.tab.filter_results = None
        self.tab._filter_all_lines = None
        self.tab.filter_context_lines = None
        self.tab._filter_hit_text_map = None
        self.tab._filter_hit_lines = None
        self.tab.filter_engine = None
        self.tab._search_engine = None
        try:
            self.tab.text.clear()
        except Exception:
            pass
        self.tab.indexer = idx
        self.tab._last_file_size = idx.size
        try:
            st = os.stat(self.tab.file_path) if self.tab.file_path else None
            if st is not None:
                self.tab._file_mtime_at_open = st.st_mtime_ns
                self.tab._file_size_at_open = st.st_size
                self.tab._last_file_inode = st.st_ino
        except OSError:
            pass
        self.tab._status(self.tab._fmt("st_done", total=idx.line_count, size=fmt_size(idx.size)))
        self.tab._load_window(at_line=0)
        self.tab._refresh_bookmarks_tree()
        self.tab._refresh_edits_tree()
        # Zaktualizuj tytuł zakładki — przywróć właściwy tytuł z sufiksem
        if self.tab.file_path:
            self.tab.title_changed.emit(getattr(self.tab, "_assigned_title", os.path.basename(self.tab.file_path)))
        # Zaktualizuj mini-mapę — natychmiast (dla małych plików) + debounced (dla dużych)
        self.tab._update_minimap()
        self.tab._minimap_update_timer.start()
        
        if getattr(self.tab, "_pending_reload_filter", False):
            self.tab._pending_reload_filter = False
            self.tab.cmd_apply_filter()

    def _start_reindex(self, saved_line: int) -> None:
        self.tab._status(self.tab.t("st_opening"))
        self.tab._reindex_saved_line = saved_line
        self.tab._indexer_thread = QThread()
        self.tab._indexer_worker = IndexerWorker(self.tab.file_path, self.tab.encoding, self.tab.index_interval_bytes)
        self.tab._indexer_worker.moveToThread(self.tab._indexer_thread)
        self.tab._indexer_thread.started.connect(self.tab._indexer_worker.run)
        self.tab._register_thread_worker(self.tab._indexer_thread, self.tab._indexer_worker)
        # QueuedConnection + metoda-slot (nie lambda) — closure nie jest
        # picklowalne cross-thread, powoduje błędy QTimer w worker thread.
        self.tab._indexer_worker.progress.connect(self.tab._on_index_progress, Qt.QueuedConnection)
        self.tab._indexer_worker.finished.connect(self.tab._on_reindex_finished, Qt.QueuedConnection)
        self.tab._indexer_worker.error.connect(self.tab._on_index_error, Qt.QueuedConnection)
        self.tab._indexer_worker.finished.connect(self.tab._indexer_thread.quit, Qt.QueuedConnection)
        self.tab._indexer_worker.error.connect(self.tab._indexer_thread.quit, Qt.QueuedConnection)
        self.tab._indexer_worker.finished.connect(self.tab._indexer_worker.deleteLater, Qt.QueuedConnection)
        self.tab._indexer_worker.error.connect(self.tab._indexer_worker.deleteLater, Qt.QueuedConnection)
        self.tab._indexer_thread.finished.connect(self.tab._indexer_thread.deleteLater, Qt.QueuedConnection)
        self.tab._indexer_thread.start()

    @Slot(object)
    def _on_reindex_finished(self, idx: LineIndexer) -> None:
        """Slot dla sygnału finished z reindex workera — przekazuje do
        _on_reindex_after_save z zapamiętanym saved_line."""
        saved_line = getattr(self.tab, "_reindex_saved_line", 0)
        self.tab._on_reindex_after_save(idx, saved_line)

    @Slot(object, int)
    def _on_reindex_after_save(self, idx: LineIndexer, saved_line: int) -> None:
        if self.tab.indexer is not None:
            try:
                self.tab.indexer.close()
            except Exception:
                pass
        self.tab.indexer = idx
        self.tab._last_file_size = idx.size
        try:
            st = os.stat(self.tab.file_path) if self.tab.file_path else None
            if st is not None:
                self.tab._file_mtime_at_open = st.st_mtime_ns
                self.tab._file_size_at_open = st.st_size
                self.tab._last_file_inode = st.st_ino
        except OSError:
            pass
        self.tab._status(self.tab._fmt("st_done", total=idx.line_count, size=fmt_size(idx.size)))
        try:
            self.tab._load_window(at_line=saved_line)
        except OSError:
            # Ignorujemy potencjalne usunięcie pliku z dysku pod maską w trakcie lub tuż po reindeksie.
            pass

    def _cancel_follow_if_active(self) -> None:
        """Helper to cancel follow mode proactively when manual jumps happen."""
        if self.tab.follow_active:
            self.tab.cmd_toggle_follow()

    def cmd_refresh(self) -> None:
        if not self.tab.file_path:
            return
        self.tab._status(self.tab.t("st_refreshing"))
        self._check_for_updates_once()

    def cmd_reload(self) -> None:
        if not self.tab.file_path:
            return
        has_filter = bool(self.tab._main.filter_entry.text().strip())
        self.tab._pending_reload_filter = has_filter
        self.open_file(self.tab.file_path, self.tab._assigned_title, preserve_state=True)

    def _check_for_updates_once(self) -> None:
        if not self.tab.file_path:
            return
        try:
            current_stat = os.stat(self.tab.file_path)
        except OSError:
            return
        current_size = current_stat.st_size
        current_inode = current_stat.st_ino
        mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(current_stat.st_mtime))
        ctime_str = time.strftime("%H:%M:%S")

        if current_inode != self.tab._last_file_inode and self.tab._last_file_inode != 0:
            self.tab._start_follow_reindex(current_size, current_inode)
            return

        if current_size > self.tab._last_file_size:
            try:
                new_lines = self.tab.indexer.update_from(current_size)
                self.tab._last_file_size = current_size
                if new_lines > 0:
                    self.tab._on_follow_new_lines(new_lines, mtime_str, ctime_str)
                else:
                    self.tab._status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))
            except Exception:
                pass
        elif current_size < self.tab._last_file_size:
            self.tab._start_follow_reindex(current_size, current_inode)
        else:
            self.tab._status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))

    def cmd_toggle_follow(self) -> None:
        if not self.tab.indexer:
            return
        self.tab.follow_active = not self.tab.follow_active
        if self.tab._main._follow_action is not None:
            self.tab._main._follow_action.setChecked(self.tab.follow_active)
        if self.tab.follow_active:
            self.tab._last_file_size = self.tab.indexer.size
            try:
                self.tab._last_file_inode = os.stat(self.tab.file_path).st_ino
            except OSError:
                self.tab._last_file_inode = 0

            if self.tab.indexer and self.tab.indexer.line_count > 0:
                last_start = max(0, self.tab.indexer.line_count - self.tab.window_size_lines)
                self.tab._load_window(at_line=last_start)
                self.tab.text.verticalScrollBar().setValue(self.tab.text.verticalScrollBar().maximum())

            self.tab._follow_poll()
        else:
            self.tab._refresh_status()

    def _follow_poll(self) -> None:
        if not self.tab.follow_active or not self.tab.file_path:
            return
        if self.tab._follow_reindexing:
            QTimer.singleShot(200, self.tab._follow_poll)
            return
        try:
            current_stat = os.stat(self.tab.file_path)
        except OSError:
            QTimer.singleShot(200, self.tab._follow_poll)
            return
        current_size = current_stat.st_size
        current_inode = current_stat.st_ino

        if current_inode != self.tab._last_file_inode and self.tab._last_file_inode != 0:
            self.tab._follow_reindexing = True
            self.tab._start_follow_reindex(current_size, current_inode)
            QTimer.singleShot(200, self.tab._follow_poll)
            return

        mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(current_stat.st_mtime))
        ctime_str = time.strftime("%H:%M:%S")

        if current_size > self.tab._last_file_size:
            next_poll = 200
            try:
                new_lines = self.tab.indexer.update_from(current_size)
                self.tab._last_file_size = current_size
                if new_lines > 0:
                    self.tab._on_follow_new_lines(new_lines, mtime_str, ctime_str)
            except Exception:
                pass
        elif current_size < self.tab._last_file_size:
            next_poll = 200
            self.tab._follow_reindexing = True
            self.tab._start_follow_reindex(current_size, current_inode)
        else:
            next_poll = 1000
            self.tab._status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))
        QTimer.singleShot(next_poll, self.tab._follow_poll)

    def _start_follow_reindex(self, current_size: int, current_inode: int) -> None:
        self.tab._follow_reindex_size = current_size
        self.tab._follow_reindex_inode = current_inode
        self.tab._indexer_thread = QThread()
        self.tab._indexer_worker = IndexerWorker(self.tab.file_path, self.tab.encoding, self.tab.index_interval_bytes)
        self.tab._indexer_worker.moveToThread(self.tab._indexer_thread)
        self.tab._indexer_thread.started.connect(self.tab._indexer_worker.run)
        self.tab._register_thread_worker(self.tab._indexer_thread, self.tab._indexer_worker)
        # QueuedConnection + metoda-slot (nie lambda) — closure nie jest
        # picklowalne cross-thread, powoduje błędy QTimer w worker thread.
        self.tab._indexer_worker.finished.connect(self.tab._on_follow_reindex_slot, Qt.QueuedConnection)
        self.tab._indexer_worker.error.connect(self.tab._on_follow_reindex_failed, Qt.QueuedConnection)
        self.tab._indexer_worker.finished.connect(self.tab._indexer_thread.quit, Qt.QueuedConnection)
        self.tab._indexer_worker.error.connect(self.tab._indexer_thread.quit, Qt.QueuedConnection)
        self.tab._indexer_worker.finished.connect(self.tab._on_follow_reindex_clear_flag, Qt.QueuedConnection)
        self.tab._indexer_worker.error.connect(self.tab._on_follow_reindex_clear_flag, Qt.QueuedConnection)
        self.tab._indexer_worker.finished.connect(self.tab._indexer_worker.deleteLater, Qt.QueuedConnection)
        self.tab._indexer_worker.error.connect(self.tab._indexer_worker.deleteLater, Qt.QueuedConnection)
        self.tab._indexer_thread.finished.connect(self.tab._indexer_thread.deleteLater, Qt.QueuedConnection)
        self.tab._indexer_thread.start()

    @Slot(object)
    def _on_follow_reindex_slot(self, idx: LineIndexer) -> None:
        """Slot pośredniczący — odbiera idx z workera i woła _on_follow_reindex
        z zapamiętanymi parametrami. Bez lambdy (cross-thread safe)."""
        size = getattr(self.tab, "_follow_reindex_size", 0)
        inode = getattr(self.tab, "_follow_reindex_inode", 0)
        self.tab._on_follow_reindex(idx, size, inode)

    @Slot()
    def _on_follow_reindex_clear_flag(self) -> None:
        """Czyści flagę _follow_reindexing po zakończeniu reindex."""
        self.tab._follow_reindexing = False

    def _on_follow_new_lines(self, new_line_count: int = 0, mtime_str: str = "", ctime_str: str = "") -> None:
        if not self.tab.indexer or self.tab.indexer.line_count == 0:
            return
            
        pending = getattr(self.tab, "_inc_pending_lines", 0) + new_line_count
        self.tab._inc_pending_lines = pending
        
        if pending > 0 or not self.tab.line_map:
            if self.tab.filter_active and getattr(self.tab, "filter_results", None) is not None:
                is_running = False
                if hasattr(self.tab, "_inc_filter_thread"):
                    try:
                        is_running = self.tab._inc_filter_thread.isRunning()
                    except RuntimeError:
                        is_running = False
                        
                if is_running:
                    return
                self.tab._inc_pending_lines = 0
                self._start_incremental_filter(pending, mtime_str, ctime_str)
                return
                
            self.tab._inc_pending_lines = 0
            self._apply_follow_new_lines(mtime_str, ctime_str)
        else:
            self.tab._status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))

    def _start_incremental_filter(self, new_line_count: int, mtime_str: str, ctime_str: str) -> None:
        from log_viewer.workers import IncrementalFilterWorker
        new_total_lines = self.tab.indexer.line_count
        start_line = max(0, new_total_lines - new_line_count)
        
        self.tab._inc_new_total_lines = new_total_lines
        self.tab._inc_mtime_str = mtime_str
        self.tab._inc_ctime_str = ctime_str
        
        pattern = self.tab._main.filter_entry.text().strip()
        use_regex = self.tab._main.filter_regex_cb.isChecked()
        case_sens = self.tab._main.filter_case_cb.isChecked()
        negate = self.tab._main.filter_negate_cb.isChecked()
        context_after = self.tab._main.filter_context_spin.value()
        
        self.tab._inc_filter_thread = QThread()
        self.tab._inc_filter_worker = IncrementalFilterWorker(
            self.tab.indexer,
            start_line, new_total_lines,
            pattern, use_regex, case_sens, negate, self.tab.encoding, context_after
        )
        self.tab._inc_filter_worker.moveToThread(self.tab._inc_filter_thread)
        self.tab._inc_filter_thread.started.connect(self.tab._inc_filter_worker.run)
        
        self.tab._inc_filter_worker.finished.connect(self._on_inc_finished_slot, Qt.QueuedConnection)
        self.tab._inc_filter_worker.finished.connect(self.tab._inc_filter_thread.quit, Qt.QueuedConnection)
        self.tab._inc_filter_worker.finished.connect(self.tab._inc_filter_worker.deleteLater, Qt.QueuedConnection)
        self.tab._inc_filter_thread.finished.connect(self.tab._inc_filter_thread.deleteLater, Qt.QueuedConnection)
        
        self.tab._register_thread_worker(self.tab._inc_filter_thread, self.tab._inc_filter_worker)
        self.tab._inc_filter_thread.start()

    @Slot(object, object)
    def _on_inc_finished_slot(self, results_list, filter_all_list) -> None:
        new_total_lines = getattr(self.tab, "_inc_new_total_lines", 0)
        mtime_str = getattr(self.tab, "_inc_mtime_str", "")
        ctime_str = getattr(self.tab, "_inc_ctime_str", "")
        if new_total_lines > 0:
            has_new = False
            if len(results_list) > 0 or len(filter_all_list) > 0:
                from log_viewer.bitset import Bitset
                
                # Zabezpieczenie przed rzutowaniem przez nadrzędny kontroler na NoneType / Array przy nakładaniu okien asynchronicznych
                if getattr(self.tab, "filter_results", None) is None or not hasattr(self.tab.filter_results, "resize"):
                    self.tab.filter_results = Bitset(new_total_lines)
                if getattr(self.tab, "_filter_all_lines", None) is None or not hasattr(self.tab._filter_all_lines, "resize"):
                    self.tab._filter_all_lines = Bitset(new_total_lines)
                    
                self.tab.filter_results.resize(new_total_lines)
                self.tab.filter_results.update_indices(results_list)
                self.tab._filter_all_lines.resize(new_total_lines)
                self.tab._filter_all_lines.update_indices(filter_all_list)
                has_new = True
                
            self._apply_follow_new_lines(mtime_str, ctime_str, force_reload=has_new)

    def _apply_follow_new_lines(self, mtime_str: str, ctime_str: str, force_reload: bool = False) -> None:
        if self.tab.filter_active and getattr(self.tab, "filter_results", None) is not None:
            last_start = max(0, len(self.tab._filter_all_lines) - self.tab.window_size_lines)
        else:
            last_start = max(0, self.tab.indexer.line_count - self.tab.window_size_lines)
            force_reload = True
            
        self.tab._ignore_scroll_events = True
        try:
            self.tab._load_window(at_line=last_start, force_reload=force_reload)
        finally:
            self.tab._ignore_scroll_events = False
            
        def _scroll_down():
            if getattr(self.tab, "_ignore_scroll_events", False) is False:
                self.tab._ignore_scroll_events = True
                self.tab.text.verticalScrollBar().setValue(self.tab.text.verticalScrollBar().maximum())
                self.tab._ignore_scroll_events = False
                
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, _scroll_down)
            
        if self.tab.filter_active and getattr(self.tab, "filter_results", None) is not None:
            status_filter = self.tab._fmt("st_filtered", hits=len(self.tab.filter_results), total=self.tab.indexer.line_count)
            status_follow = self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str)
            self.tab._status(f"{status_filter} | {status_follow}")
        else:
            self.tab._status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))

    @Slot(object, int, int)
    def _on_follow_reindex(self, idx: LineIndexer, new_size: int, new_inode: int) -> None:
        if self.tab.indexer is not None:
            try:
                self.tab.indexer.close()
            except Exception:
                pass
        self.tab.indexer = idx
        self.tab._last_file_size = new_size
        if new_inode != 0:
            self.tab._last_file_inode = new_inode
        last_start = max(0, idx.line_count - self.tab.window_size_lines)
        self.tab._load_window(at_line=last_start)
        self.tab.text.verticalScrollBar().setValue(self.tab.text.verticalScrollBar().maximum())
        mtime_str = ""
        try:
            mtime = os.stat(self.tab.file_path).st_mtime
            mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
        except OSError:
            mtime_str = "?"
        ctime_str = time.strftime("%H:%M:%S")
        self.tab._status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))

    @Slot(str)
    def _on_follow_reindex_failed(self, err: str) -> None:
        self.tab._status(self.tab.t("st_follow_reindex_failed"))