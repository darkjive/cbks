import importlib.util
from pathlib import Path

import numpy as np
import pytest

from backend.models.nodes import Node
from backend.services import tts

# synthesize() schreibt die WAV-Datei ueber soundfile. Das Paket ist eine
# optionale TTS-Abhaengigkeit (backend/requirements-tts.txt) und fehlt in einer
# Standardinstallation - dann sind diese beiden Tests nicht ausfuehrbar. Alle
# uebrigen Tests hier (build_speech_text, Datumsformatierung) kommen ohne aus.
requires_soundfile = pytest.mark.skipif(
    importlib.util.find_spec("soundfile") is None,
    reason="TTS optional: soundfile nur via backend/requirements-tts.txt",
)


@requires_soundfile
def test_synthesize_caches_and_skips_regeneration(tmp_path, monkeypatch):
    calls = []

    def fake_generate_audio(text: str):
        calls.append(text)
        return np.zeros(100, dtype="float32")

    def fake_ffmpeg_run(cmd, **kwargs):
        target = Path(cmd[-1])
        target.write_bytes(b"RIFF-fake-wav-bytes")
        return None

    monkeypatch.setattr(tts, "_generate_audio", fake_generate_audio)
    monkeypatch.setattr(tts.subprocess, "run", fake_ffmpeg_run)

    path1 = tts.synthesize("Hallo Welt", tmp_path)
    assert path1.exists()
    assert path1.read_bytes() == b"RIFF-fake-wav-bytes"
    assert calls == ["Hallo Welt"]

    path2 = tts.synthesize("Hallo Welt", tmp_path)
    assert path2 == path1
    assert calls == ["Hallo Welt"]  # zweiter Aufruf generiert NICHT erneut


@requires_soundfile
def test_synthesize_uses_distinct_cache_files_per_text(tmp_path, monkeypatch):
    def fake_generate_audio(text: str):
        return np.zeros(10, dtype="float32")

    def fake_ffmpeg_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"fake")
        return None

    monkeypatch.setattr(tts, "_generate_audio", fake_generate_audio)
    monkeypatch.setattr(tts.subprocess, "run", fake_ffmpeg_run)

    path_a = tts.synthesize("Text A", tmp_path)
    path_b = tts.synthesize("Text B", tmp_path)

    assert path_a != path_b


# ---------------------------------------------------------------------------
# build_speech_text / Datum-Formatierung / Frontmatter-Strip
# ---------------------------------------------------------------------------

def _make_node(**kwargs) -> Node:
    defaults = dict(
        id="n1", title="Titel", type="concept",
        creation_time="2026-07-04T04:37:58.594227",
        last_access="2026-07-04T04:37:58.594227",
    )
    defaults.update(kwargs)
    return Node(**defaults)


def test_format_german_date_basic():
    assert tts._format_german_date("2026-07-04T04:37:58") == "vierten Juli zweitausendsechsundzwanzig"


def test_format_german_date_year_2000_and_2099():
    assert tts._format_german_date("2000-01-01") == "ersten Januar zweitausend"
    assert tts._format_german_date("2099-12-31") == "einunddreißigsten Dezember zweitausendneunundneunzig"


def test_format_german_date_invalid_falls_back_to_raw():
    assert tts._format_german_date("not-a-date") == "not-a-date"
    assert tts._format_german_date("") == ""


def test_format_german_day_ordinals_special_and_composed():
    # Gleichheit statt Substring: "dreißigsten" ist auch in "nullunddreißigsten"
    # enthalten, ein Substring-Assert wuerde den Zehner-Bug nicht fangen.
    assert tts._format_german_date("2026-01-01") == "ersten Januar zweitausendsechsundzwanzig"
    assert tts._format_german_date("2026-01-03") == "dritten Januar zweitausendsechsundzwanzig"
    assert tts._format_german_date("2026-01-07") == "siebten Januar zweitausendsechsundzwanzig"
    assert tts._format_german_date("2026-01-21") == "einundzwanzigsten Januar zweitausendsechsundzwanzig"


def test_format_german_date_pure_tens_day_and_year():
    # Regression: Vielfache von 10 duerfen kein "nullund..." erzeugen
    # (weder beim Tag 20/30 noch im Jahr 2020/2030).
    assert tts._format_german_date("2026-01-20") == "zwanzigsten Januar zweitausendsechsundzwanzig"
    assert tts._format_german_date("2026-01-30") == "dreißigsten Januar zweitausendsechsundzwanzig"
    assert tts._format_german_date("2020-06-15") == "fünfzehnten Juni zweitausendzwanzig"
    assert tts._format_german_date("2030-06-15") == "fünfzehnten Juni zweitausenddreißig"


def test_parse_frontmatter_no_frontmatter_returns_content_untouched():
    # Nur Smoke-Test - kanonische Tests in test_parsing.py.
    from backend.services.parsing import parse_frontmatter
    body, meta = parse_frontmatter("Nur Text ohne Frontmatter.")
    assert body == "Nur Text ohne Frontmatter."
    assert meta == {}


def test_build_speech_text_strips_frontmatter_and_adds_dates():
    node = _make_node(
        title="Sozialversicherung.md",
        content=(
            "---\n"
            "type: concept\n"
            "title: Sozialversicherung\n"
            "aliases: []\n"
            "created: '2026-07-04T04:37:58.594227'\n"
            "updated: '2026-07-04T04:37:58.594227'\n"
            "relations: []\n"
            "---\n"
            "Gesetzliches System zur Absicherung sozialer Risiken."
        ),
    )
    text = tts.build_speech_text(node)
    # Titel aus Frontmatter, nicht Dateiname
    assert text.startswith("Sozialversicherung")
    # YAML-Marker werden NICHT vorgelesen
    assert "---" not in text
    assert "type:" not in text
    assert "aliases:" not in text
    # Body enthalten
    assert "Gesetzliches System zur Absicherung sozialer Risiken." in text
    # Datum als Wort
    assert "Erstellt am vierten Juli zweitausendsechsundzwanzig." in text
    # created == updated → kein "Geändert am"
    assert "Geändert" not in text


def test_build_speech_text_shows_geaendert_when_updated_differs():
    # updated-Datum aus Frontmatter, nicht via last_access (das ist Zugriffs-
    # zeit, keine Modifikationszeit).
    node = _make_node(
        title="X",
        content=(
            "---\n"
            "title: X\n"
            "created: '2026-01-01T00:00:00'\n"
            "updated: '2026-02-15T00:00:00'\n"
            "---\n"
            "Body"
        ),
    )
    text = tts.build_speech_text(node)
    assert "Erstellt am ersten Januar zweitausendsechsundzwanzig." in text
    assert "Geändert am fünfzehnten Februar zweitausendsechsundzwanzig." in text


def test_build_speech_text_no_geaendert_without_explicit_updated():
    # Ohne Frontmatter-updated darf last_access NICHT als "Geändert" ausgegeben
    # werden (Regressionsschutz fuer den alten last_access-Fallback).
    node = _make_node(
        title="X", content="Body",
        creation_time="2026-01-01T00:00:00",
        last_access="2026-02-15T00:00:00",
    )
    text = tts.build_speech_text(node)
    assert "Erstellt am ersten Januar zweitausendsechsundzwanzig." in text
    assert "Geändert" not in text


def test_build_speech_text_falls_back_to_node_title_when_no_frontmatter():
    node = _make_node(title="PDF-Dokument", content="Wichtiger Inhalt.")
    text = tts.build_speech_text(node)
    assert text.startswith("PDF-Dokument")
    assert "Wichtiger Inhalt." in text
    assert "Erstellt am vierten Juli zweitausendsechsundzwanzig." in text
