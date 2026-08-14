"""Testy jednostkowe dla FileController z log_viewer/controllers/tab_file_controller.py."""

from __future__ import annotations

import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from PySide6 import QtWidgets
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from log_viewer.bitset import Bitset
from log_viewer.config import UserConfig
from log_viewer.controllers.tab_file_controller import FileController
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


class TestFileController:
    def test_init(self, app_context):
        _, tab, _ = app_context
        controller = FileController(tab)
        assert controller.tab is tab

    def test_stop_background_threads(self, app_context):
        _, tab, _ = app_context
        controller = tab.file_controller

        mock_worker = MagicMock()
        tab.indexer_worker = mock_worker
        mock_thread = MagicMock(spec=QThread)
        mock_thread.isRunning.return_value = True
        tab.indexer_thread = mock_thread

        controller._stop_background_threads()
        mock_worker.cancel.assert_called_once()
        mock_thread.quit.assert_called_once()
        assert tab.indexer_worker is None
        assert tab.indexer_thread is None

    def test_open_file_not_found(self, app_context):
        _, tab, _ = app_context
        controller = tab.file_controller

        with patch.object(QMessageBox, "critical") as mock_crit:
            controller.open_file("non_existent_file_xyz.log")
            mock_crit.assert_called_once()

    def test_open_file_success(self, app_context):
        _, tab, test_file = app_context
        controller = tab.file_controller

        received_titles: list[str] = []
        tab.title_changed.connect(received_titles.append)

        with patch.object(tab, "set_status") as mock_status:
            controller.open_file(test_file, "Custom Title")
            assert "Custom Title" in received_titles
            mock_status.assert_called()
            assert tab.assigned_title == "Custom Title"
            assert tab.indexer_thread is not None
            assert tab.indexer_worker is not None

    def test_on_index_progress(self, app_context):
        _, tab, _ = app_context
        controller = tab.file_controller

        tab.index_progress = QProgressDialog()
        controller._on_index_progress(50.0)
        assert tab.index_progress.value() == 50

    def test_cancel_indexing(self, app_context):
        _, tab, _ = app_context
        controller = tab.file_controller

        mock_worker = MagicMock()
        tab.indexer_worker = mock_worker
        controller._cancel_indexing()
        mock_worker.cancel.assert_called_once()

    def test_close_index_progress(self, app_context):
        _, tab, _ = app_context
        controller = tab.file_controller

        tab.index_progress = QProgressDialog()
        controller._close_index_progress()
        assert tab.index_progress is None

    def test_on_index_error_cancelled(self, app_context):
        window, tab, _ = app_context
        controller = tab.file_controller

        tab.indexer = None
        with patch.object(window, "close_tab") as mock_close:
            controller._on_index_error("cancelled")
            mock_close.assert_called_once()

    def test_on_index_error_other(self, app_context):
        _, tab, _ = app_context
        controller = tab.file_controller

        with patch.object(QMessageBox, "critical") as mock_crit:
            controller._on_index_error("Some indexing failure")
            mock_crit.assert_called_once()

    def test_on_index_done(self, app_context):
        _, tab, test_file = app_context
        controller = tab.file_controller

        new_idx = LineIndexer(test_file, encoding="utf-8")
        with patch.object(tab, "load_window") as mock_load, \
             patch.object(tab, "update_minimap") as mock_minimap:
            controller._on_index_done(new_idx)
            assert tab.indexer is new_idx
            mock_load.assert_called_with(at_line=0)
            mock_minimap.assert_called_once()

    def test_start_reindex_and_finished(self, app_context):
        _, tab, test_file = app_context
        controller = tab.file_controller

        controller.start_reindex(15)
        assert tab.reindex_saved_line == 15
        assert tab.indexer_thread is not None
        assert tab.indexer_worker is not None

        new_idx = LineIndexer(test_file, encoding="utf-8")
        with patch.object(controller, "_on_reindex_after_save") as mock_after:
            controller._on_reindex_finished(new_idx)
            mock_after.assert_called_with(new_idx, 15)

    def test_on_reindex_after_save(self, app_context):
        _, tab, test_file = app_context
        controller = tab.file_controller

        new_idx = LineIndexer(test_file, encoding="utf-8")
        with patch.object(tab, "load_window") as mock_load:
            controller._on_reindex_after_save(new_idx, 25)
            assert tab.indexer is new_idx
            mock_load.assert_called_with(at_line=25)

    def test_cancel_follow_if_active(self, app_context):
        _, tab, _ = app_context
        controller = tab.file_controller

        tab.follow_active = True
        with patch.object(controller, "cmd_toggle_follow") as mock_toggle:
            controller._cancel_follow_if_active()
            mock_toggle.assert_called_once()

    def test_cmd_refresh_and_reload(self, app_context):
        _, tab, test_file = app_context
        controller = tab.file_controller

        with patch.object(controller, "_check_for_updates_once") as mock_check:
            controller.cmd_refresh()
            mock_check.assert_called_once()

        with patch.object(controller, "open_file") as mock_open:
            controller.cmd_reload()
            mock_open.assert_called_once_with(test_file, tab.assigned_title, preserve_state=True)

    def test_cmd_toggle_follow(self, app_context):
        _, tab, _ = app_context
        controller = tab.file_controller

        assert not tab.follow_active
        with patch.object(controller, "_follow_poll"):
            controller.cmd_toggle_follow()
            assert tab.follow_active

            controller.cmd_toggle_follow()
            assert not tab.follow_active

    def test_incremental_filter_done(self, app_context):
        _, tab, _ = app_context
        controller = tab.file_controller

        tab.inc_new_total_lines = 100
        tab.filter_results = Bitset(100)
        tab.filter_all_lines = Bitset(100)

        # Symulacja wyniku incremental filter: (None, words_array)
        dummy_words = [1]
        with patch.object(controller, "_apply_follow_new_lines") as mock_apply:
            controller._on_inc_finished_slot((None, dummy_words))
            mock_apply.assert_called_once()
            assert tab.filter_results is not None

    def test_on_follow_reindex_failed(self, app_context):
        _, tab, _ = app_context
        controller = tab.file_controller

        with patch.object(tab, "set_status") as mock_status:
            controller._on_follow_reindex_failed("timeout")
            mock_status.assert_called_once()
