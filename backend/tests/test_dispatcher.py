import asyncio
import json

import pytest

from backend.services.agents.prefrontal import ClassificationResult, PrefrontalAgent
from backend.services.agents.temporal import TemporalAgent
from backend.services.dispatcher import Dispatcher
from backend.services.entity_resolver import EntityResolver
from backend.services.event_log import EventLog
from backend.services.graph_backend import GraphBackend
from backend.storage.faiss_index import FaissIndex
from backend.storage.sqlite_db import get_connection, init_db


class FakeLLMClient:
    def __init__(self, classification: str, entities: list[str]):
        self._response = json.dumps({"classification": classification, "entities": entities})

    def generate(self, prompt: str, format: str = "") -> str:
        return self._response


class FakeEmbeddingClient:
    """Testdouble mit klar unterscheidbaren Vektoren für bekannte Titel."""

    _VECTORS = {
        "FAISS": [1.0, 0.0, 0.0, 0.0],
        "Ollama": [0.0, 1.0, 0.0, 0.0],
        "DMT": [0.0, 0.0, 1.0, 0.0],
        "Dimethyltryptamin": [0.0, 0.0, 0.99, 0.01],
        "MUSTERKASSE": [0.5, 0.5, 0.0, 0.0],
        "333444555": [0.5, -0.5, 0.0, 0.0],
        "Versicherung": [0.0, 0.0, 0.5, 0.5],
        "ADAC": [0.0, 0.5, 0.0, 0.5],
        "Erika Mustermann": [0.5, 0.0, 0.5, 0.0],
    }

    def embed(self, text: str) -> list[float]:
        return self._VECTORS.get(text, [0.0, 0.0, 0.0, 1.0])


class FailingLLMClient:
    def generate(self, prompt: str, format: str = "") -> str:
        raise RuntimeError("Ollama nicht erreichbar")


@pytest.fixture
def setup(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    event_log = EventLog(conn)
    graph = GraphBackend(conn)
    faiss_index = FaissIndex(dim=4, index_path=tmp_path / "index.faiss")
    return conn, event_log, graph, faiss_index


def make_dispatcher(setup, llm_client=None, embedding_client=None):
    _, event_log, graph, faiss_index = setup
    llm = llm_client or FakeLLMClient("document", ["FAISS", "Ollama"])
    prefrontal = PrefrontalAgent(llm)
    temporal = TemporalAgent(embedding_client or FakeEmbeddingClient())
    resolver = EntityResolver(graph, temporal, llm)
    return Dispatcher(event_log, graph, faiss_index, temporal, prefrontal, resolver, "bge-m3")


def test_process_pending_creates_document_and_entity_nodes(setup):
    _, event_log, graph, faiss_index = setup
    dispatcher = make_dispatcher(setup)
    event_id = event_log.append(
        "document.added", {"title": "Testdoku", "text": "Text über FAISS und Ollama"}, "cli"
    )

    summary = asyncio.run(dispatcher.process_pending())

    assert summary.processed == 1
    assert summary.failed == 0
    assert event_log.pending() == []

    doc_node = graph.find_node_by_title("Testdoku")
    assert doc_node is not None
    assert doc_node.type == "document"

    faiss_node = graph.get_node_by_faiss_id(event_id)
    assert faiss_node is not None
    assert faiss_node.id == doc_node.id

    faiss_entity = graph.find_node_by_title("FAISS")
    assert faiss_entity is not None
    assert faiss_entity.type == "concept"

    neighbors = graph.get_neighbors(doc_node.id)
    assert {n.title for n in neighbors} == {"FAISS", "Ollama"}


def test_process_pending_reuses_existing_entity_node(setup):
    _, event_log, graph, faiss_index = setup
    dispatcher = make_dispatcher(setup)
    event_log.append("document.added", {"title": "Doc1", "text": "über FAISS"}, "cli")
    asyncio.run(dispatcher.process_pending())

    event_log.append("document.added", {"title": "Doc2", "text": "auch über FAISS"}, "cli")
    asyncio.run(dispatcher.process_pending())

    rows = graph._conn.execute(
        "SELECT COUNT(*) AS n FROM nodes WHERE title = 'FAISS' COLLATE NOCASE"
    ).fetchone()
    assert rows["n"] == 1


def test_process_pending_marks_failed_event_on_exception(setup):
    _, event_log, graph, faiss_index = setup
    dispatcher = make_dispatcher(setup, llm_client=FailingLLMClient())
    event_log.append("document.added", {"title": "Doc", "text": "Text"}, "cli")

    summary = asyncio.run(dispatcher.process_pending())

    assert summary.processed == 0
    assert summary.failed == 1
    failed = event_log.failed()
    assert "Ollama nicht erreichbar" in failed[0].error


def test_process_pending_continues_after_one_failure(setup):
    _, event_log, graph, faiss_index = setup

    class SometimesFailingLLMClient:
        def generate(self, prompt: str, format: str = "") -> str:
            if "Text 1" in prompt:
                raise RuntimeError("Ollama nicht erreichbar")
            return json.dumps({"classification": "document", "entities": []})

    dispatcher = make_dispatcher(setup, llm_client=SometimesFailingLLMClient())
    event_log.append("document.added", {"title": "Doc1", "text": "Text 1"}, "cli")
    event_log.append("document.added", {"title": "Doc2", "text": "Text 2"}, "cli")

    summary = asyncio.run(dispatcher.process_pending())

    assert summary.processed == 1
    assert summary.failed == 1
    assert len(event_log.failed()) == 1
    assert len(event_log.pending()) == 0


def test_process_pending_creates_part_of_edges_for_entity_hierarchy(setup):
    _, event_log, graph, faiss_index = setup
    llm = FakeLLMClient(
        "document",
        [
            {"name": "MUSTERKASSE", "type": "organization", "parent": "Versicherung"},
            {"name": "333444555", "type": "value", "parent": "MUSTERKASSE"},
        ],
    )
    dispatcher = make_dispatcher(setup, llm_client=llm)
    event_log.append(
        "document.added", {"title": "Karte", "text": "MUSTERKASSE 333444555"}, "cli"
    )

    asyncio.run(dispatcher.process_pending())

    kasse = graph.find_node_by_title("MUSTERKASSE")
    nummer = graph.find_node_by_title("333444555")
    versicherung = graph.find_node_by_title("Versicherung")
    assert kasse is not None and nummer is not None and versicherung is not None

    edges = graph.get_all_edges()
    assert any(
        e.source == kasse.id and e.target == versicherung.id and e.relation_type == "part_of"
        for e in edges
    )
    assert any(
        e.source == nummer.id and e.target == kasse.id and e.relation_type == "part_of"
        for e in edges
    )
    # Die implizite Parent-Entität "Versicherung" wurde nicht selbst extrahiert,
    # bekommt also keine mentions-Kante vom Dokument.
    assert not any(
        e.target == versicherung.id and e.relation_type == "mentions" for e in edges
    )


def test_process_pending_reuses_parent_entity_across_documents(setup):
    _, event_log, graph, faiss_index = setup
    llm1 = FakeLLMClient(
        "document", [{"name": "MUSTERKASSE", "type": "organization", "parent": "Versicherung"}]
    )
    dispatcher1 = make_dispatcher(setup, llm_client=llm1)
    event_log.append("document.added", {"title": "Karte1", "text": "MUSTERKASSE"}, "cli")
    asyncio.run(dispatcher1.process_pending())

    llm2 = FakeLLMClient(
        "document", [{"name": "ADAC", "type": "organization", "parent": "Versicherung"}]
    )
    dispatcher2 = make_dispatcher(setup, llm_client=llm2)
    event_log.append("document.added", {"title": "Karte2", "text": "ADAC"}, "cli")
    asyncio.run(dispatcher2.process_pending())

    rows = graph._conn.execute(
        "SELECT COUNT(*) AS n FROM nodes WHERE title = 'Versicherung' COLLATE NOCASE"
    ).fetchone()
    assert rows["n"] == 1


def test_process_pending_creates_person_node_with_relationship_metadata(setup):
    _, event_log, graph, faiss_index = setup
    llm = FakeLLMClient(
        "document",
        [{"name": "Erika Mustermann", "type": "person", "parent": None, "relationship": "Ehefrau"}],
    )
    dispatcher = make_dispatcher(setup, llm_client=llm)
    event_log.append("document.added", {"title": "Urkunde", "text": "Erika Mustermann"}, "cli")

    asyncio.run(dispatcher.process_pending())

    erika = graph.find_node_by_title("Erika Mustermann")
    assert erika is not None
    assert erika.type == "person"
    assert erika.metadata["relationship"] == "Ehefrau"


def test_process_pending_merges_entities_with_similar_titles(setup):
    _, event_log, graph, faiss_index = setup
    llm = FakeLLMClient("document", ["DMT"])
    dispatcher = make_dispatcher(setup, llm_client=llm)
    event_log.append("document.added", {"title": "Doc1", "text": "über DMT"}, "cli")
    asyncio.run(dispatcher.process_pending())

    llm2 = FakeLLMClient("document", ["Dimethyltryptamin"])
    dispatcher2 = make_dispatcher(setup, llm_client=llm2)
    event_log.append("document.added", {"title": "Doc2", "text": "über Dimethyltryptamin"}, "cli")
    asyncio.run(dispatcher2.process_pending())

    concepts = graph.get_concept_nodes()
    assert len(concepts) == 1
    assert concepts[0].title == "DMT"
    assert concepts[0].metadata["aliases"] == ["Dimethyltryptamin"]


def test_process_pending_extracts_frontmatter_title_dates_and_strips_yaml(setup):
    """Vault-Markdown mit YAML-Frontmatter: Titel/Datum in Node-Felder, Body ohne YAML."""
    _, event_log, graph, faiss_index = setup
    dispatcher = make_dispatcher(setup)
    frontmatter_text = (
        "---\n"
        "type: concept\n"
        "title: Sozialversicherung\n"
        "aliases: []\n"
        "created: '2020-03-15T08:00:00'\n"
        "updated: '2021-06-01T12:00:00'\n"
        "relations: []\n"
        "---\n"
        "Gesetzliches System zur Absicherung sozialer Risiken."
    )
    event_log.append(
        "document.added",
        {"title": "Sozialversicherung.md", "text": frontmatter_text},
        "vault",
    )

    asyncio.run(dispatcher.process_pending())

    node = graph.find_node_by_title("Sozialversicherung")
    assert node is not None
    # Titel aus Frontmatter, nicht Dateiname
    assert node.title == "Sozialversicherung"
    # Body ohne Frontmatter
    assert node.content == "Gesetzliches System zur Absicherung sozialer Risiken."
    assert "---" not in node.content
    assert "type:" not in node.content
    # Daten aus Frontmatter (nicht die Ingest-Zeit)
    assert node.creation_time == "2020-03-15T08:00:00"
    # last_access initial = creation_time (NICHT updated), damit der RAG-Decay
    # "Tage seit letztem Zugriff" korrekt startet.
    assert node.last_access == node.creation_time
    # updated landet als separates Metadaten-Feld, nicht auf last_access.
    assert node.metadata["updated"] == "2021-06-01T12:00:00"


def test_process_pending_frontmatter_without_dates_falls_back_to_now(setup):
    """Frontmatter ohne created/updated → Node nutzt Ingest-Zeit."""
    _, event_log, graph, faiss_index = setup
    dispatcher = make_dispatcher(setup)
    event_log.append(
        "document.added",
        {"title": "x.md", "text": "---\ntitle: OhneDatum\n---\nBody Text."},
        "vault",
    )

    asyncio.run(dispatcher.process_pending())

    node = graph.find_node_by_title("OhneDatum")
    assert node is not None
    assert node.content == "Body Text."
    # creation_time/last_access wurden auf Ingot-Zeit gesetzt (nicht leer, nicht Frontmatter)
    assert node.creation_time
    assert node.last_access == node.creation_time


def test_process_pending_frontmatter_only_note_has_no_yaml_in_content(setup):
    """Notiz nur mit Frontmatter (leerer Body): content darf kein rohes YAML sein."""
    _, event_log, graph, faiss_index = setup
    dispatcher = make_dispatcher(setup)
    event_log.append(
        "document.added",
        {"title": "Index.md", "text": "---\ntitle: Index\ntype: note\n---\n"},
        "vault",
    )

    asyncio.run(dispatcher.process_pending())

    node = graph.find_node_by_title("Index")
    assert node is not None
    # Kein YAML-Frontmatter im content (frueher fiel text faelschlich auf raw_text zurueck).
    assert "---" not in node.content
    assert "type:" not in node.content
    assert node.content == ""


class _FakeLLMClient:
    def generate(self, prompt: str, format: str = "") -> str:
        return json.dumps({"classification": "note", "entities": []})


class _FakeEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 1.0]


@pytest.fixture()
def upsert_dispatcher(tmp_path):
    conn = get_connection(tmp_path / "test.db", check_same_thread=False)
    init_db(conn)
    event_log = EventLog(conn)
    graph = GraphBackend(conn)
    faiss_index = FaissIndex(dim=4, index_path=tmp_path / "index.faiss")
    llm = _FakeLLMClient()
    prefrontal = PrefrontalAgent(llm)
    temporal = TemporalAgent(_FakeEmbeddingClient())
    resolver = EntityResolver(graph, temporal, llm)
    dispatcher = Dispatcher(event_log, graph, faiss_index, temporal, prefrontal, resolver, "bge-m3")
    return dispatcher, event_log, graph, faiss_index


def test_process_event_with_node_id_creates_node_with_that_id(upsert_dispatcher):
    dispatcher, event_log, graph, _ = upsert_dispatcher
    event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Erster Inhalt", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    event = event_log.pending()[0]

    asyncio.run(dispatcher.process_event(event))

    node = graph.get_node("fixed-id-1")
    assert node is not None
    assert node.content == "Erster Inhalt"


def test_process_event_with_existing_node_id_upserts_not_duplicates(upsert_dispatcher):
    dispatcher, event_log, graph, faiss_index = upsert_dispatcher
    event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Alter Inhalt", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    asyncio.run(dispatcher.process_event(event_log.pending()[0]))
    old_node = graph.get_node("fixed-id-1")
    assert old_node is not None

    event_log.append(
        "vault.file",
        {"title": "Notiz neu", "text": "Neuer Inhalt", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    asyncio.run(dispatcher.process_event(event_log.pending()[0]))

    assert len(graph.get_all_nodes()) == 1
    updated_node = graph.get_node("fixed-id-1")
    assert updated_node.content == "Neuer Inhalt"
    assert updated_node.title == "Notiz neu"


def test_process_event_upsert_preserves_creation_time_and_access_counter(upsert_dispatcher):
    dispatcher, event_log, graph, _ = upsert_dispatcher
    event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Inhalt", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    asyncio.run(dispatcher.process_event(event_log.pending()[0]))
    graph.touch_access(["fixed-id-1"])
    original = graph.get_node("fixed-id-1")

    event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Geänderter Inhalt", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    asyncio.run(dispatcher.process_event(event_log.pending()[0]))

    updated = graph.get_node("fixed-id-1")
    assert updated.creation_time == original.creation_time
    assert updated.access_counter == original.access_counter
    assert updated.last_access == original.last_access


def test_process_event_upsert_replaces_faiss_vector(upsert_dispatcher):
    dispatcher, event_log, graph, faiss_index = upsert_dispatcher
    event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Inhalt eins", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    first_event = event_log.pending()[0]
    asyncio.run(dispatcher.process_event(first_event))
    row = graph._conn.execute(
        "SELECT faiss_id FROM node_vectors WHERE node_id = 'fixed-id-1'"
    ).fetchone()
    first_faiss_id = row["faiss_id"]
    assert first_faiss_id == first_event.id

    event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Inhalt zwei", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    second_event = [e for e in event_log.pending()][0]
    asyncio.run(dispatcher.process_event(second_event))

    row = graph._conn.execute(
        "SELECT faiss_id FROM node_vectors WHERE node_id = 'fixed-id-1'"
    ).fetchone()
    assert row["faiss_id"] == second_event.id
    assert row["faiss_id"] != first_faiss_id


def test_process_event_upsert_clears_stale_extracted_fields(upsert_dispatcher):
    """Wird ein extrahierbares Feld aus dem Text entfernt, darf der alte Wert
    nicht im Metadata-Dict überleben (extracted_fields wird pro Upsert frisch
    aus dem aktuellen Text abgeleitet, nicht additiv fortgeschrieben)."""
    dispatcher, event_log, graph, _ = upsert_dispatcher
    event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Tel: 0123456789", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    asyncio.run(dispatcher.process_event(event_log.pending()[0]))
    node = graph.get_node("fixed-id-1")
    assert node.metadata.get("extracted_fields")

    event_log.append(
        "vault.file",
        {
            "title": "Notiz",
            "text": "Ein ganz normaler Satz ohne besondere Daten.",
            "source_path": "n.md",
            "node_id": "fixed-id-1",
        },
        "vault",
    )
    asyncio.run(dispatcher.process_event(event_log.pending()[0]))

    updated_node = graph.get_node("fixed-id-1")
    assert not updated_node.metadata.get("extracted_fields")


def test_process_event_without_node_id_still_creates_random_id(upsert_dispatcher):
    dispatcher, event_log, graph, _ = upsert_dispatcher
    event_log.append("note.created", {"title": "Titel", "text": "Text", "source_path": None}, "cli")

    asyncio.run(dispatcher.process_event(event_log.pending()[0]))

    nodes = graph.get_all_nodes()
    assert len(nodes) == 1
    assert nodes[0].id != ""  # UUID, kein fester Wert
