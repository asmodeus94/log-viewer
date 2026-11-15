"""Testy dla SaveAsWorker i ExportWorker z log_viewer/workers.py."""

import array
import os
import tempfile
from pathlib import Path

from log_viewer.bitset import Bitset
from log_viewer.edit_buffer import EditBuffer
from log_viewer.indexer import LineIndexer
from log_viewer.workers import ExportWorker, SaveAsWorker, SaveWorker


def create_temp_file(lines: list[str]) -> str:
    fd, path = tempfile.mkstemp(suffix=".log")
    with os.fdopen(fd, "wb") as f:
        for line in lines:
            f.write(line.encode("utf-8") + b"\n")
    return path


def _cleanup_paths(*paths: str) -> None:
    for p in paths:
        if os.path.exists(p):
            try:
                os.unlink(p)
            except PermissionError:
                pass


def _run_save_as_worker(worker: SaveAsWorker, dst_path: str) -> list[str]:
    finished_called = False

    def on_finished() -> None:
        nonlocal finished_called
        finished_called = True

    worker.finished.connect(on_finished)
    worker.run()

    assert finished_called
    assert os.path.exists(dst_path)

    with open(dst_path, encoding="utf-8") as f:
        return f.read().splitlines()


def _run_export_worker(worker: ExportWorker, dst_path: str) -> tuple[int, list[str]]:
    result_count = 0

    def on_finished(count: int) -> None:
        nonlocal result_count
        result_count = count

    worker.finished.connect(on_finished)
    worker.run()

    assert os.path.exists(dst_path)
    with open(dst_path, encoding="utf-8") as f:
        return result_count, f.read().splitlines()


class TestSaveAsWorker:
    def test_save_as_basic(self):
        src_path = create_temp_file(["Line 0", "Line 1", "Line 2", "Line 3"])
        dst_path = src_path + ".dst"

        try:
            buf = EditBuffer()
            buf.set(1, "EDITED LINE 1")

            worker = SaveAsWorker(buf, src_path, dst_path, encoding="utf-8")
            content = _run_save_as_worker(worker, dst_path)
            assert content == ["Line 0", "EDITED LINE 1", "Line 2", "Line 3"]

            with open(src_path, encoding="utf-8") as f:
                src_content = f.read().splitlines()
            assert src_content == ["Line 0", "Line 1", "Line 2", "Line 3"]
        finally:
            _cleanup_paths(src_path, dst_path)

    def test_save_as_no_edits_block_copy(self):
        raw_lines = [f"Data line {i} content" for i in range(100)]
        src_path = create_temp_file(raw_lines)
        dst_path = src_path + ".dst"

        try:
            buf = EditBuffer()
            worker = SaveAsWorker(buf, src_path, dst_path, encoding="utf-8")
            content = _run_save_as_worker(worker, dst_path)
            assert content == raw_lines
        finally:
            _cleanup_paths(src_path, dst_path)


class TestSaveWorkerAndLineEndings:
    def test_save_worker_cancel_leaves_source_intact(self, tmp_path: Path):
        src_file = tmp_path / "save_cancel.log"
        payload = (b"A" * 1023 + b"\n") * 2200
        src_file.write_bytes(payload)
        st = src_file.stat()

        buf = EditBuffer()
        buf.set(0, "SHOULD NOT BE SAVED")

        worker = SaveWorker(
            buf,
            str(src_file),
            expected_mtime=st.st_mtime_ns,
            expected_size=st.st_size,
            encoding="utf-8",
        )
        finished_called = False
        errors: list[str] = []

        def on_finished(_backup: str) -> None:
            nonlocal finished_called
            finished_called = True

        def on_error(err: str) -> None:
            errors.append(err)

        def cancel_on_progress(_pct: float) -> None:
            worker.cancel()

        worker.progress.connect(cancel_on_progress)
        worker.finished.connect(on_finished)
        worker.error.connect(on_error)
        worker.run()

        assert not finished_called
        assert errors == ["cancelled"]
        assert src_file.read_bytes() == payload
        assert not (tmp_path / "save_cancel.log.bak").exists()
        assert list(tmp_path.glob(".log-viewer_tmp_*")) == []

    def test_save_as_preserves_crlf_and_lf(self, tmp_path: Path):
        src_file = tmp_path / "mixed.log"
        dst_file = tmp_path / "mixed_out.log"
        src_file.write_bytes(b"line0\r\nline1\nline2\r\n")

        buf = EditBuffer()
        buf.set(0, "NEW_0")
        buf.set(1, "NEW_1")

        worker = SaveAsWorker(buf, str(src_file), str(dst_file), encoding="utf-8")
        finished_called = False

        def on_finished() -> None:
            nonlocal finished_called
            finished_called = True

        worker.finished.connect(on_finished)
        worker.run()

        assert finished_called
        assert dst_file.read_bytes() == b"NEW_0\r\nNEW_1\nline2\r\n"

    def test_export_preserves_crlf_and_lf_filtered_and_full(self, tmp_path: Path):
        src_file = tmp_path / "export_mixed.log"
        dst_full = tmp_path / "export_full.log"
        dst_filtered = tmp_path / "export_filtered.log"
        src_file.write_bytes(b"ERR0\r\nINFO1\nERR2\r\nINFO3\n")

        buf = EditBuffer()
        buf.set(0, "ERR0_FIXED")
        buf.set(1, "INFO1_FIXED")

        w_full = ExportWorker(buf, str(src_file), str(dst_full), encoding="utf-8", filter_active=False)
        w_full.run()
        assert dst_full.read_bytes() == b"ERR0_FIXED\r\nINFO1_FIXED\nERR2\r\nINFO3\n"

        filter_results = Bitset.from_indices(array.array("Q", [0, 2]), 4)
        w_filtered = ExportWorker(
            buf,
            str(src_file),
            str(dst_filtered),
            encoding="utf-8",
            filter_active=True,
            filter_results=filter_results,
        )
        w_filtered.run()
        assert dst_filtered.read_bytes() == b"ERR0_FIXED\r\nERR2\r\n"


class TestExportWorker:
    def test_export_without_filter(self):
        src_path = create_temp_file(["Line 0", "Line 1", "Line 2"])
        dst_path = src_path + ".export"

        try:
            buf = EditBuffer()
            buf.set(2, "EDITED LINE 2")

            worker = ExportWorker(buf, src_path, dst_path, encoding="utf-8", filter_active=False)
            result_count, content = _run_export_worker(worker, dst_path)

            assert result_count == 3
            assert content == ["Line 0", "Line 1", "EDITED LINE 2"]
        finally:
            _cleanup_paths(src_path, dst_path)

    def test_export_with_filter_and_edits(self):
        src_path = create_temp_file(["Line 0", "ERROR at line 1", "Line 2", "ERROR at line 3"])
        dst_path = src_path + ".export"

        try:
            buf = EditBuffer()
            buf.set(1, "FIXED ERROR at line 1")

            indices = array.array("Q", [1, 3])
            filter_results = Bitset.from_indices(indices, 4)

            worker = ExportWorker(
                buf, src_path, dst_path, encoding="utf-8", filter_active=True, filter_results=filter_results
            )
            result_count, content = _run_export_worker(worker, dst_path)

            assert result_count == 2
            assert content == ["FIXED ERROR at line 1", "ERROR at line 3"]
        finally:
            _cleanup_paths(src_path, dst_path)

    def test_export_with_filter_and_indexer_sparse(self):
        raw_lines = [f"Line {i:04d} payload data" for i in range(600)]
        src_path = create_temp_file(raw_lines)
        dst_path = src_path + ".export"

        idx = LineIndexer(src_path, index_interval_bytes=256)
        try:
            buf = EditBuffer()
            buf.set(300, "EDITED LINE 300")

            targets = array.array("Q", [5, 300, 550])
            filter_results = Bitset.from_indices(targets, 600)

            worker = ExportWorker(
                buf,
                src_path,
                dst_path,
                encoding="utf-8",
                filter_active=True,
                filter_results=filter_results,
                total_lines=600,
                indexer=idx,
            )
            result_count, content = _run_export_worker(worker, dst_path)

            assert result_count == 3
            assert content == ["Line 0005 payload data", "EDITED LINE 300", "Line 0550 payload data"]
        finally:
            idx.close()
            _cleanup_paths(src_path, dst_path)

    def test_export_no_edits_block_copy(self):
        raw_lines = [f"Log line {i} data payload" for i in range(100)]
        src_path = create_temp_file(raw_lines)
        dst_path = src_path + ".export"

        try:
            buf = EditBuffer()
            worker = ExportWorker(buf, src_path, dst_path, encoding="utf-8", filter_active=False, total_lines=100)
            result_count, content = _run_export_worker(worker, dst_path)

            assert result_count == 100
            assert content == raw_lines
        finally:
            _cleanup_paths(src_path, dst_path)

    def test_export_no_edits_block_copy_unknown_total_lines(self):
        raw_lines = [f"Log line {i} data payload" for i in range(50)]
        src_path = create_temp_file(raw_lines)
        dst_path = src_path + ".export"

        try:
            buf = EditBuffer()
            worker = ExportWorker(buf, src_path, dst_path, encoding="utf-8", filter_active=False, total_lines=0)
            result_count, content = _run_export_worker(worker, dst_path)

            assert result_count == 50
            assert content == raw_lines
        finally:
            _cleanup_paths(src_path, dst_path)
