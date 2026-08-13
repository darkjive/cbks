import asyncio
import json
import sqlite3
from dataclasses import dataclass
from typing import Optional

import pytest

from backend.services.agents.prefrontal import PrefrontalAgent
from backend.services.agents.temporal import TemporalAgent
from backend.services.dispatcher import Dispatcher
from backend.services.entity_resolver import EntityResolver
from backend.services.event_log import EventLog
from backend.services.graph_backend import GraphBackend
from backend.services.ingestion import ingest_file
from backend.services.vault_import import (
    VaultScanState, abort_unfinished_jobs, create_job, iter_vault_files, load_state,
    save_state, scan_vault,
)
from backend.storage.faiss_index import FaissIndex
from backend.storage.sqlite_db import get_connection, init_db


class FakeLLMClient:
    def generate(self, prompt: str, format: str = "") -> str:
        return json.dumps({"classification": "document", "entities": []})


class FakeEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 1.0]


@dataclass
class FakeContext:
    event_log: EventLog
    dispatcher: Dispatcher
    faiss_index: FaissIndex
    conn: sqlite3.Connection
    vlm_client: Optional[object] = None


def make_context(tmp_path):
    conn = get_connection(tmp_path / "test.db", check_same_thread=False)
    init_db(conn)
    event_log = EventLog(conn)
    graph = GraphBackend(conn)
    faiss_index = FaissIndex(dim=4, index_path=tmp_path / "index.faiss")
    llm = FakeLLMClient()
    prefrontal = PrefrontalAgent(llm)
    temporal = TemporalAgent(FakeEmbeddingClient())
    resolver = EntityResolver(graph, temporal, llm)
    dispatcher = Dispatcher(event_log, graph, faiss_index, temporal, prefrontal, resolver, "bge-m3")
    return FakeContext(event_log=event_log, dispatcher=dispatcher, faiss_index=faiss_index, conn=conn), graph


def test_iter_vault_files_finds_supported_and_skips_unsupported(tmp_path):
    (tmp_path / "notiz.md").write_text("hallo")
    (tmp_path / "notiz.markdown").write_text("hallo")
    (tmp_path / "bild.png").write_bytes(b"\x89PNG")
    (tmp_path / "irrelevant.txt").write_text("nope")
    (tmp_path / "irrelevant.mp3").write_bytes(b"id3")

    files = iter_vault_files(tmp_path)

    names = {p.name for p in files}
    assert names == {"notiz.md", "notiz.markdown", "bild.png"}


def test_iter_vault_files_skips_excluded_dirs_and_hidden_files(tmp_path):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "config.md").write_text("x")
    (tmp_path / ".trash").mkdir()
    (tmp_path / ".trash" / "geloescht.md").write_text("x")
    (tmp_path / ".hidden.md").write_text("x")
    (tmp_path / "sichtbar.md").write_text("x")
    nested = tmp_path / "unterordner"
    nested.mkdir()
    (nested / "tief.md").write_text("x")

    files = iter_vault_files(tmp_path)

    names = {p.name for p in files}
    assert names == {"sichtbar.md", "tief.md"}


def test_scan_vault_counts_processed_and_sets_total(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "eins.md").write_text("Text über FAISS")
    (vault / "zwei.md").write_text("Text über Ollama")
    ctx, graph = make_context(tmp_path)
    create_job(ctx.conn, "job1")

    state = VaultScanState()
    asyncio.run(scan_vault(vault, ctx, state, "job1"))

    assert state.total == 2
    assert state.scanned == 2
    assert state.processed == 2
    assert state.duplicates == 0
    assert state.failed == 0
    assert state.done is True
    assert state.error is None
    assert len(graph.get_all_nodes()) >= 2


def test_scan_vault_counts_duplicate(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "eins.md"
    note.write_text("Identischer Text")
    ctx, graph = make_context(tmp_path)
    ingest_file(note, ctx.event_log, source="vault")
    create_job(ctx.conn, "job1")

    state = VaultScanState()
    asyncio.run(scan_vault(vault, ctx, state, "job1"))

    assert state.total == 1
    assert state.duplicates == 1
    assert state.processed == 0
    assert state.done is True


def test_scan_vault_counts_failed_file_and_continues(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "kaputt.pdf").write_bytes(b"das ist kein echtes PDF")
    (vault / "gut.md").write_text("Text über FAISS")
    ctx, graph = make_context(tmp_path)
    create_job(ctx.conn, "job1")

    state = VaultScanState()
    asyncio.run(scan_vault(vault, ctx, state, "job1"))

    assert state.total == 2
    assert state.scanned == 2
    assert state.failed == 1
    assert state.processed == 1
    assert state.done is True
    assert state.error is None


def test_scan_vault_handles_empty_vault(tmp_path):
    vault = tmp_path / "leer"
    vault.mkdir()
    ctx, graph = make_context(tmp_path)
    create_job(ctx.conn, "job1")

    state = VaultScanState()
    asyncio.run(scan_vault(vault, ctx, state, "job1"))

    assert state.total == 0
    assert state.scanned == 0
    assert state.done is True
    assert state.error is None


@pytest.fixture()
def conn(tmp_path):
    conn = get_connection(tmp_path / "jobs.db")
    init_db(conn)
    return conn


def test_job_state_roundtrip(conn):
    create_job(conn, "job1")
    state = VaultScanState(total=5, scanned=3, processed=2, duplicates=1, failed=0,
                           processing_total=4, processing_done=2, done=False, error=None)
    save_state(conn, "job1", state)

    loaded = load_state(conn, "job1")

    assert loaded == state


def test_load_unknown_job_returns_none(conn):
    assert load_state(conn, "gibtsnicht") is None


def test_abort_unfinished_jobs_marks_running_jobs(conn):
    create_job(conn, "laeuft")
    create_job(conn, "fertig")
    save_state(conn, "fertig", VaultScanState(done=True))

    abort_unfinished_jobs(conn)

    aborted = load_state(conn, "laeuft")
    assert aborted.done is True
    assert aborted.error == "Durch Server-Neustart abgebrochen"
    untouched = load_state(conn, "fertig")
    assert untouched.done is True
    assert untouched.error is None
