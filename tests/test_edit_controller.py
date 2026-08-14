"""Testy jednostkowe dla EditController z log_viewer/controllers/tab_edit_controller.py."""

from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from PySide6 import QtWidgets
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from log_viewer.config import UserConfig
from log_viewer.controllers.tab_edit_controller import EditController
from log_viewer.indexer import LineIndexer
from log_viewer.log_tab import LogTab
from log_viewer.main_window import LogViewerWindow


@pytest.fixture
def app_context():
    """Przygotowuje aplikację LogViewerWindow oraz pojedynczą kartę LogTab z plikiem testowym."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    cfg_file = tempfile.mktemp(suffix=".json")
    cfg = UserConfig(config_path=cfg_file)
    window = LogViewerWindow(config=cfg)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".log", mode="wb") as tmp:
        test_file = tmp.name
    with open(test_file, "wb") as f:
        for i in range(100):
            f.write(f"Line {i} content\n".encode("utf-8"))

    tab = window._new_tab()
    idx = LineIndexer(test_file, encoding="utf-8")
    tab.file_path = test_file
    tab._on_index_done(idx)
    app.processEvents()

    yield window, tab, test_file

    try:
        tab.edit_buffer.clear()
        for i in range(window.tabs.count()):
            t = window.tabs.widget(i)
            if isinstance(t, LogTab):
                t.edit_buffer.clear()
    except Exception:
        pass

    window.close()
    app.processEvents()
    for p in (test_file, cfg_file):
        if os.path.exists(p):
            try:
                os.unlink(p)
            except PermissionError:
                pass


class TestEditController:
    def test_init(self, app_context):
        _, tab, _ = app_context
        controller = EditController(tab)
        assert controller.tab is tab

    def test_revert_edit(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller

        tab.edit_buffer.set(5, "Modified Line 5")
        assert tab.edit_buffer.has(5)

        controller.revert_edit(5)
        assert not tab.edit_buffer.has(5)

    def test_cmd_clear_edits_empty(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller

        tab.edit_buffer.clear()
        with patch.object(QMessageBox, "question") as mock_q:
            controller.cmd_clear_edits()
            mock_q.assert_not_called()

    def test_cmd_clear_edits_confirmed(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller

        tab.edit_buffer.set(1, "Edit 1")
        tab.edit_buffer.set(2, "Edit 2")
        assert len(tab.edit_buffer) == 2

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            controller.cmd_clear_edits()
            assert len(tab.edit_buffer) == 0

    def test_cmd_save_edits_no_file(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller
        tab.file_path = None

        with patch.object(QMessageBox, "information") as mock_info:
            controller.cmd_save_edits()
            mock_info.assert_called_once()

    def test_cmd_save_edits_no_edits(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller
        tab.edit_buffer.clear()

        with patch.object(QMessageBox, "information") as mock_info:
            controller.cmd_save_edits()
            mock_info.assert_called_once()

    def test_on_save_done(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller

        tab.edit_buffer.set(1, "Edited line")
        tab.save_progress = QProgressDialog()

        with patch.object(QMessageBox, "information"), patch.object(tab, "start_reindex") as mock_reindex:
            controller._on_save_done("backup.log")
            assert len(tab.edit_buffer) == 0
            assert tab.save_progress is None
            mock_reindex.assert_called_once()

    def test_on_save_error(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller
        tab.save_progress = QProgressDialog()

        with patch.object(QMessageBox, "critical") as mock_crit:
            controller._on_save_error("Disk full")
            assert tab.save_progress is None
            mock_crit.assert_called_once()

    def test_on_save_compressed(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller
        tab.save_progress = QProgressDialog()

        with patch.object(QMessageBox, "warning") as mock_warn:
            controller._on_save_compressed("Compressed error")
            assert tab.save_progress is None
            mock_warn.assert_called_once()

    def test_on_save_file_changed_cancel(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller
        tab.save_progress = QProgressDialog()
        tab.edit_buffer.set(1, "Edit")

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
            controller._on_save_file_changed("File changed externally")
            assert tab.save_progress is None
            assert len(tab.edit_buffer) == 1

    def test_on_save_file_changed_yes(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller
        tab.save_progress = QProgressDialog()
        tab.edit_buffer.set(1, "Edit")

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
             patch.object(QMessageBox, "information"), \
             patch.object(tab, "start_reindex") as mock_reindex:
            controller._on_save_file_changed("File changed externally")
            assert tab.save_progress is None
            assert len(tab.edit_buffer) == 0
            mock_reindex.assert_called_once()

    def test_on_save_file_changed_no(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller
        tab.save_progress = QProgressDialog()
        tab.edit_buffer.set(1, "Edit")

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No), \
             patch.object(QMessageBox, "information") as mock_info:
            controller._on_save_file_changed("File changed externally")
            assert tab.save_progress is None
            assert len(tab.edit_buffer) == 1
            mock_info.assert_called_once()

    def test_save_as_handlers(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller

        tab.save_as_progress = QProgressDialog()
        controller._update_save_progress(50.0)
        assert tab.save_as_progress.value() == 50

        tab.save_as_path = "test_save_as.log"
        with patch.object(QMessageBox, "information") as mock_info:
            controller._on_save_as_done()
            assert tab.save_as_progress is None
            mock_info.assert_called_once()

        tab.save_as_progress = QProgressDialog()
        with patch.object(tab, "set_status") as mock_status:
            controller._on_save_as_error("cancelled")
            assert tab.save_as_progress is None
            mock_status.assert_called_once()

        tab.save_as_progress = QProgressDialog()
        with patch.object(QMessageBox, "critical") as mock_crit:
            controller._on_save_as_error("Permission denied")
            assert tab.save_as_progress is None
            mock_crit.assert_called_once()

    def test_export_handlers(self, app_context):
        _, tab, _ = app_context
        controller = tab.edit_controller

        tab.export_progress = QProgressDialog()
        controller._update_export_progress(75.0)
        assert tab.export_progress.value() == 75

        tab.export_path = "export.txt"
        with patch.object(QMessageBox, "information") as mock_info, patch.object(tab, "set_status"):
            controller._on_export_done(42)
            assert tab.export_progress is None
            mock_info.assert_called_once()

        tab.export_progress = QProgressDialog()
        with patch.object(tab, "set_status") as mock_status:
            controller._on_export_error("cancelled")
            assert tab.export_progress is None
            mock_status.assert_called_once()

        tab.export_progress = QProgressDialog()
        with patch.object(QMessageBox, "critical") as mock_crit:
            controller._on_export_error("Write error")
            assert tab.export_progress is None
            mock_crit.assert_called_once()
