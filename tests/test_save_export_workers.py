"""Testy dla SaveAsWorker i ExportWorker z log_viewer/workers.py."""

import os
import tempfile

from log_viewer.edit_buffer import EditBuffer
from log_viewer.workers import ExportWorker, SaveAsWorker


def create_temp_file(lines):
    fd, path = tempfile.mkstemp(suffix=".log")
    with os.fdopen(fd, "wb") as f:
        for line in lines:
            f.write(line.encode("utf-8") + b"\n")
    return path


class TestSaveAsWorker:
    def test_save_as_basic(self):
        src_path = create_temp_file(["Line 0", "Line 1", "Line 2", "Line 3"])
        dst_path = src_path + ".dst"

        try:
            buf = EditBuffer()
            buf.set(1, "EDITED LINE 1")

            worker = SaveAsWorker(buf, src_path, dst_path, encoding="utf-8")

            finished_called = False

            def on_finished():
                nonlocal finished_called
                finished_called = True

            worker.finished.connect(on_finished)
            worker.run()

            assert finished_called
            assert os.path.exists(dst_path)

            with open(dst_path, encoding="utf-8") as f:
                content = f.read().splitlines()

            assert content == ["Line 0", "EDITED LINE 1", "Line 2", "Line 3"]

            # Upewnij się, że źródło pozostało nienaruszone
            with open(src_path, encoding="utf-8") as f:
                src_content = f.read().splitlines()
            assert src_content == ["Line 0", "Line 1", "Line 2", "Line 3"]

        finally:
            for p in (src_path, dst_path):
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except PermissionError:
                        pass


class TestExportWorker:
    def test_export_without_filter(self):
        src_path = create_temp_file(["Line 0", "Line 1", "Line 2"])
        dst_path = src_path + ".export"

        try:
            buf = EditBuffer()
            buf.set(2, "EDITED LINE 2")

            worker = ExportWorker(buf, src_path, dst_path, encoding="utf-8", filter_active=False)

            result_count = 0

            def on_finished(count):
                nonlocal result_count
                result_count = count

            worker.finished.connect(on_finished)
            worker.run()

            assert result_count == 3
            assert os.path.exists(dst_path)

            with open(dst_path, encoding="utf-8") as f:
                content = f.read().splitlines()

            assert content == ["Line 0", "Line 1", "EDITED LINE 2"]

        finally:
            for p in (src_path, dst_path):
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except PermissionError:
                        pass

    def test_export_with_filter_and_edits(self):
        src_path = create_temp_file(["Line 0", "ERROR at line 1", "Line 2", "ERROR at line 3"])
        dst_path = src_path + ".export"

        try:
            buf = EditBuffer()
            buf.set(1, "FIXED ERROR at line 1")

            import array

            from log_viewer.bitset import Bitset

            indices = array.array("Q", [1, 3])
            filter_results = Bitset.from_indices(indices, 4)

            worker = ExportWorker(
                buf, src_path, dst_path, encoding="utf-8", filter_active=True, filter_results=filter_results
            )

            result_count = 0

            def on_finished(count):
                nonlocal result_count
                result_count = count

            worker.finished.connect(on_finished)
            worker.run()

            assert result_count == 2
            assert os.path.exists(dst_path)

            with open(dst_path, encoding="utf-8") as f:
                content = f.read().splitlines()

            assert content == ["FIXED ERROR at line 1", "ERROR at line 3"]
        finally:
            for p in (src_path, dst_path):
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except PermissionError:
                        pass

    def test_export_with_filter_and_indexer_sparse(self):
        # Tworzymy 600 linii z duzymi przerwami pomiedzy dopasowaniami (>128 linii)
        raw_lines = [f"Line {i:04d} payload data" for i in range(600)]
        src_path = create_temp_file(raw_lines)
        dst_path = src_path + ".export"

        from log_viewer.bitset import Bitset
        from log_viewer.indexer import LineIndexer

        idx = LineIndexer(src_path, index_interval_bytes=256)
        try:
            buf = EditBuffer()
            buf.set(300, "EDITED LINE 300")

            # Wybieramy rzadkie linie: 5, 300 (zmieniona), 550
            targets = [5, 300, 550]
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

            result_count = 0

            def on_finished(count):
                nonlocal result_count
                result_count = count

            worker.finished.connect(on_finished)
            worker.run()

            assert result_count == 3
            assert os.path.exists(dst_path)

            with open(dst_path, encoding="utf-8") as f:
                content = f.read().splitlines()

            assert content == ["Line 0005 payload data", "EDITED LINE 300", "Line 0550 payload data"]
        finally:
            idx.close()
            for p in (src_path, dst_path):
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except PermissionError:
                        pass
