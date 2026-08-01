from PySide6 import QtGui
from PySide6.QtWidgets import QVBoxLayout, QLabel, QPlainTextEdit, QDialogButtonBox, QPushButton
from log_viewer.widgets import FormatDialog
from PySide6.QtCore import QObject, Qt, Slot, QThread
from PySide6.QtWidgets import QMessageBox, QInputDialog, QProgressDialog, QFileDialog
from PySide6.QtGui import QTextCursor
import os
from log_viewer.workers import SaveWorker, SaveAsWorker, ExportWorker
from log_viewer.helpers import fmt_size, open_maybe_compressed
from log_viewer.widgets import FormatDialog
from PySide6.QtWidgets import QDialog
from log_viewer.formatters import format_json, format_xml, format_log

class EditController(QObject):
    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab

    def cmd_format_selection(self) -> None:
        """Pobiera zaznaczony tekst i wywołuje dialog do jego sformatowania."""
        cursor = self.tab.text.textCursor()
        if not cursor.hasSelection():
            # Jeżeli nie ma zaznaczenia, bierzemy całą bieżącą linię
            cursor.select(QtGui.QTextCursor.LineUnderCursor)

        selected_text = cursor.selectedText().replace("\u2029", "\n")

        if not selected_text.strip():
            return

        dialog = FormatDialog(self.tab, selected_text, self.tab._last_formatter)
        dialog.exec()

        # Zapisz na przyszłość (tylko w sesji) wybór formattera
        self.tab._last_formatter = dialog.get_selected_formatter()

    def cmd_edit_line(self) -> None:
        if not self.tab.indexer:
            QMessageBox.information(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_no_file"))
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
                cursor = QtGui.QTextCursor(fvb)
                self.tab.text.setTextCursor(cursor)
        widget_line = cursor.blockNumber()
        if widget_line < 0 or widget_line >= len(self.tab.line_map):
            return
        file_line = self.tab.line_map[widget_line]
        current_text = self.tab._get_display_text(file_line, widget_line)

        dialog = QDialog(self.tab._main)
        dialog.setWindowTitle(self.tab.t("dlg_edit_title").format(n=file_line + 1))
        dialog.setMinimumWidth(500)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(self.tab.t("dlg_edit_title").format(n=file_line + 1)))
        edit = QPlainTextEdit()
        edit.setPlainText(current_text)
        edit.setMinimumHeight(120)
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        if self.tab.edit_buffer.has(file_line):
            revert_btn = QPushButton(self.tab.t("mi_clear_edits"))
            buttons.addButton(revert_btn, QDialogButtonBox.ActionRole)
            revert_btn.clicked.connect(dialog.reject)
            revert_btn.clicked.connect(lambda: self.tab._revert_edit(file_line))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            new_text = edit.toPlainText().rstrip("\n")
            self.tab.edit_buffer.set(file_line, new_text)
            self.tab._refresh_edits_tree()
            self.tab._reload_current_view()
            self.tab._refresh_status()

    def _revert_edit(self, file_line: int) -> None:
        if self.tab.edit_buffer.has(file_line):
            self.tab.edit_buffer.discard(file_line)
            self.tab._refresh_edits_tree()
            self.tab._reload_current_view()
            self.tab._refresh_status()

    def cmd_save_edits(self) -> None:
        if not self.tab.file_path or not self.tab.indexer:
            QMessageBox.information(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_no_file"))
            return
        if len(self.tab.edit_buffer) == 0:
            QMessageBox.information(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_no_edits"))
            return
        size = fmt_size(self.tab.indexer.size)
        # Ostrzeżenie o czasie zapisu dla dużych plików
        save_warning = ""
        if self.tab.indexer.size > 1 * 1024 * 1024 * 1024:  # > 1 GB
            est_seconds = self.tab.indexer.size / (500 * 1024 * 1024)  # ~500 MB/s
            if est_seconds > 5:
                save_warning = f"\n\n⚠️ Plik ma {size} — zapis potrwa ~{est_seconds:.0f}s."
        if not QMessageBox.question(
            self.tab._main, self.tab.t("app_title"),
            self.tab.t("msg_confirm_save").format(n=len(self.tab.edit_buffer), size=size, path=self.tab.file_path) + save_warning,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            return

        if self.tab.follow_active:
            self.tab.cmd_toggle_follow()

        self.tab._save_progress = QProgressDialog(self.tab.t("mi_save"), self.tab.t("btn_cancel"), 0, 100, self.tab._main)
        self.tab._save_progress.setWindowTitle(self.tab.t("mi_save"))
        self.tab._save_progress.setWindowModality(Qt.WindowModal)
        self.tab._save_progress.setValue(0)

        self.tab._save_thread = QThread()
        self.tab._save_worker = SaveWorker(
            self.tab.edit_buffer, self.tab.file_path,
            self.tab._file_mtime_at_open, self.tab._file_size_at_open,
            self.tab.encoding,
        )
        self.tab._save_worker.moveToThread(self.tab._save_thread)
        self.tab._save_thread.started.connect(self.tab._save_worker.run)
        self.tab._register_thread_worker(self.tab._save_thread, self.tab._save_worker)
        # QueuedConnection + metoda-slot (nie lambda) — cross-thread safe.
        self.tab._save_worker.progress.connect(self.tab._save_progress.setValue, Qt.QueuedConnection)
        self.tab._save_worker.finished.connect(self.tab._on_save_done, Qt.QueuedConnection)
        self.tab._save_worker.error.connect(self.tab._on_save_error, Qt.QueuedConnection)
        self.tab._save_worker.file_changed.connect(self.tab._on_save_file_changed, Qt.QueuedConnection)
        self.tab._save_worker.compressed.connect(self.tab._on_save_compressed, Qt.QueuedConnection)
        self.tab._save_worker.finished.connect(self.tab._save_thread.quit, Qt.QueuedConnection)
        self.tab._save_worker.error.connect(self.tab._save_thread.quit, Qt.QueuedConnection)
        self.tab._save_worker.file_changed.connect(self.tab._save_thread.quit, Qt.QueuedConnection)
        self.tab._save_worker.compressed.connect(self.tab._save_thread.quit, Qt.QueuedConnection)
        # deleteLater — zwolnij pamięć C++ po zakończeniu
        self.tab._save_worker.finished.connect(self.tab._save_worker.deleteLater, Qt.QueuedConnection)
        self.tab._save_worker.error.connect(self.tab._save_worker.deleteLater, Qt.QueuedConnection)
        self.tab._save_worker.file_changed.connect(self.tab._save_worker.deleteLater, Qt.QueuedConnection)
        self.tab._save_worker.compressed.connect(self.tab._save_worker.deleteLater, Qt.QueuedConnection)
        self.tab._save_thread.finished.connect(self.tab._save_thread.deleteLater)
        self.tab._save_thread.start()

    @Slot(str)
    def _on_save_done(self, backup_path: str) -> None:
        if self.tab._save_progress:
            self.tab._save_progress.close()
            self.tab._save_progress = None
        QMessageBox.information(self.tab._main, self.tab.t("app_title"),
                                self.tab.t("msg_save_ok").format(n=len(self.tab.edit_buffer), path=self.tab.file_path))
        try:
            cursor = self.tab.text.textCursor()
            saved_line = self.tab.line_map[cursor.blockNumber()] if self.tab.line_map else 0
        except Exception:
            saved_line = 0
        self.tab.edit_buffer.clear()
        self.tab._refresh_edits_tree()
        self.tab._start_reindex(saved_line)

    @Slot(str)
    def _on_save_error(self, err: str) -> None:
        if self.tab._save_progress:
            self.tab._save_progress.close()
            self.tab._save_progress = None
        QMessageBox.critical(self.tab._main, self.tab.t("app_title"), f"Save error: {err}")

    @Slot(str)
    def _on_save_file_changed(self, err: str) -> None:
        if self.tab._save_progress:
            self.tab._save_progress.close()
            self.tab._save_progress = None
        choice = QMessageBox.question(
            self.tab._main, self.tab.t("app_title"),
            self.tab.t("msg_file_changed").format(error=err, n=len(self.tab.edit_buffer)),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if choice == QMessageBox.Cancel:
            self.tab._refresh_status()
            return
        if choice == QMessageBox.Yes:
            self.tab.edit_buffer.clear()
            self.tab._refresh_edits_tree()
            try:
                cursor = self.tab.text.textCursor()
                saved_line = self.tab.line_map[cursor.blockNumber()] if self.tab.line_map else 0
            except Exception:
                saved_line = 0
            self.tab._start_reindex(saved_line)
        else:
            self.tab._refresh_status()
            QMessageBox.information(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_save_as_suggested"))

    @Slot(str)
    def _on_save_compressed(self, err: str) -> None:
        if self.tab._save_progress:
            self.tab._save_progress.close()
            self.tab._save_progress = None
        QMessageBox.warning(self.tab._main, self.tab.t("app_title"), self.tab.t("mi_compressed_warn"))

    def cmd_clear_edits(self) -> None:
        if len(self.tab.edit_buffer) == 0:
            return
        if not QMessageBox.question(
            self.tab._main, self.tab.t("app_title"),
            self.tab.t("msg_clear_edits").format(n=len(self.tab.edit_buffer)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            return
        self.tab.edit_buffer.clear()
        self.tab._refresh_edits_tree()
        self.tab._reload_current_view()
        self.tab._refresh_status()

    @Slot(float)
    def _update_save_progress(self, val: float):
        if hasattr(self.tab, "_save_as_progress") and self.tab._save_as_progress:
            if not self.tab._save_as_progress.wasCanceled():
                self.tab._save_as_progress.setValue(int(val))

    @Slot()
    def _on_save_done(self):
        if hasattr(self.tab, "_save_as_progress") and self.tab._save_as_progress:
            self.tab._save_as_progress.close()
            self.tab._save_as_progress = None
        QMessageBox.information(self.tab._main, self.tab.t("app_title"),
                                self.tab.t("msg_save_ok").format(n=len(self.tab.edit_buffer), path=self.tab._save_as_path))

    @Slot(str)
    def _on_save_error(self, err: str):
        if hasattr(self.tab, "_save_as_progress") and self.tab._save_as_progress:
            self.tab._save_as_progress.close()
            self.tab._save_as_progress = None
        if err == "cancelled":
            self.tab._status(self.tab.t("st_cancelled"))
        else:
            QMessageBox.critical(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_save_error").format(err=err))

    def cmd_save_as(self) -> None:
        if not self.tab.file_path or not self.tab.indexer:
            QMessageBox.information(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_no_file"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self.tab._main, self.tab.t("mi_save_as"), "", "Log files (*.log);;Text files (*.txt);;All files (*)"
        )
        if not path:
            return
            
        self.tab._save_as_path = path

        self.tab._save_as_progress = QProgressDialog(self.tab.t("dlg_save_as_title"), self.tab.t("btn_cancel"), 0, 100, self.tab._main)
        self.tab._save_as_progress.setWindowTitle(self.tab.t("dlg_save_as_title"))
        self.tab._save_as_progress.setWindowModality(Qt.NonModal)
        self.tab._save_as_progress.setValue(0)
        self.tab._save_as_progress.show()

        self.tab._save_as_thread = QThread()
        self.tab._save_as_worker = SaveAsWorker(
            self.tab.edit_buffer, self.tab.file_path, path, self.tab.encoding
        )
        self.tab._save_as_worker.moveToThread(self.tab._save_as_thread)
        self.tab._save_as_thread.started.connect(self.tab._save_as_worker.run)
        self.tab._register_thread_worker(self.tab._save_as_thread, self.tab._save_as_worker)

        self.tab._save_as_worker.progress.connect(self._update_save_progress, Qt.QueuedConnection)
        self.tab._save_as_progress.canceled.connect(self.tab._save_as_worker.cancel, Qt.DirectConnection)

        self.tab._save_as_worker.finished.connect(self._on_save_done, Qt.QueuedConnection)
        self.tab._save_as_worker.error.connect(self._on_save_error, Qt.QueuedConnection)

        self.tab._save_as_worker.finished.connect(self.tab._save_as_worker.deleteLater, Qt.QueuedConnection)
        self.tab._save_as_worker.error.connect(self.tab._save_as_worker.deleteLater, Qt.QueuedConnection)
        self.tab._save_as_worker.finished.connect(self.tab._save_as_thread.quit, Qt.QueuedConnection)
        self.tab._save_as_worker.error.connect(self.tab._save_as_thread.quit, Qt.QueuedConnection)
        self.tab._save_as_thread.finished.connect(self.tab._save_as_thread.deleteLater)
        self.tab._save_as_thread.start()

    @Slot(float)
    def _update_export_progress(self, val: float):
        if hasattr(self.tab, "_export_progress") and self.tab._export_progress:
            if not self.tab._export_progress.wasCanceled():
                self.tab._export_progress.setValue(int(val))

    @Slot(int)
    def _on_export_done(self, count: int):
        if hasattr(self.tab, "_export_progress") and self.tab._export_progress:
            self.tab._export_progress.deleteLater()
            self.tab._export_progress = None
        self.tab._status(self.tab.t("msg_exported").format(n=count, path=self.tab._export_path))
        QMessageBox.information(self.tab._main, self.tab.t("app_title"),
                                self.tab.t("msg_exported").format(n=count, path=self.tab._export_path))

    @Slot(str)
    def _on_export_error(self, err: str):
        if hasattr(self.tab, "_export_progress") and self.tab._export_progress:
            self.tab._export_progress.deleteLater()
            self.tab._export_progress = None
        if err == "cancelled":
            self.tab._status(self.tab.t("st_cancelled"))
        else:
            QMessageBox.critical(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_export_error").format(err=err))

    def cmd_export(self) -> None:
        if not self.tab.indexer or not self.tab.file_path:
            QMessageBox.information(self.tab._main, self.tab.t("app_title"), self.tab.t("msg_no_file"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self.tab._main, self.tab.t("dlg_export_title"), "", "Text Files (*.txt);;All Files (*)"
        )
        if not path:
            return
            
        self.tab._export_path = path

        self.tab._export_progress = QProgressDialog(self.tab.t("dlg_export_title"), self.tab.t("btn_cancel"), 0, 100, self.tab._main)
        self.tab._export_progress.setWindowTitle(self.tab.t("dlg_export_title"))
        self.tab._export_progress.setWindowModality(Qt.WindowModal)
        self.tab._export_progress.setValue(0)
        self.tab._export_progress.show()

        self.tab._export_thread = QThread()
        self.tab._export_worker = ExportWorker(
            self.tab.edit_buffer, self.tab.file_path, path,
            encoding=self.tab.encoding,
            filter_active=self.tab.filter_active,
            filter_results=self.tab.filter_results,
        )
        self.tab._export_worker.moveToThread(self.tab._export_thread)
        self.tab._export_thread.started.connect(self.tab._export_worker.run)
        
        self.tab._register_thread_worker(self.tab._export_thread, self.tab._export_worker)

        self.tab._export_worker.progress.connect(self._update_export_progress, Qt.QueuedConnection)
        self.tab._export_progress.canceled.connect(self.tab._export_worker.cancel, Qt.DirectConnection)

        self.tab._export_worker.finished.connect(self._on_export_done, Qt.QueuedConnection)
        self.tab._export_worker.error.connect(self._on_export_error, Qt.QueuedConnection)

        self.tab._export_worker.finished.connect(self.tab._export_worker.deleteLater, Qt.QueuedConnection)
        self.tab._export_worker.error.connect(self.tab._export_worker.deleteLater, Qt.QueuedConnection)
        self.tab._export_worker.finished.connect(self.tab._export_thread.quit, Qt.QueuedConnection)
        self.tab._export_worker.error.connect(self.tab._export_thread.quit, Qt.QueuedConnection)
        self.tab._export_thread.finished.connect(self.tab._export_thread.deleteLater)
        self.tab._export_thread.start()