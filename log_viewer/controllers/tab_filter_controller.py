from __future__ import annotations

import array
import os
import re
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Slot
from PySide6.QtWidgets import QMessageBox

from log_viewer.bitset import Bitset
from log_viewer.filter_engine import FilterEngine
from log_viewer.workers import FilterWorker


class FilterController(QObject):
    def __init__(self, tab: Any) -> None:
        super().__init__(tab)
        self.tab = tab

    def cmd_filter_dialog(self) -> None:
        self.tab.main_window.filter_entry.setFocus()
        self.tab.main_window.filter_entry.selectAll()

    def cmd_apply_filter(self) -> None:
        if not self.tab.indexer:
            QMessageBox.information(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_no_file"))
            return
        pattern = self.tab.main_window.filter_entry.text().strip()
        if not pattern:
            self.tab.cmd_clear_filter()
            return
        use_regex = self.tab.main_window.filter_regex_cb.isChecked()
        case = self.tab.main_window.filter_case_cb.isChecked()
        negate = self.tab.main_window.filter_negate_cb.isChecked()
        # Ile linii kontekstu po każdym trafieniu (0 = bez kontekstu).
        # Przydatne dla stack trace PHP/Python — pokazuje błąd + N linii poniżej.
        self.tab.filter_context_after = 0
        if hasattr(self.tab.main_window, "filter_context_spin"):
            self.tab.filter_context_after = int(self.tab.main_window.filter_context_spin.value())

        self.tab.filter_pattern = pattern
        self.tab.filter_use_regex = use_regex
        self.tab.filter_case_sensitive = case
        self.tab.filter_negate = negate
        self.tab.tb_filter_text = pattern
        self.tab.tb_filter_regex = use_regex
        self.tab.tb_filter_case = case
        self.tab.tb_filter_negate = negate
        self.tab.tb_filter_context = self.tab.filter_context_after

        if use_regex:
            try:
                flags = re.MULTILINE if case else (re.IGNORECASE | re.MULTILINE)
                re.compile(pattern, flags)
            except re.error as e:
                QMessageBox.critical(
                    self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_filter_error").format(e=e)
                )
                return

        if self.tab.filter_engine and self.tab.filter_engine.is_running():
            self.tab.filter_engine.cancel()

        if self.tab.filter_thread is not None:
            try:
                if self.tab.filter_thread.isRunning():
                    self.tab.filter_thread.quit()
                    self.tab.filter_thread.wait(1500)
            except RuntimeError:
                pass
            self.tab.filter_thread = None

        file_path = self.tab.file_path
        indexer = self.tab.indexer
        if not file_path or not indexer:
            QMessageBox.information(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_no_file"))
            return

        try:
            current_size = os.stat(file_path).st_size
            if current_size > indexer.size:
                indexer.update_from(current_size)
                self.tab.last_file_size = current_size
        except OSError:
            pass

        self.tab.filter_engine = FilterEngine(file_path, indexer)
        self.tab.filter_active = True
        self.tab.filter_results = array.array("Q")

        self.tab.filter_thread = QThread()
        self.tab.filter_worker = FilterWorker(
            self.tab.filter_engine, pattern, use_regex, case, negate, context_after=self.tab.filter_context_after
        )
        self.tab.filter_worker.moveToThread(self.tab.filter_thread)
        self.tab.filter_thread.started.connect(self.tab.filter_worker.run)
        self.tab.register_thread_worker(self.tab.filter_thread, self.tab.filter_worker)
        self.tab.filter_worker.progress.connect(self._on_filter_progress, Qt.ConnectionType.QueuedConnection)
        self.tab.filter_worker.finished.connect(self._on_filter_done, Qt.ConnectionType.QueuedConnection)
        self.tab.filter_worker.finished.connect(self.tab.filter_thread.quit, Qt.ConnectionType.QueuedConnection)
        self.tab.filter_worker.finished.connect(self.tab.filter_worker.deleteLater, Qt.ConnectionType.QueuedConnection)
        self.tab.filter_thread.finished.connect(self.tab.filter_thread.deleteLater, Qt.ConnectionType.QueuedConnection)
        self.tab.filter_thread.start()
        self.tab.set_status(self.tab.fmt("st_filtering", pct=0.0, hits=0))

    @Slot(float, int, str, object)
    def _on_filter_progress(self, pct: float, hits: int, state: str, _partial_results: object = None) -> None:
        if state == "context":
            self.tab.set_status(self.tab.t("st_context_building"))
            return
        self.tab.set_status(self.tab.fmt("st_filtering", pct=f"{pct:.1f}", hits=hits))

    @Slot(object, object, object, object, object, object)
    def _on_filter_done(
        self,
        results_data: Any,
        context_lines: Any,
        filter_all_data: Any,
        hit_text_map: Any,
        hit_lines_set: Any,
        error: str | None,
    ) -> None:
        if error:
            if error != "cancelled":
                try:
                    QMessageBox.critical(
                        self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_filter_error").format(e=error)
                    )
                    self.tab.filter_active = False
                    self.tab.refresh_status()
                    self.tab.update_position_slider()
                except RuntimeError:
                    pass
            return
        if not results_data:
            QMessageBox.information(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_no_matches"))
            self.tab.filter_active = False
            self.tab.refresh_status()
            self.tab.update_position_slider()
            return

        results = Bitset.from_raw(results_data[0], results_data[1], results_data[2])
        filter_all_lines = Bitset.from_raw(filter_all_data[0], filter_all_data[1], filter_all_data[2])

        self.tab.filter_results = results
        self.tab.filter_all_lines = filter_all_lines
        self.tab.filter_context_lines = context_lines
        self.tab.filter_hit_text_map = hit_text_map
        self.tab.filter_hit_lines = hit_lines_set

        self.tab.ignore_scroll_events = True
        try:
            self.tab.load_window(at_line=0, force_reload=True)
            self.tab.text.verticalScrollBar().setValue(0)
        finally:
            self.tab.ignore_scroll_events = False

        self.tab.set_status(self.tab.fmt("st_filtered", hits=len(results), total=self.tab.indexer.line_count))

    def _update_filter_cache(self) -> None:
        if not self.tab.filter_active or not self.tab.filter_results:
            if self.tab.filter_hit_text_map is not None:
                self.tab.filter_hit_text_map.clear()
            else:
                self.tab.filter_hit_text_map = {}
            if self.tab.filter_hit_lines is not None:
                self.tab.filter_hit_lines.clear()
            else:
                self.tab.filter_hit_lines = set()
            self.tab.filter_all_lines = array.array("Q")

    def cmd_clear_filter(self, silent: bool = False) -> None:
        was_active = self.tab.filter_active
        if self.tab.filter_engine and self.tab.filter_engine.is_running():
            self.tab.filter_engine.cancel()
        self.tab.filter_active = False
        self.tab.filter_results = []
        self.tab.filter_context_lines = set()
        if self.tab.filter_hit_text_map is not None:
            self.tab.filter_hit_text_map.clear()
        else:
            self.tab.filter_hit_text_map = {}
        if self.tab.filter_hit_lines is not None:
            self.tab.filter_hit_lines.clear()
        else:
            self.tab.filter_hit_lines = set()
        self.tab.filter_all_lines = array.array("Q")
        self.tab.filter_context_after = 0
        self.tab.filter_pattern = ""
        self.tab.filter_use_regex = False
        self.tab.filter_case_sensitive = False
        self.tab.filter_negate = False
        self.tab.tb_filter_text = ""
        self.tab.tb_filter_regex = False
        self.tab.tb_filter_case = False
        self.tab.tb_filter_negate = False
        self.tab.tb_filter_context = 0
        if not silent and self.tab.main_window.tabs.currentWidget() == self.tab:
            self.tab.main_window.filter_entry.clear()
        if was_active and self.tab.indexer:
            self.tab.load_window(at_line=0, force_reload=True)
        else:
            self.tab.update_position_slider()
        self.tab.refresh_status()

    # Publiczne aliasy metod kontrolera
    on_filter_progress = _on_filter_progress
    on_filter_done = _on_filter_done
    update_filter_cache = _update_filter_cache
