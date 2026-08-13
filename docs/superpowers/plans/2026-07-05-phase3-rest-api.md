# CBKS Phase 3.1 (REST-API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** REST-API (FastAPI) als zweiter, unabhängiger Konsument der bestehenden Service-Schicht aus Phase 2 — 1:1-Parität zu den 9 CLI-Befehlen, plus Fix des seit Phase 1 kaputten Docker-Imports.

**Architecture:** `backend/app_context.py` (aus `cli.py` extrahiert) baut pro Aufruf einen frischen `AppContext` (Config, DB-Connection, Services, Agenten). `backend/main.py` registriert 9 FastAPI-Routen, die exakt dieselben Service-Funktionen aufrufen wie `cli.py`. Alle Routen sind als plain `def` (nicht `async def`) definiert — FastAPI/Starlette führt sie dann in einem Threadpool aus, wodurch `asyncio.run(...)`-Aufrufe (wie in `dispatcher.process_pending()` und intern in `rag_service.ask/search`) funktionieren, ohne mit dem Uvicorn-Event-Loop zu kollidieren.

**Tech Stack:** FastAPI 0.139.0, Uvicorn 0.50.0, Pydantic 2.13.4, python-multipart 0.0.32 (alle bereits in `backend/requirements.txt` gepinnt, keine neuen Abhängigkeiten nötig).

## Global Constraints

- Alle Timestamps: ISO-8601 UTC (`datetime.now(timezone.utc).isoformat()`), konsistent mit Phase 2.
- Kein Dispatcher-Daemon: alle API-Routen sind synchron/blockierend, exakt wie die CLI-Befehle.
- Ollama-Modelle: `qwen3:8b` (LLM), `bge-m3` (Embeddings, 1024 Dimensionen) — unverändert aus Phase 1/2.
- Direkt auf `main` committen, keine Branches/Worktrees (Projekt-Konvention seit Phase 1).
- Jede Route ruft `backend.app_context.build_context()` frisch auf (kein globaler State, kein Connection-Pooling) — konsistent mit `cli.py`s Pro-Invocation-Muster.
- Alle Routen sind `def`, nicht `async def` (siehe Architecture-Absatz — Korrektheitsanforderung, kein Stil-Detail).

---

### Task 1: app_context.py extrahieren (Refactor, verhaltensneutral)

**Files:**
- Create: `backend/app_context.py`
- Modify: `backend/cli.py` (komplett, siehe Step 5)
- Test: `backend/tests/test_app_context.py`

**Interfaces:**
- Produces: `AppContext` (Dataclass: `config, conn, event_log, graph, faiss_index, temporal_agent, prefrontal_agent, dispatcher`), `build_context() -> AppContext`. Wird von `cli.py` (Task 1) und `backend/main.py` (Task 5) genutzt.

- [ ] **Step 1: Fehlschlagenden Test schreiben**

`backend/tests/test_app_context.py`:

```python
import sqlite3

from backend.app_context import AppContext, build_context
from backend.services.dispatcher import Dispatcher
from backend.services.event_log import EventLog
from backend.services.graph_backend import GraphBackend
from backend.storage.faiss_index import FaissIndex


def test_build_context_returns_wired_appcontext(tmp_path, monkeypatch):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))

    ctx = build_context()

    assert isinstance(ctx, AppContext)
    assert isinstance(ctx.conn, sqlite3.Connection)
    assert isinstance(ctx.event_log, EventLog)
    assert isinstance(ctx.graph, GraphBackend)
    assert isinstance(ctx.faiss_index, FaissIndex)
    assert isinstance(ctx.dispatcher, Dispatcher)
```

- [ ] **Step 2: Test ausführen, Fehlschlag verifizieren**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_app_context.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.app_context'`.

- [ ] **Step 3: Implementieren**

`backend/app_context.py`:

```python
import sqlite3
from dataclasses import dataclass

from backend.config import Config
from backend.services.agents.prefrontal import OllamaLLMClient, PrefrontalAgent
from backend.services.agents.temporal import OllamaEmbeddingClient, TemporalAgent
from backend.services.dispatcher import Dispatcher
from backend.services.event_log import EventLog
from backend.services.graph_backend import GraphBackend
from backend.storage.faiss_index import FaissIndex
from backend.storage.sqlite_db import get_connection, init_db


@dataclass
class AppContext:
    config: Config
    conn: sqlite3.Connection
    event_log: EventLog
    graph: GraphBackend
    faiss_index: FaissIndex
    temporal_agent: TemporalAgent
    prefrontal_agent: PrefrontalAgent
    dispatcher: Dispatcher


def build_context() -> AppContext:
    config = Config.from_env()
    conn = get_connection(config.database_path)
    init_db(conn)
    event_log = EventLog(conn)
    graph = GraphBackend(conn)
    faiss_index = FaissIndex(dim=config.embedding_dim, index_path=config.faiss_index_path)
    temporal_agent = TemporalAgent(OllamaEmbeddingClient(config.ollama_host, config.embedding_model))
    prefrontal_agent = PrefrontalAgent(OllamaLLMClient(config.ollama_host, config.llm_model))
    dispatcher = Dispatcher(
        event_log, graph, faiss_index, temporal_agent, prefrontal_agent, config.embedding_model
    )
    return AppContext(
        config=config, conn=conn, event_log=event_log, graph=graph, faiss_index=faiss_index,
        temporal_agent=temporal_agent, prefrontal_agent=prefrontal_agent, dispatcher=dispatcher,
    )
```

- [ ] **Step 4: Test ausführen, Erfolg verifizieren**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_app_context.py -v`
Expected: `1 passed`.

- [ ] **Step 5: cli.py auf app_context.py umstellen**

Ersetze den kompletten Inhalt von `backend/cli.py` durch:

```python
import asyncio
import subprocess
from pathlib import Path

import typer

from backend.app_context import build_context
from backend.services import rag as rag_service
from backend.services import rebuild as rebuild_service
from backend.services.ingestion import ingest_file, ingest_note

app = typer.Typer()


@app.command()
def add(datei: str) -> None:
    ctx = build_context()
    result = ingest_file(Path(datei), ctx.event_log)
    if result.duplicate:
        typer.echo(f"Bereits bekannt seit {result.duplicate_since}")
        return
    summary = asyncio.run(ctx.dispatcher.process_pending())
    ctx.faiss_index.save()
    typer.echo(f"Verarbeitet: {summary.processed}, Fehlgeschlagen: {summary.failed}")


@app.command()
def note(text: str) -> None:
    ctx = build_context()
    result = ingest_note(text, ctx.event_log)
    if result.duplicate:
        typer.echo(f"Bereits bekannt seit {result.duplicate_since}")
        return
    summary = asyncio.run(ctx.dispatcher.process_pending())
    ctx.faiss_index.save()
    typer.echo(f"Verarbeitet: {summary.processed}, Fehlgeschlagen: {summary.failed}")


@app.command()
def ask(frage: str) -> None:
    ctx = build_context()
    result = rag_service.ask(
        frage, ctx.temporal_agent, ctx.faiss_index, ctx.graph, ctx.prefrontal_agent
    )
    typer.echo(result.answer)
    typer.echo("Quellen: " + ", ".join(result.sources))


@app.command()
def search(begriff: str) -> None:
    ctx = build_context()
    hits = rag_service.search(begriff, ctx.temporal_agent, ctx.faiss_index, ctx.graph, limit=10)
    for hit in hits:
        typer.echo(f"{hit.node.title} (score={hit.score:.3f})")


@app.command()
def show(node_id: str) -> None:
    ctx = build_context()
    node = ctx.graph.get_node(node_id)
    if node is None:
        typer.echo("Node nicht gefunden")
        raise typer.Exit(code=1)
    typer.echo(f"{node.title} ({node.type})")
    for neighbor in ctx.graph.get_neighbors(node_id):
        typer.echo(f"  - {neighbor.title} ({neighbor.type})")


@app.command()
def stats() -> None:
    ctx = build_context()
    event_counts = ctx.event_log.counts()
    graph_counts = ctx.graph.counts()
    typer.echo(f"Events: {event_counts}")
    typer.echo(f"Graph: {graph_counts}")


@app.command()
def retry() -> None:
    ctx = build_context()
    summary = asyncio.run(ctx.dispatcher.process_events(ctx.event_log.failed()))
    ctx.faiss_index.save()
    typer.echo(f"Erneut verarbeitet: {summary.processed}, weiterhin fehlgeschlagen: {summary.failed}")


@app.command()
def rebuild() -> None:
    ctx = build_context()
    summary = rebuild_service.rebuild(ctx.event_log, ctx.graph, ctx.faiss_index, ctx.dispatcher)
    ctx.faiss_index.save()
    typer.echo(f"Rebuild abgeschlossen: {summary.processed} verarbeitet, {summary.failed} fehlgeschlagen")


@app.command()
def backup() -> None:
    ctx = build_context()
    subprocess.run([str(ctx.config.backup_script_path)], check=True)
    typer.echo("Backup abgeschlossen")


if __name__ == "__main__":
    app()
```

- [ ] **Step 6: Bestehende CLI-Tests als Regressionstest ausführen**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_cli.py backend/tests/test_app_context.py -v`
Expected: alle Tests weiterhin `passed` (8 aus test_cli.py + 1 neu aus test_app_context.py) — reines Refactoring, kein Verhalten geändert.

- [ ] **Step 7: Commit**

```bash
git add backend/app_context.py backend/cli.py backend/tests/test_app_context.py
git commit -m "refactor: AppContext/build_context aus cli.py nach app_context.py extrahiert (DRY-Basis für REST-API)"
```

---

### Task 2: Config um API-Key erweitern

**Files:**
- Modify: `backend/config.py` (komplett, siehe Step 3)
- Modify: `backend/tests/test_config.py` (Ergänzung, siehe Step 1)

**Interfaces:**
- Produces: `Config.api_key: Optional[str]`, gelesen aus `CBKS_API_KEY`. Genutzt von `backend/auth.py` (Task 3).

- [ ] **Step 1: Fehlschlagenden Test schreiben**

Füge an das Ende von `backend/tests/test_config.py` an:

```python

def test_from_env_api_key_default_none(monkeypatch):
    monkeypatch.delenv("CBKS_API_KEY", raising=False)

    config = Config.from_env()

    assert config.api_key is None


def test_from_env_api_key_set(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")

    config = Config.from_env()

    assert config.api_key == "secret123"
```

- [ ] **Step 2: Test ausführen, Fehlschlag verifizieren**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_config.py -v`
Expected: FAIL mit `AttributeError: 'Config' object has no attribute 'api_key'`.

- [ ] **Step 3: Implementieren**

Ersetze den kompletten Inhalt von `backend/config.py` durch:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    data_dir: Path
    database_path: Path
    faiss_index_path: Path
    ollama_host: str
    llm_model: str
    embedding_model: str
    embedding_dim: int
    backup_script_path: Path
    api_key: Optional[str]

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = Path(os.environ.get("CBKS_DATA_DIR", str(REPO_ROOT / "data")))
        return cls(
            data_dir=data_dir,
            database_path=Path(os.environ.get("CBKS_DATABASE_PATH", str(data_dir / "cbks.db"))),
            faiss_index_path=Path(
                os.environ.get("CBKS_FAISS_PATH", str(data_dir / "faiss_index" / "index.faiss"))
            ),
            ollama_host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
            llm_model=os.environ.get("CBKS_LLM_MODEL", "qwen3:8b"),
            embedding_model=os.environ.get("CBKS_EMBEDDING_MODEL", "bge-m3"),
            embedding_dim=1024,
            backup_script_path=Path(
                os.environ.get("CBKS_BACKUP_SCRIPT", str(data_dir / "backup.sh"))
            ),
            api_key=os.environ.get("CBKS_API_KEY"),
        )
```

- [ ] **Step 4: Test ausführen, Erfolg verifizieren**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_config.py -v`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/tests/test_config.py
git commit -m "feat: Config um optionalen CBKS_API_KEY erweitern"
```

---

### Task 3: API-Key-Auth-Dependency

**Files:**
- Create: `backend/auth.py`
- Test: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `Config.api_key` (Task 2).
- Produces: `require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None`, eine FastAPI-Dependency. Genutzt von `backend/main.py` (Task 5) als `dependencies=[Depends(require_api_key)]` auf App-Ebene.

- [ ] **Step 1: Fehlschlagenden Test schreiben**

`backend/tests/test_auth.py`:

```python
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.auth import require_api_key


def _make_app() -> FastAPI:
    app = FastAPI(dependencies=[Depends(require_api_key)])

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    return app


def test_no_api_key_configured_allows_request(monkeypatch):
    monkeypatch.delenv("CBKS_API_KEY", raising=False)
    client = TestClient(_make_app())

    response = client.get("/ping")

    assert response.status_code == 200


def test_missing_header_rejected_when_key_configured(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")
    client = TestClient(_make_app())

    response = client.get("/ping")

    assert response.status_code == 401


def test_correct_header_accepted(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")
    client = TestClient(_make_app())

    response = client.get("/ping", headers={"X-API-Key": "secret123"})

    assert response.status_code == 200


def test_wrong_header_rejected(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")
    client = TestClient(_make_app())

    response = client.get("/ping", headers={"X-API-Key": "wrong"})

    assert response.status_code == 401
```

- [ ] **Step 2: Test ausführen, Fehlschlag verifizieren**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_auth.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.auth'`.

- [ ] **Step 3: Implementieren**

`backend/auth.py`:

```python
from typing import Optional

from fastapi import Header, HTTPException, status

from backend.config import Config


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    config = Config.from_env()
    if config.api_key is None:
        return
    if x_api_key != config.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiger oder fehlender API-Key"
        )
```

- [ ] **Step 4: Test ausführen, Erfolg verifizieren**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_auth.py -v`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/auth.py backend/tests/test_auth.py
git commit -m "feat: API-Key-Auth-Dependency (X-API-Key Header, nur aktiv wenn CBKS_API_KEY gesetzt)"
```

---

### Task 4: API-Response-/Request-Modelle

**Files:**
- Create: `backend/api_models.py`
- Test: `backend/tests/test_api_models.py`

**Interfaces:**
- Consumes: `backend.models.nodes.Node` (Phase 2).
- Produces: `IngestResponse`, `NoteRequest`, `AskRequest`, `AskResponse`, `SearchHitResponse`, `NodeResponse`, `StatsResponse`, `ProcessSummaryResponse`, `BackupResponse` (alle Pydantic `BaseModel`). Genutzt von `backend/main.py` (Task 5).

- [ ] **Step 1: Fehlschlagenden Test schreiben**

`backend/tests/test_api_models.py`:

```python
from backend.api_models import (
    AskRequest,
    AskResponse,
    BackupResponse,
    IngestResponse,
    NodeResponse,
    NoteRequest,
    ProcessSummaryResponse,
    SearchHitResponse,
    StatsResponse,
)
from backend.models.nodes import Node


def _make_node() -> Node:
    return Node(
        id="n1", title="Test", type="document",
        creation_time="2026-07-05T00:00:00Z", last_access="2026-07-05T00:00:00Z",
    )


def test_ingest_response_duplicate_fields_optional():
    response = IngestResponse(event_id=-1, duplicate=True, duplicate_since="2026-07-01T00:00:00Z")
    assert response.processed is None
    assert response.failed is None


def test_ingest_response_success_fields():
    response = IngestResponse(event_id=1, duplicate=False, processed=1, failed=0)
    assert response.duplicate_since is None


def test_note_request_requires_text():
    body = NoteRequest(text="Hallo")
    assert body.text == "Hallo"


def test_ask_request_response_roundtrip():
    request = AskRequest(question="Was?")
    response = AskResponse(answer="Antwort", sources=["n1"])
    assert request.question == "Was?"
    assert response.sources == ["n1"]


def test_search_hit_response_wraps_node():
    hit = SearchHitResponse(node=_make_node(), score=0.9)
    assert hit.node.title == "Test"
    assert hit.score == 0.9


def test_node_response_wraps_node_and_neighbors():
    node = _make_node()
    response = NodeResponse(node=node, neighbors=[node])
    assert response.neighbors == [node]


def test_stats_response_shape():
    response = StatsResponse(
        events={"pending": 0, "processed": 1, "failed": 0}, graph={"nodes": 2, "edges": 1}
    )
    assert response.events["processed"] == 1


def test_process_summary_response_shape():
    response = ProcessSummaryResponse(processed=2, failed=1)
    assert response.processed == 2


def test_backup_response_status():
    response = BackupResponse(status="ok")
    assert response.status == "ok"
```

- [ ] **Step 2: Test ausführen, Fehlschlag verifizieren**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_api_models.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.api_models'`.

- [ ] **Step 3: Implementieren**

`backend/api_models.py`:

```python
from typing import Optional

from pydantic import BaseModel

from backend.models.nodes import Node


class IngestResponse(BaseModel):
    event_id: int
    duplicate: bool
    duplicate_since: Optional[str] = None
    processed: Optional[int] = None
    failed: Optional[int] = None


class NoteRequest(BaseModel):
    text: str


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    sources: list[str]


class SearchHitResponse(BaseModel):
    node: Node
    score: float


class NodeResponse(BaseModel):
    node: Node
    neighbors: list[Node]


class StatsResponse(BaseModel):
    events: dict[str, int]
    graph: dict[str, int]


class ProcessSummaryResponse(BaseModel):
    processed: int
    failed: int


class BackupResponse(BaseModel):
    status: str
```

- [ ] **Step 4: Test ausführen, Erfolg verifizieren**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_api_models.py -v`
Expected: `9 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/api_models.py backend/tests/test_api_models.py
git commit -m "feat: Pydantic Request-/Response-Modelle für die REST-API"
```

---

### Task 5: FastAPI-App mit allen 9 Routen

**Files:**
- Create: `backend/main.py` (ersetzt den bisherigen Stub-Kommentar)
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `build_context()` (Task 1), `require_api_key` (Task 3), alle `api_models`-Klassen (Task 4), `rag_service.ask/search` (Phase 2 Task 12), `rebuild_service.rebuild` (Phase 2 Task 11), `ingest_file/ingest_note` (Phase 2 Task 10).
- Produces: `backend.main.app` (FastAPI-Instanz). Genutzt von Task 6 (Docker) und Task 7 (E2E-Test) sowie später vom Frontend-Sub-Projekt.

- [ ] **Step 1: Fehlschlagenden Test schreiben**

`backend/tests/test_api.py`:

```python
import json

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.agents.prefrontal import OllamaLLMClient
from backend.services.agents.temporal import OllamaEmbeddingClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_ollama(monkeypatch):
    def fake_embed(self, text: str) -> list[float]:
        return [1.0] + [0.0] * 1023

    def fake_generate(self, prompt: str) -> str:
        if "Beantworte die folgende Frage" in prompt:
            return "Das Dokument handelt von FAISS."
        return json.dumps({"classification": "document", "entities": ["FAISS"]})

    monkeypatch.setattr(OllamaEmbeddingClient, "embed", fake_embed)
    monkeypatch.setattr(OllamaLLMClient, "generate", fake_generate)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CBKS_API_KEY", raising=False)
    return tmp_path


def test_stats_on_empty_db():
    response = client.get("/stats")
    assert response.status_code == 200
    assert response.json()["events"] == {"pending": 0, "processed": 0, "failed": 0}


def test_note_then_ask_end_to_end():
    note_response = client.post("/notes", json={"text": "Ein Text über FAISS"})
    assert note_response.status_code == 200
    assert note_response.json()["failed"] == 0

    ask_response = client.post("/ask", json={"question": "Worum geht es?"})
    assert ask_response.status_code == 200
    assert ask_response.json()["answer"] == "Das Dokument handelt von FAISS."
    assert len(ask_response.json()["sources"]) == 1


def test_search_finds_ingested_note():
    client.post("/notes", json={"text": "Ein Text über Graphentheorie"})

    response = client.get("/search", params={"q": "Graphentheorie"})

    assert response.status_code == 200
    hits = response.json()
    assert len(hits) == 1
    assert hits[0]["node"]["title"] == "Ein Text über Graphentheorie"


def test_add_document_duplicate_reports_conflict():
    content = b"Einmaliger Inhalt"
    first = client.post("/documents", files={"file": ("doc.md", content, "text/markdown")})
    assert first.status_code == 200
    assert first.json()["duplicate"] is False

    second = client.post("/documents", files={"file": ("doc.md", content, "text/markdown")})
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["duplicate_since"] is not None


def test_get_unknown_node_returns_404():
    response = client.get("/nodes/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "Node nicht gefunden"


def test_get_known_node_returns_node_and_neighbors():
    client.post("/notes", json={"text": "Ein Text über Graphentheorie"})
    search_response = client.get("/search", params={"q": "Graphentheorie"})
    node_id = search_response.json()[0]["node"]["id"]

    response = client.get(f"/nodes/{node_id}")

    assert response.status_code == 200
    assert response.json()["node"]["id"] == node_id
    assert len(response.json()["neighbors"]) == 1


def test_retry_reprocesses_failed_events(monkeypatch):
    calls = {"n": 0}

    def flaky_generate(self, prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Simulierter LLM-Fehler")
        return json.dumps({"classification": "document", "entities": ["FAISS"]})

    monkeypatch.setattr(OllamaLLMClient, "generate", flaky_generate)

    note_response = client.post("/notes", json={"text": "Text der zunächst fehlschlägt"})
    assert note_response.json()["failed"] == 1

    retry_response = client.post("/retry")
    assert retry_response.status_code == 200
    assert retry_response.json() == {"processed": 1, "failed": 0}


def test_rebuild_runs_without_error():
    client.post("/notes", json={"text": "Text für Rebuild"})

    response = client.post("/rebuild")

    assert response.status_code == 200
    assert response.json()["failed"] == 0


def test_backup_runs_configured_script(tmp_path, monkeypatch):
    script_path = tmp_path / "backup.sh"
    script_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script_path.chmod(0o755)
    monkeypatch.setenv("CBKS_BACKUP_SCRIPT", str(script_path))

    response = client.post("/backup")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_missing_api_key_rejected_when_configured(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")

    response = client.get("/stats")

    assert response.status_code == 401


def test_correct_api_key_accepted(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")

    response = client.get("/stats", headers={"X-API-Key": "secret123"})

    assert response.status_code == 200
```

- [ ] **Step 2: Test ausführen, Fehlschlag verifizieren**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_api.py -v`
Expected: FAIL — `backend.main` hat noch keine `app`, Import-/Attributfehler.

- [ ] **Step 3: Implementieren**

Ersetze den kompletten Inhalt von `backend/main.py` durch:

```python
import asyncio
import subprocess
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status

from backend.api_models import (
    AskRequest,
    AskResponse,
    BackupResponse,
    IngestResponse,
    NodeResponse,
    NoteRequest,
    ProcessSummaryResponse,
    SearchHitResponse,
    StatsResponse,
)
from backend.app_context import build_context
from backend.auth import require_api_key
from backend.services import rag as rag_service
from backend.services import rebuild as rebuild_service
from backend.services.ingestion import ingest_file, ingest_note

app = FastAPI(title="CBKS API", dependencies=[Depends(require_api_key)])


@app.post("/documents", response_model=IngestResponse)
def create_document(file: UploadFile = File(...)) -> IngestResponse:
    ctx = build_context()
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir) / file.filename
        tmp_path.write_bytes(file.file.read())
        result = ingest_file(tmp_path, ctx.event_log, source="api")
    if result.duplicate:
        return IngestResponse(
            event_id=result.event_id, duplicate=True, duplicate_since=result.duplicate_since
        )
    summary = asyncio.run(ctx.dispatcher.process_pending())
    ctx.faiss_index.save()
    return IngestResponse(
        event_id=result.event_id, duplicate=False,
        processed=summary.processed, failed=summary.failed,
    )


@app.post("/notes", response_model=IngestResponse)
def create_note(body: NoteRequest) -> IngestResponse:
    ctx = build_context()
    result = ingest_note(body.text, ctx.event_log, source="api")
    if result.duplicate:
        return IngestResponse(
            event_id=result.event_id, duplicate=True, duplicate_since=result.duplicate_since
        )
    summary = asyncio.run(ctx.dispatcher.process_pending())
    ctx.faiss_index.save()
    return IngestResponse(
        event_id=result.event_id, duplicate=False,
        processed=summary.processed, failed=summary.failed,
    )


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest) -> AskResponse:
    ctx = build_context()
    result = rag_service.ask(
        body.question, ctx.temporal_agent, ctx.faiss_index, ctx.graph, ctx.prefrontal_agent
    )
    return AskResponse(answer=result.answer, sources=result.sources)


@app.get("/search", response_model=list[SearchHitResponse])
def search(q: str, limit: int = 10) -> list[SearchHitResponse]:
    ctx = build_context()
    hits = rag_service.search(q, ctx.temporal_agent, ctx.faiss_index, ctx.graph, limit=limit)
    return [SearchHitResponse(node=hit.node, score=hit.score) for hit in hits]


@app.get("/nodes/{node_id}", response_model=NodeResponse)
def get_node(node_id: str) -> NodeResponse:
    ctx = build_context()
    node = ctx.graph.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node nicht gefunden")
    neighbors = ctx.graph.get_neighbors(node_id)
    return NodeResponse(node=node, neighbors=neighbors)


@app.get("/stats", response_model=StatsResponse)
def stats() -> StatsResponse:
    ctx = build_context()
    return StatsResponse(events=ctx.event_log.counts(), graph=ctx.graph.counts())


@app.post("/retry", response_model=ProcessSummaryResponse)
def retry() -> ProcessSummaryResponse:
    ctx = build_context()
    summary = asyncio.run(ctx.dispatcher.process_events(ctx.event_log.failed()))
    ctx.faiss_index.save()
    return ProcessSummaryResponse(processed=summary.processed, failed=summary.failed)


@app.post("/rebuild", response_model=ProcessSummaryResponse)
def rebuild() -> ProcessSummaryResponse:
    ctx = build_context()
    summary = rebuild_service.rebuild(ctx.event_log, ctx.graph, ctx.faiss_index, ctx.dispatcher)
    ctx.faiss_index.save()
    return ProcessSummaryResponse(processed=summary.processed, failed=summary.failed)


@app.post("/backup", response_model=BackupResponse)
def backup() -> BackupResponse:
    ctx = build_context()
    subprocess.run([str(ctx.config.backup_script_path)], check=True)
    return BackupResponse(status="ok")
```

- [ ] **Step 4: Test ausführen, Erfolg verifizieren**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_api.py -v`
Expected: `11 passed`.

- [ ] **Step 5: Gesamte Suite laufen lassen**

Run: `cd $REPO && .venv/bin/python -m pytest backend -q`
Expected: alle bisherigen Tests weiterhin grün (60 aus Phase 2 + neue aus Tasks 1-5 dieses Plans), keine Regression.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_api.py
git commit -m "feat: REST-API (FastAPI) mit 9 Routen — 1:1-Parität zur CLI"
```

---

### Task 6: Docker-Fix (Import-Bug beheben)

**Files:**
- Modify: `docker/compose.yml` (komplett)
- Modify: `backend/Dockerfile` (komplett)

**Interfaces:**
- Consumes: `backend.main:app` (Task 5).
- Produces: lauffähiges Docker-Image, das die REST-API auf `127.0.0.1:8000` bereitstellt.

- [ ] **Step 1: docker/compose.yml korrigieren**

Ersetze den kompletten Inhalt von `docker/compose.yml` durch:

```yaml
services:
  cbks-backend:
    build:
      context: ..
      dockerfile: backend/Dockerfile
    container_name: cbks-backend
    network_mode: host          # Damit 127.0.0.1:11434 (natives Ollama) erreichbar ist
    volumes:
      - $REPO/data:/data
    environment:
      - OLLAMA_HOST=http://127.0.0.1:11434
      - CBKS_DATABASE_PATH=/data/cbks.db
      - CBKS_FAISS_PATH=/data/faiss_index/index.faiss
      - CBKS_BACKUP_SCRIPT=/data/backup.sh
    restart: unless-stopped
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

Hinweis: `context: ..` ist relativ zum Ordner der compose.yml (`docker/`), zeigt also auf das Repo-Root. `SNAPSHOT_PATH` entfällt — FAISS-Snapshots sind laut Phase-2-Design weiterhin zurückgestellt. Env-Var-Namen sind jetzt identisch zu `backend/config.py`s `CBKS_*`-Namen (vorher inkonsistent: `DATABASE_PATH`/`FAISS_PATH` statt `CBKS_DATABASE_PATH`/`CBKS_FAISS_PATH`).

- [ ] **Step 2: backend/Dockerfile korrigieren**

Ersetze den kompletten Inhalt von `backend/Dockerfile` durch:

```dockerfile
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app

# Nur Build-Werkzeuge – KEIN ROCm nötig:
# Das Backend spricht mit Ollama nur per HTTP, GPU-Zugriff hat allein das native Ollama auf dem Host.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/

RUN useradd -m cbksuser && chown -R cbksuser /app
USER cbksuser

# Kein --reload im Betrieb (Entwicklungsmodus).
# Host statt 0.0.0.0: Container läuft mit network_mode: host, daher muss
# Uvicorn selbst auf 127.0.0.1 binden, um "nur localhost" zu garantieren.
CMD ["uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"]
```

Kern des Fixes: Build-Context ist jetzt das Repo-Root (`..` von `docker/` aus), `COPY backend/ backend/` erzeugt im Image `/app/backend/...` (statt vorher `/app/...` ohne Package-Ordner) — dadurch funktioniert `from backend.config import Config` etc. im Container. `ENV PYTHONPATH=/app` stellt sicher, dass `backend` als Package gefunden wird, unabhängig davon, wie der `uvicorn`-Konsolenbefehl sein `sys.path` aufbaut.

- [ ] **Step 3: Build + Start verifizieren**

Run:
```bash
cd $REPO
docker compose -f docker/compose.yml build
docker compose -f docker/compose.yml up -d
```
Expected: Build erfolgreich (kann mehrere Minuten dauern, installiert faiss-cpu/pymupdf etc.), Container startet ohne `ImportError` im Log (`docker compose -f docker/compose.yml logs cbks-backend` zeigt eine laufende Uvicorn-Zeile wie `Uvicorn running on http://127.0.0.1:8000`).

- [ ] **Step 4: Funktionalen Smoke-Test gegen den Container fahren**

Run: `curl -s http://127.0.0.1:8000/stats`
Expected: JSON-Antwort wie `{"events":{"pending":0,"processed":0,"failed":0},"graph":{"nodes":0,"edges":0}}` (Zahlen können abweichen, falls `data/cbks.db` bereits Einträge aus vorherigen CLI-Läufen enthält — das ist erwartet, kein Fehler).

Falls Berechtigungsprobleme beim Zugriff auf `/data` auftreten (`cbksuser` im Container vs. Host-User `a`): das ist ein vorbestehendes Docker-Volume-Permission-Thema aus Phase 1, unabhängig vom hier behobenen Import-Bug — nicht Teil dieses Tasks, nur zur Kenntnisnahme dokumentieren falls es auftritt.

- [ ] **Step 5: Container stoppen**

Run: `cd $REPO && docker compose -f docker/compose.yml down`

- [ ] **Step 6: Commit**

```bash
git add docker/compose.yml backend/Dockerfile
git commit -m "fix: Docker-Build-Context auf Repo-Root, damit backend.*-Imports im Container funktionieren"
```

---

### Task 7: End-to-End-Test der REST-API (echtes Ollama)

**Files:**
- Create: `backend/tests/test_e2e_api_milestone.py`

**Interfaces:**
- Consumes: `backend.main.app` (Task 5), läuft In-Process via `TestClient` (kein Docker nötig) gegen die echte, native Ollama-Instanz.

- [ ] **Step 1: Test schreiben**

`backend/tests/test_e2e_api_milestone.py`:

```python
"""Echter Ende-zu-Ende-Test der REST-API gegen die laufende native Ollama-Instanz.

Voraussetzung: `systemctl --user status ollama` ist aktiv und `qwen3:8b`
sowie `bge-m3` sind gepullt. Kein Mock, keine Fakes - läuft In-Process via
FastAPI TestClient (kein Docker nötig, das API-Objekt läuft direkt im
Testprozess, spricht aber echtes HTTP mit dem echten Ollama).
"""
import ollama
import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def _ollama_available() -> bool:
    try:
        ollama.Client(host="http://127.0.0.1:11434").list()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_available(), reason="Natives Ollama nicht erreichbar auf 127.0.0.1:11434"
)


def test_add_pdf_then_ask_via_api(tmp_path, monkeypatch):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CBKS_API_KEY", raising=False)

    import fitz

    pdf_path = tmp_path / "papier.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "CBKS ist ein persoenliches Wissensmanagementsystem. "
        "Es nutzt FAISS fuer Vektorsuche und Ollama fuer lokale Sprachmodelle.",
    )
    doc.save(str(pdf_path))
    doc.close()

    with open(pdf_path, "rb") as fh:
        add_response = client.post(
            "/documents", files={"file": ("papier.pdf", fh, "application/pdf")}
        )
    assert add_response.status_code == 200
    assert add_response.json()["failed"] == 0

    ask_response = client.post("/ask", json={"question": "Was macht CBKS?"})
    assert ask_response.status_code == 200
    assert len(ask_response.json()["answer"].strip()) > 0
    assert len(ask_response.json()["sources"]) >= 1


def test_rebuild_restores_graph_via_api(tmp_path, monkeypatch):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CBKS_API_KEY", raising=False)

    client.post("/notes", json={"text": "Eine Notiz über Graphentheorie und DAGs."})
    stats_before = client.get("/stats")
    assert stats_before.json()["events"]["processed"] == 1

    rebuild_response = client.post("/rebuild")
    assert rebuild_response.status_code == 200
    assert rebuild_response.json()["failed"] == 0

    stats_after = client.get("/stats")
    assert stats_after.json()["events"]["processed"] == 1
```

- [ ] **Step 2: Test ausführen**

Run: `cd $REPO && .venv/bin/python -m pytest backend/tests/test_e2e_api_milestone.py -v -s`
Expected: `2 passed` (echte Ollama-Latenz, kann 30-90s dauern). Falls Ollama nicht erreichbar: `2 skipped`.

- [ ] **Step 3: Gesamte Suite komplett laufen lassen**

Run: `cd $REPO && .venv/bin/python -m pytest backend -v`
Expected: alle Tests aus Phase 2 + diesem Plan zusammen `passed`, keine Warnings, kein Traceback.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_e2e_api_milestone.py
git commit -m "test: Ende-zu-Ende-Meilenstein der REST-API gegen echtes Ollama"
```

---

## Abschluss-Kriterium für Phase 3.1

1. Alle 9 REST-Endpunkte (`POST /documents`, `POST /notes`, `POST /ask`, `GET /search`, `GET /nodes/{id}`, `GET /stats`, `POST /retry`, `POST /rebuild`, `POST /backup`) funktionieren und sind mindestens einmal getestet.
2. `backend/cli.py` funktioniert nach dem Refactor (Task 1) unverändert — alle Phase-2-CLI-Tests bleiben grün.
3. Der Docker-Container aus Phase 1 startet fehlerfrei und beantwortet `curl http://127.0.0.1:8000/stats`.
4. Ein echter End-to-End-Test (PDF hochladen → fragen) läuft gegen die REST-API mit echtem Ollama durch.
5. `.venv/bin/python -m pytest backend -v` läuft vollständig durch.

Damit ist die Grundlage für das nächste Phase-3-Sub-Projekt (Frontend-Grundgerüst) gelegt.
