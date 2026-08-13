import re

# Best-effort Regex-Extraktion für klar beschriftete Felder ("Label: Wert").
# Deckt keine Layouts ab, in denen Label und Wert ohne Trenner nah beieinander
# stehen (z.B. OCR-Text von fotografierten Karten) – dafür braucht es die
# LLM-Entity-Extraktion in prefrontal.py.
_FIELD_PATTERNS: dict[str, re.Pattern] = {
    "iban": re.compile(
        r"IBAN[ \t]*[:\-]?[ \t]*([A-Z]{2}\d{2}[A-Z0-9 \t]{10,30}[A-Z0-9])", re.IGNORECASE
    ),
    "mitgliedsnummer": re.compile(
        r"Mitglieds(?:-?nr\.?|nummer)\s*[:\-]?\s*(\S{3,20})", re.IGNORECASE
    ),
    "kundennummer": re.compile(
        r"Kunden(?:-?nr\.?|nummer)\s*[:\-]?\s*(\S{3,20})", re.IGNORECASE
    ),
    "versicherungsnummer": re.compile(
        r"Versich(?:erungs|erten)(?:-?nr\.?|nummer)\s*[:\-]?\s*(\S{3,20})", re.IGNORECASE
    ),
    "geburtsdatum": re.compile(
        r"(?:Geburtsdatum|geboren am)\s*[:\-]?\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", re.IGNORECASE
    ),
    "telefon": re.compile(
        r"(?:Tel\.?|Telefon)\s*[:\-]?\s*(\+?\d[\d /\-]{5,20}\d)", re.IGNORECASE
    ),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
}


def extract_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name, pattern in _FIELD_PATTERNS.items():
        match = pattern.search(text)
        if match:
            value = match.group(1) if match.groups() else match.group(0)
            fields[name] = value.strip()
    return fields
