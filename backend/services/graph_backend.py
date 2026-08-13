import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import networkx as nx
import numpy as np

from backend.models.edges import Edge
from backend.models.nodes import Node


class GraphBackend:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self.graph = nx.DiGraph()
        self._load_cache()

    def _load_cache(self) -> None:
        self.graph.clear()
        for row in self._conn.execute("SELECT id FROM nodes").fetchall():
            self.graph.add_node(row["id"])
        for row in self._conn.execute("SELECT source, target FROM edges").fetchall():
            self.graph.add_edge(row["source"], row["target"])

    def add_node(self, node: Node) -> None:
        try:
            self._conn.execute(
                "INSERT INTO nodes (id, title, type, hemisphere, content, content_hash, "
                "activation, confidence, emotional_weight, decay_rate, importance, "
                "creation_time, last_access, access_counter, metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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

    def add_edge(self, edge: Edge) -> None:
        try:
            self._conn.execute(
                "INSERT INTO edges (id, source, target, relation_type, strength, "
                "temporal_score, emotional_score, reinforcement_count, creation_time, "
                "last_updated, metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    edge.id, edge.source, edge.target, edge.relation_type, edge.strength,
                    edge.temporal_score, edge.emotional_score, edge.reinforcement_count,
                    edge.creation_time, edge.last_updated, json.dumps(edge.metadata),
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        self.graph.add_edge(edge.source, edge.target)

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

    def get_all_edges(self) -> list[Edge]:
        rows = self._conn.execute("SELECT * FROM edges").fetchall()
        return [self._row_to_edge(row) for row in rows]

    def get_node(self, node_id: str) -> Optional[Node]:
        row = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return None if row is None else self._row_to_node(row)

    def get_all_nodes(self) -> list[Node]:
        rows = self._conn.execute("SELECT * FROM nodes").fetchall()
        return [self._row_to_node(row) for row in rows]

    def get_concept_nodes(self) -> list[Node]:
        rows = self._conn.execute(
            "SELECT * FROM nodes WHERE type = 'concept' ORDER BY creation_time ASC"
        ).fetchall()
        return [self._row_to_node(row) for row in rows]

    def find_node_by_title(self, title: str) -> Optional[Node]:
        # Python-seitiger Vergleich statt COLLATE NOCASE: SQLite faltet nur ASCII
        # A-Z/a-z, nicht deutsche Umlaute/ß (Ä/ä, Ö/ö, Ü/ü). str.lower() faltet
        # Unicode korrekt und verhindert doppelte Concept-Nodes bei Dedup.
        normalized = title.lower()
        for row in self._conn.execute("SELECT * FROM nodes").fetchall():
            if row["title"].lower() == normalized:
                return self._row_to_node(row)
        return None

    def get_neighbors(self, node_id: str, depth: int = 1) -> list[Node]:
        if node_id not in self.graph:
            return []
        seen = {node_id}
        frontier = {node_id}
        for _ in range(depth):
            next_frontier: set[str] = set()
            for current in frontier:
                next_frontier |= set(self.graph.successors(current))
                next_frontier |= set(self.graph.predecessors(current))
            next_frontier -= seen
            seen |= next_frontier
            frontier = next_frontier
        seen.discard(node_id)
        nodes = [self.get_node(n) for n in seen]
        return [n for n in nodes if n is not None]

    def link_vector(self, node_id: str, faiss_id: int, model: str) -> None:
        try:
            self._conn.execute(
                "INSERT INTO node_vectors (node_id, faiss_id, model) VALUES (?, ?, ?)",
                (node_id, faiss_id, model),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def touch_access(self, node_ids: list[str]) -> None:
        # last_access aktualisieren + access_counter inkrementieren, damit der
        # Decay-Faktor in rag.ask() tatsaechlich "Tage seit letztem Zugriff"
        # widerspiegelt statt "Tage seit Erzeugung". Batch per executemany statt
        # N einzelner UPDATE-Roundtrips.
        if not node_ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        self._conn.executemany(
            "UPDATE nodes SET last_access = ?, access_counter = access_counter + 1 "
            "WHERE id = ?",
            [(now, nid) for nid in node_ids],
        )
        self._conn.commit()

    def get_node_by_faiss_id(self, faiss_id: int) -> Optional[Node]:
        row = self._conn.execute(
            "SELECT node_id FROM node_vectors WHERE faiss_id = ?", (faiss_id,)
        ).fetchone()
        if row is None:
            return None
        return self.get_node(row["node_id"])

    def set_concept_vector(self, node_id: str, vector: list[float]) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO concept_title_vectors (node_id, vector) VALUES (?, ?)",
            (node_id, self._vector_to_blob(vector)),
        )
        self._conn.commit()

    def get_concept_vectors(self) -> list[tuple[str, list[float]]]:
        rows = self._conn.execute("SELECT node_id, vector FROM concept_title_vectors").fetchall()
        return [(row["node_id"], self._blob_to_vector(row["vector"])) for row in rows]

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

    def delete_node(self, node_id: str) -> Optional[int]:
        # Liefert die faiss_id, damit der Aufrufer Vektor + Origin-Event
        # entfernen kann (faiss_id == events.id, siehe dispatcher.process_event).
        # Gibt None zurueck, wenn der Node keinen Vektor hatte (z.B. Concept).
        row = self._conn.execute(
            "SELECT faiss_id FROM node_vectors WHERE node_id = ?", (node_id,)
        ).fetchone()
        faiss_id = None if row is None else row["faiss_id"]

        try:
            # Foreign-Key-CASCADE loescht edges, node_vectors und
            # concept_title_vectors automatisch.
            self._conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        self.graph.remove_node(node_id)
        return faiss_id

    def merge_nodes(self, keep_id: str, remove_id: str) -> None:
        if keep_id == remove_id:
            return
        keep_node = self.get_node(keep_id)
        remove_node = self.get_node(remove_id)
        if keep_node is None or remove_node is None:
            raise ValueError(f"Node nicht gefunden: keep={keep_id}, remove={remove_id}")

        try:
            # Case-insensitive Dedup: dict, keyed per lowercase, damit auch
            # Dubletten innerhalb der remove_node-eigenen Aliase/Titel erkannt
            # werden (nicht nur gegen den keep_node-Stand).
            keep_title_lower = keep_node.title.lower()
            combined: dict[str, str] = {}
            for existing_alias in keep_node.metadata.get("aliases", []):
                combined.setdefault(existing_alias.lower(), existing_alias)

            candidates = list(remove_node.metadata.get("aliases", [])) + [remove_node.title]
            for candidate in candidates:
                candidate_lower = candidate.lower()
                if candidate_lower == keep_title_lower:
                    continue
                combined.setdefault(candidate_lower, candidate)

            aliases = set(combined.values())
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

    _EDGE_COLUMNS = frozenset({"source", "target"})

    def _rewire_edges(self, column: str, keep_id: str, remove_id: str) -> None:
        if column not in self._EDGE_COLUMNS:
            raise ValueError(f"Ungueltige Spalte fuer _rewire_edges: {column!r}")
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

    def counts(self) -> dict[str, int]:
        nodes = self._conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()["n"]
        edges = self._conn.execute("SELECT COUNT(*) AS n FROM edges").fetchone()["n"]
        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> Node:
        return Node(
            id=row["id"], title=row["title"], type=row["type"],
            hemisphere=row["hemisphere"], content=row["content"],
            content_hash=row["content_hash"], activation=row["activation"],
            confidence=row["confidence"], emotional_weight=row["emotional_weight"],
            decay_rate=row["decay_rate"], importance=row["importance"],
            creation_time=row["creation_time"], last_access=row["last_access"],
            access_counter=row["access_counter"], metadata=json.loads(row["metadata"]),
        )

    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> Edge:
        return Edge(
            id=row["id"], source=row["source"], target=row["target"],
            relation_type=row["relation_type"], strength=row["strength"],
            temporal_score=row["temporal_score"], emotional_score=row["emotional_score"],
            reinforcement_count=row["reinforcement_count"], creation_time=row["creation_time"],
            last_updated=row["last_updated"], metadata=json.loads(row["metadata"]),
        )

    @staticmethod
    def _vector_to_blob(vector: list[float]) -> bytes:
        return np.array(vector, dtype="float32").tobytes()

    @staticmethod
    def _blob_to_vector(blob: bytes) -> list[float]:
        return np.frombuffer(blob, dtype="float32").tolist()
