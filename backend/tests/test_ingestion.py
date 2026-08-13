import json

import pytest

from backend.services.event_log import EventLog
from backend.services.ingestion import ingest_file, ingest_note
from backend.storage.sqlite_db import get_connection, init_db


@pytest.fixture
def event_log(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return EventLog(conn)


def test_ingest_file_appends_document_event(tmp_path, event_log):
    path = tmp_path / "note.md"
    path.write_text("# Titel\n\nInhalt.", encoding="utf-8")

    result = ingest_file(path, event_log)

    assert result.duplicate is False
    pending = event_log.pending()
    assert len(pending) == 1
    assert pending[0].event_type == "document.added"
    payload = json.loads(pending[0].payload)
    assert payload["title"] == "note.md"
    assert "Inhalt." in payload["text"]


def test_ingest_file_duplicate_is_detected(tmp_path, event_log):
    path = tmp_path / "note.md"
    path.write_text("Immer derselbe Inhalt.", encoding="utf-8")

    ingest_file(path, event_log)
    result = ingest_file(path, event_log)

    assert result.duplicate is True
    assert result.duplicate_since is not None


def test_ingest_note_appends_note_event(event_log):
    result = ingest_note("Schnelle Notiz", event_log)

    assert result.duplicate is False
    pending = event_log.pending()
    assert pending[0].event_type == "note.created"
    payload = json.loads(pending[0].payload)
    assert payload["text"] == "Schnelle Notiz"
