from __future__ import annotations

import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Slot
from PySide6.QtWidgets import QMessageBox

from log_viewer.bitset import Bitset
from log_viewer.filter_engine import FilterEngine
from log_viewer.workers import FilterWorker


class SearchController(QObject):
    def __init__(self, tab: Any) -> None:
        super().__init__(tab)
        self.tab = tab
        self._search_start_from_end = False
        self._last_search_model_update: float = 0.0

    def cmd_find_dialog(self) -> None:
        self.tab.main_window.search_entry.setFocus()
        self.tab.main_window.search_entry.selectAll()

    def _compile_search(self) -> str | None:
        pattern: str = self.tab.main_window.search_entry.text().strip()
        if not pattern:
            return None
        use_regex = self.tab.main_window.search_regex_cb.isChecked()
        case = self.tab.main_window.search_case_cb.isChecked()
        negate = self.tab.main_window.search_negate_cb.isChecked()
        search_in_filter = (
            getattr(self.tab.main_window, "search_in_filter_cb", None) is not None
            and self.tab.main_window.search_in_filter_cb.isChecked()
        )

        if use_regex:
            try:
                flags = re.MULTILINE if case else (re.IGNORECASE | re.MULTILINE)
                self.tab.search_compiled = re.compile(pattern, flags)
            except re.error as e:
                QMessageBox.critical(
                    self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_filter_error").format(e=e)
                )
                return None
        else:
            self.tab.search_compiled = None
        self.tab.search_pattern = pattern
        self.tab.last_search_case = case
        self.tab.last_search_negate = negate
        self.tab.last_search_in_filter = search_in_filter
        return pattern

    def _search_pattern_changed(self) -> bool:
        pattern = self.tab.main_window.search_entry.text().strip()
        use_regex = self.tab.main_window.search_regex_cb.isChecked()
        case = self.tab.main_window.search_case_cb.isChecked()
        negate = self.tab.main_window.search_negate_cb.isChecked()
        search_in_filter = (
            getattr(self.tab.main_window, "search_in_filter_cb", None) is not None
            and self.tab.main_window.search_in_filter_cb.isChecked()
        )

        return (
            pattern != self.tab.search_pattern
            or use_regex != self.tab.last_search_regex
            or case != self.tab.last_search_case
            or negate != self.tab.last_search_negate
            or search_in_filter != getattr(self.tab, "last_search_in_filter", False)
        )

    def _start_background_search(self, start_from_end: bool = False) -> None:
        self._search_start_from_end = start_from_end
        file_path = self.tab.file_path
        indexer = self.tab.indexer
        if not indexer or not file_path:
            return
        pattern = self._compile_search()
        if pattern is None:
            return

        try:
            current_size = Path(file_path).stat().st_size
            if current_size > indexer.size:
                indexer.update_from(current_size)
                self.tab.last_file_size = current_size
        except OSError:
            pass

        self.tab.search_pattern = pattern
        self.tab.last_search_regex = self.tab.main_window.search_regex_cb.isChecked()
        self.tab.last_search_case = self.tab.main_window.search_case_cb.isChecked()
        self.tab.last_search_negate = self.tab.main_window.search_negate_cb.isChecked()
        self.tab.last_search_in_filter = (
            getattr(self.tab.main_window, "search_in_filter_cb", None) is not None
            and self.tab.main_window.search_in_filter_cb.isChecked()
        )

        # Anuluj poprzednie wyszukiwanie
        if self.tab.search_engine and self.tab.search_engine.is_running():
            self.tab.search_engine.cancel()
        # Sprawdź czy stary thread żyje — deleteLater może go już zwolnić
        if self.tab.search_thread is not None:
            try:
                if self.tab.search_thread.isRunning():
                    self.tab.search_thread.quit()
                    self.tab.search_thread.wait(2000)
            except RuntimeError:
                pass
        self.tab.search_thread = None
        self.tab.search_worker = None

        self.tab.search_engine = FilterEngine(file_path, indexer)

        self.tab.search_results = []
        total = self.tab.indexer.line_count if self.tab.indexer else 0
        self.tab.search_results_all = Bitset(total)
        self.tab.search_result_index = -1
        if self.tab.search_model:
            self.tab.search_model.clear()
        self.tab.search_results_label.setText(self.tab.t("lbl_search_results_searching"))
        self.tab.set_status(self.tab.fmt("st_searching_progress", pct=0.0, hits=0))

        self.tab.search_thread = QThread()
        self.tab.search_worker = FilterWorker(
            self.tab.search_engine,
            pattern,
            self.tab.last_search_regex,
            self.tab.last_search_case,
            self.tab.last_search_negate,
            context_after=0,
            search_in_filter=self.tab.last_search_in_filter and self.tab.filter_active,
            filtered_lines=self.tab.filter_all_lines if self.tab.filter_active else None,
        )
        self.tab.search_worker.moveToThread(self.tab.search_thread)
        self.tab.search_thread.started.connect(self.tab.search_worker.run)
        self.tab.register_thread_worker(self.tab.search_thread, self.tab.search_worker)
        self.tab.search_worker.progress.connect(self._on_search_progress, Qt.ConnectionType.QueuedConnection)
        self.tab.search_worker.finished.connect(self._on_search_finished, Qt.ConnectionType.QueuedConnection)
        self.tab.search_worker.finished.connect(self.tab.search_thread.quit, Qt.ConnectionType.QueuedConnection)
        self.tab.search_worker.finished.connect(self.tab.search_worker.deleteLater, Qt.ConnectionType.QueuedConnection)
        self.tab.search_thread.finished.connect(self.tab.search_thread.deleteLater, Qt.ConnectionType.QueuedConnection)
        self.tab.search_thread.start()

    @Slot(float, int, str, object)
    def _on_search_progress(
        self,
        pct: float,
        hits: int,
        state: str,
        partial_results: tuple[int, Sequence[int]] | list[int] | Bitset | None = None,
    ) -> None:
        if state == "context":
            self.tab.set_status(self.tab.t("st_context_building"))
            return
        self.tab.set_status(self.tab.fmt("st_searching_progress", pct=f"{pct:.1f}", hits=hits))
        self.tab.search_results_label.setText(f"{self.tab.t('lbl_search_results_searching')} ({hits:,})")
        # Jeśli otrzymaliśmy częściowe wyniki z nowo ukończonego chunku,
        # dodajemy je na bieżąco do modelu listy (bez resetowania całości)
        has_results = (
            partial_results is not None and isinstance(partial_results, (Sequence, Bitset)) and len(partial_results) > 0
        )
        if has_results and self.tab.search_model:
            if not isinstance(getattr(self.tab, "search_results_all", None), Bitset):
                total = self.tab.indexer.line_count if self.tab.indexer else 0
                self.tab.search_results_all = Bitset(total)

            if isinstance(partial_results, tuple) and len(partial_results) == 2:
                base_word, words = partial_results
                self.tab.search_results_all.merge_chunk_words(base_word, words)
            elif isinstance(partial_results, Bitset):
                self.tab.search_results_all.or_words(partial_results.words)
            elif isinstance(partial_results, list):
                self.tab.search_results_all.update_indices(partial_results)

            now = time.time()
            # Throttle model updates to max 2 times per second to prevent GUI freeze on huge results
            if now - self._last_search_model_update > 0.5:
                self.tab.search_model.set_results(self.tab.search_results_all, indexer=self.tab.indexer)
                self._last_search_model_update = now

    @Slot(object, object, object, object, object, object)
    def _on_search_finished(
        self,
        results_data: Any,
        _context_lines: Any,
        filter_all_data: Any,
        _hit_text_map: Any,
        _hit_lines_set: Any,
        error: str | None,
    ) -> None:
        if error:
            if error != "cancelled":
                try:
                    self.tab.search_results_label.setText(self.tab.t("lbl_search_results_empty"))
                except RuntimeError:
                    pass
            return

        if results_data:
            _results = Bitset.from_raw(results_data[0], results_data[1], results_data[2])
            filter_all_lines = Bitset.from_raw(filter_all_data[0], filter_all_data[1], filter_all_data[2])
        else:
            filter_all_lines = Bitset(0)

        self.tab.search_results_all = filter_all_lines

        total_hits = len(self.tab.search_results_all)

        self.tab.search_results = self.tab.search_results_all

        if self.tab.search_model:
            # Używamy tylko numerów linii, model sam pobierze tekst za pomocą indexera
            self.tab.search_model.set_results(self.tab.search_results, indexer=self.tab.indexer)

        self.tab.set_status(self.tab.t("st_search_done").format(n=total_hits))

        if total_hits == 0:
            self.tab.search_results_label.setText(self.tab.t("lbl_search_results_empty"))
            return

        # Skocz do odpowiedniego wyniku — odroczone przez QTimer.singleShot
        if getattr(self, "_search_start_from_end", False):
            self.tab.search_result_index = total_hits - 1
            QTimer.singleShot(0, lambda: self.tab.navigate_to_search_result(total_hits - 1))
        else:
            self.tab.search_result_index = 0
            QTimer.singleShot(0, lambda: self.tab.navigate_to_search_result(0))

        self.tab.update_search_results_label()

    def update_search_results_label(self) -> None:
        self._update_search_results_label()

    def _update_search_results_label(self) -> None:
        total = len(self.tab.search_results_all)
        if total == 0:
            self.tab.search_results_label.setText(self.tab.t("lbl_search_results_empty"))
        else:
            current = self.tab.search_result_index + 1
            self.tab.search_results_label.setText(
                self.tab.t("lbl_search_results_count").format(n=total, current=current, total=total)
            )

    def cmd_find_next(self) -> None:
        if not self.tab.indexer:
            return
        if self._search_pattern_changed() or not self.tab.search_results_all:
            self._start_background_search(start_from_end=False)
            return
        if self.tab.search_result_index < len(self.tab.search_results_all) - 1:
            self.tab.navigate_to_search_result(self.tab.search_result_index + 1)
        else:
            self.tab.navigate_to_search_result(0)

    def cmd_find_prev(self) -> None:
        if not self.tab.indexer:
            return
        if self._search_pattern_changed() or not self.tab.search_results_all:
            self._start_background_search(start_from_end=True)
            return
        if self.tab.search_result_index > 0:
            self.tab.navigate_to_search_result(self.tab.search_result_index - 1)
        else:
            self.tab.navigate_to_search_result(len(self.tab.search_results_all) - 1)

    def cmd_clear_search(self) -> None:
        if self.tab.search_engine and self.tab.search_engine.is_running():
            self.tab.search_engine.cancel()
        if self.tab.search_thread is not None:
            try:
                if self.tab.search_thread.isRunning():
                    self.tab.search_thread.quit()
                    self.tab.search_thread.wait(2000)
            except RuntimeError:
                pass
        self.tab.search_thread = None
        self.tab.search_worker = None

        self.tab.search_pattern = ""
        self.tab.search_results = []
        total = self.tab.indexer.line_count if self.tab.indexer else 0
        self.tab.search_results_all = Bitset(total)
        self.tab.search_result_index = -1
        self.tab.search_extra_sel = None

        if self.tab.search_model:
            self.tab.search_model.clear()

        self.tab.search_results_label.setText(self.tab.t("lbl_search_results_empty"))
        self.tab.main_window.search_entry.clear()

        self.tab.update_current_line_highlight()
        self.tab.refresh_status()

    # Publiczne aliasy metod kontrolera
    compile_search = _compile_search
    search_pattern_changed = _search_pattern_changed
    start_background_search = _start_background_search
    on_search_progress = _on_search_progress
    on_search_finished = _on_search_finished
