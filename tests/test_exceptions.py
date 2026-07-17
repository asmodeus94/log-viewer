"""Testy exceptions.py — FileChangedError, CompressedSaveError."""
import pytest
from log_reader.exceptions import FileChangedError, CompressedSaveError


class TestExceptions:
    def test_file_changed_error_is_exception(self):
        err = FileChangedError("test")
        assert isinstance(err, Exception)
        assert str(err) == "test"

    def test_compressed_save_error_is_exception(self):
        err = CompressedSaveError("test")
        assert isinstance(err, Exception)
        assert str(err) == "test"

    def test_file_changed_error_can_be_raised(self):
        with pytest.raises(FileChangedError):
            raise FileChangedError("file changed")

    def test_compressed_save_error_can_be_raised(self):
        with pytest.raises(CompressedSaveError):
            raise CompressedSaveError("compressed")

    def test_exceptions_are_distinct(self):
        assert FileChangedError is not CompressedSaveError
        assert not issubclass(FileChangedError, CompressedSaveError)
        assert not issubclass(CompressedSaveError, FileChangedError)
