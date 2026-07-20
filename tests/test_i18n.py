"""Testy i18n.py — parzystość kluczy PL/EN."""
from log_reader.i18n import I18N


class TestI18N:
    def test_pl_en_key_parity(self):
        pl_keys = set(I18N["pl"].keys())
        en_keys = set(I18N["en"].keys())
        assert pl_keys == en_keys, f"Key mismatch: PL only: {pl_keys - en_keys}, EN only: {en_keys - pl_keys}"

    def test_pl_has_all_keys(self):
        assert len(I18N["pl"]) >= 90  # powinno być ~93

    def test_en_has_all_keys(self):
        assert len(I18N["en"]) >= 90

    def test_no_empty_values_pl(self):
        for key, value in I18N["pl"].items():
            assert value, f"Empty value for key '{key}' in PL"

    def test_no_empty_values_en(self):
        for key, value in I18N["en"].items():
            assert value, f"Empty value for key '{key}' in EN"

    def test_known_keys_exist(self):
        """Sprawdź kluczowe klucze które muszą istnieć."""
        required = [
            "app_title", "menu_file", "menu_edit", "menu_view", "menu_goto",
            "mi_open", "mi_save", "mi_exit", "mi_find", "mi_filter",
            "mi_goto", "mi_goto_start", "mi_goto_end", "mi_follow", "mi_about", "mi_settings",
            "mi_next_tab", "mi_prev_tab", "mi_close_tab",
            "st_ready", "st_indexing", "st_done",
            "msg_no_file", "msg_about",
        ]
        for key in required:
            assert key in I18N["pl"], f"Missing PL key: {key}"
            assert key in I18N["en"], f"Missing EN key: {key}"
