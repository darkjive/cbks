import asyncio
import uuid
from datetime import datetime, timezone

import pytest

from backend.models.edges import Edge
from backend.models.nodes import Node
from backend.services.agents.prefrontal import PrefrontalAgent
from backend.services.agents.temporal import TemporalAgent
from backend.services.graph_backend import GraphBackend
from backend.services.rag import ask, search
from backend.storage.faiss_index import FaissIndex
from backend.storage.sqlite_db import get_connection, init_db


class FakeEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]


class FakeLLMClient:
    def __init__(self):
        self.last_prompt = None

    def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        return "Die Antwort basiert auf dem Kontext."


@pytest.fixture
def setup(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    graph = GraphBackend(conn)
    faiss_index = FaissIndex(dim=4, index_path=tmp_path / "index.faiss")

    now = datetime.now(timezone.utc).isoformat()
    node = Node(
        id=str(uuid.uuid4()), title="Testdokument", type="document",
        content="Ein Dokument über FAISS.", creation_time=now, last_access=now,
    )
    graph.add_node(node)
    faiss_index.add(1, [1.0, 0.0, 0.0, 0.0])
    graph.link_vector(node.id, 1, "bge-m3")

    return graph, faiss_index, node


@pytest.fixture
def bare_setup(tmp_path):
    conn = get_connection(tmp_path / "bare.db")
    init_db(conn)
    graph = GraphBackend(conn)
    faiss_index = FaissIndex(dim=4, index_path=tmp_path / "bare_index.faiss")
    return graph, faiss_index


def _make_node(title: str, type_: str = "concept", content: str | None = None, metadata=None):
    now = datetime.now(timezone.utc).isoformat()
    return Node(
        id=str(uuid.uuid4()), title=title, type=type_, content=content,
        creation_time=now, last_access=now, metadata=metadata or {},
    )


def test_search_returns_matching_node(setup):
    graph, faiss_index, node = setup
    temporal_agent = TemporalAgent(FakeEmbeddingClient())

    hits = asyncio.run(search("FAISS", temporal_agent, faiss_index, graph, limit=10))

    assert len(hits) == 1
    assert hits[0].node.id == node.id
    assert hits[0].score > 0.99


def test_ask_returns_answer_with_sources(setup):
    graph, faiss_index, node = setup
    temporal_agent = TemporalAgent(FakeEmbeddingClient())
    fake_llm = FakeLLMClient()
    prefrontal_agent = PrefrontalAgent(fake_llm)

    result = asyncio.run(ask("Was steht im Dokument?", temporal_agent, faiss_index, graph, prefrontal_agent))

    assert result.answer == "Die Antwort basiert auf dem Kontext."
    assert result.sources == [node.id]
    assert "Ein Dokument über FAISS." in fake_llm.last_prompt


def test_ask_includes_graph_neighborhood_for_mentioned_entity(bare_setup):
    graph, faiss_index = bare_setup
    now = datetime.now(timezone.utc).isoformat()

    versicherung = _make_node("Versicherung")
    kasse = _make_node("MUSTERKASSE", metadata={"entity_type": "organization"})
    nummer = _make_node("333444555", metadata={"entity_type": "value"})
    for n in (versicherung, kasse, nummer):
        graph.add_node(n)
    graph.add_edge(Edge(
        id=str(uuid.uuid4()), source=kasse.id, target=versicherung.id,
        relation_type="part_of", creation_time=now, last_updated=now,
    ))
    graph.add_edge(Edge(
        id=str(uuid.uuid4()), source=nummer.id, target=kasse.id,
        relation_type="part_of", creation_time=now, last_updated=now,
    ))

    temporal_agent = TemporalAgent(FakeEmbeddingClient())
    fake_llm = FakeLLMClient()
    prefrontal_agent = PrefrontalAgent(fake_llm)

    result = asyncio.run(ask(
        "Wie lautet meine Versicherungsnummer bei der Musterkasse?",
        temporal_agent, faiss_index, graph, prefrontal_agent,
    ))

    assert "333444555" in fake_llm.last_prompt
    assert nummer.id in result.sources


def test_ask_includes_documents_mentioning_year(bare_setup):
    graph, faiss_index = bare_setup
    doc = _make_node(
        "Arbeitszeugnis", type_="document",
        content="Herr Mustermann war 2012 bei der Musterco GmbH in Musterstadt tätig.",
    )
    graph.add_node(doc)

    temporal_agent = TemporalAgent(FakeEmbeddingClient())
    fake_llm = FakeLLMClient()
    prefrontal_agent = PrefrontalAgent(fake_llm)

    result = asyncio.run(ask(
        "Wo habe ich im Jahr 2012 gearbeitet?", temporal_agent, faiss_index, graph, prefrontal_agent
    ))

    assert "Musterco GmbH" in fake_llm.last_prompt
    assert doc.id in result.sources


def test_ask_resolves_relationship_alias(bare_setup):
    graph, faiss_index = bare_setup
    erika = _make_node(
        "Erika Mustermann", type_="person",
        metadata={"relationship": "Ehefrau", "extracted_fields": {"geburtsdatum": "14.07.1986"}},
    )
    graph.add_node(erika)

    temporal_agent = TemporalAgent(FakeEmbeddingClient())
    fake_llm = FakeLLMClient()
    prefrontal_agent = PrefrontalAgent(fake_llm)

    result = asyncio.run(ask(
        "Wann hat meine Frau Geburtstag?", temporal_agent, faiss_index, graph, prefrontal_agent
    ))

    assert "14.07.1986" in fake_llm.last_prompt
    assert erika.id in result.sources
