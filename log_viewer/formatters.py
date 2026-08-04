import re
import json
from typing import Tuple, Optional
import defusedxml.minidom
import defusedxml.expatreader
import xml.sax

def extract_json(text: str) -> Tuple[str, str, str, bool]:
    """
    Próbuje znaleźć pierwszy prawidłowy obiekt lub listę JSON w tekście.
    Zwraca krotkę: (prefix, json_text, suffix, czy_znaleziono)
    """
    decoder = json.JSONDecoder()

    # Szukamy pierwszego znaku, który może być początkiem JSONa ({ lub [)
    i = 0

    while True:
        idx1 = text.find('{', i)
        idx2 = text.find('[', i)

        if idx1 == -1:
            i = idx2
        elif idx2 == -1:
            i = idx1
        else:
            i = idx1 if idx1 < idx2 else idx2

        if i == -1:
            break

        try:
            # Używamy argumentu idx w raw_decode dla lepszej wydajności w przypadkach długich ciągów znaków
            data, end_idx = decoder.raw_decode(text, i)

            # Upewniamy się, że znaleziony obiekt jest strukturą (dict lub list)
            if isinstance(data, (dict, list)):
                prefix = text[:i]
                json_text = text[i:end_idx]
                suffix = text[end_idx:]
                return prefix, json_text, suffix, True
        except json.JSONDecodeError:
            pass # Próbujemy dalej, może to nie był właściwy początek

        i += 1

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
    # Szukamy pierwszego znacznika XML, może to być deklaracja <?xml... lub tag <nazwa...
    for start_match in re.finditer(r'<([a-zA-Z_][\w:.-]*|\?xml)', text):
        i = start_match.start()
        tag_name = start_match.group(1)

        remaining_text = text[i:]

        # Szybka heurystyka: przed uruchomieniem wolnego parsera expat sprawdzamy,
        # czy w ogóle istnieje potencjalne zamknięcie tagu.
        if tag_name == '?xml':
            if '?>' not in remaining_text:
                continue
        else:
            closing_tag = f"</{tag_name}>"
            if closing_tag not in remaining_text and '/>' not in remaining_text:
                continue

        # Kodujemy ciąg do bajtów, aby uniknąć problemów ze wskaźnikami przesunięcia bajtów w parserze C (expat)
        # dla znaków wielobajtowych (np. polskich znaków) i błędów obsługi wieloliniowych ciągów.
        encoded_text = remaining_text.encode('utf-8')

        parser = defusedxml.expatreader.create_parser()
        try:
            parser.feed(encoded_text)
            parser.close()
            # Jeśli sparsuje całą resztę wejścia bez błędu, cały pozostały string jest prawidłowym XMLem
            return text[:i], remaining_text, "", True
        except xml.sax.SAXParseException as e:
            inner = e.getException()
            # 9 to kod dla XML_ERROR_JUNK_AFTER_DOC_ELEMENT
            if inner and getattr(inner, 'code', None) == 9:
                # W expat inner.offset to kolumna, dla wielu linii bywa zawodne.
                # Do ucięcia bajtów wykorzystujemy bezwzględny ErrorByteIndex.
                byte_offset = getattr(parser._parser, 'ErrorByteIndex', None)
                if byte_offset is not None:
                    # Wyodrębniamy podciąg w bajtach i dekodujemy na znaki
                    candidate_bytes = encoded_text[:byte_offset]
                    try:
                        candidate = candidate_bytes.decode('utf-8')

                        # Expat uznaje białe znaki po dokumencie za część dokumentu.
                        candidate_stripped = candidate.rstrip()
                        if candidate_stripped.endswith('>'):
                            # Podwójne sprawdzenie kandydata
                            try:
                                p2 = defusedxml.expatreader.create_parser()
                                p2.feed(candidate_stripped.encode('utf-8'))
                                p2.close()
                                return text[:i], candidate_stripped, text[i+len(candidate_stripped):], True
                            except Exception:
                                pass
                    except UnicodeDecodeError:
                        pass
            # Jeśli inny błąd parsowania (np. niezamknięty tag), kontynuujemy szukanie

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
