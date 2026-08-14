"""Testy i18n.py — parzystość kluczy PL/EN."""

from log_viewer.i18n import I18N


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
            "app_title",
            "menu_file",
            "menu_edit",
            "menu_view",
            "menu_goto",
            "mi_open",
            "mi_save",
            "mi_exit",
            "mi_find",
            "mi_filter",
            "mi_goto",
            "mi_goto_start",
            "mi_goto_end",
            "mi_follow",
            "mi_about",
            "mi_settings",
            "mi_next_tab",
            "mi_prev_tab",
            "mi_close_tab",
            "mi_refresh",
            "mi_reload",
            "st_ready",
            "st_indexing",
            "st_done",
            "st_refreshing",
            "btn_refresh",
            "btn_reload",
            "msg_no_file",
            "msg_about",
            "btn_prev_line",
            "btn_next_line",
        ]
        for key in required:
            assert key in I18N["pl"], f"Missing PL key: {key}"
            assert key in I18N["en"], f"Missing EN key: {key}"

    def test_thousands_separator_formatting(self):
        """Sprawdź czy szablony i18n prawidłowo formatują liczby > 999 z separatorem tysięcy."""
        msg_pl = I18N["pl"]["st_filtering"].format(pct="50.0", hits=1250)
        assert "1,250" in msg_pl

        msg_en = I18N["en"]["st_filtering"].format(pct="50.0", hits=1250)
        assert "1,250" in msg_en

        msg_sp_pl = I18N["pl"]["st_searching_progress"].format(pct="50.0", hits=1250)
        assert "1,250" in msg_sp_pl

        msg_sp_en = I18N["en"]["st_searching_progress"].format(pct="50.0", hits=1250)
        assert "1,250" in msg_sp_en

        msg_edits = I18N["pl"]["st_edits"].format(n=1000)
        assert "1,000" in msg_edits

        msg_search = I18N["pl"]["lbl_search_results_count"].format(n=5000, current=1, total=5000)
        assert "5,000" in msg_search

        msg_bm = I18N["pl"]["msg_bookmark_added"].format(n=12345)
        assert "12,345" in msg_bm
