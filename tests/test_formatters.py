"""Testy formatters.py — format_json, format_xml, format_log."""
from log_reader.formatters import format_json, format_xml, format_log

class TestFormatJson:
    def test_valid_json(self):
        input_text = '{"key":"value"}'
        expected_output = '{\n    "key": "value"\n}'
        assert format_json(input_text) == expected_output

    def test_valid_json_with_polish_chars(self):
        input_text = '{"klucz":"wartość"}'
        expected_output = '{\n    "klucz": "wartość"\n}'
        assert format_json(input_text) == expected_output

    def test_valid_json_with_prefix_and_suffix(self):
        input_text = '2023-10-10 INFO: {"key":"value"} [END]'
        expected_output = '2023-10-10 INFO:\n{\n    "key": "value"\n}\n[END]'
        assert format_json(input_text) == expected_output

    def test_invalid_json(self):
        input_text = '{"key":"value"'
        assert format_json(input_text) == input_text

    def test_empty_string(self):
        assert format_json("") == ""

    def test_not_a_json(self):
        input_text = "To nie jest json."
        assert format_json(input_text) == input_text


class TestFormatXml:
    def test_valid_xml(self):
        input_text = '<root><key>value</key></root>'
        expected_output = '<?xml version="1.0" ?>\n<root>\n    <key>value</key>\n</root>'
        assert format_xml(input_text) == expected_output

    def test_valid_xml_with_prefix_and_suffix(self):
        input_text = '2023-10-10 INFO: <root><key>value</key></root> [END]'
        expected_output = '2023-10-10 INFO:\n<?xml version="1.0" ?>\n<root>\n    <key>value</key>\n</root>\n[END]'
        assert format_xml(input_text) == expected_output

    def test_invalid_xml(self):
        # Tutaj nie możemy użyć tagu, który sam w sobie jest prawidłowy np. <key>value</key>
        # wewnątrz nieprawidłowego, bo nasz parser wyłapie poprawny wewnętrzny tag i go sformatuje.
        input_text = '<root><key>value</root>'
        assert format_xml(input_text) == input_text

    def test_empty_string(self):
        assert format_xml("") == ""

    def test_not_a_xml(self):
        input_text = "To nie jest xml."
        assert format_xml(input_text) == input_text


class TestFormatLog:
    def test_existing_formatter_json(self):
        input_text = '{"key":"value"}'
        expected_output = '{\n    "key": "value"\n}'
        assert format_log(input_text, "JSON") == expected_output

    def test_existing_formatter_xml(self):
        input_text = '<test/>'
        expected_output = '<?xml version="1.0" ?>\n<test/>'
        assert format_log(input_text, "XML") == expected_output

    def test_existing_formatter_failure(self):
        input_text = '{"key":"value"'
        assert format_log(input_text, "JSON") == input_text

    def test_non_existing_formatter(self):
        input_text = '{"key":"value"}'
        assert format_log(input_text, "UNKNOWN") == input_text
