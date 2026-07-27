from PySide6.QtCore import QObject, Qt, Slot, QThread
from PySide6.QtWidgets import QMessageBox
import array
import re
from typing import Optional, List, Tuple
from log_reader.filter_engine import FilterEngine
from log_reader.workers import FilterWorker

class FilterController(QObject):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab

    def cmd_filter_dialog(self) -> None:
        self.tab._main.filter_entry.setFocus()
        self.tab._main.filter_entry.selectAll()

    def cmd_apply_filter(self) -> None:
        if not self.tab.indexer:
            QMessageBox.information(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_no_file"))
            return
        pattern = self.tab._main.filter_entry.text().strip()
        if not pattern:
            self.tab.cmd_clear_filter()
            return
        use_regex = self.tab._main.filter_regex_cb.isChecked()
        case = self.tab._main.filter_case_cb.isChecked()
        negate = self.tab._main.filter_negate_cb.isChecked()
        # Ile linii kontekstu po każdym trafieniu (0 = bez kontekstu).
        # Przydatne dla stack trace PHP/Python — pokazuje błąd + N linii poniżej.
        self.tab._filter_context_after = 0
        if hasattr(self.tab._main, "filter_context_spin"):
            self.tab._filter_context_after = int(self.tab._main.filter_context_spin.value())

        if use_regex:
            try:
                flags = 0 if case else re.IGNORECASE
                re.compile(pattern, flags)
            except re.error as e:
                QMessageBox.critical(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_filter_error").format(e=e))
                return

        if self.tab.filter_engine and self.tab.filter_engine.is_running():
            self.tab.filter_engine.cancel()

        if self.tab.filter_engine is None or self.tab.filter_engine.path != self.tab.file_path:
            self.tab.filter_engine = FilterEngine(self.tab.file_path, self.tab.indexer)
        self.tab.filter_active = True
        import array
        self.tab.filter_results = array.array('Q')

        self.tab._filter_thread = QThread()
        self.tab._filter_worker = FilterWorker(self.tab.filter_engine, pattern, use_regex, case, negate, context_after=self.tab._filter_context_after)
        self.tab._filter_worker.moveToThread(self.tab._filter_thread)
        self.tab._filter_thread.started.connect(self.tab._filter_worker.run)
        self.tab._register_thread_worker(self.tab._filter_thread, self.tab._filter_worker)
        self.tab._filter_worker.progress.connect(self.tab._on_filter_progress, Qt.QueuedConnection)
        self.tab._filter_worker.finished.connect(self.tab._on_filter_done, Qt.QueuedConnection)
        self.tab._filter_worker.finished.connect(self.tab._filter_thread.quit, Qt.QueuedConnection)
        self.tab._filter_worker.finished.connect(self.tab._filter_worker.deleteLater, Qt.QueuedConnection)
        self.tab._filter_thread.finished.connect(self.tab._filter_thread.deleteLater, Qt.QueuedConnection)
        self.tab._filter_thread.start()
        self.tab._status(self.tab._fmt("st_filtering", pct=0.0, hits=0))

    @Slot(float, int, str)
    def _on_filter_progress(self, pct: float, hits: int, state: str) -> None:
        if state == "context":
            self.tab._status(self.tab.t("st_context_building"))
            return
        self.tab._status(self.tab._fmt("st_filtering", pct=f"{pct:.1f}", hits=hits))

    @Slot(object, object, object, object, object, object)
    def _on_filter_done(self, results, context_lines, filter_all_lines, hit_text_map, hit_lines_set, error) -> None:
        if error:
            if error != "cancelled":
                try:
                    QMessageBox.critical(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_filter_error").format(e=error))
                    self.tab.filter_active = False
                    self.tab._refresh_status()
                    self.tab._update_position_slider()
                except RuntimeError:
                    pass
            return
        if not results:
            QMessageBox.information(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_no_matches"))
            self.tab.filter_active = False
            self.tab._refresh_status()
            self.tab._update_position_slider()
            return

        self.tab.filter_results = results
        self.tab._filter_all_lines = filter_all_lines
        self.tab.filter_context_lines = context_lines
        self.tab._filter_hit_text_map = hit_text_map
        self.tab._filter_hit_lines = hit_lines_set

        self.tab._load_window(at_line=0)
        self.tab._status(self.tab._fmt("st_filtered", hits=len(results), total=self.tab.indexer.line_count))

    def _update_filter_cache(self) -> None:
        if not self.tab.filter_active or not self.tab.filter_results:
            self.tab._filter_hit_text_map.clear()
            self.tab._filter_hit_lines.clear()
            import array
            import array
        self.tab._filter_all_lines = array.array('Q')

    def cmd_clear_filter(self, silent: bool = False) -> None:
        was_active = self.tab.filter_active
        if self.tab.filter_engine and self.tab.filter_engine.is_running():
            self.tab.filter_engine.cancel()
        self.tab.filter_active = False
        self.tab.filter_results = []
        self.tab.filter_context_lines = set()
        self.tab._filter_hit_text_map.clear()
        self.tab._filter_hit_lines.clear()
        import array
        import array
        self.tab._filter_all_lines = array.array('Q')
        self.tab._filter_context_after = 0
        if not silent:
            self.tab._main.filter_entry.clear()
        if was_active and self.tab.indexer:
            self.tab._load_window(at_line=0)
        else:
            self.tab._update_position_slider()
        self.tab._refresh_status()