from PySide6.QtCore import QObject, Qt, Slot, QThread, QTimer
from PySide6.QtWidgets import QMessageBox
import re
from typing import Optional, List, Tuple
from log_reader.filter_engine import FilterEngine
from log_reader.workers import FilterWorker

class SearchController(QObject):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab

    def cmd_find_dialog(self) -> None:
        self.tab._main.search_entry.setFocus()
        self.tab._main.search_entry.selectAll()

    def _compile_search(self) -> Optional[str]:
        pattern = self.tab._main.search_entry.text().strip()
        if not pattern:
            return None
        use_regex = self.tab._main.search_regex_cb.isChecked()
        case = self.tab._main.search_case_cb.isChecked()
        negate = self.tab._main.search_negate_cb.isChecked()
        if use_regex:
            try:
                flags = 0 if case else re.IGNORECASE
                self.tab._search_compiled = re.compile(pattern, flags)
            except re.error as e:
                QMessageBox.critical(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_filter_error").format(e=e))
                return None
        else:
            self.tab._search_compiled = None
        self.tab.search_pattern = pattern
        self.tab._search_case = case
        self.tab._search_negate = negate
        return pattern

    def _search_pattern_changed(self) -> bool:
        pattern = self.tab._main.search_entry.text().strip()
        use_regex = self.tab._main.search_regex_cb.isChecked()
        case = self.tab._main.search_case_cb.isChecked()
        negate = self.tab._main.search_negate_cb.isChecked()
        if pattern != self.tab.search_pattern:
            return True
        if use_regex != self.tab._last_search_regex:
            return True
        if case != self.tab._last_search_case:
            return True
        if negate != self.tab._last_search_negate:
            return True
        return False

    def _start_background_search(self) -> None:
        if not self.tab.indexer or not self.tab.file_path:
            return
        pattern = self.tab._compile_search()
        if pattern is None:
            return
        self.tab.search_pattern = pattern
        self.tab._last_search_regex = self.tab._main.search_regex_cb.isChecked()
        self.tab._last_search_case = self.tab._main.search_case_cb.isChecked()
        self.tab._last_search_negate = self.tab._main.search_negate_cb.isChecked()

        # Anuluj poprzednie wyszukiwanie
        if self.tab._search_engine and self.tab._search_engine.is_running():
            self.tab._search_engine.cancel()
        # Sprawdź czy stary thread żyje — deleteLater może go już zwolnić
        if self.tab._search_thread is not None:
            try:
                if self.tab._search_thread.isRunning():
                    self.tab._search_thread.quit()
                    self.tab._search_thread.wait(2000)
            except RuntimeError:
                pass
        self.tab._search_thread = None
        self.tab._search_worker = None

        if self.tab._search_engine is None or self.tab._search_engine.path != self.tab.file_path:
            self.tab._search_engine = FilterEngine(self.tab.file_path, self.tab.indexer)

        self.tab._search_results = []
        self.tab._search_results_all = []
        self.tab._search_result_index = -1
        if self.tab._search_model:
            self.tab._search_model.clear()
        self.tab._search_results_label.setText(self.tab.t("lbl_search_results_searching"))
        self.tab._status(self.tab._fmt("st_filtering", pct=0.0, hits=0))

        self.tab._search_thread = QThread()
        self.tab._search_worker = FilterWorker(
            self.tab._search_engine, pattern,
            self.tab._last_search_regex, self.tab._last_search_case, self.tab._last_search_negate,
            context_after=0
        )
        self.tab._search_worker.moveToThread(self.tab._search_thread)
        self.tab._search_thread.started.connect(self.tab._search_worker.run)
        self.tab._register_thread_worker(self.tab._search_thread, self.tab._search_worker)
        self.tab._search_worker.progress.connect(self.tab._on_search_progress, Qt.QueuedConnection)
        self.tab._search_worker.finished.connect(self.tab._on_search_finished, Qt.QueuedConnection)
        self.tab._search_worker.finished.connect(self.tab._search_thread.quit, Qt.QueuedConnection)
        self.tab._search_worker.finished.connect(self.tab._search_worker.deleteLater, Qt.QueuedConnection)
        self.tab._search_thread.finished.connect(self.tab._search_thread.deleteLater, Qt.QueuedConnection)
        self.tab._search_thread.start()

    @Slot(float, int, str)
    def _on_search_progress(self, pct: float, hits: int, state: str) -> None:
        if state == "context":
            self.tab._status(self.tab.t("st_context_building"))
            return
        self.tab._status(self.tab._fmt("st_filtering", pct=f"{pct:.1f}", hits=hits))
        self.tab._search_results_label.setText(
            f"{self.tab.t('lbl_search_results_searching')} ({hits})"
        )

    @Slot(object, object, object, object, object, object)
    def _on_search_finished(self, results, context_lines, filter_all_lines, hit_text_map, hit_lines_set, error) -> None:
        if error:
            if error != "cancelled":
                try:
                    self.tab._search_results_label.setText(self.tab.t("lbl_search_results_empty"))
                except RuntimeError:
                    pass
            return

        # Zapisujemy tylko listę numerów linii, teksty będą ładowane lazy (SearchResultsModel)
        self.tab._search_results_all = results
        total_hits = len(self.tab._search_results_all)

        self.tab._search_results = self.tab._search_results_all

        if self.tab._search_model:
            # Używamy tylko numerów linii, model sam pobierze tekst za pomocą indexera
            self.tab._search_model.set_results(self.tab._search_results, indexer=self.tab.indexer)

        self.tab._status(self.tab.t("st_search_done").format(n=total_hits))

        if total_hits == 0:
            self.tab._search_results_label.setText(self.tab.t("lbl_search_results_empty"))
            return

        # Skocz do pierwszego wyniku — odroczone przez QTimer.singleShot
        self.tab._search_result_index = 0
        QTimer.singleShot(0, lambda: self.tab._navigate_to_search_result(0))

        self.tab._update_search_results_label()

    def _update_search_results_label(self) -> None:
        total = len(self.tab._search_results_all)
        if total == 0:
            self.tab._search_results_label.setText(self.tab.t("lbl_search_results_empty"))
        else:
            current = self.tab._search_result_index + 1
            self.tab._search_results_label.setText(
                self.tab.t("lbl_search_results_count").format(n=total, current=current, total=total)
            )

    def cmd_find_next(self) -> None:
        if not self.tab.indexer:
            return
        if self.tab._search_pattern_changed() or not self.tab._search_results_all:
            self.tab._start_background_search()
            return
        if self.tab._search_result_index < len(self.tab._search_results_all) - 1:
            self.tab._navigate_to_search_result(self.tab._search_result_index + 1)
        else:
            self.tab._navigate_to_search_result(0)

    def cmd_find_prev(self) -> None:
        if not self.tab.indexer:
            return
        if self.tab._search_pattern_changed() or not self.tab._search_results_all:
            self.tab._start_background_search()
            return
        if self.tab._search_result_index > 0:
            self.tab._navigate_to_search_result(self.tab._search_result_index - 1)
        else:
            self.tab._navigate_to_search_result(len(self.tab._search_results_all) - 1)

    def cmd_clear_search(self) -> None:
        if self.tab._search_engine and self.tab._search_engine.is_running():
            self.tab._search_engine.cancel()
        if self.tab._search_thread is not None:
            try:
                if self.tab._search_thread.isRunning():
                    self.tab._search_thread.quit()
                    self.tab._search_thread.wait(2000)
            except RuntimeError:
                pass
        self.tab._search_thread = None
        self.tab._search_worker = None

        self.tab.search_pattern = ""
        self.tab._search_results = []
        self.tab._search_results_all = []
        self.tab._search_result_index = -1
        self.tab._search_extra_sel = None

        if self.tab._search_model:
            self.tab._search_model.clear()

        self.tab._search_results_label.setText(self.tab.t("lbl_search_results_empty"))
        self.tab._main.search_entry.clear()

        self.tab._update_current_line_highlight()
        self.tab._refresh_status()