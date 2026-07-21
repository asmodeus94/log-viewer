"""Testy edit_buffer.py — EditBuffer, save_to_file, FileChangedError, CompressedSaveError."""
import os
import gzip
import stat
import time
import pytest
from log_reader.edit_buffer import EditBuffer
from log_reader.exceptions import FileChangedError, CompressedSaveError


class TestEditBuffer:
    def test_basic_operations(self):
        buf = EditBuffer()
        assert len(buf) == 0
        buf.set(10, "EDITED LINE 10")
        assert len(buf) == 1
        assert buf.has(10)
        assert not buf.has(11)
        assert buf.get(10) == "EDITED LINE 10"
        buf.discard(10)
        assert len(buf) == 0
        assert not buf.has(10)

    def test_clear(self):
        buf = EditBuffer()
        buf.set(1, "a")
        buf.set(2, "b")
        buf.set(3, "c")
        assert len(buf) == 3
        buf.clear()
        assert len(buf) == 0

    def test_items(self):
        buf = EditBuffer()
        buf.set(5, "five")
        buf.set(10, "ten")
        items = dict(buf.items())
        assert items == {5: "five", 10: "ten"}


class TestEditBufferSave:
    def test_save_basic(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        buf = EditBuffer()
        buf.set(10, "EDITED LINE 10")
        buf.set(50, "EDITED LINE 50")
        backup = buf.save_to_file(path)
        assert os.path.exists(backup)
        with open(path, "rb") as f:
            lines = f.readlines()
        assert b"EDITED LINE 10" in lines[10]
        assert b"EDITED LINE 50" in lines[50]
        try:
            os.unlink(backup)
        except PermissionError:
            pass

    def test_save_preserves_permissions(self, temp_log_file):
        path = temp_log_file(num_lines=100)
        os.chmod(path, 0o640)
        original_mode = stat.S_IMODE(os.stat(path).st_mode)
        buf = EditBuffer()
        buf.set(5, "EDITED")
        buf.save_to_file(path)
        new_mode = stat.S_IMODE(os.stat(path).st_mode)
        assert new_mode == original_mode

    def test_save_preserves_exec_bit(self, temp_log_file):
        path = temp_log_file(num_lines=100)
        os.chmod(path, 0o755)
        original_mode = stat.S_IMODE(os.stat(path).st_mode)
        buf = EditBuffer()
        buf.set(5, "EDITED")
        buf.save_to_file(path)
        new_mode = stat.S_IMODE(os.stat(path).st_mode)
        assert new_mode == original_mode

    def test_backup_file_permissions_secure(self, temp_log_file):
        """Weryfikuje, że w najgorszym wypadku (np. przy awarii save lub przerywaniu) backup
        został otwarty w sposób bezpieczny. Sprawdzamy to nadając źródłu konkretne restrykcyjne prawa."""
        path = temp_log_file(num_lines=10)
        # Nadajemy źródłu prawa 0o600. Z racji, że save_to_file wywołuje copystat
        # przenosząc metadane ze źródła, oczekujemy, że ostateczny backup również nie będzie
        # miał szerszych uprawnień. Jednak najważniejsze jest to, że fix chroni przed domyślnymi
        # uprawnieniami (z umaska) przed wykonaniem copystat.
        os.chmod(path, 0o600)

        buf = EditBuffer()
        buf.set(1, "EDITED")
        backup = buf.save_to_file(path)

        # Pobierz rzeczywiste uprawnienia pliku backup (tylko bity rw-rw-rw-)
        mode = stat.S_IMODE(os.stat(backup).st_mode)

        # Na Linux/Mac OS ostateczny plik nie powinien mieć uprawnień odczytu dla grupy/innych.
        if os.name != 'nt':
            assert not (mode & stat.S_IRWXG)  # brak uprawnień dla grupy
            assert not (mode & stat.S_IRWXO)  # brak uprawnień dla innych

        try:
            os.unlink(backup)
        except PermissionError:
            pass

    def test_save_preserves_mtime(self, temp_log_file):
        path = temp_log_file(num_lines=100)
        old_time = time.time() - 86400
        os.utime(path, (old_time, old_time))
        original_mtime = os.stat(path).st_mtime
        buf = EditBuffer()
        buf.set(5, "EDITED")
        time.sleep(0.1)
        buf.save_to_file(path)
        new_mtime = os.stat(path).st_mtime
        assert abs(new_mtime - original_mtime) < 1.0


class TestEditBufferFileChanged:
    def test_blocks_on_size_change(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        buf = EditBuffer()
        buf.set(5, "EDITED")
        with open(path, "ab") as f:
            f.write(b"appended\n")
        with pytest.raises(FileChangedError):
            buf.save_to_file(path, expected_size=original_size)

    def test_blocks_on_mtime_change(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        original_mtime = os.stat(path).st_mtime
        original_size = os.path.getsize(path)
        buf = EditBuffer()
        buf.set(5, "EDITED")
        time.sleep(1.1)
        new_mtime = time.time()
        os.utime(path, (new_mtime, new_mtime))
        with pytest.raises(FileChangedError):
            buf.save_to_file(path, expected_mtime=original_mtime, expected_size=original_size)

    def test_no_backup_on_blocked_save(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        buf = EditBuffer()
        buf.set(5, "EDITED")
        with open(path, "ab") as f:
            f.write(b"appended\n")
        with pytest.raises(FileChangedError):
            buf.save_to_file(path, expected_size=original_size)
        assert not os.path.exists(path + ".bak")

    def test_allows_when_unchanged(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        original_mtime = os.stat(path).st_mtime
        buf = EditBuffer()
        buf.set(5, "EDITED LINE 5")
        backup = buf.save_to_file(path, expected_size=original_size, expected_mtime=original_mtime)
        assert os.path.exists(backup)
        try:
            os.unlink(backup)
        except PermissionError:
            pass

    def test_allows_without_validation(self, temp_log_file):
        path = temp_log_file(num_lines=1000)
        buf = EditBuffer()
        buf.set(5, "EDITED")
        with open(path, "ab") as f:
            f.write(b"appended\n")
        # Bez expected_size/mtime — zapis powinien zadziałać
        backup = buf.save_to_file(path)
        assert os.path.exists(backup)
        try:
            os.unlink(backup)
        except PermissionError:
            pass

    def test_mtime_exact_match_required(self, temp_log_file):
        """Po naprawie #3 (Gemini Pro) — brak tolerancji mtime.
        Zmiana mtime nawet o 0.5s powinna być wykryta."""
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        original_mtime = os.stat(path).st_mtime
        buf = EditBuffer()
        buf.set(5, "EDITED")
        # Zmień mtime o 0.5s — powinno być wykryte (brak tolerancji)
        new_mtime = original_mtime + 0.5
        os.utime(path, (new_mtime, new_mtime))
        with pytest.raises(FileChangedError):
            buf.save_to_file(path, expected_size=original_size, expected_mtime=original_mtime)

    def test_encoding_passed_to_save(self, temp_log_file):
        """Po naprawie #1 (Gemini Pro) — save_to_file używa podanego kodowania."""
        path = temp_log_file(num_lines=1000)
        buf = EditBuffer()
        # Edytuj linię z polskimi znakami
        buf.set(5, "zażółć gęślą jaźń")
        backup = buf.save_to_file(path, encoding="utf-8")
        # Sprawdź że polskie znaki są poprawnie zapisane w UTF-8
        with open(path, "rb") as f:
            lines = f.readlines()
        assert "zażółć gęślą jaźń".encode("utf-8") in lines[5]
        try:
            os.unlink(backup)
        except PermissionError:
            pass

    def test_encoding_latin1(self, temp_log_file):
        """Save z encoding=latin-1 zapisuje znaki Latin-1."""
        path = temp_log_file(num_lines=1000)
        buf = EditBuffer()
        # Użyj znaków które istnieją w Latin-1 (é, ü, ñ)
        buf.set(5, "café résumé niño")
        backup = buf.save_to_file(path, encoding="latin-1")
        # Sprawdź zapis w Latin-1
        with open(path, "rb") as f:
            lines = f.readlines()
        assert "café résumé niño".encode("latin-1") in lines[5]
        try:
            os.unlink(backup)
        except PermissionError:
            pass


class TestEditBufferCompressed:
    def test_blocks_compressed_save(self):
        import tempfile
        path = tempfile.mktemp(suffix=".gz")
        try:
            with gzip.open(path, "wb") as f:
                f.write(b"line 1\nline 2\n")
            buf = EditBuffer()
            buf.set(0, "EDITED")
            with pytest.raises(CompressedSaveError):
                buf.save_to_file(path)
        finally:
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except PermissionError:
                    pass

class TestMtimeNsTolerance:
    """Testy tolerancji nanosekundowej dla st_mtime_ns."""

    def test_allows_when_unchanged(self, temp_log_file):
        """Zapis powinien przejść gdy plik nie zmienił się (st_mtime_ns)."""
        path = temp_log_file(num_lines=1000)
        st = os.stat(path)
        original_mtime_ns = st.st_mtime_ns
        original_size = st.st_size
        buf = EditBuffer()
        buf.set(5, "EDITED LINE 5")
        backup = buf.save_to_file(path, expected_size=original_size, expected_mtime=original_mtime_ns)
        assert os.path.exists(backup)
        try:
            os.unlink(backup)
        except PermissionError:
            pass

    def test_blocks_on_real_mtime_change(self, temp_log_file):
        """Zapis zablokowany gdy mtime zmienił się o >1ms."""
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        original_mtime_ns = os.stat(path).st_mtime_ns
        buf = EditBuffer()
        buf.set(5, "EDITED")
        # Zmień mtime o 2 sekundy (na pewno >1ms)
        new_mtime = time.time() + 2.0
        os.utime(path, (new_mtime, new_mtime))
        with pytest.raises(FileChangedError):
            buf.save_to_file(path, expected_size=original_size, expected_mtime=original_mtime_ns)

    def test_tolerates_sub_ms_ns_drift(self, temp_log_file):
        """Tolerancja dla różnicy <1ms (filesystem zaokrągla ns)."""
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        original_mtime_ns = os.stat(path).st_mtime_ns
        buf = EditBuffer()
        buf.set(5, "EDITED")
        # Symuluj drift 500_000 ns (0.5ms) — w tolerancji
        fake_mtime_ns = original_mtime_ns + 500_000
        backup = buf.save_to_file(path, expected_size=original_size, expected_mtime=fake_mtime_ns)
        assert os.path.exists(backup)
        try:
            os.unlink(backup)
        except PermissionError:
            pass

    def test_blocks_on_2ms_drift(self, temp_log_file):
        """Blokada przy różnicy 2ms (poza tolerancją)."""
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        original_mtime_ns = os.stat(path).st_mtime_ns
        buf = EditBuffer()
        buf.set(5, "EDITED")
        # Symuluj drift 2_000_000 ns (2ms) — poza tolerancją
        fake_mtime_ns = original_mtime_ns + 2_000_000
        with pytest.raises(FileChangedError):
            buf.save_to_file(path, expected_size=original_size, expected_mtime=fake_mtime_ns)

    def test_accepts_float_mtime(self, temp_log_file):
        """Kompatybilność wsteczna — expected_mtime jako float (stare wersje)."""
        path = temp_log_file(num_lines=1000)
        original_size = os.path.getsize(path)
        original_mtime_float = os.stat(path).st_mtime  # float, nie ns
        buf = EditBuffer()
        buf.set(5, "EDITED")
        # float → konwersja na ns w edit_buffer.py
        backup = buf.save_to_file(path, expected_size=original_size, expected_mtime=original_mtime_float)
        assert os.path.exists(backup)
        try:
            os.unlink(backup)
        except PermissionError:
            pass
