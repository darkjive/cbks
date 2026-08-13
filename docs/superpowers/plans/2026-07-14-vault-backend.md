# Vault-Backend (Phase 1+2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Den Vault-Ordner (`CBKS_VAULT_DIR`) als Source of Truth etablieren: Dateien tragen ihre Node-Identität im Frontmatter, ein Rescan/Index-Mechanismus upserted Nodes statt sie zu duplizieren, und eine vollständige `/vault/*`-API macht das Backend editorfähig — ohne die bestehende Ingest-Pipeline (Dokument-Upload, Notizen, alter Obsidian-Importer unter `/vault/scan`) zu verändern.

**Architecture:** Dieser Plan deckt **Phase 1 + Phase 2** aus `docs/superpowers/specs/2026-07-14-obsidian-abloesung-design.md` ab (Deliverable laut Spec-Phasentabelle: "Backend komplett editorfähig"). Phase 3–5 (Editor-Frontend) und Phase 6 (Schreibpfad-Umstellung `note`/`add`, Rescan-bei-Serverstart, Spec-Update) sind **bewusst nicht Teil dieses Plans** — Phase 6 hängt laut Spec-Reihenfolge am fertigen Editor und würde Kernkommandos (`cbks note`, `POST /notes`) verhalten-brechend umstellen, bevor ein Editor existiert, der das auffängt.

Kernidee der Datenmodell-Inversion: Der Dispatcher (`backend/services/dispatcher.py`) verarbeitet heute jedes Event als reines "neuer Node" (`id=uuid4()`, `INSERT`). Für Vault-Dateien braucht es stattdessen **Upsert-by-frontmatter-id**: Trägt das Event-Payload ein `node_id`-Feld, wird der bestehende Node aktualisiert (Content, Embedding-Vektor, abgeleitete Kanten neu berechnet) statt dupliziert zu werden. Nicht-Vault-Events (`document.added`, `note.created`) durchlaufen exakt denselben Code-Pfad unverändert, weil sie nie `node_id` im Payload setzen. Das hält `cbks rebuild` (Event-Replay) automatisch korrekt: Mehrere Vault-Events mit derselben `node_id` konvergieren beim Replay zum letzten Stand.

**Tech Stack:** Python 3.12, FastAPI, SQLite (stdlib `sqlite3`), Typer CLI, pytest. Keine neuen Abhängigkeiten.

## Global Constraints

- Alle Nutzer-/Fehlertexte (CLI-Ausgaben, `HTTPException(detail=...)`) auf Deutsch, wie im restlichen Backend.
- Neuer Event-Type für Vault-Dateien: `"vault.file"` (analog zu `"document.added"`/`"note.created"`), Event-Source `"vault"`.
- Neuer Kantentyp für Wiki-Links: `"links_to"` — muss in `RelationType` (Literal in `backend/models/edges.py`) ergänzt werden.
- Node-`metadata`-Keys, die dieser Plan neu einführt: `source_path` (relativer Pfad ab Vault-Root, POSIX-Separatoren via `Path.as_posix()`), `file_hash` (Hash des rohen Dateiinhalts nach Frontmatter-id-Ergänzung, für Rescan-Unchanged-Skip — **nicht** identisch mit `node.content_hash`, das weiterhin der Event-Payload-Hash ist).
- `CBKS_VAULT_DIR` ist eine **andere** Config als das bestehende `CBKS_VAULT_PATH`/`vault_path` (alter Einweg-Importer unter `/vault/scan`, bleibt unverändert bestehen — kein Namens- oder Pfadkonflikt, da neue Endpoints `/vault/tree`, `/vault/file`, `/vault/rename`, `/vault/attachment`, `/vault/rescan`, `/vault/backlinks`, `/vault/search` heißen).
- Tests folgen dem bestehenden Muster: `CBKS_DATA_DIR=tmp_path`, Ollama gemockt (siehe `fake_ollama`-Fixture in `test_api.py` bzw. `FakeLLMClient`/`FakeEmbeddingClient` in `test_vault_import.py`). Kein Test braucht echtes Ollama.
- Tests ausführen: `.venv/bin/pytest` vom Repo-Root aus.
- Commit-Stil: conventional commits (`feat:`, `fix:`, `test:`).

---

### Task 1: Config `CBKS_VAULT_DIR`

**Files:**
- Modify: `backend/config.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Config.vault_dir: Optional[Path]` — neues Feld, `None` wenn `CBKS_VAULT_DIR` nicht gesetzt.

- [ ] **Step 1: Failing Tests schreiben**

An `backend/tests/test_config.py` anhängen (folgt exakt dem Muster von `test_from_env_vault_path_default_none`/`test_from_env_vault_path_set`):

```python
def test_from_env_vault_dir_default_none(monkeypatch):
    monkeypatch.delenv("CBKS_VAULT_DIR", raising=False)

    config = Config.from_env()

    assert config.vault_dir is None


def test_from_env_vault_dir_set(monkeypatch):
    monkeypatch.setenv("CBKS_VAULT_DIR", "$VAULT")

    config = Config.from_env()

    assert config.vault_dir == Path("$VAULT")
```

`Path` ist in `test_config.py` noch nicht importiert — am Dateianfang ergänzen:

```python
from pathlib import Path
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_config.py -v`
Expected: FAIL mit `AttributeError: 'Config' object has no attribute 'vault_dir'`

- [ ] **Step 3: `Config` erweitern**

In `backend/config.py` das Feld zur Dataclass hinzufügen (nach `vault_path: Optional[str]`):

```python
    vault_dir: Optional[Path]
```

Und in `from_env()` (nach `vault_path=os.environ.get("CBKS_VAULT_PATH"),`):

```python
            vault_dir=(
                Path(os.environ["CBKS_VAULT_DIR"]) if os.environ.get("CBKS_VAULT_DIR") else None
            ),
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_config.py -v`
Expected: PASS (alle Tests der Datei)

- [ ] **Step 5: Commit**

```bash
git add backend/config.py backend/tests/test_config.py
git commit -m "feat: CBKS_VAULT_DIR Config-Feld für Vault-Editor"
```

---

### Task 2: GraphBackend — Upsert-Primitive

**Files:**
- Modify: `backend/services/graph_backend.py`
- Test: `backend/tests/test_graph_backend.py`

**Interfaces:**
- Consumes: `backend.models.nodes.Node`, `backend.models.edges.Edge` (unverändert).
- Produces (neue public Methoden auf `GraphBackend`):
  - `upsert_node(node: Node) -> None` — INSERT, bei existierender `id` UPDATE aller Felder.
  - `replace_vector(node_id: str, faiss_id: int, model: str) -> Optional[int]` — setzt `node_vectors`-Zeile, gibt die **alte** `faiss_id` zurück (oder `None`, wenn keine existierte).
  - `delete_outgoing_edges(node_id: str) -> None` — löscht alle Kanten mit `source = node_id`.
  - `update_metadata_fields(node_id: str, updates: dict) -> None` — merged `updates` in `node.metadata`, no-op wenn Node nicht existiert.
  - `get_incoming_edges(node_id: str, relation_type: Optional[str] = None) -> list[Edge]`.
  - `search_vault_content(query: str, limit: int = 20) -> list[Node]` — `LIKE`-Suche über `title`/`content`, nur Nodes mit `metadata.source_path`.

- [ ] **Step 1: Failing Tests schreiben**

An `backend/tests/test_graph_backend.py` anhängen. Zuerst prüfen, welche Imports/Helper (z. B. eine `make_node()`-Fabrik oder direkte `Node(...)`-Konstruktion, `conn`-Fixture) die Datei bereits nutzt, und diesem Muster folgen. Falls kein Helper existiert, folgende self-contained Tests anhängen:

```python
from datetime import datetime, timezone

from backend.models.edges import Edge
from backend.models.nodes import Node


def _make_node(node_id: str, title: str = "Titel", content: str = "Inhalt") -> Node:
    now = datetime.now(timezone.utc).isoformat()
    return Node(
        id=node_id, title=title, type="note", content=content,
        creation_time=now, last_access=now,
    )


def test_upsert_node_inserts_new_node(conn):
    graph = GraphBackend(conn)
    node = _make_node("n1")

    graph.upsert_node(node)

    fetched = graph.get_node("n1")
    assert fetched is not None
    assert fetched.title == "Titel"
    assert "n1" in graph.graph.nodes


def test_upsert_node_updates_existing_node(conn):
    graph = GraphBackend(conn)
    graph.add_node(_make_node("n1", title="Alt", content="Alter Inhalt"))

    graph.upsert_node(_make_node("n1", title="Neu", content="Neuer Inhalt"))

    fetched = graph.get_node("n1")
    assert fetched.title == "Neu"
    assert fetched.content == "Neuer Inhalt"
    assert len(graph.get_all_nodes()) == 1


def test_replace_vector_returns_old_faiss_id_and_updates(conn):
    graph = GraphBackend(conn)
    graph.add_node(_make_node("n1"))
    graph.link_vector("n1", faiss_id=10, model="bge-m3")

    old_id = graph.replace_vector("n1", faiss_id=20, model="bge-m3")

    assert old_id == 10
    row = conn.execute("SELECT faiss_id FROM node_vectors WHERE node_id = 'n1'").fetchone()
    assert row["faiss_id"] == 20


def test_replace_vector_returns_none_when_no_prior_vector(conn):
    graph = GraphBackend(conn)
    graph.add_node(_make_node("n1"))

    old_id = graph.replace_vector("n1", faiss_id=20, model="bge-m3")

    assert old_id is None


def test_delete_outgoing_edges_removes_only_source_matches(conn):
    graph = GraphBackend(conn)
    graph.add_node(_make_node("n1"))
    graph.add_node(_make_node("n2"))
    graph.add_node(_make_node("n3"))
    now = datetime.now(timezone.utc).isoformat()
    graph.add_edge(Edge(id="e1", source="n1", target="n2", relation_type="mentions",
                         creation_time=now, last_updated=now))
    graph.add_edge(Edge(id="e2", source="n3", target="n1", relation_type="mentions",
                         creation_time=now, last_updated=now))

    graph.delete_outgoing_edges("n1")

    edges = graph.get_all_edges()
    assert len(edges) == 1
    assert edges[0].id == "e2"


def test_update_metadata_fields_merges_without_dropping_existing_keys(conn):
    graph = GraphBackend(conn)
    node = _make_node("n1")
    node.metadata = {"existing": "wert"}
    graph.add_node(node)

    graph.update_metadata_fields("n1", {"file_hash": "abc123"})

    fetched = graph.get_node("n1")
    assert fetched.metadata == {"existing": "wert", "file_hash": "abc123"}


def test_update_metadata_fields_noop_for_unknown_node(conn):
    graph = GraphBackend(conn)

    graph.update_metadata_fields("gibtsnicht", {"file_hash": "abc"})  # darf nicht werfen


def test_get_incoming_edges_filters_by_relation_type(conn):
    graph = GraphBackend(conn)
    graph.add_node(_make_node("n1"))
    graph.add_node(_make_node("n2"))
    now = datetime.now(timezone.utc).isoformat()
    graph.add_edge(Edge(id="e1", source="n1", target="n2", relation_type="mentions",
                         creation_time=now, last_updated=now))
    graph.add_edge(Edge(id="e2", source="n1", target="n2", relation_type="links_to",
                         creation_time=now, last_updated=now))

    all_incoming = graph.get_incoming_edges("n2")
    only_links = graph.get_incoming_edges("n2", relation_type="links_to")

    assert len(all_incoming) == 2
    assert len(only_links) == 1
    assert only_links[0].id == "e2"


def test_search_vault_content_only_matches_source_path_nodes(conn):
    graph = GraphBackend(conn)
    vault_node = _make_node("n1", title="Meeting Notizen", content="Über FAISS gesprochen")
    vault_node.metadata = {"source_path": "notizen/meeting.md"}
    graph.add_node(vault_node)
    non_vault_node = _make_node("n2", title="FAISS Konzept", content="Ein Konzept")
    graph.add_node(non_vault_node)

    hits = graph.search_vault_content("FAISS")

    ids = {n.id for n in hits}
    assert ids == {"n1"}
```

Falls die Datei keine `conn`-Fixture besitzt, folgende ergänzen (nach dem Muster von `test_vault_import.py`):

```python
import pytest

from backend.storage.sqlite_db import get_connection, init_db


@pytest.fixture()
def conn(tmp_path):
    connection = get_connection(tmp_path / "test.db")
    init_db(connection)
    return connection
```

(Falls bereits eine gleichnamige Fixture existiert, diesen Block weglassen und die bestehende nutzen — keine Duplikate anlegen.)

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_graph_backend.py -v -k "upsert or replace_vector or delete_outgoing or update_metadata or incoming_edges or search_vault"`
Expected: FAIL mit `AttributeError: 'GraphBackend' object has no attribute 'upsert_node'` (und analog für die anderen)

- [ ] **Step 3: Methoden implementieren**

In `backend/services/graph_backend.py`, nach `def add_node(...)` einfügen:

```python
    def upsert_node(self, node: Node) -> None:
        try:
            self._conn.execute(
                "INSERT INTO nodes (id, title, type, hemisphere, content, content_hash, "
                "activation, confidence, emotional_weight, decay_rate, importance, "
                "creation_time, last_access, access_counter, metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title, type=excluded.type, hemisphere=excluded.hemisphere, "
                "content=excluded.content, content_hash=excluded.content_hash, "
                "activation=excluded.activation, confidence=excluded.confidence, "
                "emotional_weight=excluded.emotional_weight, decay_rate=excluded.decay_rate, "
                "importance=excluded.importance, creation_time=excluded.creation_time, "
                "last_access=excluded.last_access, access_counter=excluded.access_counter, "
                "metadata=excluded.metadata",
                (
                    node.id, node.title, node.type, node.hemisphere, node.content,
                    node.content_hash, node.activation, node.confidence,
                    node.emotional_weight, node.decay_rate, node.importance,
                    node.creation_time, node.last_access, node.access_counter,
                    json.dumps(node.metadata),
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        self.graph.add_node(node.id)
```

Nach `def add_edge(...)` / vor `def get_all_edges(...)` einfügen:

```python
    def replace_vector(self, node_id: str, faiss_id: int, model: str) -> Optional[int]:
        row = self._conn.execute(
            "SELECT faiss_id FROM node_vectors WHERE node_id = ?", (node_id,)
        ).fetchone()
        old_faiss_id = None if row is None else row["faiss_id"]
        try:
            self._conn.execute(
                "INSERT INTO node_vectors (node_id, faiss_id, model) VALUES (?, ?, ?) "
                "ON CONFLICT(node_id) DO UPDATE SET faiss_id=excluded.faiss_id, model=excluded.model",
                (node_id, faiss_id, model),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return old_faiss_id

    def delete_outgoing_edges(self, node_id: str) -> None:
        try:
            self._conn.execute("DELETE FROM edges WHERE source = ?", (node_id,))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        self._load_cache()

    def get_incoming_edges(self, node_id: str, relation_type: Optional[str] = None) -> list[Edge]:
        if relation_type is not None:
            rows = self._conn.execute(
                "SELECT * FROM edges WHERE target = ? AND relation_type = ?",
                (node_id, relation_type),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM edges WHERE target = ?", (node_id,)).fetchall()
        return [self._row_to_edge(row) for row in rows]

    def search_vault_content(self, query: str, limit: int = 20) -> list[Node]:
        like = f"%{query}%"
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE metadata LIKE '%\"source_path\":%' "
            "AND (title LIKE ? OR content LIKE ?) ORDER BY title LIMIT ?",
            (like, like, limit),
        ).fetchall()
        return [self._row_to_node(row) for row in rows]
```

Nach `_write_aliases` (vor `def counts`) einfügen:

```python
    def update_metadata_fields(self, node_id: str, updates: dict) -> None:
        node = self.get_node(node_id)
        if node is None:
            return
        new_metadata = dict(node.metadata)
        new_metadata.update(updates)
        try:
            self._conn.execute(
                "UPDATE nodes SET metadata = ? WHERE id = ?",
                (json.dumps(new_metadata), node_id),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_graph_backend.py -v`
Expected: PASS (alle Tests der Datei, inkl. bereits bestehender)

- [ ] **Step 5: Commit**

```bash
git add backend/services/graph_backend.py backend/tests/test_graph_backend.py
git commit -m "feat: GraphBackend Upsert-/Metadata-/Backlink-Primitive für Vault"
```

---

### Task 3: Dispatcher — Upsert-fähiges `process_event`

**Files:**
- Modify: `backend/services/dispatcher.py`
- Test: `backend/tests/test_dispatcher.py`

**Interfaces:**
- Consumes: `GraphBackend.upsert_node`, `.replace_vector`, `.delete_outgoing_edges`, `.get_node` (aus Task 2).
- Produces: `process_event` erkennt `payload["node_id"]`. Ist es gesetzt und existiert der Node bereits → Upsert-Pfad (Content/Vektor/Kanten neu, `activation`/`confidence`/`decay_rate`/`importance`/`hemisphere`/`last_access`/`access_counter`/`creation_time` bleiben erhalten). Ist es gesetzt aber der Node existiert noch nicht, oder ist es nicht gesetzt → bisheriges Verhalten (neuer Node, `id=uuid4()` falls kein `node_id`).

- [ ] **Step 1: Failing Tests schreiben**

An `backend/tests/test_dispatcher.py` anhängen. Falls die Datei noch keine Fake-Client-Helfer hat, folgende self-contained Version verwenden (Muster identisch zu `test_vault_import.py`):

```python
import json
from datetime import datetime, timezone

import pytest

from backend.services.agents.prefrontal import PrefrontalAgent
from backend.services.agents.temporal import TemporalAgent
from backend.services.dispatcher import Dispatcher
from backend.services.entity_resolver import EntityResolver
from backend.services.event_log import EventLog
from backend.services.graph_backend import GraphBackend
from backend.storage.faiss_index import FaissIndex
from backend.storage.sqlite_db import get_connection, init_db


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


@pytest.mark.asyncio
async def test_process_event_with_node_id_creates_node_with_that_id(upsert_dispatcher):
    dispatcher, event_log, graph, _ = upsert_dispatcher
    event_id = event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Erster Inhalt", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    event = event_log.pending()[0]

    await dispatcher.process_event(event)

    node = graph.get_node("fixed-id-1")
    assert node is not None
    assert node.content == "Erster Inhalt"


@pytest.mark.asyncio
async def test_process_event_with_existing_node_id_upserts_not_duplicates(upsert_dispatcher):
    dispatcher, event_log, graph, faiss_index = upsert_dispatcher
    event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Alter Inhalt", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    await dispatcher.process_event(event_log.pending()[0])
    old_node = graph.get_node("fixed-id-1")
    assert old_node is not None

    event_log.append(
        "vault.file",
        {"title": "Notiz neu", "text": "Neuer Inhalt", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    await dispatcher.process_event(event_log.pending()[0])

    assert len(graph.get_all_nodes()) == 1
    updated_node = graph.get_node("fixed-id-1")
    assert updated_node.content == "Neuer Inhalt"
    assert updated_node.title == "Notiz neu"


@pytest.mark.asyncio
async def test_process_event_upsert_preserves_creation_time_and_access_counter(upsert_dispatcher):
    dispatcher, event_log, graph, _ = upsert_dispatcher
    event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Inhalt", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    await dispatcher.process_event(event_log.pending()[0])
    graph.touch_access(["fixed-id-1"])
    original = graph.get_node("fixed-id-1")

    event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Geänderter Inhalt", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    await dispatcher.process_event(event_log.pending()[0])

    updated = graph.get_node("fixed-id-1")
    assert updated.creation_time == original.creation_time
    assert updated.access_counter == original.access_counter
    assert updated.last_access == original.last_access


@pytest.mark.asyncio
async def test_process_event_upsert_replaces_faiss_vector(upsert_dispatcher):
    dispatcher, event_log, graph, faiss_index = upsert_dispatcher
    event_log.append(
        "vault.file",
        {"title": "Notiz", "text": "Inhalt eins", "source_path": "n.md", "node_id": "fixed-id-1"},
        "vault",
    )
    first_event = event_log.pending()[0]
    await dispatcher.process_event(first_event)
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
    await dispatcher.process_event(second_event)

    row = graph._conn.execute(
        "SELECT faiss_id FROM node_vectors WHERE node_id = 'fixed-id-1'"
    ).fetchone()
    assert row["faiss_id"] == second_event.id
    assert row["faiss_id"] != first_faiss_id


@pytest.mark.asyncio
async def test_process_event_without_node_id_still_creates_random_id(upsert_dispatcher):
    dispatcher, event_log, graph, _ = upsert_dispatcher
    event_log.append("note.created", {"title": "Titel", "text": "Text", "source_path": None}, "cli")

    await dispatcher.process_event(event_log.pending()[0])

    nodes = graph.get_all_nodes()
    assert len(nodes) == 1
    assert nodes[0].id != ""  # UUID, kein fester Wert
```

Prüfe, ob `pytest-asyncio` bereits konfiguriert ist (andere `async def test_...` in `test_dispatcher.py` oder `test_vault_import.py` nutzen `asyncio.run(...)` statt `@pytest.mark.asyncio` — falls kein `asyncio_mode = auto` in `pyproject.toml`/`pytest.ini` gesetzt ist, die obigen Tests stattdessen synchron mit `asyncio.run(...)` schreiben, analog zu `test_vault_import.py`. Prüfen mit:

```bash
grep -r "asyncio_mode\|pytest-asyncio" pyproject.toml backend/requirements.txt
```

Falls nichts gefunden wird: alle `@pytest.mark.asyncio`-Dekoratoren entfernen, `async def test_...` zu `def test_...` machen und die Bodies in `asyncio.run(async def ...)`-Wrapper packen (wie in `test_vault_import.py::test_scan_vault_counts_processed_and_sets_total`).

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_dispatcher.py -v -k upsert`
Expected: FAIL — `fixed-id-1` wird nicht gefunden (aktuell generiert `process_event` immer eine zufällige UUID), zweiter Test zeigt 2 statt 1 Node.

- [ ] **Step 3: `process_event` umbauen**

In `backend/services/dispatcher.py` die komplette Methode `process_event` ersetzen durch:

```python
    async def process_event(self, event: Event) -> None:
        payload = json.loads(event.payload)
        title = payload["title"]
        raw_text = payload["text"]
        now = datetime.now(timezone.utc).isoformat()

        # YAML-Frontmatter (Obsidian-Vault-Notizen) vor der Verarbeitung abtrennen:
        # Klassifizierung, Embedding und Feldextraktion arbeiten auf dem reinen Body,
        # Titel/Datum werden als echte Node-Felder übernommen.
        body, meta = parse_frontmatter(raw_text)
        # Wurde Frontmatter erkannt (meta befuellt), gilt der abgetrennte Body -
        # auch wenn er leer ist. Sonst landet bei reinen Frontmatter-Notizen das
        # rohe YAML in Klassifikation/Embedding/content. Nur ohne Frontmatter
        # (meta leer) faellt text auf den Rohtext zurueck.
        text = body if meta else raw_text
        if meta.get("title"):
            title = meta["title"]
        updated = meta.get("updated")

        # Vault-Events tragen ihre Node-Identitaet explizit im Payload (Frontmatter-id).
        # Existiert bereits ein Node mit dieser id, ist dies ein Upsert (Datei bearbeitet):
        # Content/Vektor/abgeleitete Kanten neu, Aktivierungs-/Zugriffsfelder bleiben stehen.
        explicit_node_id = payload.get("node_id")
        base_node = self.graph.get_node(explicit_node_id) if explicit_node_id else None
        node_id = explicit_node_id or str(uuid.uuid4())
        creation_time = base_node.creation_time if base_node else (meta.get("created") or now)

        tasks = [
            self.prefrontal_agent.classify_and_extract(text),
            self.temporal_agent.embed(text),
        ]
        if self.sentiment is not None:
            tasks.append(self.sentiment.analyze(text))
        results = await asyncio.gather(*tasks)
        classification_result = results[0]
        vector = results[1]
        emotional_weight = results[2] if len(results) > 2 else 0.0

        node_type = _resolve_node_type(classification_result.classification, event.event_type)
        extracted_fields = extract_fields(text)
        metadata: dict = dict(base_node.metadata) if base_node else {}
        if extracted_fields:
            metadata["extracted_fields"] = extracted_fields
        if updated:
            metadata["updated"] = updated
        if explicit_node_id:
            metadata["source_path"] = payload.get("source_path")

        doc_node = Node(
            id=node_id, title=title, type=node_type, content=text,
            content_hash=event.content_hash, creation_time=creation_time,
            last_access=base_node.last_access if base_node else creation_time,
            access_counter=base_node.access_counter if base_node else 0,
            activation=base_node.activation if base_node else 1.0,
            confidence=base_node.confidence if base_node else 1.0,
            decay_rate=base_node.decay_rate if base_node else 0.001,
            importance=base_node.importance if base_node else 0.5,
            hemisphere=base_node.hemisphere if base_node else "auto",
            emotional_weight=emotional_weight,
            metadata=metadata,
        )

        if base_node is not None:
            self.graph.upsert_node(doc_node)
            old_faiss_id = self.graph.replace_vector(doc_node.id, event.id, self.embedding_model_name)
            if old_faiss_id is not None and old_faiss_id != event.id:
                self.faiss_index.remove(old_faiss_id)
            self.graph.delete_outgoing_edges(doc_node.id)
        else:
            self.graph.add_node(doc_node)
            self.graph.link_vector(doc_node.id, event.id, self.embedding_model_name)
        self.faiss_index.add(event.id, vector)

        entity_nodes: dict[str, Node] = {}
        for entity in classification_result.entities:
            entity_node = await self._resolve_or_create_entity(
                entity.name, entity.type, now, entity.relationship
            )
            entity_nodes[entity.name] = entity_node
            edge = Edge(
                id=str(uuid.uuid4()), source=doc_node.id, target=entity_node.id,
                relation_type="mentions", creation_time=now, last_updated=now,
            )
            self.graph.add_edge(edge)

        # Zweiter Durchlauf: Hierarchie-Kanten (z.B. Krankenkasse -part_of-> Versicherung).
        # Die Parent-Entität kann implizit sein (nicht selbst in entities enthalten).
        for entity in classification_result.entities:
            if not entity.parent:
                continue
            parent_node = entity_nodes.get(entity.parent)
            if parent_node is None:
                parent_node = await self._resolve_or_create_entity(entity.parent, "concept", now)
                entity_nodes[entity.parent] = parent_node
            edge = Edge(
                id=str(uuid.uuid4()), source=entity_nodes[entity.name].id, target=parent_node.id,
                relation_type="part_of", creation_time=now, last_updated=now,
            )
            self.graph.add_edge(edge)

        self.event_log.mark_processed(event.id)
```

(Einzige Änderungen gegenüber dem Original: `explicit_node_id`/`base_node`-Ermittlung, `node_id`/`creation_time`-Herleitung, `metadata`-Basis aus `base_node`, `source_path`-Übernahme, und der `if base_node is not None: ... else: ...`-Block statt des einzelnen `self.graph.add_node(doc_node)` + `link_vector`. Der Rest — Entity-/Kanten-Loop, `mark_processed` — bleibt zeichengleich.)

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_dispatcher.py backend/tests/test_rebuild.py backend/tests/test_api.py backend/tests/test_ingestion.py backend/tests/test_e2e_milestone.py backend/tests/test_e2e_api_milestone.py -v`
Expected: PASS — sowohl die neuen Upsert-Tests als auch **alle bisherigen** Dispatcher-/Rebuild-/API-/E2E-Tests (kein Regressionsrisiko, da `explicit_node_id` bei nicht-Vault-Events immer `None` ist und der `else`-Zweig exakt dem alten Code entspricht).

- [ ] **Step 5: Commit**

```bash
git add backend/services/dispatcher.py backend/tests/test_dispatcher.py
git commit -m "feat: Dispatcher upserted Nodes bei explizitem node_id (Vault-Events)"
```

---

### Task 4: `services/vault_fs.py` — Dateioperationen

**Files:**
- Create: `backend/services/vault_fs.py`
- Test: `backend/tests/test_vault_fs.py`

**Interfaces:**
- Produces:
  - `class VaultPathError(Exception)`, `class VaultConflictError(Exception)`
  - `@dataclass TreeEntry(name: str, path: str, is_dir: bool, children: Optional[list["TreeEntry"]] = None)`
  - `list_tree(root: Path) -> list[TreeEntry]`
  - `read_file(root: Path, relative: str) -> tuple[str, str]` (content, content_hash)
  - `write_file(root: Path, relative: str, content: str, expected_hash: Optional[str]) -> str` (neuer content_hash)
  - `rename(root: Path, source: str, target: str) -> None`
  - `delete(root: Path, relative: str) -> None`
  - `save_attachment(root: Path, filename: str, content: bytes) -> str` (relativer Pfad ab Root)

- [ ] **Step 1: Failing Tests schreiben**

`backend/tests/test_vault_fs.py` neu anlegen:

```python
import pytest

from backend.services.vault_fs import (
    VaultConflictError, VaultPathError, delete, list_tree, read_file, rename,
    save_attachment, write_file,
)


def test_write_and_read_file_roundtrip(tmp_path):
    write_file(tmp_path, "notiz.md", "# Hallo", expected_hash=None)

    content, digest = read_file(tmp_path, "notiz.md")

    assert content == "# Hallo"
    assert len(digest) == 64


def test_write_file_creates_parent_dirs(tmp_path):
    write_file(tmp_path, "ordner/unterordner/notiz.md", "Inhalt", expected_hash=None)

    assert (tmp_path / "ordner" / "unterordner" / "notiz.md").is_file()


def test_write_file_conflict_when_hash_mismatches(tmp_path):
    write_file(tmp_path, "notiz.md", "Original", expected_hash=None)

    with pytest.raises(VaultConflictError):
        write_file(tmp_path, "notiz.md", "Überschrieben", expected_hash="falscher-hash")


def test_write_file_succeeds_with_correct_expected_hash(tmp_path):
    write_file(tmp_path, "notiz.md", "Original", expected_hash=None)
    _, current_hash = read_file(tmp_path, "notiz.md")

    write_file(tmp_path, "notiz.md", "Geändert", expected_hash=current_hash)

    content, _ = read_file(tmp_path, "notiz.md")
    assert content == "Geändert"


def test_read_file_missing_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_file(tmp_path, "gibtsnicht.md")


def test_read_file_rejects_path_traversal(tmp_path):
    with pytest.raises(VaultPathError):
        read_file(tmp_path, "../../etc/passwd")


def test_write_file_rejects_path_traversal(tmp_path):
    with pytest.raises(VaultPathError):
        write_file(tmp_path, "../escaped.md", "böse", expected_hash=None)


def test_rename_moves_file(tmp_path):
    write_file(tmp_path, "alt.md", "Inhalt", expected_hash=None)

    rename(tmp_path, "alt.md", "neu.md")

    assert not (tmp_path / "alt.md").exists()
    assert (tmp_path / "neu.md").is_file()


def test_rename_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        rename(tmp_path, "gibtsnicht.md", "ziel.md")


def test_rename_existing_target_raises(tmp_path):
    write_file(tmp_path, "a.md", "A", expected_hash=None)
    write_file(tmp_path, "b.md", "B", expected_hash=None)

    with pytest.raises(FileExistsError):
        rename(tmp_path, "a.md", "b.md")


def test_delete_removes_file(tmp_path):
    write_file(tmp_path, "weg.md", "Inhalt", expected_hash=None)

    delete(tmp_path, "weg.md")

    assert not (tmp_path / "weg.md").exists()


def test_save_attachment_writes_under_attachments_dir(tmp_path):
    rel_path = save_attachment(tmp_path, "bild.png", b"\x89PNG")

    assert rel_path == "attachments/bild.png"
    assert (tmp_path / "attachments" / "bild.png").read_bytes() == b"\x89PNG"


def test_save_attachment_avoids_overwriting_existing_file(tmp_path):
    save_attachment(tmp_path, "bild.png", b"erste-version")

    second_path = save_attachment(tmp_path, "bild.png", b"zweite-version")

    assert second_path != "attachments/bild.png"
    assert (tmp_path / second_path).read_bytes() == b"zweite-version"
    assert (tmp_path / "attachments" / "bild.png").read_bytes() == b"erste-version"


def test_list_tree_returns_nested_structure(tmp_path):
    (tmp_path / "ordner").mkdir()
    (tmp_path / "ordner" / "tief.md").write_text("x")
    (tmp_path / "oben.md").write_text("x")

    tree = list_tree(tmp_path)

    names = {entry.name for entry in tree}
    assert names == {"ordner", "oben.md"}
    folder = next(e for e in tree if e.name == "ordner")
    assert folder.is_dir is True
    assert folder.children is not None
    assert folder.children[0].name == "tief.md"


def test_list_tree_skips_hidden_entries(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".hidden.md").write_text("x")
    (tmp_path / "sichtbar.md").write_text("x")

    tree = list_tree(tmp_path)

    names = {entry.name for entry in tree}
    assert names == {"sichtbar.md"}
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_vault_fs.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.services.vault_fs'`

- [ ] **Step 3: `vault_fs.py` implementieren**

```python
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.services.hashing import content_hash


class VaultPathError(Exception):
    pass


class VaultConflictError(Exception):
    pass


@dataclass
class TreeEntry:
    name: str
    path: str
    is_dir: bool
    children: Optional[list["TreeEntry"]] = None


def _resolve(root: Path, relative: str) -> Path:
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise VaultPathError(f"Pfad verlässt den Vault: {relative}")
    return candidate


def list_tree(root: Path) -> list[TreeEntry]:
    def _walk(dir_path: Path) -> list[TreeEntry]:
        entries = []
        for item in sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if item.name.startswith("."):
                continue
            rel = item.relative_to(root).as_posix()
            if item.is_dir():
                entries.append(TreeEntry(name=item.name, path=rel, is_dir=True, children=_walk(item)))
            else:
                entries.append(TreeEntry(name=item.name, path=rel, is_dir=False))
        return entries

    if not root.is_dir():
        return []
    return _walk(root)


def read_file(root: Path, relative: str) -> tuple[str, str]:
    path = _resolve(root, relative)
    if not path.is_file():
        raise FileNotFoundError(relative)
    content = path.read_text(encoding="utf-8")
    return content, content_hash(content)


def write_file(root: Path, relative: str, content: str, expected_hash: Optional[str]) -> str:
    path = _resolve(root, relative)
    if path.is_file() and expected_hash is not None:
        current = path.read_text(encoding="utf-8")
        if content_hash(current) != expected_hash:
            raise VaultConflictError(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return content_hash(content)


def rename(root: Path, source: str, target: str) -> None:
    src = _resolve(root, source)
    dst = _resolve(root, target)
    if not src.is_file():
        raise FileNotFoundError(source)
    if dst.exists():
        raise FileExistsError(target)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)


def delete(root: Path, relative: str) -> None:
    path = _resolve(root, relative)
    if not path.is_file():
        raise FileNotFoundError(relative)
    path.unlink()


def save_attachment(root: Path, filename: str, content: bytes) -> str:
    safe_name = Path(filename).name
    dest_dir = root / "attachments"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name
    if dest.exists():
        digest = hashlib.sha256(content).hexdigest()[:8]
        dest = dest_dir / f"{dest.stem}-{digest}{dest.suffix}"
    dest.write_bytes(content)
    return dest.relative_to(root).as_posix()
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_vault_fs.py -v`
Expected: PASS (alle Tests)

- [ ] **Step 5: Commit**

```bash
git add backend/services/vault_fs.py backend/tests/test_vault_fs.py
git commit -m "feat: vault_fs Dateioperationen mit Traversal-Schutz"
```

---

### Task 5: `services/vault_index.py` — Indexierung (Upsert, Rescan, Wiki-Links)

**Files:**
- Create: `backend/services/vault_index.py`
- Modify: `backend/models/edges.py` (RelationType um `"links_to"` erweitern)
- Modify: `backend/services/event_log.py` (Methode `get(event_id)` ergänzen)
- Test: `backend/tests/test_vault_index.py`
- Test: `backend/tests/test_event_log.py`

**Interfaces:**
- Consumes: `backend.app_context.AppContext`, `GraphBackend.{get_node, find_node_by_title, update_metadata_fields, add_edge, delete_node}` (Task 2 + bestehend), `EventLog.{append, get}`, `Dispatcher.process_pending`, `parse_frontmatter`.
- Produces:
  - `iter_vault_notes(root: Path) -> list[Path]`
  - `async def index_file(path: Path, root: Path, ctx: AppContext) -> None`
  - `@dataclass RescanSummary(processed: int, skipped: int, failed: int, deleted: int)`
  - `async def rescan(root: Path, ctx: AppContext, full: bool = False) -> RescanSummary`
  - `EventLog.get(event_id: int) -> Optional[Event]`

- [ ] **Step 1: `RelationType` erweitern**

In `backend/models/edges.py` die `Literal`-Zeile ändern von:

```python
RelationType = Literal[
    "related_to", "depends_on", "extends", "contradicts", "supports",
    "mentions", "part_of", "requires", "alternative_to", "causes", "solves",
]
```

zu:

```python
RelationType = Literal[
    "related_to", "depends_on", "extends", "contradicts", "supports",
    "mentions", "part_of", "requires", "alternative_to", "causes", "solves",
    "links_to",
]
```

- [ ] **Step 2: Failing Test für `EventLog.get` schreiben**

An `backend/tests/test_event_log.py` anhängen:

```python
def test_get_returns_event_by_id(conn):
    event_log = EventLog(conn)
    event_id = event_log.append("note.created", {"title": "t", "text": "x"}, "cli")

    event = event_log.get(event_id)

    assert event is not None
    assert event.id == event_id
    assert event.event_type == "note.created"


def test_get_returns_none_for_unknown_id(conn):
    event_log = EventLog(conn)

    assert event_log.get(999999) is None
```

(Falls `test_event_log.py` keine `conn`-Fixture oder keinen `EventLog`-Import auf Modulebene hat, an bestehendem Muster der Datei orientieren — beide sind Standardnamen, die in Ingest-/EventLog-Tests dieses Repos durchgängig verwendet werden.)

- [ ] **Step 3: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_event_log.py -v -k test_get`
Expected: FAIL mit `AttributeError: 'EventLog' object has no attribute 'get'`

- [ ] **Step 4: `EventLog.get` implementieren**

In `backend/services/event_log.py`, nach `def append(...)` (vor `def pending(...)`) einfügen:

```python
    def get(self, event_id: int) -> Optional[Event]:
        row = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return None if row is None else self._row_to_event(row)
```

`Optional` ist bereits über `from typing import Iterator` **nicht** importiert — Zeile 4 ändern von:

```python
from typing import Iterator
```

zu:

```python
from typing import Iterator, Optional
```

- [ ] **Step 5: Tests laufen lassen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_event_log.py -v`
Expected: PASS

- [ ] **Step 6: Failing Tests für `vault_index` schreiben**

`backend/tests/test_vault_index.py` neu anlegen:

```python
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
```

- [ ] **Step 7: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_vault_index.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.services.vault_index'`

- [ ] **Step 8: `vault_index.py` implementieren**

```python
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.app_context import AppContext
from backend.models.edges import Edge
from backend.services.hashing import content_hash
from backend.services.parsing import parse_frontmatter

_NOTE_SUFFIXES = {".md", ".markdown"}
_EXCLUDED_DIRS = {"attachments"}
_OPENING_FRONTMATTER_RE = re.compile(r"\A---\s*\n")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def iter_vault_notes(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _NOTE_SUFFIXES:
            continue
        relative_parts = path.relative_to(root).parts
        if any(part in _EXCLUDED_DIRS or part.startswith(".") for part in relative_parts):
            continue
        files.append(path)
    return files


def _ensure_id(path: Path, raw: str) -> tuple[str, str]:
    _, meta = parse_frontmatter(raw)
    if meta.get("id"):
        return meta["id"], raw
    new_id = str(uuid.uuid4())
    match = _OPENING_FRONTMATTER_RE.match(raw)
    if match:
        updated = raw[: match.end()] + f"id: {new_id}\n" + raw[match.end() :]
    else:
        updated = f"---\nid: {new_id}\n---\n\n{raw}"
    path.write_text(updated, encoding="utf-8")
    return new_id, updated


@dataclass
class _Staged:
    node_id: str
    file_hash: str
    event_id: int
    body: str


def stage_file(path: Path, root: Path, ctx: AppContext) -> _Staged:
    raw = path.read_text(encoding="utf-8")
    node_id, raw = _ensure_id(path, raw)
    file_hash = content_hash(raw)
    body, _ = parse_frontmatter(raw)
    rel_path = path.relative_to(root).as_posix()
    payload = {"title": path.stem, "text": raw, "source_path": rel_path, "node_id": node_id}
    event_id = ctx.event_log.append("vault.file", payload, source="vault")
    return _Staged(node_id=node_id, file_hash=file_hash, event_id=event_id, body=body)


def _sync_wikilinks(node_id: str, body: str, ctx: AppContext) -> None:
    now = datetime.now(timezone.utc).isoformat()
    seen: set[str] = set()
    for match in _WIKILINK_RE.finditer(body):
        title = match.group(1).strip()
        if not title:
            continue
        target = ctx.graph.find_node_by_title(title)
        if target is None or target.id == node_id or target.id in seen:
            continue
        seen.add(target.id)
        ctx.graph.add_edge(Edge(
            id=str(uuid.uuid4()), source=node_id, target=target.id,
            relation_type="links_to", creation_time=now, last_updated=now,
        ))


def _finalize(staged: list[_Staged], ctx: AppContext) -> None:
    for item in staged:
        event = ctx.event_log.get(item.event_id)
        if event is None or event.status != "processed":
            continue
        ctx.graph.update_metadata_fields(item.node_id, {"file_hash": item.file_hash})
        _sync_wikilinks(item.node_id, item.body, ctx)


async def index_file(path: Path, root: Path, ctx: AppContext) -> None:
    staged = stage_file(path, root, ctx)
    await ctx.dispatcher.process_pending()
    ctx.faiss_index.save()
    _finalize([staged], ctx)


@dataclass
class RescanSummary:
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    deleted: int = 0


async def rescan(root: Path, ctx: AppContext, full: bool = False) -> RescanSummary:
    summary = RescanSummary()
    known_paths: set[str] = set()
    staged: list[_Staged] = []

    for path in iter_vault_notes(root):
        rel_path = path.relative_to(root).as_posix()
        known_paths.add(rel_path)
        try:
            raw = path.read_text(encoding="utf-8")
            node_id, raw = _ensure_id(path, raw)
            file_hash = content_hash(raw)
            existing = ctx.graph.get_node(node_id)
            if not full and existing is not None and existing.metadata.get("file_hash") == file_hash:
                summary.skipped += 1
                continue
            staged.append(stage_file(path, root, ctx))
        except Exception:
            summary.failed += 1

    if staged:
        process_summary = await ctx.dispatcher.process_pending()
        summary.processed = process_summary.processed
        summary.failed += process_summary.failed
        _finalize(staged, ctx)

    for node in ctx.graph.get_all_nodes():
        source_path = node.metadata.get("source_path")
        if source_path is not None and source_path not in known_paths:
            faiss_id = ctx.graph.delete_node(node.id)
            if faiss_id is not None:
                ctx.faiss_index.remove(faiss_id)
                ctx.event_log.delete(faiss_id)
            summary.deleted += 1

    ctx.faiss_index.save()
    return summary
```

- [ ] **Step 9: Tests laufen lassen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_vault_index.py backend/tests/test_event_log.py -v`
Expected: PASS (alle Tests)

- [ ] **Step 10: Commit**

```bash
git add backend/services/vault_index.py backend/models/edges.py backend/services/event_log.py backend/tests/test_vault_index.py backend/tests/test_event_log.py
git commit -m "feat: vault_index Upsert-Indexierung, Rescan und Wiki-Link-Kanten"
```

---

### Task 6: CLI `cbks index [--full]`

**Files:**
- Modify: `backend/cli.py`
- Test: `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: `backend.services.vault_index.rescan`, `Config.vault_dir`.

- [ ] **Step 1: Failing Test schreiben**

An `backend/tests/test_cli.py` anhängen (Muster: `CliRunner` — prüfe den existierenden Import/Verwendungsstil am Dateianfang und übernimm ihn 1:1; typisches Typer-Testmuster ist `from typer.testing import CliRunner`):

```python
def test_index_command_without_vault_dir_exits_with_error(monkeypatch, tmp_path):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CBKS_VAULT_DIR", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["index"])

    assert result.exit_code == 1
    assert "CBKS_VAULT_DIR" in result.output


def test_index_command_indexes_vault(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "notiz.md").write_text("Text über FAISS")
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CBKS_VAULT_DIR", str(vault))
    runner = CliRunner()

    result = runner.invoke(app, ["index"])

    assert result.exit_code == 0
    assert "Verarbeitet: 1" in result.output
```

(Diese beiden Tests benötigen gemockte Ollama-Clients, falls `test_cli.py` dafür bereits ein autouse-Fixture besitzt — prüfen und ggf. wiederverwenden statt neu zu bauen. Falls kein autouse-Mock existiert, vor beiden Tests ergänzen:

```python
@pytest.fixture(autouse=True)
def fake_ollama(monkeypatch):
    from backend.services.agents.prefrontal import OllamaLLMClient
    from backend.services.agents.temporal import OllamaEmbeddingClient
    import json as _json

    monkeypatch.setattr(OllamaEmbeddingClient, "embed", lambda self, text: [1.0] + [0.0] * 1023)
    monkeypatch.setattr(
        OllamaLLMClient, "generate",
        lambda self, prompt, format="": _json.dumps({"classification": "note", "entities": []}),
    )
```

— nur hinzufügen, wenn `test_cli.py` noch keinen solchen Fixture besitzt; sonst Duplikat vermeiden.)

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_cli.py -v -k test_index_command`
Expected: FAIL — Kommando `index` existiert noch nicht (`Error: No such command 'index'`)

- [ ] **Step 3: Kommando implementieren**

In `backend/cli.py`, Import ergänzen (nach `from backend.services.vault_export import export_nodes`):

```python
from backend.services import vault_index
```

Und ein neues Kommando ergänzen (z. B. nach `retry`):

```python
@app.command(name="index")
def index_vault(
    full: bool = typer.Option(False, "--full", help="Alle Dateien neu indexieren, auch unveränderte"),
) -> None:
    ctx = build_context()
    if ctx.config.vault_dir is None:
        typer.echo("CBKS_VAULT_DIR ist nicht gesetzt")
        raise typer.Exit(code=1)
    summary = asyncio.run(vault_index.rescan(ctx.config.vault_dir, ctx, full=full))
    typer.echo(
        f"Verarbeitet: {summary.processed}, Übersprungen: {summary.skipped}, "
        f"Fehlgeschlagen: {summary.failed}, Gelöscht: {summary.deleted}"
    )
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_cli.py -v`
Expected: PASS (alle Tests der Datei)

- [ ] **Step 5: Commit**

```bash
git add backend/cli.py backend/tests/test_cli.py
git commit -m "feat: CLI cbks index [--full] für Vault-Rescan"
```

---

### Task 7: API — `GET /vault/tree`, `GET /vault/file`, `PUT /vault/file`

**Files:**
- Modify: `backend/api_models.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `vault_fs.{list_tree, TreeEntry, read_file, write_file, VaultPathError, VaultConflictError}`, `vault_index.index_file`, `Config.vault_dir`.
- Produces (Pydantic-Modelle): `VaultTreeEntry`, `VaultFileResponse`, `VaultFileWriteRequest`, `VaultFileWriteResponse`.

- [ ] **Step 1: Failing Tests schreiben**

An `backend/tests/test_api.py` anhängen:

```python
@pytest.fixture()
def vault_client(isolated_data_dir, monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("CBKS_VAULT_DIR", str(vault))
    with TestClient(app) as c:
        yield c, vault


def test_vault_tree_reflects_filesystem(vault_client):
    client, vault = vault_client
    (vault / "notiz.md").write_text("Text")

    response = client.get("/vault/tree")

    assert response.status_code == 200
    names = {entry["name"] for entry in response.json()}
    assert names == {"notiz.md"}


def test_vault_file_get_returns_content_and_hash(vault_client):
    client, vault = vault_client
    (vault / "notiz.md").write_text("Hallo Vault")

    response = client.get("/vault/file", params={"path": "notiz.md"})

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "Hallo Vault"
    assert len(body["content_hash"]) == 64


def test_vault_file_get_missing_returns_404(vault_client):
    client, _ = vault_client

    response = client.get("/vault/file", params={"path": "gibtsnicht.md"})

    assert response.status_code == 404


def test_vault_file_get_traversal_returns_400(vault_client):
    client, _ = vault_client

    response = client.get("/vault/file", params={"path": "../../etc/passwd"})

    assert response.status_code == 400


def test_vault_file_put_creates_and_indexes(vault_client):
    client, vault = vault_client

    response = client.put("/vault/file", json={"path": "neu.md", "content": "Text über FAISS"})

    assert response.status_code == 200
    body = response.json()
    assert body["indexed"] is True
    assert (vault / "neu.md").is_file()

    graph_response = client.get("/graph")
    assert len(graph_response.json()["nodes"]) == 1


def test_vault_file_put_conflict_returns_409(vault_client):
    client, vault = vault_client
    (vault / "notiz.md").write_text("Original")

    response = client.put(
        "/vault/file", json={"path": "notiz.md", "content": "Überschrieben", "expected_hash": "falsch"}
    )

    assert response.status_code == 409


def test_vault_file_put_without_vault_dir_returns_400(client):
    response = client.put("/vault/file", json={"path": "notiz.md", "content": "x"})

    assert response.status_code == 400
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_api.py -v -k vault_tree or vault_file_get or vault_file_put`
Expected: FAIL mit 404 (Route existiert nicht)

- [ ] **Step 3: Pydantic-Modelle ergänzen**

In `backend/api_models.py` am Ende der Datei ergänzen:

```python
class VaultTreeEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    children: Optional[list["VaultTreeEntry"]] = None


VaultTreeEntry.model_rebuild()


class VaultFileResponse(BaseModel):
    path: str
    content: str
    content_hash: str


class VaultFileWriteRequest(BaseModel):
    path: str
    content: str
    expected_hash: Optional[str] = None


class VaultFileWriteResponse(BaseModel):
    path: str
    content_hash: str
    indexed: bool
```

- [ ] **Step 4: Endpoints implementieren**

In `backend/main.py`, Imports ergänzen:

```python
from backend.api_models import (
    ...  # bestehende Liste
    VaultFileResponse,
    VaultFileWriteRequest,
    VaultFileWriteResponse,
    VaultTreeEntry,
)
...
from backend.services import vault_fs, vault_index
from backend.services.parsing import parse_frontmatter
from backend.services.vault_fs import VaultConflictError, VaultPathError
```

Nach `def get_context(...)` einfügen:

```python
def _vault_root(ctx: AppContext) -> Path:
    if ctx.config.vault_dir is None:
        raise HTTPException(status_code=400, detail="CBKS_VAULT_DIR ist nicht konfiguriert")
    return ctx.config.vault_dir


def _to_tree_response(entry: vault_fs.TreeEntry) -> VaultTreeEntry:
    return VaultTreeEntry(
        name=entry.name, path=entry.path, is_dir=entry.is_dir,
        children=[_to_tree_response(c) for c in entry.children] if entry.children is not None else None,
    )
```

Am Ende der Datei (nach `get_vault_scan`) einfügen:

```python
@app.get("/vault/tree", response_model=list[VaultTreeEntry])
async def get_vault_tree(ctx: AppContext = Depends(get_context)) -> list[VaultTreeEntry]:
    root = _vault_root(ctx)
    return [_to_tree_response(e) for e in vault_fs.list_tree(root)]


@app.get("/vault/file", response_model=VaultFileResponse)
async def get_vault_file(
    path: str = Query(...), ctx: AppContext = Depends(get_context)
) -> VaultFileResponse:
    root = _vault_root(ctx)
    try:
        content, digest = vault_fs.read_file(root, path)
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    return VaultFileResponse(path=path, content=content, content_hash=digest)


@app.put("/vault/file", response_model=VaultFileWriteResponse)
async def put_vault_file(
    body: VaultFileWriteRequest, ctx: AppContext = Depends(get_context)
) -> VaultFileWriteResponse:
    root = _vault_root(ctx)
    try:
        vault_fs.write_file(root, body.path, body.content, body.expected_hash)
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except VaultConflictError as exc:
        raise HTTPException(status_code=409, detail=f"Datei extern geändert: {exc}")
    indexed = True
    try:
        await vault_index.index_file(root / body.path, root, ctx)
    except Exception:
        logger.exception("Vault-Indexierung fehlgeschlagen (path=%s)", body.path)
        indexed = False
    _, digest = vault_fs.read_file(root, body.path)
    return VaultFileWriteResponse(path=body.path, content_hash=digest, indexed=indexed)
```

- [ ] **Step 5: Tests laufen lassen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_api.py -v`
Expected: PASS (alle Tests der Datei, inkl. bestehender)

- [ ] **Step 6: Commit**

```bash
git add backend/api_models.py backend/main.py backend/tests/test_api.py
git commit -m "feat: API GET/PUT /vault/tree /vault/file"
```

---

### Task 8: API — `POST /vault/rename`, `DELETE /vault/file`, `POST /vault/attachment`

**Files:**
- Modify: `backend/api_models.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `vault_fs.{rename, delete, save_attachment}`, `GraphBackend.{update_metadata_fields, delete_node}` (Task 2), `DeleteResponse` (bestehend).
- Produces: `VaultRenameRequest`, `VaultAttachmentResponse`.

- [ ] **Step 1: Failing Tests schreiben**

An `backend/tests/test_api.py` anhängen:

```python
def test_vault_rename_moves_file_and_updates_node_metadata(vault_client):
    client, vault = vault_client
    client.put("/vault/file", json={"path": "alt.md", "content": "Text über FAISS"})

    response = client.post("/vault/rename", json={"source": "alt.md", "target": "neu.md"})

    assert response.status_code == 200
    assert not (vault / "alt.md").exists()
    assert (vault / "neu.md").is_file()
    graph = client.get("/graph").json()
    assert graph["nodes"][0]["metadata"]["source_path"] == "neu.md"


def test_vault_rename_missing_source_returns_404(vault_client):
    client, _ = vault_client

    response = client.post("/vault/rename", json={"source": "weg.md", "target": "ziel.md"})

    assert response.status_code == 404


def test_vault_delete_removes_file_and_node(vault_client):
    client, vault = vault_client
    client.put("/vault/file", json={"path": "weg.md", "content": "Text über FAISS"})

    response = client.request("DELETE", "/vault/file", params={"path": "weg.md"})

    assert response.status_code == 200
    assert not (vault / "weg.md").exists()
    graph = client.get("/graph").json()
    assert graph["nodes"] == []


def test_vault_delete_missing_returns_404(vault_client):
    client, _ = vault_client

    response = client.request("DELETE", "/vault/file", params={"path": "gibtsnicht.md"})

    assert response.status_code == 404


def test_vault_attachment_upload_saves_under_attachments(vault_client):
    client, vault = vault_client

    response = client.post(
        "/vault/attachment", files={"file": ("bild.png", b"\x89PNG", "image/png")}
    )

    assert response.status_code == 200
    assert response.json()["path"] == "attachments/bild.png"
    assert (vault / "attachments" / "bild.png").read_bytes() == b"\x89PNG"
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_api.py -v -k "vault_rename or vault_delete or vault_attachment"`
Expected: FAIL mit 404 (Routen existieren nicht)

- [ ] **Step 3: Pydantic-Modell ergänzen**

In `backend/api_models.py` ergänzen:

```python
class VaultRenameRequest(BaseModel):
    source: str
    target: str


class VaultAttachmentResponse(BaseModel):
    path: str
```

- [ ] **Step 4: Endpoints implementieren**

In `backend/main.py` Import ergänzen: `VaultAttachmentResponse, VaultRenameRequest,` in die bestehende `from backend.api_models import (...)`-Liste einsortieren; `DeleteResponse` ist bereits importiert.

Ans Dateiende anfügen:

```python
@app.post("/vault/rename", response_model=VaultFileWriteResponse)
async def post_vault_rename(
    body: VaultRenameRequest, ctx: AppContext = Depends(get_context)
) -> VaultFileWriteResponse:
    root = _vault_root(ctx)
    try:
        old_content, _ = vault_fs.read_file(root, body.source)
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    _, meta = parse_frontmatter(old_content)
    node_id = meta.get("id")
    try:
        vault_fs.rename(root, body.source, body.target)
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileExistsError:
        raise HTTPException(status_code=409, detail="Zieldatei existiert bereits")
    if node_id:
        ctx.graph.update_metadata_fields(node_id, {"source_path": body.target})
    _, digest = vault_fs.read_file(root, body.target)
    return VaultFileWriteResponse(path=body.target, content_hash=digest, indexed=True)


@app.delete("/vault/file", response_model=DeleteResponse)
async def delete_vault_file(
    path: str = Query(...), ctx: AppContext = Depends(get_context)
) -> DeleteResponse:
    root = _vault_root(ctx)
    try:
        content, _ = vault_fs.read_file(root, path)
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    _, meta = parse_frontmatter(content)
    node_id = meta.get("id")
    vault_fs.delete(root, path)
    removed_event_id: Optional[int] = None
    if node_id:
        faiss_id = ctx.graph.delete_node(node_id)
        if faiss_id is not None:
            try:
                ctx.faiss_index.remove(faiss_id)
                ctx.faiss_index.save()
            except Exception:
                logger.exception("FAISS-Cleanup fehlgeschlagen (faiss_id=%s)", faiss_id)
            ctx.event_log.delete(faiss_id)
            removed_event_id = faiss_id
    return DeleteResponse(deleted_node_id=node_id or "", removed_event_id=removed_event_id)


@app.post("/vault/attachment", response_model=VaultAttachmentResponse)
async def post_vault_attachment(
    file: UploadFile = File(...), ctx: AppContext = Depends(get_context)
) -> VaultAttachmentResponse:
    root = _vault_root(ctx)
    content = await file.read()
    rel_path = vault_fs.save_attachment(root, file.filename or "anhang", content)
    return VaultAttachmentResponse(path=rel_path)
```

- [ ] **Step 5: Tests laufen lassen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_api.py -v`
Expected: PASS (alle Tests der Datei)

- [ ] **Step 6: Commit**

```bash
git add backend/api_models.py backend/main.py backend/tests/test_api.py
git commit -m "feat: API rename/delete/attachment für Vault-Dateien"
```

---

### Task 9: API — `POST /vault/rescan`, `GET /vault/backlinks`, `GET /vault/search`

**Files:**
- Modify: `backend/api_models.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `vault_index.rescan`, `GraphBackend.{get_incoming_edges, search_vault_content}` (Task 2/5).
- Produces: `VaultRescanResponse`, `VaultBacklinksResponse`, `VaultSearchHitResponse`.

- [ ] **Step 1: Failing Tests schreiben**

An `backend/tests/test_api.py` anhängen:

```python
def test_vault_rescan_endpoint_reports_summary(vault_client):
    client, vault = vault_client
    (vault / "notiz.md").write_text("Text über FAISS")

    response = client.post("/vault/rescan")

    assert response.status_code == 200
    body = response.json()
    assert body["processed"] == 1
    assert body["deleted"] == 0


def test_vault_backlinks_returns_incoming_wikilinks(vault_client):
    client, vault = vault_client
    (vault / "ziel.md").write_text("---\ntitle: Ziel-Notiz\n---\n\nInhalt")
    (vault / "quelle.md").write_text("Verweist auf [[Ziel-Notiz]]")
    client.post("/vault/rescan")

    response = client.get("/vault/backlinks", params={"path": "ziel.md"})

    assert response.status_code == 200
    backlinks = response.json()["backlinks"]
    assert len(backlinks) == 1
    assert backlinks[0]["metadata"]["source_path"] == "quelle.md"


def test_vault_backlinks_empty_for_unlinked_note(vault_client):
    client, vault = vault_client
    (vault / "einsam.md").write_text("Niemand verlinkt mich")
    client.post("/vault/rescan")

    response = client.get("/vault/backlinks", params={"path": "einsam.md"})

    assert response.status_code == 200
    assert response.json()["backlinks"] == []


def test_vault_search_finds_matching_note(vault_client):
    client, vault = vault_client
    (vault / "notiz.md").write_text("Ein Text über Graphentheorie")
    client.post("/vault/rescan")

    response = client.get("/vault/search", params={"q": "Graphentheorie"})

    assert response.status_code == 200
    hits = response.json()
    assert len(hits) == 1
    assert hits[0]["node"]["metadata"]["source_path"] == "notiz.md"
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_api.py -v -k "vault_rescan or vault_backlinks or vault_search"`
Expected: FAIL mit 404 (Routen existieren nicht)

- [ ] **Step 3: GraphBackend um `search_vault_content` ergänzen (falls in Task 2 übersprungen)**

Diese Methode wurde bereits in Task 2 implementiert. Falls Task 2 exakt wie oben beschrieben ausgeführt wurde, ist dieser Schritt bereits erledigt — kurz mit `grep -n "search_vault_content" backend/services/graph_backend.py` verifizieren.

- [ ] **Step 4: Pydantic-Modelle ergänzen**

In `backend/api_models.py` ergänzen:

```python
class VaultRescanResponse(BaseModel):
    processed: int
    skipped: int
    failed: int
    deleted: int


class VaultBacklinksResponse(BaseModel):
    backlinks: list[Node]


class VaultSearchHitResponse(BaseModel):
    node: Node
```

- [ ] **Step 5: Endpoints implementieren**

In `backend/main.py` Import ergänzen: `VaultBacklinksResponse, VaultRescanResponse, VaultSearchHitResponse,` in die `api_models`-Importliste einsortieren.

Ans Dateiende anfügen:

```python
@app.post("/vault/rescan", response_model=VaultRescanResponse)
async def post_vault_rescan(
    full: bool = False, ctx: AppContext = Depends(get_context)
) -> VaultRescanResponse:
    root = _vault_root(ctx)
    summary = await vault_index.rescan(root, ctx, full=full)
    return VaultRescanResponse(
        processed=summary.processed, skipped=summary.skipped,
        failed=summary.failed, deleted=summary.deleted,
    )


@app.get("/vault/backlinks", response_model=VaultBacklinksResponse)
async def get_vault_backlinks(
    path: str = Query(...), ctx: AppContext = Depends(get_context)
) -> VaultBacklinksResponse:
    root = _vault_root(ctx)
    try:
        content, _ = vault_fs.read_file(root, path)
    except VaultPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    _, meta = parse_frontmatter(content)
    node_id = meta.get("id")
    if not node_id:
        return VaultBacklinksResponse(backlinks=[])
    edges = ctx.graph.get_incoming_edges(node_id, relation_type="links_to")
    nodes = [ctx.graph.get_node(e.source) for e in edges]
    return VaultBacklinksResponse(backlinks=[n for n in nodes if n is not None])


@app.get("/vault/search", response_model=list[VaultSearchHitResponse])
async def get_vault_search(
    q: str = Query(..., min_length=1), ctx: AppContext = Depends(get_context)
) -> list[VaultSearchHitResponse]:
    _vault_root(ctx)
    nodes = ctx.graph.search_vault_content(q)
    return [VaultSearchHitResponse(node=n) for n in nodes]
```

- [ ] **Step 6: Tests laufen lassen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_api.py -v`
Expected: PASS (alle Tests der Datei)

- [ ] **Step 7: Gesamten Testlauf verifizieren**

Run: `.venv/bin/pytest`
Expected: PASS — komplette Testsuite grün, keine Regression in bestehenden Tests (insbesondere `test_dispatcher.py`, `test_rebuild.py`, `test_e2e_*`, `test_vault_import.py` — letzterer bleibt unverändert, da der alte `/vault/scan`-Importer nicht angefasst wurde).

- [ ] **Step 8: Commit**

```bash
git add backend/api_models.py backend/main.py backend/tests/test_api.py
git commit -m "feat: API rescan/backlinks/search für Vault"
```

---

## Nach diesem Plan

Backend ist laut Spec-Phasentabelle "editorfähig". Nächste Schritte (separate Pläne, nicht Teil dieses Dokuments):

- **Frontend-Plan** (Phase 3–5): Editor-Tab in `frontend/src/App.tsx` (viertes `view`-State), CodeMirror-6-Integration (`@codemirror/lang-markdown` als neue Dependency), Live-Preview-Decorations, Wiki-Link-Autocomplete, Dateibaum/Suche/Backlinks-Panel gegen die hier gebaute API.
- **Cutover-Plan** (Phase 6): `cbks note`/`add` (und `POST /notes`) auf Vault-Schreibpfad (`inbox/`-Ordner) umstellen, Rescan-bei-Serverstart-Flag, `docs/CBKS_SPEC_v1.2.md` aktualisieren. Bewusst erst nach dem Frontend-Plan, da der Editor im Alltag getragen haben muss, bevor die bisherigen Schreibpfade verändert werden.

## Bekannte Einschränkung: `cbks rebuild` + Vault

`cbks rebuild` rekonstruiert Node-Content, -Vektoren und Entity-Kanten korrekt
aus dem Event-Log. `metadata["file_hash"]` und `links_to`-Wiki-Link-Kanten
leben aber außerhalb des Event-Replay-Pfads (in `vault_index._finalize`/
`_sync_wikilinks`) und gehen bei einem Rebuild verloren. Nach einem
`cbks rebuild` auf einer Vault-Datenbank zusätzlich `cbks index --full`
ausführen, um beides wiederherzustellen. Festgestellt im finalen
Whole-Branch-Review dieses Plans, bewusst als dokumentierte Einschränkung
belassen statt architektonisch vertieft (rebuild vault-aware zu machen wäre
ein größerer Eingriff als der aktuelle Scope rechtfertigt).
