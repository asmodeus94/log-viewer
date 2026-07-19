"""Testy formatters.py — format_json, format_log."""
from log_reader.formatters import format_json, format_log

class TestFormatJson:
    def test_valid_json(self):
        input_text = '{"key":"value"}'
        expected_output = '{\n    "key": "value"\n}'
        assert format_json(input_text) == expected_output

    def test_valid_json_with_polish_chars(self):
        input_text = '{"klucz":"wartość"}'
        expected_output = '{\n    "klucz": "wartość"\n}'
        assert format_json(input_text) == expected_output

    def test_invalid_json(self):
        input_text = '{"key":"value"'
        assert format_json(input_text) == ""

    def test_empty_string(self):
        assert format_json("") == ""

    def test_not_a_json(self):
        input_text = "To nie jest json."
        assert format_json(input_text) == ""


class TestFormatLog:
    def test_existing_formatter(self):
        input_text = '{"key":"value"}'
        expected_output = '{\n    "key": "value"\n}'
        assert format_log(input_text, "JSON") == expected_output

    def test_existing_formatter_failure(self):
        input_text = '{"key":"value"'
        assert format_log(input_text, "JSON") == ""

    def test_non_existing_formatter(self):
        input_text = '{"key":"value"}'
        assert format_log(input_text, "XML") == ""
