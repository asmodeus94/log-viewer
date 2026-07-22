import json
import re
from typing import Tuple, Optional
import defusedxml.minidom

def extract_json(text: str) -> Tuple[str, str, str, bool]:
    """
    Próbuje znaleźć pierwszy prawidłowy obiekt lub listę JSON w tekście.
    Zwraca krotkę: (prefix, json_text, suffix, czy_znaleziono)
    """
    decoder = json.JSONDecoder()

    # Szukamy pierwszego znaku, który może być początkiem JSONa ({ lub [)
    for i, char in enumerate(text):
        if char in ('{', '['):
            try:
                # raw_decode próbuje sparsować JSON od podanego indeksu i zwraca dane oraz pozycję końcową
                data, end_idx = decoder.raw_decode(text[i:])

                # Upewniamy się, że znaleziony obiekt jest strukturą (dict lub list)
                if isinstance(data, (dict, list)):
                    prefix = text[:i]
                    json_text = text[i:i+end_idx]
                    suffix = text[i+end_idx:]
                    return prefix, json_text, suffix, True
            except json.JSONDecodeError:
                continue # Próbujemy dalej, może to nie był właściwy początek

    return text, "", "", False

def format_json(text: str) -> str:
    """
    Próbuje wyodrębnić JSON z tekstu, zachowując ewentualny tekst przed i po.
    Zwraca sformatowany log lub oryginalny tekst w przypadku niepowodzenia.
    """
    prefix, json_text, suffix, found = extract_json(text)

    if not found:
        return text

    try:
        data = json.loads(json_text)
        formatted_json = json.dumps(data, indent=4, ensure_ascii=False)

        result = []
        if prefix.strip():
            result.append(prefix.strip())
        result.append(formatted_json)
        if suffix.strip():
            result.append(suffix.strip())

        return "\n".join(result)
    except Exception:
        return text

def extract_xml(text: str) -> Tuple[str, str, str, bool]:
    """
    Próbuje znaleźć pierwszy prawidłowy blok XML w tekście.
    Zwraca krotkę: (prefix, xml_text, suffix, czy_znaleziono)
    """
    # Szybka ścieżka za pomocą wyrażenia regularnego, aby znaleźć kandydatów
    # Szukamy <tag ...> ... </tag> lub <tag .../>
    # Wyrażenie nie jest perfekcyjne (nie radzi sobie z zagłębieniami tak samo nazwanych tagów,
    # ale używamy minidom do walidacji)

    # Najpierw spróbujmy prościej - iterujemy po każdym potencjalnym początku '<'
    # i próbujemy sparsować XML od tego miejsca używając defusedxml.

    for i, char in enumerate(text):
        if char == '<':
            # XML musi zamykać się '>', więc szukamy od tyłu do przodu dla tego samego początkowego indeksu
            for j in range(len(text), i, -1):
                if text[j-1] == '>':
                    candidate = text[i:j]

                    # Ignorujemy błahe przypadki np. pojedynczy tag bez atrybutów jeśli szukamy struktury
                    if len(candidate) < 5:
                        continue

                    try:
                        # Walidujemy kandydata parserem XML
                        defusedxml.minidom.parseString(candidate)

                        prefix = text[:i]
                        suffix = text[j:]
                        return prefix, candidate, suffix, True
                    except Exception:
                        # Może jest źle sformułowany, zmniejszamy j (odrzucamy końcówkę)
                        continue

    return text, "", "", False

def format_xml(text: str) -> str:
    """
    Próbuje wyodrębnić XML z tekstu, zachowując ewentualny tekst przed i po.
    Zwraca sformatowany log lub oryginalny tekst w przypadku niepowodzenia.
    """
    prefix, xml_text, suffix, found = extract_xml(text)

    if not found:
        return text

    try:
        dom = defusedxml.minidom.parseString(xml_text)
        formatted_xml = dom.toprettyxml(indent="    ")

        # toprettyxml lubi dodawać dużo pustych linii, jeśli wejście miało białe znaki między tagami
        # Oczyszczamy to
        formatted_xml = "\n".join([line for line in formatted_xml.splitlines() if line.strip()])

        result = []
        if prefix.strip():
            result.append(prefix.strip())
        result.append(formatted_xml)
        if suffix.strip():
            result.append(suffix.strip())

        return "\n".join(result)
    except Exception:
        return text

FORMATTERS = {
    "JSON": format_json,
    "XML": format_xml,
}

def format_log(text: str, formatter_name: str) -> str:
    """
    Formatuje tekst używając wybranego formatera.
    Jeśli formater zawiedzie lub nie jest zdefiniowany, zwraca oryginalny tekst.
    """
    formatter = FORMATTERS.get(formatter_name)
    if formatter:
        return formatter(text)
    return text
