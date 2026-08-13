from backend.services.field_extractor import extract_fields


def test_extract_fields_finds_iban():
    text = "Bankverbindung\nIBAN: DE12 1204 5678 9012 3456 78\nBIC: MUSTDEFFXXX"

    fields = extract_fields(text)

    assert fields["iban"] == "DE12 1204 5678 9012 3456 78"


def test_extract_fields_finds_mitgliedsnummer():
    text = "Musterverein e.V.\nMax Mustermann\nMitgliedsnummer: 100200300\nMitglied seit: 2012"

    fields = extract_fields(text)

    assert fields["mitgliedsnummer"] == "100200300"


def test_extract_fields_finds_email_and_telefon():
    text = "Kontakt\nE-Mail: info@musterco.example\nTel: +49 1234-56789"

    fields = extract_fields(text)

    assert fields["email"] == "info@musterco.example"
    assert fields["telefon"] == "+49 1234-56789"


def test_extract_fields_finds_geburtsdatum():
    text = "Herr Max Mustermann, geboren am 01.01.1980 war vom 01.12.2012 bis..."

    fields = extract_fields(text)

    assert fields["geburtsdatum"] == "01.01.1980"


def test_extract_fields_returns_empty_dict_when_nothing_matches():
    assert extract_fields("Ein ganz normaler Text ohne Felder.") == {}


def test_extract_fields_ignores_unlabeled_numbers():
    text = "Gesundheitskarte\n111222\nMUSTERKASSE KIDS\nErika Mustermann\nMUSTERKASSE\n333444555"

    fields = extract_fields(text)

    assert fields == {}
