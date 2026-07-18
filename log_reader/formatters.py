import json

def format_json(text: str) -> str:
    """Próbuje sparsować tekst jako JSON i zwrócić go w ładnym formacie."""
    try:
        data = json.loads(text)
        return json.dumps(data, indent=4, ensure_ascii=False)
    except Exception:
        return ""

FORMATTERS = {
    "JSON": format_json,
}

def format_log(text: str, formatter_name: str) -> str:
    """
    Formatuje tekst używając wybranego formatera.
    Jeśli formater zawiedzie lub nie jest zdefiniowany, zwraca pusty ciąg.
    """
    formatter = FORMATTERS.get(formatter_name)
    if formatter:
        return formatter(text)
    return ""
