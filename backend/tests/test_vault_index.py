import json
from dataclasses import dataclass
from typing import Optional

import pytest

from backend.services.agents.prefrontal import PrefrontalAgent
from backend.services.agents.temporal import TemporalAgent
from backend.services.dispatcher import Dispatcher
from backend.services.entity_resolver import EntityResolver
from backend.services.event_log import EventLog
from backend.services.graph_backend import GraphBackend
from backend.services.vault_index import iter_vault_notes, index_file, rescan
from backend.storage.faiss_index import FaissIndex
from backend.storage.sqlite_db import get_connection, init_db


class _FakeLLMClient:
    def generate(self, prompt: str, format: str = "") -> str:
        return json.dumps({"classification": "note", "entities": []})


class _FakeEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 1.0]


@dataclass
class _FakeContext:
    event_log: EventLog
    dispatcher: Dispatcher
    faiss_index: FaissIndex
    graph: GraphBackend
    vlm_client: Optional[object] = None


def _make_ctx(tmp_path):
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
    return _FakeContext(event_log=event_log, dispatcher=dispatcher, faiss_index=faiss_index, graph=graph)


def test_iter_vault_notes_finds_markdown_and_skips_attachments(tmp_path):
    (tmp_path / "notiz.md").write_text("Text")
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "bild.png").write_bytes(b"\x89PNG")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")

    files = iter_vault_notes(tmp_path)

    names = {p.name for p in files}
    assert names == {"notiz.md"}


def test_index_file_assigns_id_to_file_without_frontmatter(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "notiz.md"
    note.write_text("Ein Text über FAISS")
    ctx = _make_ctx(tmp_path)

    import asyncio
    asyncio.run(index_file(note, vault, ctx))

    content = note.read_text()
    assert content.startswith("---\nid: ")
    nodes = ctx.graph.get_all_nodes()
    assert len(nodes) == 1
    assert nodes[0].metadata["source_path"] == "notiz.md"
    assert nodes[0].metadata["file_hash"]


def test_index_file_twice_is_idempotent_upsert(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "notiz.md"
    note.write_text("Erster Inhalt")
    ctx = _make_ctx(tmp_path)

    import asyncio
    asyncio.run(index_file(note, vault, ctx))
    first_id = ctx.graph.get_all_nodes()[0].id

    note.write_text(note.read_text().replace("Erster Inhalt", "Geänderter Inhalt"))
    asyncio.run(index_file(note, vault, ctx))

    nodes = ctx.graph.get_all_nodes()
    assert len(nodes) == 1
    assert nodes[0].id == first_id
    assert nodes[0].content == "Geänderter Inhalt"


def test_rescan_skips_unchanged_files(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "eins.md").write_text("Text über FAISS")
    ctx = _make_ctx(tmp_path)

    import asyncio
    first = asyncio.run(rescan(vault, ctx))
    assert first.processed == 1
    assert first.skipped == 0

    second = asyncio.run(rescan(vault, ctx))
    assert second.processed == 0
    assert second.skipped == 1


def test_rescan_full_reprocesses_unchanged_files(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "eins.md").write_text("Text über FAISS")
    ctx = _make_ctx(tmp_path)

    import asyncio
    asyncio.run(rescan(vault, ctx))
    result = asyncio.run(rescan(vault, ctx, full=True))

    assert result.processed == 1
    assert result.skipped == 0


def test_rescan_full_counts_both_unchanged_and_changed_files(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "unveraendert.md").write_text("Text über FAISS")
    (vault / "geaendert.md").write_text("Alter Inhalt")
    ctx = _make_ctx(tmp_path)

    import asyncio
    asyncio.run(rescan(vault, ctx))
    (vault / "geaendert.md").write_text("Neuer Inhalt")

    result = asyncio.run(rescan(vault, ctx, full=True))

    # "unveraendert.md" full-rescanned but byte-identical -> counted via the
    # DuplicateEventError path; "geaendert.md" genuinely changed -> counted
    # via process_pending(). Both must show up in the total.
    assert result.processed == 2


def test_rescan_does_not_mark_permanently_failed_file_as_processed(tmp_path):
    class _FailingLLMClient:
        def generate(self, prompt: str, format: str = "") -> str:
            raise RuntimeError("Ollama nicht erreichbar (simuliert)")

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "kaputt.md").write_text("Text der beim Indexieren scheitert")
    ctx = _make_ctx(tmp_path)
    ctx.dispatcher.prefrontal_agent = PrefrontalAgent(_FailingLLMClient())

    import asyncio
    first = asyncio.run(rescan(vault, ctx))
    assert first.processed == 0
    assert first.failed == 1
    assert ctx.graph.get_all_nodes() == []

    second = asyncio.run(rescan(vault, ctx))
    assert second.processed == 0
    assert second.failed == 1
    assert ctx.graph.get_all_nodes() == []


def test_rescan_deletes_node_for_removed_file(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "eins.md"
    note.write_text("Text über FAISS")
    ctx = _make_ctx(tmp_path)

    import asyncio
    asyncio.run(rescan(vault, ctx))
    assert len(ctx.graph.get_all_nodes()) == 1

    note.unlink()
    result = asyncio.run(rescan(vault, ctx))

    assert result.deleted == 1
    assert len(ctx.graph.get_all_nodes()) == 0


def test_rescan_creates_links_to_edge_for_wikilink(tmp_path):
    # Titel wird explizit per Frontmatter gesetzt, da der Fallback-Titel
    # sonst der Dateiname (path.stem) waere, nicht "Ziel-Notiz".
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "ziel.md").write_text("---\ntitle: Ziel-Notiz\n---\n\nZiel-Notiz Inhalt")
    (vault / "quelle.md").write_text("Verweist auf [[Ziel-Notiz]] im Text")
    ctx = _make_ctx(tmp_path)

    import asyncio
    asyncio.run(rescan(vault, ctx))

    target_node = ctx.graph.find_node_by_title("Ziel-Notiz")
    assert target_node is not None
    incoming = ctx.graph.get_incoming_edges(target_node.id, relation_type="links_to")
    assert len(incoming) == 1


def test_rescan_handles_empty_vault(tmp_path):
    vault = tmp_path / "leer"
    vault.mkdir()
    ctx = _make_ctx(tmp_path)

    import asyncio
    result = asyncio.run(rescan(vault, ctx))

    assert result.processed == 0
    assert result.deleted == 0


def test_deleted_vault_node_does_not_resurrect_on_rebuild(tmp_path):
    from backend.services import rebuild as rebuild_service

    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "n.md"
    note.write_text("Version 1")
    ctx = _make_ctx(tmp_path)

    import asyncio
    asyncio.run(rescan(vault, ctx))
    node_id = ctx.graph.get_all_nodes()[0].id

    current = note.read_text()
    note.write_text(current.replace("Version 1", "Version 2"))
    asyncio.run(rescan(vault, ctx))
    assert ctx.graph.get_all_nodes()[0].content == "Version 2"

    # Simuliert die Cleanup-Sequenz von DELETE /vault/file direkt gegen
    # graph/event_log, da dieser Test nur ctx.graph/ctx.event_log hat,
    # nicht die FastAPI-App.
    ctx.event_log.delete_by_vault_node_id(node_id)
    faiss_id = ctx.graph.delete_node(node_id)
    if faiss_id is not None:
        ctx.faiss_index.remove(faiss_id)
        ctx.event_log.delete(faiss_id)
    assert ctx.graph.get_all_nodes() == []

    asyncio.run(rebuild_service.rebuild(ctx.event_log, ctx.graph, ctx.faiss_index, ctx.dispatcher))

    assert ctx.graph.get_all_nodes() == []


def test_rebuild_drops_links_to_and_file_hash_but_full_reindex_restores_them(tmp_path):
    from backend.services import rebuild as rebuild_service

    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "ziel.md").write_text("---\ntitle: Ziel-Notiz\n---\n\nInhalt")
    (vault / "quelle.md").write_text("Verweist auf [[Ziel-Notiz]]")
    ctx = _make_ctx(tmp_path)

    import asyncio
    asyncio.run(rescan(vault, ctx))
    target = ctx.graph.find_node_by_title("Ziel-Notiz")
    assert len(ctx.graph.get_incoming_edges(target.id, relation_type="links_to")) == 1
    assert all(n.metadata.get("file_hash") for n in ctx.graph.get_all_nodes())

    asyncio.run(rebuild_service.rebuild(ctx.event_log, ctx.graph, ctx.faiss_index, ctx.dispatcher))

    # Dokumentierte Einschraenkung: nach rebuild() sind file_hash/links_to weg.
    target_after_rebuild = ctx.graph.find_node_by_title("Ziel-Notiz")
    assert ctx.graph.get_incoming_edges(target_after_rebuild.id, relation_type="links_to") == []
    assert all(n.metadata.get("file_hash") is None for n in ctx.graph.get_all_nodes())

    # Dokumentierter Workaround: cbks index --full stellt beides wieder her.
    asyncio.run(rescan(vault, ctx, full=True))
    target_restored = ctx.graph.find_node_by_title("Ziel-Notiz")
    assert len(ctx.graph.get_incoming_edges(target_restored.id, relation_type="links_to")) == 1
    assert all(n.metadata.get("file_hash") for n in ctx.graph.get_all_nodes())

    # Regression: file_hash ist jetzt wieder konsistent, ein weiterer
    # `cbks index --full` darf die links_to-Kante nicht erneut anlegen.
    asyncio.run(rescan(vault, ctx, full=True))
    target_again = ctx.graph.find_node_by_title("Ziel-Notiz")
    assert len(ctx.graph.get_incoming_edges(target_again.id, relation_type="links_to")) == 1
