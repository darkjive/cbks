import re
from pathlib import Path
from typing import Optional

import fitz

from backend.services.vision import VLMClient, render_pdf_pages

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def parse_frontmatter(content: str) -> tuple[str, dict[str, str]]:
    """Trennt YAML-Frontmatter (--- ... ---) vom Body. Liefert (body, mapping).

    Bewusst keine yaml-Abhängigkeit: das Obsidian-Frontmatter in den Vault-Notizen
    ist flach (key: value, teils Listen als []), ein Zeilenparser reicht.
    """
    if not content:
        return "", {}
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return content, {}
    body = content[match.end():]
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip("'\"")
        if value.startswith("[") and value.endswith("]"):
            value = value[1:-1].strip()
        meta[key.strip()] = value
    return body.strip(), meta


_GERMAN_STOPWORDS = frozenset({
    "der", "die", "das", "und", "ist", "von", "mit", "für", "im", "auf",
    "den", "dem", "des", "ein", "eine", "nicht", "auch", "sich", "sie",
    "wir", "als", "bei", "aus", "wird", "werden", "war", "sind", "zu",
    "in", "an", "am", "vom", "zum", "zur", "er", "es", "oder", "durch",
})
_MIXED_ALNUM_RE = re.compile(r"^(?=.*[A-Za-zÄÖÜäöüß])(?=.*\d).+$")
_CLEAN_NUMERIC_RE = re.compile(r"^[+\-]?\d{1,4}([.\-/:]\d{1,4}){1,4}$")
_MIN_WORDS_FOR_GARBLE_CHECK = 30


def _looks_garbled(text: str) -> bool:
    """Erkennt Text mit kaputtem PDF-Font-CMap (Ligatur-Encoding-Bug).

    Solche PDFs mischen bei der Embedded-Text-Extraktion Ziffern in Wörter
    ("6d,brl", "O1.12.?O12"), was in normalem Fließtext praktisch nicht
    vorkommt. Bei zu kurzem Text ist die Heuristik unzuverlässig.
    """
    words = text.split()
    if len(words) < _MIN_WORDS_FOR_GARBLE_CHECK:
        return False
    stripped = [w.strip(".,;:!?()[]{}\"'") for w in words]
    mixed = sum(
        1 for w in stripped
        if _MIXED_ALNUM_RE.match(w) and not _CLEAN_NUMERIC_RE.match(w)
    )
    if mixed / len(words) > 0.02:
        return True
    stopword_ratio = sum(1 for w in stripped if w.lower() in _GERMAN_STOPWORDS) / len(words)
    return stopword_ratio < 0.01


def _parse_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_pdf(path: Path, vlm_client: Optional[VLMClient] = None) -> str:
    doc = fitz.open(str(path))
    try:
        text = "\n".join(page.get_text() for page in doc).strip()
    finally:
        doc.close()
    # Eingebetteter Text vorhanden und unauffällig → direkt verwenden (schnell, keine VLM-Kosten).
    if text and not _looks_garbled(text):
        return text
    # Kein VLM verfügbar: kaputter/fehlender Text ist besser als gar nichts.
    if vlm_client is None:
        return text
    pages = render_pdf_pages(path)
    ocr_text = "\n\n".join(vlm_client.extract_text(page) for page in pages).strip()
    return ocr_text or text


def _parse_image(path: Path, vlm_client: Optional[VLMClient]) -> str:
    if vlm_client is None:
        raise ValueError("Bild-OCR benötigt einen VLM-Client (keiner konfiguriert)")
    return vlm_client.extract_text(path.read_bytes())


def parse_file(path: Path, vlm_client: Optional[VLMClient] = None) -> str:
    suffix = path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return _parse_image(path, vlm_client)
    if suffix == ".pdf":
        return _parse_pdf(path, vlm_client)
    if suffix in (".md", ".markdown"):
        return _parse_markdown(path)
    raise ValueError(f"Nicht unterstützte Dateiendung: {suffix}")
