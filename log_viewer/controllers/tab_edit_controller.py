"""tab_edit_controller.py — Kontroler edycji tekstu, zapisu i eksportu dla LogTab."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QThread, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from log_viewer.helpers import fmt_size
from log_viewer.widgets import FormatDialog
from log_viewer.workers import ExportWorker, SaveAsWorker, SaveWorker

if TYPE_CHECKING:
    from log_viewer.log_tab import LogTab


class EditController(QObject):
    """Kontroler odpowiedzialny za operacje edycji (formatowanie, edycja linii,
    zapis, zapis jako, eksport) w pojedynczej karcie LogTab.
    """

    def __init__(self, tab: LogTab) -> None:
        super().__init__(tab)
        self.tab: LogTab = tab

    def cmd_format_selection(self) -> None:
        """Pobiera zaznaczony tekst i wywołuje dialog do jego sformatowania."""
        cursor = self.tab.text.textCursor()
        if not cursor.hasSelection():
            # Jeżeli nie ma zaznaczenia, bierzemy całą bieżącą linię
            cursor.select(QTextCursor.SelectionType.LineUnderCursor)

        selected_text = cursor.selectedText().replace("\u2029", "\n")

        if not selected_text.strip():
            return

        dialog = FormatDialog(self.tab, selected_text, self.tab.last_formatter)
        dialog.exec()

        # Zapisz na przyszłość (tylko w sesji) wybór formattera
        self.tab.last_formatter = dialog.get_selected_formatter()
        dialog.deleteLater()

    def cmd_edit_line(self) -> None:
        """Otwiera okno dialogowe do edycji wybranej linii pliku."""
        if not self.tab.indexer:
            QMessageBox.information(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_no_file"))
            return
        cursor = self.tab.text.textCursor()
        # Jeśli kursor jest POZA widocznym obszarem (np. po przewinięciu
        # widoku po wyniku wyszukiwania), użyj pierwszej widocznej linii.
        # Bez tego user widzi jedną linię, ale edytuje inną (tę, na której
        # pozostał kursor) — to było zgłoszone jako błąd „edytuje linię
        # kilka pozycji niżej".
        cursor_rect = self.tab.text.cursorRect(cursor)
        viewport_rect = self.tab.text.viewport().rect()
        if not viewport_rect.contains(cursor_rect.topLeft()):
            fvb = self.tab.text.firstVisibleBlock()
            if fvb.isValid():
                cursor = QTextCursor(fvb)
                self.tab.text.setTextCursor(cursor)
        widget_line = cursor.blockNumber()
        if self.tab.line_map is None or widget_line < 0 or widget_line >= len(self.tab.line_map):
            return
        file_line = self.tab.line_map[widget_line]
        current_text = self.tab.get_display_text(file_line, widget_line)

        dialog = QDialog(self.tab.main_window)
        dialog.setWindowTitle(self.tab.t("dlg_edit_title").format(n=file_line + 1))
        dialog.setMinimumWidth(500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(self.tab.t("dlg_edit_title").format(n=file_line + 1)))
        edit = QPlainTextEdit()
        edit.setPlainText(current_text)
        edit.setMinimumHeight(120)
        layout.addWidget(edit)
        buttons = QDialogButtonBox()
        buttons.addButton(QDialogButtonBox.StandardButton.Ok)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        if self.tab.edit_buffer.has(file_line):
            revert_btn = QPushButton(self.tab.t("mi_clear_edits"))
            buttons.addButton(revert_btn, QDialogButtonBox.ButtonRole.ActionRole)
            revert_btn.clicked.connect(dialog.reject)
            revert_btn.clicked.connect(lambda: self.revert_edit(file_line))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_text = edit.toPlainText().rstrip("\n")
            self.tab.edit_buffer.set(file_line, new_text)
            self.tab.refresh_edits_tree()
            self.tab.reload_current_view()
            self.tab.refresh_status()

    def revert_edit(self, file_line: int) -> None:
        """Cofa wprowadzoną modyfikację dla podanej linii."""
        if self.tab.edit_buffer.has(file_line):
            self.tab.edit_buffer.discard(file_line)
            self.tab.refresh_edits_tree()
            self.tab.reload_current_view()
            self.tab.refresh_status()

    _revert_edit = revert_edit

    def _can_start_file_operation(self, thread: QThread | None) -> bool:
        try:
            if thread is not None and thread.isRunning():
                return False
        except RuntimeError:
            pass
        if not self.tab.file_path or not self.tab.indexer:
            QMessageBox.information(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_no_file"))
            return False
        return True

    def _start_worker_thread(
        self,
        thread: QThread,
        worker: Any,
        on_progress: Callable[..., None],
        on_finished: Callable[..., None],
        on_error: Callable[..., None],
        progress_dialog: QProgressDialog | None = None,
        extra_done_signals: tuple[tuple[Any, Callable[..., None]], ...] = (),
    ) -> None:
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        self.tab.register_thread_worker(thread, worker)

        worker.progress.connect(on_progress, Qt.ConnectionType.QueuedConnection)
        if progress_dialog is not None and hasattr(worker, "cancel"):
            progress_dialog.canceled.connect(worker.cancel, Qt.ConnectionType.DirectConnection)

        worker.finished.connect(on_finished, Qt.ConnectionType.QueuedConnection)
        worker.error.connect(on_error, Qt.ConnectionType.QueuedConnection)
        for sig, slot in extra_done_signals:
            sig.connect(slot, Qt.ConnectionType.QueuedConnection)

        for sig in (worker.finished, worker.error, *(s for s, _ in extra_done_signals)):
            sig.connect(thread.quit, Qt.ConnectionType.QueuedConnection)
            sig.connect(worker.deleteLater, Qt.ConnectionType.QueuedConnection)

        thread.finished.connect(thread.deleteLater)
        thread.start()

    def cmd_save_edits(self) -> None:
        """Rozpoczyna asynchroniczny zapis zmodyfikowanych linii do bieżącego pliku."""
        if not self._can_start_file_operation(self.tab.save_thread):
            return
        assert self.tab.file_path is not None and self.tab.indexer is not None
        if len(self.tab.edit_buffer) == 0:
            QMessageBox.information(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_no_edits"))
            return
        size = fmt_size(self.tab.indexer.size)
        # Ostrzeżenie o czasie zapisu dla dużych plików
        save_warning = ""
        if self.tab.indexer.size > 1 * 1024 * 1024 * 1024:  # > 1 GB
            est_seconds = self.tab.indexer.size / (500 * 1024 * 1024)  # ~500 MB/s
            if est_seconds > 5:
                save_warning = f"\n\n⚠️ Plik ma {size} — zapis potrwa ~{est_seconds:.0f}s."
        choice = QMessageBox.question(
            self.tab.main_window,
            self.tab.t("app_title"),
            self.tab.t("msg_confirm_save").format(n=len(self.tab.edit_buffer), size=size, path=self.tab.file_path)
            + save_warning,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice not in (QMessageBox.StandardButton.Yes, int(QMessageBox.StandardButton.Yes)):
            return

        if self.tab.follow_active:
            self.tab.cmd_toggle_follow()

        save_progress = QProgressDialog(self.tab.t("mi_save"), self.tab.t("btn_cancel"), 0, 100, self.tab.main_window)
        save_progress.setWindowTitle(self.tab.t("mi_save"))
        save_progress.setWindowModality(Qt.WindowModality.WindowModal)
        save_progress.setValue(0)
        self.tab.save_progress = save_progress

        save_thread = QThread()
        save_worker = SaveWorker(
            self.tab.edit_buffer,
            self.tab.file_path,
            self.tab.file_mtime_at_open,
            self.tab.file_size_at_open,
            self.tab.encoding,
        )
        self.tab.save_thread = save_thread
        self.tab.save_worker = save_worker

        self._start_worker_thread(
            save_thread,
            save_worker,
            on_progress=save_progress.setValue,
            on_finished=self._on_save_done,
            on_error=self._on_save_error,
            extra_done_signals=(
                (save_worker.file_changed, self._on_save_file_changed),
                (save_worker.compressed, self._on_save_compressed),
            ),
        )

    @Slot(str)
    def _on_save_done(self, _backup_path: str) -> None:
        progress = self.tab.save_progress
        if progress is not None:
            progress.close()
            self.tab.save_progress = None
        QMessageBox.information(
            self.tab.main_window,
            self.tab.t("app_title"),
            self.tab.t("msg_save_ok").format(n=len(self.tab.edit_buffer), path=self.tab.file_path),
        )
        try:
            cursor = self.tab.text.textCursor()
            saved_line = self.tab.line_map[cursor.blockNumber()] if self.tab.line_map else 0
        except (IndexError, TypeError, AttributeError, KeyError):
            saved_line = 0
        self.tab.edit_buffer.clear()
        self.tab.refresh_edits_tree()
        self.tab.start_reindex(saved_line)

    @Slot(str)
    def _on_save_error(self, err: str) -> None:
        progress = self.tab.save_progress
        if progress is not None:
            progress.close()
            self.tab.save_progress = None
        QMessageBox.critical(self.tab.main_window, self.tab.t("app_title"), f"Save error: {err}")

    @Slot(str)
    def _on_save_file_changed(self, err: str) -> None:
        progress = self.tab.save_progress
        if progress is not None:
            progress.close()
            self.tab.save_progress = None
        choice = QMessageBox.question(
            self.tab.main_window,
            self.tab.t("app_title"),
            self.tab.t("msg_file_changed").format(error=err, n=len(self.tab.edit_buffer)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if choice in (QMessageBox.StandardButton.Cancel, int(QMessageBox.StandardButton.Cancel)):
            self.tab.refresh_status()
            return
        if choice in (QMessageBox.StandardButton.Yes, int(QMessageBox.StandardButton.Yes)):
            self.tab.edit_buffer.clear()
            self.tab.refresh_edits_tree()
            try:
                cursor = self.tab.text.textCursor()
                saved_line = self.tab.line_map[cursor.blockNumber()] if self.tab.line_map else 0
            except (IndexError, TypeError, AttributeError, KeyError):
                saved_line = 0
            self.tab.start_reindex(saved_line)
        else:
            self.tab.refresh_status()
            QMessageBox.information(self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_save_as_suggested"))

    @Slot(str)
    def _on_save_compressed(self, _err: str) -> None:
        progress = self.tab.save_progress
        if progress is not None:
            progress.close()
            self.tab.save_progress = None
        QMessageBox.warning(self.tab.main_window, self.tab.t("app_title"), self.tab.t("mi_compressed_warn"))

    def cmd_clear_edits(self) -> None:
        """Czyści wszystkie wprowadzone modyfikacje w buforze edycji."""
        if len(self.tab.edit_buffer) == 0:
            return
        choice = QMessageBox.question(
            self.tab.main_window,
            self.tab.t("app_title"),
            self.tab.t("msg_clear_edits").format(n=len(self.tab.edit_buffer)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if choice not in (QMessageBox.StandardButton.Yes, int(QMessageBox.StandardButton.Yes)):
            return
        self.tab.edit_buffer.clear()
        self.tab.refresh_edits_tree()
        self.tab.reload_current_view()
        self.tab.refresh_status()

    @Slot(float)
    def _update_save_progress(self, val: float) -> None:
        progress = self.tab.save_as_progress
        if progress is not None and not progress.wasCanceled():
            progress.setValue(int(val))

    @Slot()
    def _on_save_as_done(self) -> None:
        progress = self.tab.save_as_progress
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
            progress.deleteLater()
            self.tab.save_as_progress = None
        path = self.tab.save_as_path or ""
        QMessageBox.information(
            self.tab.main_window,
            self.tab.t("app_title"),
            self.tab.t("msg_save_ok").format(n=len(self.tab.edit_buffer), path=path),
        )

    @Slot(str)
    def _on_save_as_error(self, err: str) -> None:
        progress = self.tab.save_as_progress
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
            progress.deleteLater()
            self.tab.save_as_progress = None
        if err == "cancelled":
            self.tab.set_status(self.tab.t("st_cancelled"))
        else:
            QMessageBox.critical(
                self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_save_error").format(err=err)
            )

    def cmd_save_as(self) -> None:
        """Zapisuje zawartość pliku z uwzględnieniem edycji do nowo wybranego pliku."""
        if not self._can_start_file_operation(self.tab.save_as_thread):
            return
        assert self.tab.file_path is not None and self.tab.indexer is not None
        path, _ = QFileDialog.getSaveFileName(
            self.tab.main_window, self.tab.t("mi_save_as"), "", "Log files (*.log);;Text files (*.txt);;All files (*)"
        )
        if not path:
            return

        self.tab.save_as_path = path

        save_as_progress = QProgressDialog(
            self.tab.t("dlg_save_as_title"), self.tab.t("btn_cancel"), 0, 100, self.tab.main_window
        )
        save_as_progress.setWindowTitle(self.tab.t("dlg_save_as_title"))
        save_as_progress.setWindowModality(Qt.WindowModality.NonModal)
        save_as_progress.setValue(0)
        save_as_progress.show()
        self.tab.save_as_progress = save_as_progress

        save_as_thread = QThread()
        save_as_worker = SaveAsWorker(
            self.tab.edit_buffer, self.tab.file_path, path, self.tab.encoding, total_lines=self.tab.indexer.line_count
        )
        self.tab.save_as_thread = save_as_thread
        self.tab.save_as_worker = save_as_worker

        self._start_worker_thread(
            save_as_thread,
            save_as_worker,
            on_progress=self._update_save_progress,
            on_finished=self._on_save_as_done,
            on_error=self._on_save_as_error,
            progress_dialog=save_as_progress,
        )

    @Slot(float)
    def _update_export_progress(self, val: float) -> None:
        progress = self.tab.export_progress
        if progress is not None and not progress.wasCanceled():
            progress.setValue(int(val))

    @Slot(int)
    def _on_export_done(self, count: int) -> None:
        progress = self.tab.export_progress
        if progress is not None:
            progress.deleteLater()
            self.tab.export_progress = None
        path = self.tab.export_path or ""
        self.tab.set_status(self.tab.t("msg_exported").format(n=count, path=path))
        QMessageBox.information(
            self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_exported").format(n=count, path=path)
        )

    @Slot(str)
    def _on_export_error(self, err: str) -> None:
        progress = self.tab.export_progress
        if progress is not None:
            progress.blockSignals(True)
            progress.close()
            progress.deleteLater()
            self.tab.export_progress = None
        if err == "cancelled":
            self.tab.set_status(self.tab.t("st_cancelled"))
        else:
            QMessageBox.critical(
                self.tab.main_window, self.tab.t("app_title"), self.tab.t("msg_export_error").format(err=err)
            )

    def cmd_export(self) -> None:
        """Eksportuje przefiltrowany lub cały plik (wraz z edycjami) do nowego pliku."""
        if not self._can_start_file_operation(self.tab.export_thread):
            return
        assert self.tab.file_path is not None and self.tab.indexer is not None
        path, _ = QFileDialog.getSaveFileName(
            self.tab.main_window, self.tab.t("dlg_export_title"), "", "Text Files (*.txt);;All Files (*)"
        )
        if not path:
            return

        self.tab.export_path = path

        export_progress = QProgressDialog(
            self.tab.t("dlg_export_title"), self.tab.t("btn_cancel"), 0, 100, self.tab.main_window
        )
        export_progress.setWindowTitle(self.tab.t("dlg_export_title"))
        export_progress.setWindowModality(Qt.WindowModality.WindowModal)
        export_progress.setValue(0)
        export_progress.show()
        self.tab.export_progress = export_progress

        export_thread = QThread()
        export_worker = ExportWorker(
            self.tab.edit_buffer,
            self.tab.file_path,
            path,
            encoding=self.tab.encoding,
            filter_active=self.tab.filter_active,
            filter_results=self.tab.filter_results,
            total_lines=self.tab.indexer.line_count if self.tab.indexer else 0,
            indexer=self.tab.indexer,
        )
        self.tab.export_thread = export_thread
        self.tab.export_worker = export_worker

        self._start_worker_thread(
            export_thread,
            export_worker,
            on_progress=self._update_export_progress,
            on_finished=self._on_export_done,
            on_error=self._on_export_error,
            progress_dialog=export_progress,
        )

    # Publiczne aliasy metod kontrolera
    on_save_done = _on_save_done
    on_save_error = _on_save_error
    on_save_file_changed = _on_save_file_changed
    on_save_compressed = _on_save_compressed
