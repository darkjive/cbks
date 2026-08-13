import asyncio
import json

import pytest

from backend.models.nodes import Node
from backend.services.agents.temporal import TemporalAgent
from backend.services.entity_resolver import EntityResolver
from backend.services.graph_backend import GraphBackend
from backend.storage.sqlite_db import get_connection, init_db

TS = "2026-07-05T00:00:00+00:00"


class VectorEmbeddingClient:
    """Testdouble mit fest zugeordneten Vektoren pro Titel."""

    def __init__(self, vectors: dict[str, list[float]]):
        self._vectors = vectors

    def embed(self, text: str) -> list[float]:
        return self._vectors[text]


class StubLLMClient:
    def __init__(self, same: bool):
        self._same = same

    def generate(self, prompt: str) -> str:
        return json.dumps({"same": self._same})


class FailingLLMClient:
    def generate(self, prompt: str) -> str:
        raise RuntimeError("Ollama nicht erreichbar")


@pytest.fixture
def graph(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return GraphBackend(conn)


def make_concept(node_id: str, title: str, creation_time: str = TS) -> Node:
    return Node(id=node_id, title=title, type="concept", creation_time=creation_time, last_access=TS)


def test_resolve_returns_exact_title_match(graph):
    graph.add_node(make_concept("c1", "FAISS"))
    resolver = EntityResolver(graph, TemporalAgent(VectorEmbeddingClient({})), StubLLMClient(True))

    result = asyncio.run(resolver.resolve("faiss"))

    assert result is not None
    assert result.id == "c1"


def test_resolve_returns_alias_match(graph):
    node = make_concept("c1", "DMT")
    node.metadata["aliases"] = ["Dimethyltryptamin"]
    graph.add_node(node)
    resolver = EntityResolver(graph, TemporalAgent(VectorEmbeddingClient({})), StubLLMClient(True))

    result = asyncio.run(resolver.resolve("dimethyltryptamin"))

    assert result is not None
    assert result.id == "c1"


def test_resolve_matches_high_similarity_without_llm(graph):
    graph.add_node(make_concept("c1", "DMT"))
    graph.set_concept_vector("c1", [1.0, 0.0])
    embedding = VectorEmbeddingClient({"Dimethyltryptamin": [0.99, 0.01]})
    resolver = EntityResolver(graph, TemporalAgent(embedding), FailingLLMClient())

    result = asyncio.run(resolver.resolve("Dimethyltryptamin"))

    assert result is not None
    assert result.id == "c1"
    assert graph.get_node("c1").metadata["aliases"] == ["Dimethyltryptamin"]


def test_resolve_confirms_borderline_match_via_llm(graph):
    graph.add_node(make_concept("c1", "Serotonin"))
    graph.set_concept_vector("c1", [1.0, 0.0])
    embedding = VectorEmbeddingClient({"5-HT": [0.8, 0.6]})
    resolver = EntityResolver(graph, TemporalAgent(embedding), StubLLMClient(True))

    result = asyncio.run(resolver.resolve("5-HT"))

    assert result is not None
    assert result.id == "c1"
    assert graph.get_node("c1").metadata["aliases"] == ["5-HT"]


def test_resolve_rejects_borderline_match_when_llm_says_different(graph):
    graph.add_node(make_concept("c1", "Serotonin"))
    graph.set_concept_vector("c1", [1.0, 0.0])
    embedding = VectorEmbeddingClient({"Dopamin": [0.8, 0.6]})
    resolver = EntityResolver(graph, TemporalAgent(embedding), StubLLMClient(False))

    result = asyncio.run(resolver.resolve("Dopamin"))

    assert result is None


def test_resolve_returns_none_below_threshold(graph):
    graph.add_node(make_concept("c1", "FAISS"))
    graph.set_concept_vector("c1", [1.0, 0.0])
    embedding = VectorEmbeddingClient({"NetworkX": [0.0, 1.0]})
    resolver = EntityResolver(graph, TemporalAgent(embedding), FailingLLMClient())

    result = asyncio.run(resolver.resolve("NetworkX"))

    assert result is None


def test_resolve_treats_llm_failure_as_no_match(graph):
    graph.add_node(make_concept("c1", "Serotonin"))
    graph.set_concept_vector("c1", [1.0, 0.0])
    embedding = VectorEmbeddingClient({"5-HT": [0.8, 0.6]})
    resolver = EntityResolver(graph, TemporalAgent(embedding), FailingLLMClient())

    result = asyncio.run(resolver.resolve("5-HT"))

    assert result is None


def test_register_stores_embedding_for_title(graph):
    node = make_concept("c1", "FAISS")
    graph.add_node(node)
    embedding = VectorEmbeddingClient({"FAISS": [1.0, 0.0]})
    resolver = EntityResolver(graph, TemporalAgent(embedding), FailingLLMClient())

    asyncio.run(resolver.register(node))

    vectors = dict(graph.get_concept_vectors())
    assert vectors["c1"] == pytest.approx([1.0, 0.0])


def test_dedupe_all_merges_similar_concepts(graph):
    older = make_concept("c1", "DMT", creation_time="2026-07-01T00:00:00+00:00")
    newer = make_concept("c2", "Dimethyltryptamin", creation_time="2026-07-02T00:00:00+00:00")
    graph.add_node(older)
    graph.add_node(newer)
    graph.set_concept_vector("c1", [1.0, 0.0])
    graph.set_concept_vector("c2", [0.99, 0.01])
    resolver = EntityResolver(graph, TemporalAgent(VectorEmbeddingClient({})), FailingLLMClient())

    summary = asyncio.run(resolver.dedupe_all())

    assert summary.checked == 2
    assert summary.merged == 1
    assert graph.get_node("c2") is None
    assert graph.get_node("c1") is not None


def test_dedupe_all_is_idempotent(graph):
    older = make_concept("c1", "DMT", creation_time="2026-07-01T00:00:00+00:00")
    newer = make_concept("c2", "Dimethyltryptamin", creation_time="2026-07-02T00:00:00+00:00")
    graph.add_node(older)
    graph.add_node(newer)
    graph.set_concept_vector("c1", [1.0, 0.0])
    graph.set_concept_vector("c2", [0.99, 0.01])
    resolver = EntityResolver(graph, TemporalAgent(VectorEmbeddingClient({})), FailingLLMClient())
    asyncio.run(resolver.dedupe_all())

    second_run = asyncio.run(resolver.dedupe_all())

    assert second_run.checked == 1
    assert second_run.merged == 0


def test_dedupe_all_keeps_distinct_concepts_separate(graph):
    graph.add_node(make_concept("c1", "FAISS", creation_time="2026-07-01T00:00:00+00:00"))
    graph.add_node(make_concept("c2", "NetworkX", creation_time="2026-07-02T00:00:00+00:00"))
    graph.set_concept_vector("c1", [1.0, 0.0])
    graph.set_concept_vector("c2", [0.0, 1.0])
    resolver = EntityResolver(graph, TemporalAgent(VectorEmbeddingClient({})), FailingLLMClient())

    summary = asyncio.run(resolver.dedupe_all())

    assert summary.checked == 2
    assert summary.merged == 0


def test_dedupe_all_backfills_missing_vector_for_legacy_concept(graph):
    graph.add_node(make_concept("c1", "FAISS", creation_time="2026-07-01T00:00:00+00:00"))
    # Kein set_concept_vector aufgerufen — simuliert einen Concept-Node von vor diesem Feature
    embedding = VectorEmbeddingClient({"FAISS": [1.0, 0.0]})
    resolver = EntityResolver(graph, TemporalAgent(embedding), FailingLLMClient())

    summary = asyncio.run(resolver.dedupe_all())

    assert summary.checked == 1
    assert summary.merged == 0
    vectors = dict(graph.get_concept_vectors())
    assert vectors["c1"] == pytest.approx([1.0, 0.0])
