"""tab_file_controller.py — Kontroler operacji plikowych, indeksowania i trybu follow dla LogTab."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Slot
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from log_viewer.bitset import Bitset
from log_viewer.helpers import fmt_size
from log_viewer.indexer import LineIndexer
from log_viewer.workers import IndexerWorker

if TYPE_CHECKING:
    from log_viewer.log_tab import LogTab


class FileController(QObject):
    """Kontroler odpowiedzialny za operacje wejścia/wyjścia na plikach (otwieranie,
    reindeksowanie, przeładowywanie, śledzenie dopisywania zmian — Follow mode)
    oraz powiązane wątki tła w pojedynczej karcie LogTab.
    """

    def __init__(self, tab: LogTab) -> None:
        super().__init__(tab)
        self.tab: LogTab = tab

    def stop_background_threads(self) -> None:
        """Bezpiecznie zatrzymuje aktywne workery i wątki pracujące w tle dla karty."""
        self._stop_background_threads()

    def _stop_background_threads(self) -> None:
        """Bezpiecznie zatrzymuje aktywne workery i wątki pracujące w tle dla karty."""
        workers = (
            self.tab.indexer_worker,
            self.tab.filter_worker,
            self.tab.save_worker,
            self.tab.save_as_worker,
            self.tab.export_worker,
            self.tab.inc_filter_worker,
        )
        for worker in workers:
            if worker is not None:
                try:
                    if hasattr(worker, "cancel"):
                        worker.cancel()
                except (AttributeError, RuntimeError):
                    pass

        self.tab.indexer_worker = None
        self.tab.filter_worker = None
        self.tab.save_worker = None
        self.tab.save_as_worker = None
        self.tab.export_worker = None
        self.tab.inc_filter_worker = None

        threads = (
            self.tab.indexer_thread,
            self.tab.filter_thread,
            self.tab.save_thread,
            self.tab.search_thread,
            self.tab.inc_filter_thread,
        )
        for thread in threads:
            if thread is not None:
                try:
                    if thread.isRunning():
                        thread.quit()
                        thread.wait(1500)
                except (RuntimeError, AttributeError):
                    pass

        self.tab.indexer_thread = None
        self.tab.filter_thread = None
        self.tab.save_thread = None
        self.tab.search_thread = None
        self.tab.inc_filter_thread = None

        indexer = self.tab.indexer
        if indexer is not None:
            try:
                indexer.close()
            except (OSError, RuntimeError):
                pass
            self.tab.indexer = None

    def open_file(self, path: str, title: str | None = None, preserve_state: bool = False) -> None:
        """Rozpoczyna asynchroniczne otwarcie i indeksowanie pliku."""
        if not os.path.isfile(path):
            QMessageBox.critical(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_no_file"))
            return

        # Bezpieczeństwo wątkowe: zatrzymaj poprzednie instancje
        self._stop_background_threads()

        if not preserve_state:
            self.tab.cmd_clear_filter(silent=True)
        if self.tab.follow_active:
            self.tab.cmd_toggle_follow()
        self.tab.file_path = path
        assigned_title = title or os.path.basename(path)
        self.tab.assigned_title = assigned_title
        self.tab.set_status(self.tab.t("st_opening"))
        self.tab.title_changed.emit(assigned_title)
        self.tab.window_start = 0
        self.tab.window_lines = []
        self.tab.line_map = []
        if not preserve_state:
            self.tab.edit_buffer.clear()
            self.tab.bookmarks.clear()
            self.tab.refresh_bookmarks_tree()
            self.tab.refresh_edits_tree()
            self.tab.pct_label.setText("0%")
            self.tab.text.setPlainText("")
        self.tab.text.set_line_map([])

        encoding = self.tab.encoding

        # QProgressDialog — pokazuje postęp indeksowania z przyciskiem Anuluj dla plików > 50 MB
        try:
            file_size = os.path.getsize(path)
        except OSError:
            file_size = 0

        index_progress: QProgressDialog | None = None
        if file_size > 50 * 1024 * 1024:
            dlg = QProgressDialog(
                self.tab.fmt("st_indexing", pct="0.0"),
                self.tab.t("btn_cancel"),
                0,
                100,
                self.tab.main_window,
            )
            dlg.setWindowTitle(self.tab.t("dlg_index_title"))
            dlg.setMinimumDuration(500)  # pokaż po 500ms
            dlg.setAutoClose(True)
            dlg.setAutoReset(True)
            dlg.canceled.connect(self._cancel_indexing)
            index_progress = dlg

        self.tab.index_progress = index_progress

        indexer_thread = QThread()
        indexer_worker = IndexerWorker(path, encoding, self.tab.index_interval_bytes)
        self.tab.indexer_thread = indexer_thread
        self.tab.indexer_worker = indexer_worker

        indexer_worker.moveToThread(indexer_thread)
        indexer_thread.started.connect(indexer_worker.run)
        self.tab.register_thread_worker(indexer_thread, indexer_worker)

        # Używamy metod-slotów (nie closure) — Qt QueuedConnection wymaga
        # picklowalnych odbiorców, a closure nie jest picklowalne cross-thread.
        indexer_worker.progress.connect(self._on_index_progress, Qt.ConnectionType.QueuedConnection)
        indexer_worker.finished.connect(self._on_index_done, Qt.ConnectionType.QueuedConnection)
        indexer_worker.error.connect(self._on_index_error, Qt.ConnectionType.QueuedConnection)
        indexer_worker.finished.connect(indexer_thread.quit, Qt.ConnectionType.QueuedConnection)
        indexer_worker.error.connect(indexer_thread.quit, Qt.ConnectionType.QueuedConnection)
        indexer_worker.finished.connect(indexer_worker.deleteLater, Qt.ConnectionType.QueuedConnection)
        indexer_worker.error.connect(indexer_worker.deleteLater, Qt.ConnectionType.QueuedConnection)
        indexer_thread.finished.connect(indexer_thread.deleteLater, Qt.ConnectionType.QueuedConnection)
        # Cleanup dialogu przy zakończeniu (sukces lub błąd).
        indexer_worker.finished.connect(self._close_index_progress, Qt.ConnectionType.QueuedConnection)
        indexer_worker.error.connect(self._close_index_progress, Qt.ConnectionType.QueuedConnection)
        indexer_thread.start()

    @Slot(float)
    def _on_index_progress(self, p: float) -> None:
        """Slot dla sygnału progress z IndexerWorker. Aktualizuje status bar
        i dialog postępu. MUSI być metodą (nie closure) żeby Qt QueuedConnection
        działał poprawnie — closure nie jest picklowalne cross-thread."""
        self.tab.set_status(self.tab.fmt("st_indexing", pct=f"{p:.1f}"))
        dialog = self.tab.index_progress
        if dialog is not None:
            dialog.setValue(int(p))
            dialog.setLabelText(self.tab.fmt("st_indexing", pct=f"{p:.1f}"))

    def _cancel_indexing(self) -> None:
        """Anuluje indeksowanie — ustawia flagę w workerze."""
        worker = self.tab.indexer_worker
        if worker is not None:
            worker.cancel()
        self.tab.set_status(self.tab.t("st_cancelling"))

    def _close_index_progress(self) -> None:
        """Zamyka dialog postępu indeksowania (sukces, błąd, anulowanie)."""
        dialog = self.tab.index_progress
        if dialog is not None:
            dialog.blockSignals(True)
            dialog.close()
            dialog.deleteLater()
            self.tab.index_progress = None

    @Slot(object)
    def _on_index_error(self, err: str) -> None:
        self.tab.file_loaded.emit(False)
        if err == "cancelled":
            # Anulowane przez użytkownika — nie pokazuj jako błąd, tylko status.
            self.tab.set_status(self.tab.t("st_cancelled"))
            if self.tab.indexer is None:
                idx = self.tab.main_window.tabs.indexOf(self.tab)
                if idx >= 0:
                    self.tab.main_window.close_tab(idx)
            return
        QMessageBox.critical(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_index_error").format(e=err))
        self.tab.set_status(self.tab.t("st_ready"))

    @Slot(object)
    def _on_index_done(self, idx: LineIndexer) -> None:
        if getattr(self.tab, "edit_buffer", None) is None:
            return
        if self.tab.indexer is not None:
            try:
                self.tab.indexer.close()
            except (OSError, RuntimeError):
                pass
            self.tab.indexer = None

        self.tab.line_map = None
        self.tab.filter_results = None
        self.tab.filter_all_lines = None
        self.tab.filter_context_lines = None
        self.tab.filter_hit_text_map = {}
        self.tab.filter_hit_lines = set()
        self.tab.filter_engine = None
        self.tab.search_engine = None
        try:
            self.tab.text.clear()
        except RuntimeError:
            pass
        self.tab.indexer = idx
        self.tab.last_file_size = idx.size
        file_path = self.tab.file_path
        if file_path:
            try:
                st = os.stat(file_path)
                self.tab.file_mtime_at_open = st.st_mtime_ns
                self.tab.file_size_at_open = st.st_size
                self.tab.last_file_inode = st.st_ino
            except OSError:
                pass
        self.tab.set_status(self.tab.fmt("st_done", total=idx.line_count, size=fmt_size(idx.size)))
        try:
            self.tab.load_window(at_line=0)
        except OSError:
            pass
        self.tab.refresh_bookmarks_tree()
        self.tab.refresh_edits_tree()
        # Zaktualizuj tytuł zakładki — przywróć właściwy tytuł
        if file_path:
            self.tab.title_changed.emit(self.tab.assigned_title or os.path.basename(file_path))
        # Zaktualizuj mini-mapę — natychmiast (dla małych plików) + debounced (dla dużych)
        self.tab.update_minimap()
        self.tab.minimap_update_timer.start()

        if self.tab.pending_reload_filter:
            self.tab.pending_reload_filter = False
            self.tab.cmd_apply_filter()

        self.tab.file_loaded.emit(True)

    def start_reindex(self, saved_line: int) -> None:
        """Rozpoczyna ponowne indeksowanie pliku i po zakończeniu ustawia widok na saved_line."""
        file_path = self.tab.file_path
        if not file_path:
            return

        self.tab.set_status(self.tab.t("st_opening"))
        self.tab.reindex_saved_line = saved_line

        thread = self.tab.indexer_thread
        if thread is not None:
            try:
                if thread.isRunning():
                    worker = self.tab.indexer_worker
                    if worker is not None and hasattr(worker, "cancel"):
                        worker.cancel()
                    thread.quit()
                    thread.wait(1000)
            except (RuntimeError, AttributeError):
                pass
        self.tab.indexer_thread = None

        indexer_thread = QThread()
        indexer_worker = IndexerWorker(file_path, self.tab.encoding, self.tab.index_interval_bytes)
        self.tab.indexer_thread = indexer_thread
        self.tab.indexer_worker = indexer_worker

        indexer_worker.moveToThread(indexer_thread)
        indexer_thread.started.connect(indexer_worker.run)
        self.tab.register_thread_worker(indexer_thread, indexer_worker)
        # QueuedConnection + metoda-slot (nie lambda) — cross-thread safe
        indexer_worker.progress.connect(self._on_index_progress, Qt.ConnectionType.QueuedConnection)
        indexer_worker.finished.connect(self._on_reindex_finished, Qt.ConnectionType.QueuedConnection)
        indexer_worker.error.connect(self._on_index_error, Qt.ConnectionType.QueuedConnection)
        indexer_worker.finished.connect(indexer_thread.quit, Qt.ConnectionType.QueuedConnection)
        indexer_worker.error.connect(indexer_thread.quit, Qt.ConnectionType.QueuedConnection)
        indexer_worker.finished.connect(indexer_worker.deleteLater, Qt.ConnectionType.QueuedConnection)
        indexer_worker.error.connect(indexer_worker.deleteLater, Qt.ConnectionType.QueuedConnection)
        indexer_thread.finished.connect(indexer_thread.deleteLater, Qt.ConnectionType.QueuedConnection)
        indexer_thread.start()

    _start_reindex = start_reindex

    @Slot(object)
    def _on_reindex_finished(self, idx: LineIndexer) -> None:
        """Slot dla sygnału finished z reindex workera — przekazuje do
        _on_reindex_after_save z zapamiętanym saved_line."""
        saved_line = self.tab.reindex_saved_line
        self._on_reindex_after_save(idx, saved_line)

    @Slot(object, int)
    def _on_reindex_after_save(self, idx: LineIndexer, saved_line: int) -> None:
        if self.tab.indexer is not None:
            try:
                self.tab.indexer.close()
            except (OSError, RuntimeError):
                pass
        self.tab.indexer = idx
        self.tab.last_file_size = idx.size
        file_path = self.tab.file_path
        if file_path:
            try:
                st = os.stat(file_path)
                self.tab.file_mtime_at_open = st.st_mtime_ns
                self.tab.file_size_at_open = st.st_size
                self.tab.last_file_inode = st.st_ino
            except OSError:
                pass
        self.tab.set_status(self.tab.fmt("st_done", total=idx.line_count, size=fmt_size(idx.size)))
        try:
            self.tab.load_window(at_line=saved_line)
        except OSError:
            # Ignorujemy potencjalne usunięcie pliku z dysku pod maską w trakcie lub tuż po reindeksie.
            pass

    def cancel_follow_if_active(self) -> None:
        """Wyłącza tryb śledzenia (follow) przy ręcznych przesunięciach użytkownika."""
        if self.tab.follow_active:
            self.cmd_toggle_follow()

    _cancel_follow_if_active = cancel_follow_if_active

    def cmd_refresh(self) -> None:
        """Sprawdza plik pod kątem aktualizacji jednorazowo."""
        if not self.tab.file_path:
            return
        self.tab.set_status(self.tab.t("st_refreshing"))
        self._check_for_updates_once()

    def cmd_reload(self) -> None:
        """Przeładowuje bieżący plik od nowa zachowując stan zakładek i filtrów."""
        file_path = self.tab.file_path
        if not file_path:
            return
        is_current = self.tab.main_window.tabs.currentWidget() == self.tab
        entry_text = self.tab.main_window.filter_entry.text().strip() if is_current else ""
        has_filter = (
            self.tab.filter_active
            or bool(getattr(self.tab, "filter_pattern", ""))
            or bool(getattr(self.tab, "tb_filter_text", ""))
            or bool(entry_text)
        )
        self.tab.pending_reload_filter = has_filter
        self.open_file(file_path, self.tab.assigned_title, preserve_state=True)

    def _check_for_updates_once(self) -> None:
        file_path = self.tab.file_path
        indexer = self.tab.indexer
        if not file_path or not indexer:
            return
        try:
            current_stat = os.stat(file_path)
        except OSError:
            return
        current_size = current_stat.st_size
        current_inode = current_stat.st_ino
        mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(current_stat.st_mtime))
        ctime_str = time.strftime("%H:%M:%S")

        if current_inode != self.tab.last_file_inode and self.tab.last_file_inode != 0:
            self._start_follow_reindex(current_size, current_inode)
            return

        if current_size > self.tab.last_file_size:
            try:
                new_lines = indexer.update_from(current_size)
                self.tab.last_file_size = current_size
                if new_lines > 0:
                    self._on_follow_new_lines(new_lines, mtime_str, ctime_str)
                else:
                    self.tab.set_status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))
            except (OSError, RuntimeError, ValueError):
                pass
        elif current_size < self.tab.last_file_size:
            self._start_follow_reindex(current_size, current_inode)
        else:
            self.tab.set_status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))

    def cmd_toggle_follow(self) -> None:
        """Przełącza tryb ciągłego śledzenia dopisywania nowych linii do pliku (Tail/Follow)."""
        indexer = self.tab.indexer
        file_path = self.tab.file_path
        if not indexer or not file_path:
            return
        self.tab.follow_active = not self.tab.follow_active
        follow_action = self.tab.main_window.follow_action
        if follow_action is not None:
            follow_action.setChecked(self.tab.follow_active)
        if self.tab.follow_active:
            self.tab.last_file_size = indexer.size
            try:
                self.tab.last_file_inode = os.stat(file_path).st_ino
            except OSError:
                self.tab.last_file_inode = 0

            if indexer.line_count > 0:
                last_start = max(0, indexer.line_count - self.tab.window_size_lines)
                self.tab.load_window(at_line=last_start)
                self.tab.text.verticalScrollBar().setValue(self.tab.text.verticalScrollBar().maximum())

            self._follow_poll()
        else:
            self.tab.refresh_status()

    def _follow_poll(self) -> None:
        file_path = self.tab.file_path
        indexer = self.tab.indexer
        if not self.tab.follow_active or not file_path or not indexer:
            return
        if self.tab.follow_reindexing:
            QTimer.singleShot(200, self._follow_poll)
            return
        try:
            current_stat = os.stat(file_path)
        except OSError:
            QTimer.singleShot(200, self._follow_poll)
            return
        current_size = current_stat.st_size
        current_inode = current_stat.st_ino

        if current_inode != self.tab.last_file_inode and self.tab.last_file_inode != 0:
            self.tab.follow_reindexing = True
            self._start_follow_reindex(current_size, current_inode)
            QTimer.singleShot(200, self._follow_poll)
            return

        mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(current_stat.st_mtime))
        ctime_str = time.strftime("%H:%M:%S")

        if current_size > self.tab.last_file_size:
            next_poll = 200
            try:
                new_lines = indexer.update_from(current_size)
                self.tab.last_file_size = current_size
                if new_lines > 0:
                    self._on_follow_new_lines(new_lines, mtime_str, ctime_str)
            except (OSError, RuntimeError, ValueError):
                pass
        elif current_size < self.tab.last_file_size:
            next_poll = 200
            self.tab.follow_reindexing = True
            self._start_follow_reindex(current_size, current_inode)
        else:
            next_poll = 1000
            self.tab.set_status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))
        QTimer.singleShot(next_poll, self._follow_poll)

    def _start_follow_reindex(self, current_size: int, current_inode: int) -> None:
        file_path = self.tab.file_path
        if not file_path:
            return

        self._stop_background_threads()
        self.tab.follow_reindex_size = current_size
        self.tab.follow_reindex_inode = current_inode

        indexer_thread = QThread()
        indexer_worker = IndexerWorker(file_path, self.tab.encoding, self.tab.index_interval_bytes)
        self.tab.indexer_thread = indexer_thread
        self.tab.indexer_worker = indexer_worker

        indexer_worker.moveToThread(indexer_thread)
        indexer_thread.started.connect(indexer_worker.run)
        self.tab.register_thread_worker(indexer_thread, indexer_worker)
        # QueuedConnection + metoda-slot (nie lambda) — cross-thread safe
        indexer_worker.finished.connect(self._on_follow_reindex_slot, Qt.ConnectionType.QueuedConnection)
        indexer_worker.error.connect(self._on_follow_reindex_failed, Qt.ConnectionType.QueuedConnection)
        indexer_worker.finished.connect(indexer_thread.quit, Qt.ConnectionType.QueuedConnection)
        indexer_worker.error.connect(indexer_thread.quit, Qt.ConnectionType.QueuedConnection)
        indexer_worker.finished.connect(self._on_follow_reindex_clear_flag, Qt.ConnectionType.QueuedConnection)
        indexer_worker.error.connect(self._on_follow_reindex_clear_flag, Qt.ConnectionType.QueuedConnection)
        indexer_worker.finished.connect(indexer_worker.deleteLater, Qt.ConnectionType.QueuedConnection)
        indexer_worker.error.connect(indexer_worker.deleteLater, Qt.ConnectionType.QueuedConnection)
        indexer_thread.finished.connect(indexer_thread.deleteLater, Qt.ConnectionType.QueuedConnection)
        indexer_thread.start()

    @Slot(object)
    def _on_follow_reindex_slot(self, idx: LineIndexer) -> None:
        """Slot pośredniczący — odbiera idx z workera i woła _on_follow_reindex
        z zapamiętanymi parametrami."""
        size = self.tab.follow_reindex_size
        inode = self.tab.follow_reindex_inode
        self._on_follow_reindex(idx, size, inode)

    @Slot()
    def _on_follow_reindex_clear_flag(self) -> None:
        """Czyści flagę follow_reindexing po zakończeniu reindex."""
        self.tab.follow_reindexing = False

    def _on_follow_new_lines(self, new_line_count: int = 0, mtime_str: str = "", ctime_str: str = "") -> None:
        indexer = self.tab.indexer
        if not indexer or indexer.line_count == 0:
            return

        pending = self.tab.inc_pending_lines + new_line_count
        self.tab.inc_pending_lines = pending

        if pending > 0 or not self.tab.line_map:
            if self.tab.filter_active and self.tab.filter_results is not None:
                is_running = False
                thread = self.tab.inc_filter_thread
                if thread is not None:
                    try:
                        is_running = thread.isRunning()
                    except (RuntimeError, AttributeError):
                        is_running = False

                if is_running:
                    return
                self.tab.inc_pending_lines = 0
                self._start_incremental_filter(pending, mtime_str, ctime_str)
                return

            self.tab.inc_pending_lines = 0
            self._apply_follow_new_lines(mtime_str, ctime_str)
        else:
            self.tab.set_status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))

    def _start_incremental_filter(self, new_line_count: int, mtime_str: str, ctime_str: str) -> None:
        from log_viewer.workers import IncrementalFilterWorker

        indexer = self.tab.indexer
        if not indexer:
            return
        new_total_lines = indexer.line_count
        start_line = max(0, new_total_lines - new_line_count)

        self.tab.inc_new_total_lines = new_total_lines
        self.tab.inc_mtime_str = mtime_str
        self.tab.inc_ctime_str = ctime_str

        # Pobieraj stan filtra przypisany wyłącznie do tej zakładki
        pattern = getattr(self.tab, "filter_pattern", "")
        use_regex = getattr(self.tab, "filter_use_regex", False)
        case_sens = getattr(self.tab, "filter_case_sensitive", False)
        negate = getattr(self.tab, "filter_negate", False)

        inc_filter_thread = QThread()
        inc_filter_worker = IncrementalFilterWorker(
            indexer,
            start_line,
            new_total_lines,
            pattern,
            use_regex,
            case_sens,
            negate,
            self.tab.encoding,
        )
        self.tab.inc_filter_thread = inc_filter_thread
        self.tab.inc_filter_worker = inc_filter_worker

        inc_filter_worker.moveToThread(inc_filter_thread)
        inc_filter_thread.started.connect(inc_filter_worker.run)

        inc_filter_worker.finished.connect(self._on_inc_finished_slot, Qt.ConnectionType.QueuedConnection)
        inc_filter_worker.finished.connect(inc_filter_thread.quit, Qt.ConnectionType.QueuedConnection)
        inc_filter_worker.finished.connect(inc_filter_worker.deleteLater, Qt.ConnectionType.QueuedConnection)
        inc_filter_thread.finished.connect(inc_filter_thread.deleteLater, Qt.ConnectionType.QueuedConnection)

        self.tab.register_thread_worker(inc_filter_thread, inc_filter_worker)
        inc_filter_thread.start()

    @Slot(object)
    def _on_inc_finished_slot(self, results_data: object) -> None:
        new_total_lines = self.tab.inc_new_total_lines
        mtime_str = self.tab.inc_mtime_str
        ctime_str = self.tab.inc_ctime_str
        if new_total_lines > 0:
            filter_results = self.tab.filter_results
            filter_all_lines = self.tab.filter_all_lines
            old_total = filter_results.size if isinstance(filter_results, Bitset) else 0

            if not isinstance(filter_results, Bitset):
                filter_results = Bitset(new_total_lines)
                self.tab.filter_results = filter_results
            if not isinstance(filter_all_lines, Bitset):
                filter_all_lines = Bitset(new_total_lines)
                self.tab.filter_all_lines = filter_all_lines

            filter_results.resize(new_total_lines)

            has_new_hits = False
            inc_res_words = None
            if isinstance(results_data, (tuple, list)) and len(results_data) > 1 and results_data[1] is not None:
                inc_res_words = results_data[1]
                if inc_res_words and any(inc_res_words):
                    filter_results.or_words(inc_res_words)
                    has_new_hits = True

            context_after = self.tab.filter_context_after
            has_new_filtered_lines = False

            if context_after > 0:
                old_all_len = len(filter_all_lines)
                filter_results.expand_context_incremental(filter_all_lines, old_total, context_after)
                if len(filter_all_lines) > old_all_len or has_new_hits:
                    has_new_filtered_lines = True
            else:
                filter_all_lines.resize(new_total_lines)
                if has_new_hits and inc_res_words is not None:
                    filter_all_lines.or_words(inc_res_words)
                    has_new_filtered_lines = True

            self._apply_follow_new_lines(mtime_str, ctime_str, force_reload=has_new_filtered_lines)

            # Jeśli w międzyczasie przybyły kolejne linie, uruchom kolejny worker dla oczekujących linii
            if self.tab.inc_pending_lines > 0 and self.tab.follow_active:
                pending = self.tab.inc_pending_lines
                self.tab.inc_pending_lines = 0
                self._start_incremental_filter(pending, mtime_str, ctime_str)

    def _apply_follow_new_lines(self, mtime_str: str, ctime_str: str, force_reload: bool = False) -> None:
        # Sprawdź czy ta zakładka jest aktywna w UI (dla kart w tle odraczamy ciężkie renderowanie)
        is_active = self.tab.main_window.tabs.currentWidget() == self.tab
        if not is_active:
            if force_reload:
                self.tab.needs_follow_refresh = True
            return

        if self.tab.filter_active and self.tab.filter_results is not None:
            filter_all = self.tab.filter_all_lines
            total_lines = len(filter_all) if filter_all is not None else 0
            last_start = max(0, total_lines - self.tab.window_size_lines)
        else:
            indexer = self.tab.indexer
            line_count = indexer.line_count if indexer else 0
            last_start = max(0, line_count - self.tab.window_size_lines)
            force_reload = True

        self.tab.ignore_scroll_events = True
        try:
            self.tab.load_window(at_line=last_start, force_reload=force_reload)
        finally:
            self.tab.ignore_scroll_events = False

        def _scroll_down() -> None:
            if not self.tab.ignore_scroll_events:
                self.tab.ignore_scroll_events = True
                self.tab.text.verticalScrollBar().setValue(self.tab.text.verticalScrollBar().maximum())
                self.tab.ignore_scroll_events = False

        QTimer.singleShot(0, _scroll_down)

        if self.tab.filter_active and self.tab.filter_results is not None:
            indexer = self.tab.indexer
            total_lines = indexer.line_count if indexer else 0
            status_filter = self.tab.fmt("st_filtered", hits=len(self.tab.filter_results), total=total_lines)
            status_follow = self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str)
            self.tab.set_status(f"{status_filter} | {status_follow}")
        else:
            self.tab.set_status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))

    @Slot(object, int, int)
    def _on_follow_reindex(self, idx: LineIndexer, new_size: int, new_inode: int) -> None:
        if self.tab.indexer is not None:
            try:
                self.tab.indexer.close()
            except (OSError, RuntimeError):
                pass
        self.tab.indexer = idx
        self.tab.last_file_size = new_size
        if new_inode != 0:
            self.tab.last_file_inode = new_inode
        last_start = max(0, idx.line_count - self.tab.window_size_lines)
        self.tab.load_window(at_line=last_start)
        self.tab.text.verticalScrollBar().setValue(self.tab.text.verticalScrollBar().maximum())
        file_path = self.tab.file_path
        try:
            mtime = os.stat(file_path).st_mtime if file_path else 0
            mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime))
        except OSError:
            mtime_str = "?"
        ctime_str = time.strftime("%H:%M:%S")
        self.tab.set_status(self.tab.t("st_following").format(mtime=mtime_str, ctime=ctime_str))

    @Slot(str)
    def _on_follow_reindex_failed(self, _err: str) -> None:
        self.tab.set_status(self.tab.t("st_follow_reindex_failed"))

    # Publiczne aliasy metod kontrolera
    on_index_progress = _on_index_progress
    cancel_indexing = _cancel_indexing
    close_index_progress = _close_index_progress
    on_index_error = _on_index_error
    on_index_done = _on_index_done
    on_reindex_finished = _on_reindex_finished
    on_reindex_after_save = _on_reindex_after_save
    follow_poll = _follow_poll
    start_follow_reindex = _start_follow_reindex
    on_follow_reindex_slot = _on_follow_reindex_slot
    on_follow_reindex_clear_flag = _on_follow_reindex_clear_flag
    on_follow_new_lines = _on_follow_new_lines
    apply_follow_new_lines = _apply_follow_new_lines
    on_follow_reindex = _on_follow_reindex
    on_follow_reindex_failed = _on_follow_reindex_failed
