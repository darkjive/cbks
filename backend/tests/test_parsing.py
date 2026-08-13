import fitz
import pytest

from backend.services.parsing import _looks_garbled, parse_file, parse_frontmatter

# Synthetischer Auszug im Stil eines PDFs mit kaputtem Font-CMap
# (Ligatur-Encoding-Bug): Ziffern werden in Wörter gemischt ("6d,brl",
# "O1.12.?O12"). Alle Namen, Nummern und Adressen sind frei erfunden — die
# Heuristik wertet nur die Zeichenstatistik aus, nicht den Inhalt.
_REAL_GARBLED_EXCERPT = """It
mus1erco
musterco 6d,brl lBersp1elstr.ße I112345 Musterstadt lGernrdrry wwwmusterco.exarnple lrnro@musterco.exarnple Ie1:+49{01234-56789lFax+49tO1234-567890
Ge.chahslührer: Erika Muslernrann lAmrsqencht Musterstadt IHRB: a0000 | UStndNr-: D 0az0a00
Ban* Musterbank Musrersradr IBAN: DE!2 12O4 5678 90l2 3ot 5t LBlc MUSTDEFFXXX
Arbeitszeugnis
Herr Max Mustermann, geboren am O1.O1.?O80 war vom O1.12.?O12 bis zum 31.12.2024 bei der musterco
GmbH tätig. Er war zunächst als Anwendungsentwickler im Bereich Frontend beschäftigt. Von
01.01.2016 bis 31.12.2022 war Herr Mustermann als Teamleiter und zusätzlich als Scrum Master tätig.
Seit 01.01.2023 war er als Projektleitung im Bereich KMU im Einsatz. Herr Mustermann übernahm
zusätzlich seit 01.06.2024 Aufgaben im Bereich Quality Assurance. Die musterco GmbH ist eine
Digitalmanufaktur. Sie entwickelt und betreut maßgeschneiderte komplexe E-Commerce-, Web-,
SaaS- und Cloud-Lösungen zur Digitalisierung von Geschäftsprozessen für Konzerne und große
mittelständige Unternehmen. Seit 2002 begleitet musterco Unternehmen bei ihrer digitalen
Weiterentwicklung - strategisch, nachhaltig, wertsteigernd und effizient."""

_CLEAN_TEXT = """Sehr geehrte Damen und Herren, hiermit bewerbe ich mich um die ausgeschriebene
Stelle als Fullstack Developer in Ihrem Unternehmen. Ich habe langjährige Erfahrung mit der
Entwicklung von Webanwendungen und bin mit modernen Frameworks vertraut. In meiner
aktuellen Position bin ich für die Konzeption und Umsetzung von komplexen Projekten
verantwortlich und arbeite eng mit dem Team und den Kunden zusammen. Über die Möglichkeit
eines persönlichen Gesprächs würde ich mich sehr freuen und stehe für Rückfragen jederzeit
zur Verfügung. Mit freundlichen Grüßen, Max Mustermann. Meine Kontaktdaten finden Sie im
Anhang dieser Bewerbung, ebenso wie meinen tabellarischen Lebenslauf und die Zeugnisse
der letzten Jahre bei verschiedenen Arbeitgebern in der IT-Branche."""


class FakeVLMClient:
    def __init__(self, response: str = "sauberer OCR-Text"):
        self.response = response
        self.calls = 0

    def extract_text(self, image_bytes: bytes) -> str:
        self.calls += 1
        return self.response


def _make_pdf(path, text: str):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def test_looks_garbled_detects_broken_font_encoding():
    assert _looks_garbled(_REAL_GARBLED_EXCERPT) is True


def test_looks_garbled_accepts_clean_text():
    assert _looks_garbled(_CLEAN_TEXT) is False


def test_looks_garbled_ignores_short_text():
    assert _looks_garbled("Kurzer Text 6d,brl") is False


def test_parse_markdown_returns_text(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("# Titel\n\nInhalt der Notiz.", encoding="utf-8")

    text = parse_file(path)

    assert "Titel" in text
    assert "Inhalt der Notiz." in text


def test_parse_pdf_returns_text(tmp_path):
    path = tmp_path / "doc.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hallo aus dem Testdokument")
    doc.save(str(path))
    doc.close()

    text = parse_file(path)

    assert "Hallo aus dem Testdokument" in text


def test_parse_pdf_falls_back_to_ocr_when_text_layer_is_garbled(tmp_path, monkeypatch):
    path = tmp_path / "kaputt.pdf"
    _make_pdf(path, _REAL_GARBLED_EXCERPT)
    monkeypatch.setattr(
        "backend.services.parsing.render_pdf_pages", lambda p, dpi=200: [b"seite1"]
    )
    vlm = FakeVLMClient("sauberer OCR-Text von der Bildseite")

    text = parse_file(path, vlm_client=vlm)

    assert text == "sauberer OCR-Text von der Bildseite"
    assert vlm.calls == 1


def test_parse_pdf_keeps_garbled_text_if_no_vlm_available(tmp_path):
    path = tmp_path / "kaputt.pdf"
    _make_pdf(path, _REAL_GARBLED_EXCERPT)

    text = parse_file(path)

    assert "musterco" in text


def test_parse_unsupported_extension_raises(tmp_path):
    path = tmp_path / "bild.png"
    path.write_bytes(b"\x89PNG")

    with pytest.raises(ValueError):
        parse_file(path)


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------

_FRONTMATTER_SAMPLE = (
    "---\n"
    "type: concept\n"
    "title: Sozialversicherung\n"
    "aliases: []\n"
    "created: '2026-07-04T04:37:58.594227'\n"
    "updated: '2026-07-04T04:37:58.594227'\n"
    "relations: []\n"
    "---\n"
    "Das ist der eigentliche Notiztext."
)


def test_parse_frontmatter_strips_yaml_and_returns_body_and_meta():
    body, meta = parse_frontmatter(_FRONTMATTER_SAMPLE)
    assert body == "Das ist der eigentliche Notiztext."
    assert meta["title"] == "Sozialversicherung"
    assert meta["type"] == "concept"
    assert meta["created"] == "2026-07-04T04:37:58.594227"


def test_parse_frontmatter_no_frontmatter_returns_content_untouched():
    body, meta = parse_frontmatter("Nur Text ohne Frontmatter.")
    assert body == "Nur Text ohne Frontmatter."
    assert meta == {}


def test_parse_frontmatter_empty_input():
    assert parse_frontmatter("") == ("", {})
    assert parse_frontmatter(None) == ("", {})


def test_parse_frontmatter_strips_quotes_and_list_brackets():
    content = "---\ntitle: 'Mein Titel'\ntags: [a, b, c]\n---\nBody."
    body, meta = parse_frontmatter(content)
    assert body == "Body."
    assert meta["title"] == "Mein Titel"
    assert meta["tags"] == "a, b, c"


def test_parse_frontmatter_handles_only_frontmatter_no_body():
    content = "---\ntitle: Leer\n---\n"
    body, meta = parse_frontmatter(content)
    assert body == ""
    assert meta["title"] == "Leer"
