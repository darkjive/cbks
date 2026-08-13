import asyncio
import json

import pytest

from backend.services.agents.prefrontal import PrefrontalAgent
from backend.services.agents.temporal import TemporalAgent
from backend.services.dispatcher import Dispatcher
from backend.services.entity_resolver import EntityResolver
from backend.services.event_log import EventLog
from backend.services.graph_backend import GraphBackend
from backend.services.rebuild import rebuild
from backend.storage.faiss_index import FaissIndex
from backend.storage.sqlite_db import get_connection, init_db


class FakeLLMClient:
    def generate(self, prompt: str, format: str = "") -> str:
        return json.dumps({"classification": "document", "entities": ["FAISS"]})


class FakeEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]


@pytest.fixture
def setup(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    event_log = EventLog(conn)
    graph = GraphBackend(conn)
    faiss_index = FaissIndex(dim=4, index_path=tmp_path / "index.faiss")
    temporal = TemporalAgent(FakeEmbeddingClient())
    llm = FakeLLMClient()
    resolver = EntityResolver(graph, temporal, llm)
    dispatcher = Dispatcher(
        event_log, graph, faiss_index, temporal, PrefrontalAgent(llm), resolver, "bge-m3",
    )
    return event_log, graph, faiss_index, dispatcher


def test_rebuild_reprocesses_all_events_from_scratch(setup):
    event_log, graph, faiss_index, dispatcher = setup
    event_log.append("document.added", {"title": "Doc1", "text": "über FAISS"}, "cli")
    asyncio.run(dispatcher.process_pending())
    assert graph.counts()["nodes"] == 2  # Dokument + Entität

    summary = asyncio.run(rebuild(event_log, graph, faiss_index, dispatcher))

    assert summary.processed == 1
    assert summary.failed == 0
    assert graph.counts()["nodes"] == 2
    assert event_log.counts()["processed"] == 1


def test_rebuild_clears_faiss_index_before_replay(setup):
    event_log, graph, faiss_index, dispatcher = setup
    event_id = event_log.append("document.added", {"title": "Doc1", "text": "Text"}, "cli")
    asyncio.run(dispatcher.process_pending())

    asyncio.run(rebuild(event_log, graph, faiss_index, dispatcher))

    results = faiss_index.search([1.0, 0.0, 0.0, 0.0], k=5)
    assert [faiss_id for faiss_id, _ in results] == [event_id]
