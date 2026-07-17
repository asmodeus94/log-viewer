"""Testy config.py — UserConfig, save/load, defaults, type safety."""
import os
import json
import pytest
from log_reader.config import UserConfig


class TestUserConfig:
    def test_defaults(self, temp_config_path):
        cfg = UserConfig(config_path=temp_config_path)
        assert cfg.get("language") == "pl"
        assert cfg.get("encoding") == "utf-8"
        assert cfg.get("font_size") == 10
        assert cfg.get("font_family") is None

    def test_save_and_load(self, temp_config_path):
        cfg1 = UserConfig(config_path=temp_config_path)
        cfg1.set("language", "en")
        cfg1.set("encoding", "latin-1")
        cfg1.set("font_size", 12)
        cfg2 = UserConfig(config_path=temp_config_path)
        assert cfg2.get("language") == "en"
        assert cfg2.get("encoding") == "latin-1"
        assert cfg2.get("font_size") == 12

    def test_corrupted_file(self, temp_config_path):
        with open(temp_config_path, "w") as f:
            f.write("{ this is not valid json")
        cfg = UserConfig(config_path=temp_config_path)
        assert cfg.get("language") == "pl"
        assert cfg.get("encoding") == "utf-8"

    def test_atomic_save(self, temp_config_path):
        cfg = UserConfig(config_path=temp_config_path)
        cfg.set("language", "en")
        assert os.path.exists(temp_config_path)
        with open(temp_config_path, "r") as f:
            data = json.load(f)
        assert data["language"] == "en"

    def test_type_safety(self, temp_config_path):
        with open(temp_config_path, "w") as f:
            json.dump({
                "language": "pl",
                "font_size": "not a number",
                "encoding": "utf-8",
            }, f)
        cfg = UserConfig(config_path=temp_config_path)
        assert cfg.get("font_size") == 10  # default, bo zły typ

    def test_font_family_none_accepts_string(self, temp_config_path):
        """font_family=None w defaults — akceptuje string z pliku."""
        cfg = UserConfig(config_path=temp_config_path)
        cfg.set("font_family", "Courier New")
        cfg2 = UserConfig(config_path=temp_config_path)
        assert cfg2.get("font_family") == "Courier New"

    def test_nonexistent_file_uses_defaults(self, temp_config_path):
        # Nie twórz pliku
        cfg = UserConfig(config_path=temp_config_path)
        assert cfg.get("language") == "pl"
        assert cfg.get("font_size") == 10
