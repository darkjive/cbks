# Entity Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Concept-Nodes, die dieselbe Entität unter leicht anderem Namen bezeichnen (z.B. „DMT" vs. „Dimethyltryptamin"), werden beim Ingest automatisch zusammengeführt statt dupliziert; ein Batch-Befehl bereinigt zusätzlich den bestehenden Graphen.

**Architecture:** Neuer Service `EntityResolver` matched Konzept-Titel über einen Schnellpfad (exakter/Alias-Titelvergleich) und, falls kein Treffer, über Cosine-Similarity von Titel-Embeddings (gespeichert als BLOB in einer neuen SQLite-Tabelle), mit LLM-Bestätigung bei Grenzfällen. `GraphBackend.merge_nodes()` führt den eigentlichen Merge durch (Kanten umhängen, Alias speichern, Verlierer löschen). Der Dispatcher nutzt den Resolver statt des bisherigen exakten Titelvergleichs; CLI und REST-API bekommen einen `dedupe`-Befehl für den Bestand.

**Tech Stack:** Bestehend — sqlite3, numpy (bereits Dependency via faiss), Ollama (Embeddings + LLM). Keine neuen Abhängigkeiten.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-07-entity-resolution-design.md`
- TDD für jeden Task: Test zuerst, Fehlschlag verifizieren, implementieren, Erfolg verifizieren.
- Schwellwerte (`0.92` / `0.75`) sind Modul-Konstanten in `entity_resolver.py`, keine Config-Werte.
- Bei LLM-Fehler im Grenzfall: kein Merge (konservativ) — Ingest darf dadurch nie fehlschlagen.
- Kein zweiter FAISS-Index, kein Review-UI, keine konfigurierbaren Schwellwerte (siehe Spec „Nicht in Scope").
- Alle Backend-Tests müssen am Ende weiterhin grün sein: `.venv/bin/python -m pytest backend -q`.

---

### Task 1: GraphBackend — Konzept-Vektor-Speicher

**Files:**
- Modify: `backend/storage/sqlite_db.py`
- Modify: `backend/services/graph_backend.py`
- Test: `backend/tests/test_sqlite_db.py`
- Test: `backend/tests/test_graph_backend.py`

**Interfaces:**
- Produces: `GraphBackend.get_concept_nodes() -> list[Node]`, `GraphBackend.set_concept_vector(node_id: str, vector: list[float]) -> None`, `GraphBackend.get_concept_vectors() -> list[tuple[str, list[float]]]`

- [x] **Step 1: Fehlschlagenden Schema-Test schreiben**

Füge in `backend/tests/test_sqlite_db.py` am Ende an:

```python
def test_init_db_creates_concept_title_vectors_table(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)

    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {row["name"] for row in rows}

    assert "concept_title_vectors" in table_names
```

- [x] **Step 2: Test ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_sqlite_db.py -v -k concept_title_vectors`
Expected: FAIL — `assert "concept_title_vectors" in table_names` schlägt fehl (Tabelle existiert nicht)

- [x] **Step 3: Tabelle im Schema ergänzen**

In `backend/storage/sqlite_db.py`, in der `SCHEMA`-Konstante, nach dem `node_vectors`-Table-Block (vor dem schließenden `"""`) einfügen:

```sql

CREATE TABLE IF NOT EXISTS concept_title_vectors (
    node_id TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
    vector  BLOB NOT NULL
);
```

- [x] **Step 4: Schema-Test ausführen, Erfolg verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_sqlite_db.py -v`
Expected: PASS (alle Tests in der Datei)

- [x] **Step 5: Fehlschlagende GraphBackend-Tests schreiben**

Füge in `backend/tests/test_graph_backend.py` am Ende an:

```python
def test_set_and_get_concept_vector(graph):
    graph.add_node(make_node("n1", "FAISS"))

    graph.set_concept_vector("n1", [1.0, 0.0, 0.5])

    vectors = graph.get_concept_vectors()
    assert len(vectors) == 1
    node_id, vector = vectors[0]
    assert node_id == "n1"
    assert vector == pytest.approx([1.0, 0.0, 0.5])


def test_get_concept_nodes_returns_only_concepts_ordered_by_creation(graph):
    graph.add_node(make_node("doc1", "Dokument", "document"))
    graph.add_node(Node(
        id="c1", title="Erstes Konzept", type="concept",
        creation_time="2026-07-01T00:00:00+00:00", last_access=TS,
    ))
    graph.add_node(Node(
        id="c2", title="Zweites Konzept", type="concept",
        creation_time="2026-07-02T00:00:00+00:00", last_access=TS,
    ))

    concepts = graph.get_concept_nodes()

    assert [n.id for n in concepts] == ["c1", "c2"]


def test_clear_all_also_clears_concept_vectors(graph):
    graph.add_node(make_node("n1", "FAISS"))
    graph.set_concept_vector("n1", [1.0, 0.0])

    graph.clear_all()

    assert graph.get_concept_vectors() == []
```

- [x] **Step 6: Tests ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_graph_backend.py -v -k "concept_vector or get_concept_nodes"`
Expected: FAIL mit `AttributeError: 'GraphBackend' object has no attribute 'set_concept_vector'`

- [x] **Step 7: Implementieren**

In `backend/services/graph_backend.py`, `import numpy as np` nach `import sqlite3` ergänzen:

```python
import json
import sqlite3
from typing import Optional

import networkx as nx
import numpy as np

from backend.models.edges import Edge
from backend.models.nodes import Node
```

Nach `get_all_nodes` (nach der bestehenden Methode, vor `find_node_by_title`) einfügen:

```python
    def get_concept_nodes(self) -> list[Node]:
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE type = 'concept' ORDER BY creation_time ASC"
        ).fetchall()
        return [self._row_to_node(row) for row in rows]
```

Nach `get_node_by_faiss_id` (vor `clear_all`) einfügen:

```python
    def set_concept_vector(self, node_id: str, vector: list[float]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO concept_title_vectors (node_id, vector) VALUES (?, ?)",
            (node_id, self._vector_to_blob(vector)),
        )
        self._conn.commit()

    def get_concept_vectors(self) -> list[tuple[str, list[float]]]:
        rows = self._conn.execute("SELECT node_id, vector FROM concept_title_vectors").fetchall()
        return [(row["node_id"], self._blob_to_vector(row["vector"])) for row in rows]
```

`clear_all` erweitern (Zeile mit `DELETE FROM node_vectors` ergänzen um eine weitere DELETE-Zeile):

```python
    def clear_all(self) -> None:
        try:
            self._conn.execute("DELETE FROM edges")
            self._conn.execute("DELETE FROM node_vectors")
            self._conn.execute("DELETE FROM concept_title_vectors")
            self._conn.execute("DELETE FROM nodes")
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        self.graph.clear()
```

Am Ende der Klasse (nach `_row_to_edge`) die Blob-Helper ergänzen:

```python
    @staticmethod
    def _vector_to_blob(vector: list[float]) -> bytes:
        return np.array(vector, dtype="float32").tobytes()

    @staticmethod
    def _blob_to_vector(blob: bytes) -> list[float]:
        return np.frombuffer(blob, dtype="float32").tolist()
```

- [x] **Step 8: Tests ausführen, Erfolg verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_graph_backend.py -v`
Expected: PASS (alle Tests in der Datei)

- [x] **Step 9: Commit**

```bash
git add backend/storage/sqlite_db.py backend/services/graph_backend.py backend/tests/test_sqlite_db.py backend/tests/test_graph_backend.py
git commit -m "feat: Konzept-Titel-Vektoren in SQLite speichern (Grundlage Entity Resolution)"
```

---

### Task 2: GraphBackend — merge_nodes

**Files:**
- Modify: `backend/services/graph_backend.py`
- Test: `backend/tests/test_graph_backend.py`

**Interfaces:**
- Consumes: `GraphBackend.set_concept_vector`, `GraphBackend.get_concept_vectors` (Task 1)
- Produces: `GraphBackend.merge_nodes(keep_id: str, remove_id: str) -> None`, `GraphBackend.add_alias(node_id: str, alias: str) -> None`

- [x] **Step 1: Fehlschlagende Tests schreiben**

Füge in `backend/tests/test_graph_backend.py` am Ende an:

```python
def test_merge_nodes_keeps_older_node_and_rewires_edges(graph):
    older = Node(id="c1", title="DMT", type="concept",
                 creation_time="2026-07-01T00:00:00+00:00", last_access=TS)
    newer = Node(id="c2", title="Dimethyltryptamin", type="concept",
                 creation_time="2026-07-02T00:00:00+00:00", last_access=TS)
    graph.add_node(older)
    graph.add_node(newer)
    graph.add_node(make_node("doc1", "Dokument", "document"))
    graph.add_edge(make_edge("e1", "doc1", "c2"))

    graph.merge_nodes("c1", "c2")

    assert graph.get_node("c2") is None
    assert graph.get_node("c1") is not None
    neighbors = graph.get_neighbors("doc1")
    assert [n.id for n in neighbors] == ["c1"]


def test_merge_nodes_records_alias(graph):
    graph.add_node(make_node("c1", "DMT"))
    graph.add_node(make_node("c2", "Dimethyltryptamin"))

    graph.merge_nodes("c1", "c2")

    survivor = graph.get_node("c1")
    assert survivor.metadata["aliases"] == ["Dimethyltryptamin"]


def test_merge_nodes_drops_duplicate_edge_after_rewire(graph):
    graph.add_node(make_node("doc1", "Dokument", "document"))
    graph.add_node(make_node("c1", "DMT"))
    graph.add_node(make_node("c2", "Dimethyltryptamin"))
    graph.add_edge(make_edge("e1", "doc1", "c1"))
    graph.add_edge(make_edge("e2", "doc1", "c2"))

    graph.merge_nodes("c1", "c2")

    rows = graph._conn.execute(
        "SELECT COUNT(*) AS n FROM edges WHERE source = 'doc1' AND target = 'c1'"
    ).fetchone()
    assert rows["n"] == 1


def test_merge_nodes_drops_self_loop_after_rewire(graph):
    graph.add_node(make_node("c1", "DMT"))
    graph.add_node(make_node("c2", "Dimethyltryptamin"))
    graph.add_edge(make_edge("e1", "c2", "c1"))

    graph.merge_nodes("c1", "c2")

    rows = graph._conn.execute("SELECT COUNT(*) AS n FROM edges").fetchone()
    assert rows["n"] == 0


def test_merge_nodes_deletes_removed_node_and_its_vector(graph):
    graph.add_node(make_node("c1", "DMT"))
    graph.add_node(make_node("c2", "Dimethyltryptamin"))
    graph.set_concept_vector("c1", [1.0, 0.0])
    graph.set_concept_vector("c2", [0.9, 0.1])

    graph.merge_nodes("c1", "c2")

    vectors = dict(graph.get_concept_vectors())
    assert "c2" not in vectors
    assert "c1" in vectors


def test_merge_nodes_rolls_back_on_partial_failure(graph):
    graph.add_node(make_node("c1", "DMT"))
    graph.add_node(make_node("c2", "Dimethyltryptamin"))

    graph._conn.execute(
        "CREATE TEMP TRIGGER fail_node_delete BEFORE DELETE ON nodes "
        "BEGIN SELECT RAISE(ABORT, 'boom'); END;"
    )

    with pytest.raises(sqlite3.DatabaseError):
        graph.merge_nodes("c1", "c2")

    graph._conn.execute("DROP TRIGGER fail_node_delete")
    assert graph.get_node("c1") is not None
    assert graph.get_node("c2") is not None
    assert graph.get_node("c1").metadata.get("aliases") is None


def test_add_alias_records_new_alias(graph):
    graph.add_node(make_node("c1", "DMT"))

    graph.add_alias("c1", "Dimethyltryptamin")

    assert graph.get_node("c1").metadata["aliases"] == ["Dimethyltryptamin"]


def test_add_alias_ignores_duplicate(graph):
    graph.add_node(make_node("c1", "DMT"))
    graph.add_alias("c1", "Dimethyltryptamin")

    graph.add_alias("c1", "dimethyltryptamin")  # gleicher Alias, andere Groß-/Kleinschreibung

    assert graph.get_node("c1").metadata["aliases"] == ["Dimethyltryptamin"]
```

- [x] **Step 2: Tests ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_graph_backend.py -v -k "merge_nodes or add_alias"`
Expected: FAIL mit `AttributeError: 'GraphBackend' object has no attribute 'merge_nodes'`

- [x] **Step 3: Implementieren**

In `backend/services/graph_backend.py`, nach `clear_all` (vor `counts`) einfügen:

```python
    def merge_nodes(self, keep_id: str, remove_id: str) -> None:
        if keep_id == remove_id:
            return
        keep_node = self.get_node(keep_id)
        remove_node = self.get_node(remove_id)
        if keep_node is None or remove_node is None:
            raise ValueError(f"Node nicht gefunden: keep={keep_id}, remove={remove_id}")

        try:
            aliases = set(keep_node.metadata.get("aliases", []))
            aliases.update(remove_node.metadata.get("aliases", []))
            aliases.add(remove_node.title)
            aliases.discard(keep_node.title)
            self._write_aliases(keep_id, keep_node.metadata, aliases)

            self._rewire_edges("source", keep_id, remove_id)
            self._rewire_edges("target", keep_id, remove_id)
            self._conn.execute(
                "DELETE FROM edges WHERE source = ? AND target = ?", (keep_id, keep_id)
            )
            self._conn.execute("DELETE FROM nodes WHERE id = ?", (remove_id,))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        self._load_cache()

    def _rewire_edges(self, column: str, keep_id: str, remove_id: str) -> None:
        other_column = "target" if column == "source" else "source"
        rows = self._conn.execute(
            f"SELECT id, {other_column}, relation_type FROM edges WHERE {column} = ?",
            (remove_id,),
        ).fetchall()
        for row in rows:
            other_id = row[other_column]
            duplicate = self._conn.execute(
                f"SELECT 1 FROM edges WHERE {column} = ? AND {other_column} = ? "
                "AND relation_type = ? AND id != ?",
                (keep_id, other_id, row["relation_type"], row["id"]),
            ).fetchone()
            if duplicate is not None:
                self._conn.execute("DELETE FROM edges WHERE id = ?", (row["id"],))
            else:
                self._conn.execute(
                    f"UPDATE edges SET {column} = ? WHERE id = ?", (keep_id, row["id"])
                )

    def add_alias(self, node_id: str, alias: str) -> None:
        node = self.get_node(node_id)
        if node is None:
            return
        aliases = set(node.metadata.get("aliases", []))
        if alias.lower() == node.title.lower() or alias.lower() in (a.lower() for a in aliases):
            return
        aliases.add(alias)
        self._write_aliases(node_id, node.metadata, aliases)
        self._conn.commit()

    def _write_aliases(self, node_id: str, metadata: dict, aliases: set[str]) -> None:
        new_metadata = dict(metadata)
        new_metadata["aliases"] = sorted(aliases)
        self._conn.execute(
            "UPDATE nodes SET metadata = ? WHERE id = ?",
            (json.dumps(new_metadata), node_id),
        )
```

- [x] **Step 4: Tests ausführen, Erfolg verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_graph_backend.py -v`
Expected: PASS (alle Tests in der Datei)

- [x] **Step 5: Commit**

```bash
git add backend/services/graph_backend.py backend/tests/test_graph_backend.py
git commit -m "feat: GraphBackend.merge_nodes führt Concept-Nodes zusammen"
```

---

### Task 3: EntityResolver — resolve()

**Files:**
- Create: `backend/services/entity_resolver.py`
- Test: `backend/tests/test_entity_resolver.py`

**Interfaces:**
- Consumes: `GraphBackend.get_concept_nodes/get_concept_vectors/get_node/add_alias` (Task 1, 2), `TemporalAgent.embed` (bestehend), `LLMClient`-Protocol (bestehend aus `backend/services/agents/prefrontal.py`)
- Produces: `EntityResolver(graph, temporal_agent, llm_client)`, `EntityResolver.resolve(title: str) -> Node | None` (async)

**Hinweis:** Wenn `resolve()` über die Embedding-/LLM-Schiene (nicht den exakten Titel) einen Treffer findet, wird der eingehende Titel per `graph.add_alias()` am gefundenen Node hinterlegt — sonst geht die „gleiche Entität, anderer Name"-Information beim Live-Ingest verloren und jeder künftige Ingest mit demselben Titel müsste erneut die teurere Embedding-Schiene durchlaufen statt den schnellen Alias-Fastpath zu treffen.

- [x] **Step 1: Fehlschlagende Tests schreiben**

Erstelle `backend/tests/test_entity_resolver.py`:

```python
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
```

- [x] **Step 2: Tests ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_entity_resolver.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.services.entity_resolver'`

- [x] **Step 3: Implementieren**

Erstelle `backend/services/entity_resolver.py`:

```python
import asyncio
import json
from dataclasses import dataclass

import numpy as np

from backend.models.nodes import Node
from backend.services.agents.prefrontal import LLMClient
from backend.services.agents.temporal import TemporalAgent
from backend.services.graph_backend import GraphBackend

_HIGH_SIMILARITY = 0.92
_LOW_SIMILARITY = 0.75

_SAME_ENTITY_PROMPT = (
    'Bezeichnen "{title_a}" und "{title_b}" dieselbe Entität (Konzept, Person, '
    'Technologie)? Antworte ausschließlich als JSON: {{"same": true}} oder {{"same": false}}.'
)


@dataclass
class MergeSummary:
    checked: int
    merged: int


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype="float32")
    vb = np.asarray(b, dtype="float32")
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


class EntityResolver:
    def __init__(self, graph: GraphBackend, temporal_agent: TemporalAgent, llm_client: LLMClient):
        self._graph = graph
        self._temporal_agent = temporal_agent
        self._llm_client = llm_client

    def _exact_or_alias_match(self, title: str) -> Node | None:
        normalized = title.lower()
        for node in self._graph.get_concept_nodes():
            if node.title.lower() == normalized:
                return node
            aliases = node.metadata.get("aliases", [])
            if normalized in (alias.lower() for alias in aliases):
                return node
        return None

    async def _confirm_same(self, title_a: str, title_b: str) -> bool:
        prompt = _SAME_ENTITY_PROMPT.format(title_a=title_a, title_b=title_b)
        try:
            raw = await asyncio.to_thread(self._llm_client.generate, prompt)
            return bool(json.loads(raw)["same"])
        except Exception:  # noqa: BLE001 - LLM-Fehler im Grenzfall => konservativ kein Merge
            return False

    async def resolve(self, title: str) -> Node | None:
        exact = self._exact_or_alias_match(title)
        if exact is not None:
            return exact

        vectors = self._graph.get_concept_vectors()
        if not vectors:
            return None

        query_vector = await self._temporal_agent.embed(title)
        best_id, best_score = None, -1.0
        for node_id, vector in vectors:
            score = _cosine_similarity(query_vector, vector)
            if score > best_score:
                best_id, best_score = node_id, score

        if best_score >= _HIGH_SIMILARITY:
            candidate = self._graph.get_node(best_id)
            if candidate is not None:
                self._graph.add_alias(candidate.id, title)
            return candidate
        if best_score >= _LOW_SIMILARITY:
            candidate = self._graph.get_node(best_id)
            if candidate is not None and await self._confirm_same(title, candidate.title):
                self._graph.add_alias(candidate.id, title)
                return candidate
        return None
```

- [x] **Step 4: Tests ausführen, Erfolg verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_entity_resolver.py -v`
Expected: PASS (alle 7 Tests)

- [x] **Step 5: Commit**

```bash
git add backend/services/entity_resolver.py backend/tests/test_entity_resolver.py
git commit -m "feat: EntityResolver.resolve() für Embedding+LLM-basierte Entity Resolution"
```

---

### Task 4: EntityResolver — register() und dedupe_all()

**Files:**
- Modify: `backend/services/entity_resolver.py`
- Test: `backend/tests/test_entity_resolver.py`

**Interfaces:**
- Consumes: `GraphBackend.merge_nodes` (Task 2), `EntityResolver.resolve`-Matching-Logik (Task 3)
- Produces: `EntityResolver.register(node: Node) -> None` (async), `EntityResolver.dedupe_all() -> MergeSummary` (async)

- [x] **Step 1: Fehlschlagende Tests schreiben**

Füge in `backend/tests/test_entity_resolver.py` am Ende an:

```python
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
```

- [x] **Step 2: Tests ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_entity_resolver.py -v -k "register or dedupe"`
Expected: FAIL mit `AttributeError: 'EntityResolver' object has no attribute 'register'`

- [x] **Step 3: Implementieren**

In `backend/services/entity_resolver.py` am Ende der Klasse `EntityResolver` (nach `resolve`) ergänzen:

```python
    async def register(self, node: Node) -> None:
        vector = await self._temporal_agent.embed(node.title)
        self._graph.set_concept_vector(node.id, vector)

    async def dedupe_all(self) -> MergeSummary:
        concepts = self._graph.get_concept_nodes()
        vectors = dict(self._graph.get_concept_vectors())
        kept: list[Node] = []
        checked = merged = 0

        for node in concepts:
            checked += 1
            vector = vectors.get(node.id)
            if vector is None:
                vector = await self._temporal_agent.embed(node.title)
                self._graph.set_concept_vector(node.id, vector)

            match: Node | None = None
            best_node, best_score = None, -1.0
            for kept_node in kept:
                score = _cosine_similarity(vector, vectors.get(kept_node.id) or [])
                if score > best_score:
                    best_node, best_score = kept_node, score

            if best_score >= _HIGH_SIMILARITY:
                match = best_node
            elif best_score >= _LOW_SIMILARITY and best_node is not None:
                if await self._confirm_same(node.title, best_node.title):
                    match = best_node

            if match is not None:
                self._graph.merge_nodes(match.id, node.id)
                merged += 1
            else:
                kept.append(node)
                vectors[node.id] = vector

        return MergeSummary(checked=checked, merged=merged)
```

Hinweis: `vectors[node.id] = vector` im `else`-Zweig stellt sicher, dass ein per Backfill nachträglich embeddeter Vektor für nachfolgende Vergleiche in `kept` verfügbar ist (`vectors.get(kept_node.id)` in der Schleife greift sonst ins Leere für gerade erst geheilte Legacy-Konzepte).

- [x] **Step 4: Tests ausführen, Erfolg verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_entity_resolver.py -v`
Expected: PASS (alle 12 Tests)

- [x] **Step 5: Commit**

```bash
git add backend/services/entity_resolver.py backend/tests/test_entity_resolver.py
git commit -m "feat: EntityResolver.register/dedupe_all für Ingest-Registrierung und Batch-Bereinigung"
```

---

### Task 5: Dispatcher- und AppContext-Integration

**Files:**
- Modify: `backend/services/dispatcher.py`
- Modify: `backend/app_context.py`
- Modify: `backend/tests/test_dispatcher.py`
- Modify: `backend/tests/test_rebuild.py`

**Interfaces:**
- Consumes: `EntityResolver.resolve/register` (Task 3, 4)
- Produces: `Dispatcher(event_log, graph, faiss_index, temporal_agent, prefrontal_agent, entity_resolver, embedding_model_name)`, `AppContext.entity_resolver: EntityResolver`

- [x] **Step 1: Test-Doppelgänger und neuen Integrationstest schreiben**

In `backend/tests/test_dispatcher.py` den Import ergänzen (nach `from backend.services.event_log import EventLog`):

```python
from backend.services.entity_resolver import EntityResolver
```

`FakeEmbeddingClient` ersetzen mit:

```python
class FakeEmbeddingClient:
    """Testdouble mit klar unterscheidbaren Vektoren für bekannte Titel."""

    _VECTORS = {
        "FAISS": [1.0, 0.0, 0.0, 0.0],
        "Ollama": [0.0, 1.0, 0.0, 0.0],
        "DMT": [0.0, 0.0, 1.0, 0.0],
        "Dimethyltryptamin": [0.0, 0.0, 0.99, 0.01],
    }

    def embed(self, text: str) -> list[float]:
        return self._VECTORS.get(text, [0.0, 0.0, 0.0, 1.0])
```

`make_dispatcher` ersetzen mit:

```python
def make_dispatcher(setup, llm_client=None, embedding_client=None):
    _, event_log, graph, faiss_index = setup
    llm = llm_client or FakeLLMClient("document", ["FAISS", "Ollama"])
    prefrontal = PrefrontalAgent(llm)
    temporal = TemporalAgent(embedding_client or FakeEmbeddingClient())
    resolver = EntityResolver(graph, temporal, llm)
    return Dispatcher(event_log, graph, faiss_index, temporal, prefrontal, resolver, "bge-m3")
```

Am Ende der Datei einen neuen Integrationstest ergänzen:

```python
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
```

In `backend/tests/test_rebuild.py` den Import ergänzen (nach `from backend.services.event_log import EventLog`):

```python
from backend.services.entity_resolver import EntityResolver
```

Die `setup`-Fixture ersetzen mit:

```python
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
```

- [x] **Step 2: Tests ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_dispatcher.py backend/tests/test_rebuild.py -v`
Expected: FAIL — `TypeError: Dispatcher.__init__() takes 7 positional arguments but 8 were given`

- [x] **Step 3: Dispatcher und AppContext implementieren**

In `backend/services/dispatcher.py` den Import ergänzen (zwischen `TemporalAgent`- und `EventLog`-Import):

```python
from backend.services.agents.prefrontal import PrefrontalAgent
from backend.services.agents.temporal import TemporalAgent
from backend.services.entity_resolver import EntityResolver
from backend.services.event_log import EventLog
```

Konstruktor ersetzen:

```python
    def __init__(
        self,
        event_log: EventLog,
        graph: GraphBackend,
        faiss_index: FaissIndex,
        temporal_agent: TemporalAgent,
        prefrontal_agent: PrefrontalAgent,
        entity_resolver: EntityResolver,
        embedding_model_name: str,
    ):
        self.event_log = event_log
        self.graph = graph
        self.faiss_index = faiss_index
        self.temporal_agent = temporal_agent
        self.prefrontal_agent = prefrontal_agent
        self.entity_resolver = entity_resolver
        self.embedding_model_name = embedding_model_name
```

Die Entity-Schleife in `process_event` ersetzen:

```python
        for entity_title in classification_result.entities:
            entity_node = await self.entity_resolver.resolve(entity_title)
            if entity_node is None:
                entity_node = Node(
                    id=str(uuid.uuid4()), title=entity_title, type="concept",
                    creation_time=now, last_access=now,
                )
                self.graph.add_node(entity_node)
                await self.entity_resolver.register(entity_node)
            edge = Edge(
                id=str(uuid.uuid4()), source=doc_node.id, target=entity_node.id,
                relation_type="mentions", creation_time=now, last_updated=now,
            )
            self.graph.add_edge(edge)
```

In `backend/app_context.py` den Import ergänzen und `AppContext` sowie `build_context` anpassen — komplette neue Datei:

```python
import sqlite3
from dataclasses import dataclass

from backend.config import Config
from backend.services.agents.prefrontal import OllamaLLMClient, PrefrontalAgent
from backend.services.agents.temporal import OllamaEmbeddingClient, TemporalAgent
from backend.services.dispatcher import Dispatcher
from backend.services.entity_resolver import EntityResolver
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
    entity_resolver: EntityResolver
    dispatcher: Dispatcher


def build_context() -> AppContext:
    config = Config.from_env()
    conn = get_connection(config.database_path)
    init_db(conn)
    event_log = EventLog(conn)
    graph = GraphBackend(conn)
    faiss_index = FaissIndex(dim=config.embedding_dim, index_path=config.faiss_index_path)
    temporal_agent = TemporalAgent(OllamaEmbeddingClient(config.ollama_host, config.embedding_model))
    llm_client = OllamaLLMClient(config.ollama_host, config.llm_model)
    prefrontal_agent = PrefrontalAgent(llm_client)
    entity_resolver = EntityResolver(graph, temporal_agent, llm_client)
    dispatcher = Dispatcher(
        event_log, graph, faiss_index, temporal_agent, prefrontal_agent,
        entity_resolver, config.embedding_model,
    )
    return AppContext(
        config=config, conn=conn, event_log=event_log, graph=graph, faiss_index=faiss_index,
        temporal_agent=temporal_agent, prefrontal_agent=prefrontal_agent,
        entity_resolver=entity_resolver, dispatcher=dispatcher,
    )
```

- [x] **Step 4: Tests ausführen, Erfolg verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_dispatcher.py backend/tests/test_rebuild.py backend/tests/test_app_context.py -v`
Expected: PASS (alle Tests in den drei Dateien)

- [x] **Step 5: Gesamte Suite laufen lassen**

Run: `.venv/bin/python -m pytest backend -q`
Expected: alle Tests grün (deckt test_api.py, test_cli.py, e2e-Tests ab, die transitiv über `build_context()` laufen)

- [x] **Step 6: Commit**

```bash
git add backend/services/dispatcher.py backend/app_context.py backend/tests/test_dispatcher.py backend/tests/test_rebuild.py
git commit -m "feat: Dispatcher nutzt EntityResolver statt exaktem Titelvergleich"
```

---

### Task 6: CLI-Befehl `cbks dedupe`

**Files:**
- Modify: `backend/cli.py`
- Test: `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: `AppContext.entity_resolver.dedupe_all()` (Task 5)
- Produces: `cbks dedupe` CLI-Befehl

- [x] **Step 1: Fehlschlagenden Test schreiben**

Füge in `backend/tests/test_cli.py` am Ende an:

```python
def test_dedupe_runs_without_error(tmp_path):
    runner.invoke(app, ["note", "Ein Text über FAISS"])

    result = runner.invoke(app, ["dedupe"])

    assert result.exit_code == 0
    assert "Geprüft" in result.stdout
```

- [x] **Step 2: Test ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_cli.py -v -k test_dedupe`
Expected: FAIL — Typer meldet `No such command 'dedupe'` (exit_code 2)

- [x] **Step 3: Implementieren**

In `backend/cli.py` nach dem `rebuild`-Befehl (vor `backup`) einfügen:

```python
@app.command()
def dedupe() -> None:
    ctx = build_context()
    summary = asyncio.run(ctx.entity_resolver.dedupe_all())
    typer.echo(f"Geprüft: {summary.checked}, Zusammengeführt: {summary.merged}")
```

- [x] **Step 4: Test ausführen, Erfolg verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_cli.py -v`
Expected: PASS (alle Tests in der Datei)

- [x] **Step 5: Commit**

```bash
git add backend/cli.py backend/tests/test_cli.py
git commit -m "feat: cbks dedupe bereinigt Concept-Duplikate im Bestand"
```

---

### Task 7: REST-Route `POST /dedupe`

**Files:**
- Modify: `backend/api_models.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `AppContext.entity_resolver.dedupe_all()` (Task 5)
- Produces: `POST /dedupe` → `DedupeResponse { checked: int, merged: int }`

- [x] **Step 1: Fehlschlagenden Test schreiben**

Füge in `backend/tests/test_api.py` am Ende an:

```python
def test_dedupe_route_returns_checked_and_merged_counts():
    client.post("/notes", json={"text": "Ein Text über FAISS"})

    response = client.post("/dedupe")

    assert response.status_code == 200
    body = response.json()
    assert "checked" in body
    assert "merged" in body
```

- [x] **Step 2: Test ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_api.py -v -k test_dedupe_route`
Expected: FAIL mit 404 (Route existiert nicht)

- [x] **Step 3: Implementieren**

In `backend/api_models.py` am Ende ergänzen:

```python


class DedupeResponse(BaseModel):
    checked: int
    merged: int
```

In `backend/main.py`:
- Import ergänzen: `DedupeResponse` zur bestehenden `from backend.api_models import (...)`-Liste hinzufügen (alphabetisch, zwischen `BackupResponse` und `GraphResponse`).
- Route ergänzen (nach `rebuild()`, vor `backup()`):

```python
@app.post("/dedupe", response_model=DedupeResponse)
def dedupe() -> DedupeResponse:
    ctx = build_context()
    summary = asyncio.run(ctx.entity_resolver.dedupe_all())
    return DedupeResponse(checked=summary.checked, merged=summary.merged)
```

- [x] **Step 4: Test ausführen, Erfolg verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_api.py -v -k test_dedupe_route`
Expected: PASS

- [x] **Step 5: Gesamte Suite laufen lassen**

Run: `.venv/bin/python -m pytest backend -q`
Expected: alle Tests grün

- [x] **Step 6: Commit**

```bash
git add backend/api_models.py backend/main.py backend/tests/test_api.py
git commit -m "feat: POST /dedupe bereinigt Concept-Duplikate über die REST-API"
```

---

## Abschluss-Kriterium

1. `.venv/bin/python -m pytest backend -q` läuft vollständig grün durch.
2. `cbks dedupe` und `POST /dedupe` liefern `{checked, merged}` und laufen gegen den bestehenden lokalen Graphen ohne Fehler (auch mit Concept-Nodes von vor diesem Feature — Backfill-Pfad in `dedupe_all` deckt das ab).
3. Ein Ingest mit einer Entität, die einer bestehenden sehr ähnlich ist (z.B. „DMT" nach „Dimethyltryptamin"), erzeugt keinen zweiten Concept-Node mehr, sondern hängt die Kante an den bestehenden.
4. Alle sieben Tasks sind committet.
