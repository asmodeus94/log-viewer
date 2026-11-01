"""Testy formatters.py — format_json, format_xml, format_log oraz integracja z log_generator."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from log_viewer.formatters import format_json, format_log, format_xml


def _load_log_generator_module():
    gen_path = Path(__file__).resolve().parent.parent / "dev-scripts" / "log_generator.py"
    spec = importlib.util.spec_from_file_location("log_generator", gen_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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

    def test_multiple_json_objects_in_single_line(self):
        input_text = '2026-11-01 INFO Request: {"req": 1} Response: {"res": 2} Done'
        expected_output = '2026-11-01 INFO Request:\n{\n    "req": 1\n}\nResponse:\n{\n    "res": 2\n}\nDone'
        assert format_json(input_text) == expected_output

    def test_pathological_braces_safety(self):
        # Tysiące otwierających nawiasów klamrowych nie mogą zawiesić parsera (limit prób)
        input_text = "{" * 5000 + " not a json"
        assert format_json(input_text) == input_text

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
        input_text = "<root><key>value</key></root>"
        expected_output = '<?xml version="1.0" ?>\n<root>\n    <key>value</key>\n</root>'
        assert format_xml(input_text) == expected_output

    def test_valid_xml_with_prefix_and_suffix(self):
        input_text = "2023-10-10 INFO: <root><key>value</key></root> [END]"
        expected_output = '2023-10-10 INFO:\n<?xml version="1.0" ?>\n<root>\n    <key>value</key>\n</root>\n[END]'
        assert format_xml(input_text) == expected_output

    def test_multiple_xml_blocks_in_single_line(self):
        input_text = "REQ: <a/> RESP: <b/> END"
        expected_output = 'REQ:\n<?xml version="1.0" ?>\n<a/>\nRESP:\n<?xml version="1.0" ?>\n<b/>\nEND'
        assert format_xml(input_text) == expected_output

    def test_invalid_xml(self):
        # Tutaj nie możemy użyć tagu, który sam w sobie jest prawidłowy np. <key>value</key>
        # wewnątrz nieprawidłowego, bo nasz parser wyłapie poprawny wewnętrzny tag i go sformatuje.
        input_text = "<root><key>value</root>"
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
        input_text = "<test/>"
        expected_output = '<?xml version="1.0" ?>\n<test/>'
        assert format_log(input_text, "XML") == expected_output

    def test_existing_formatter_failure(self):
        input_text = '{"key":"value"'
        assert format_log(input_text, "JSON") == input_text

    def test_non_existing_formatter(self):
        input_text = '{"key":"value"}'
        assert format_log(input_text, "UNKNOWN") == input_text


class TestLogGeneratorChallengeIntegration:
    """Weryfikuje, czy logi generowane przez ulepszony log_generator.py poprawnie przechodzą przez formatery."""

    def test_generated_json_xml_and_hybrid_challenge_lines(self, tmp_path: Path):
        mod = _load_log_generator_module()
        gen = mod.JsonLogGenerator

        # 1. Linia z dwoma strukturami JSON i fałszywymi tropami (decoys) w prefiksie
        json_line = gen.generate_formatted_line(
            4, fmt_mode="JSON", multi_structure=True, include_decoys=True, long_line=False
        )
        formatted_j = format_json(json_line)
        assert formatted_j != json_line
        assert "\n{\n" in formatted_j
        assert '"response_for_seq": 5' in formatted_j

        # 2. Linia z dwoma kopertami SOAP XML, komentarzami, CDATA, polskimi znakami i fałszywymi tropami
        xml_line = gen.generate_formatted_line(
            4, fmt_mode="XML", multi_structure=True, include_decoys=True, long_line=False
        )
        formatted_x = format_xml(xml_line)
        assert formatted_x != xml_line
        assert "<soap:Envelope" in formatted_x
        assert "<!--" in formatted_x
        assert "<![CDATA[" in formatted_x

        # 3. Linia hybrydowa z transformacją JSON -> XML (variant 2: i=2)
        hybrid_line = gen.generate_formatted_line(
            2, fmt_mode="HYBRID", multi_structure=True, include_decoys=True, long_line=False
        )
        assert format_json(hybrid_line) != hybrid_line
        assert format_xml(hybrid_line) != hybrid_line

        # 4. Bardzo długa linia (> 15 KB) z markerem wyszukiwania na samym końcu (i=14 -> is_long=True)
        long_line = gen.generate_formatted_line(
            14, fmt_mode="JSON", multi_structure=True, include_decoys=True, long_line=True
        )
        assert len(long_line) > 12_000
        assert "END_OF_LONG_LINE_MARKER_15" in long_line
        assert '"END_OF_LONG_LINE_MARKER_15"' in format_json(long_line)

        # 5. Zapis wsadowy (run_batch) w trybie HYBRID
        out_file = tmp_path / "challenge_batch.log"
        written = gen.run_batch(
            filepath=str(out_file),
            count=20,
            overwrite=True,
            fmt_mode="HYBRID",
            multi_structure=True,
            include_decoys=True,
            long_lines=True,
        )
        assert written == 20
        lines = out_file.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 20
