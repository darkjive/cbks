import pytest

from backend.services.event_log import DuplicateEventError, EventLog
from backend.storage.sqlite_db import get_connection, init_db


@pytest.fixture
def event_log(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return EventLog(conn)


def test_append_and_pending(event_log):
    event_id = event_log.append("note.created", {"text": "hallo"}, "cli")
    assert isinstance(event_id, int)

    pending = event_log.pending()
    assert len(pending) == 1
    assert pending[0].id == event_id
    assert pending[0].status == "pending"


def test_append_duplicate_raises(event_log):
    event_log.append("note.created", {"text": "hallo"}, "cli")
    with pytest.raises(DuplicateEventError):
        event_log.append("note.created", {"text": "hallo"}, "cli")


def test_mark_processed_removes_from_pending(event_log):
    event_id = event_log.append("note.created", {"text": "hallo"}, "cli")
    event_log.mark_processed(event_id)
    assert event_log.pending() == []


def test_mark_failed_appears_in_failed(event_log):
    event_id = event_log.append("note.created", {"text": "hallo"}, "cli")
    event_log.mark_failed(event_id, "boom")
    failed = event_log.failed()
    assert len(failed) == 1
    assert failed[0].error == "boom"


def test_replay_all_returns_every_event_in_order(event_log):
    id1 = event_log.append("note.created", {"text": "eins"}, "cli")
    id2 = event_log.append("note.created", {"text": "zwei"}, "cli")
    event_log.mark_processed(id1)

    replayed = list(event_log.replay_all())
    assert [e.id for e in replayed] == [id1, id2]


def test_counts(event_log):
    id1 = event_log.append("note.created", {"text": "eins"}, "cli")
    event_log.append("note.created", {"text": "zwei"}, "cli")
    event_log.mark_processed(id1)

    counts = event_log.counts()
    assert counts == {"pending": 1, "processed": 1, "failed": 0}


def test_get_returns_event_by_id(event_log):
    event_id = event_log.append("note.created", {"title": "t", "text": "x"}, "cli")

    event = event_log.get(event_id)

    assert event is not None
    assert event.id == event_id
    assert event.event_type == "note.created"


def test_get_returns_none_for_unknown_id(event_log):
    assert event_log.get(999999) is None


def test_delete_by_vault_node_id_removes_all_events_of_that_node(event_log):
    event_log.append("vault.file", {"node_id": "abc-123", "text": "v1"}, "vault")
    event_log.append("vault.file", {"node_id": "abc-123", "text": "v2"}, "vault")
    keep = event_log.append("vault.file", {"node_id": "other-9", "text": "x"}, "vault")

    event_log.delete_by_vault_node_id("abc-123")

    remaining = [e.id for e in event_log.replay_all()]
    assert remaining == [keep]


def test_delete_by_vault_node_id_treats_wildcards_literally(event_log):
    # node_id kommt aus dem Frontmatter der Vault-Datei und ist damit
    # nutzerkontrolliert. Ein "%" darf nicht als LIKE-Wildcard wirken,
    # sonst raeumt ein einziges Delete das gesamte Vault-Event-Log ab.
    keep_a = event_log.append("vault.file", {"node_id": "abc-123", "text": "a"}, "vault")
    keep_b = event_log.append("vault.file", {"node_id": "def-456", "text": "b"}, "vault")

    event_log.delete_by_vault_node_id("%")

    remaining = [e.id for e in event_log.replay_all()]
    assert remaining == [keep_a, keep_b]


def test_delete_by_vault_node_id_treats_underscore_literally(event_log):
    # "_" matcht in LIKE genau ein Zeichen — "a_c" duerfte "abc" nicht treffen.
    keep = event_log.append("vault.file", {"node_id": "abc", "text": "a"}, "vault")

    event_log.delete_by_vault_node_id("a_c")

    remaining = [e.id for e in event_log.replay_all()]
    assert remaining == [keep]
