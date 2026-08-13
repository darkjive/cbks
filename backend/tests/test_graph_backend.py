import sqlite3
from datetime import datetime, timezone

import pytest

from backend.models.edges import Edge
from backend.models.nodes import Node
from backend.services.graph_backend import GraphBackend
from backend.storage.sqlite_db import get_connection, init_db

TS = "2026-07-05T00:00:00+00:00"


@pytest.fixture
def graph(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    return GraphBackend(conn)


@pytest.fixture()
def conn(tmp_path):
    connection = get_connection(tmp_path / "test.db")
    init_db(connection)
    return connection


def make_node(node_id: str, title: str, node_type: str = "concept") -> Node:
    return Node(id=node_id, title=title, type=node_type, creation_time=TS, last_access=TS)


def make_edge(edge_id: str, source: str, target: str) -> Edge:
    return Edge(
        id=edge_id, source=source, target=target, relation_type="mentions",
        creation_time=TS, last_updated=TS,
    )


def test_add_node_and_get_node(graph):
    graph.add_node(make_node("n1", "FAISS"))
    node = graph.get_node("n1")
    assert node is not None
    assert node.title == "FAISS"


def test_find_node_by_title_case_insensitive(graph):
    graph.add_node(make_node("n1", "Graphentheorie"))
    found = graph.find_node_by_title("graphentheorie")
    assert found is not None
    assert found.id == "n1"


def test_find_node_by_title_returns_none_when_missing(graph):
    assert graph.find_node_by_title("Unbekannt") is None


def test_find_node_by_title_case_insensitive_unicode(graph):
    graph.add_node(make_node("n1", "Übersicht"))
    found = graph.find_node_by_title("übersicht")
    assert found is not None
    assert found.id == "n1"


def test_add_edge_and_get_neighbors(graph):
    graph.add_node(make_node("doc1", "Dokument", "document"))
    graph.add_node(make_node("concept1", "Entität"))
    graph.add_edge(make_edge("e1", "doc1", "concept1"))

    neighbors = graph.get_neighbors("doc1")
    assert [n.id for n in neighbors] == ["concept1"]


def test_link_vector_and_get_node_by_faiss_id(graph):
    graph.add_node(make_node("doc1", "Dokument", "document"))
    graph.link_vector("doc1", faiss_id=42, model="bge-m3")

    node = graph.get_node_by_faiss_id(42)
    assert node is not None
    assert node.id == "doc1"


def test_clear_all_empties_graph(graph):
    graph.add_node(make_node("doc1", "Dokument", "document"))
    graph.add_node(make_node("concept1", "Entität"))
    graph.add_edge(make_edge("e1", "doc1", "concept1"))

    graph.clear_all()

    assert graph.get_node("doc1") is None
    assert graph.counts() == {"nodes": 0, "edges": 0}


def test_clear_all_rolls_back_on_partial_failure(graph):
    graph.add_node(make_node("doc1", "Dokument", "document"))
    graph.add_node(make_node("concept1", "Entität"))
    graph.add_edge(make_edge("e1", "doc1", "concept1"))

    graph._conn.execute(
        "CREATE TEMP TRIGGER fail_node_delete BEFORE DELETE ON nodes "
        "BEGIN SELECT RAISE(ABORT, 'boom'); END;"
    )

    with pytest.raises(sqlite3.DatabaseError):
        graph.clear_all()

    graph._conn.execute("DROP TRIGGER fail_node_delete")
    assert graph.counts() == {"nodes": 2, "edges": 1}


def test_counts(graph):
    graph.add_node(make_node("doc1", "Dokument", "document"))
    graph.add_node(make_node("concept1", "Entität"))
    graph.add_edge(make_edge("e1", "doc1", "concept1"))

    assert graph.counts() == {"nodes": 2, "edges": 1}


def test_cache_loaded_from_existing_sqlite_data(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    first = GraphBackend(conn)
    first.add_node(make_node("doc1", "Dokument", "document"))

    second = GraphBackend(conn)  # neue Instanz, muss aus SQLite laden
    assert second.get_node("doc1") is not None


def test_get_all_nodes_returns_every_node(graph):
    graph.add_node(make_node("n1", "FAISS"))
    graph.add_node(make_node("n2", "NetworkX"))

    nodes = graph.get_all_nodes()

    assert {n.id for n in nodes} == {"n1", "n2"}


def test_get_all_edges_returns_every_edge(graph):
    graph.add_node(make_node("n1", "FAISS"))
    graph.add_node(make_node("n2", "NetworkX"))
    graph.add_edge(make_edge("e1", "n1", "n2"))

    edges = graph.get_all_edges()

    assert len(edges) == 1
    assert edges[0].id == "e1"
    assert edges[0].source == "n1"
    assert edges[0].target == "n2"


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


def test_merge_nodes_deduplicates_aliases_case_insensitively(graph):
    """Test that merge_nodes deduplicates aliases case-insensitively, like add_alias()."""
    # keep_node: title="DMT", aliases=["dmt", "Dimethyltryptamine"]
    keep = Node(
        id="c1", title="DMT", type="concept",
        creation_time=TS, last_access=TS,
        metadata={"aliases": ["dmt", "Dimethyltryptamine"]},
    )
    # remove_node: title="dmt" (case-variant of keep's title), aliases=["Dmt"] (case-variant)
    remove = Node(
        id="c2", title="dmt", type="concept",
        creation_time=TS, last_access=TS,
        metadata={"aliases": ["Dmt"]},
    )
    graph.add_node(keep)
    graph.add_node(remove)

    graph.merge_nodes("c1", "c2")

    survivor = graph.get_node("c1")
    aliases = survivor.metadata.get("aliases", [])
    # Should contain "Dimethyltryptamine" and possibly one case-variant of "DMT"/"dmt",
    # but NOT multiple case-variants (no "dmt", "Dmt" both present; no "dmt" or "Dmt" added from remove)
    assert "Dimethyltryptamine" in aliases
    # Count how many aliases are case-variants of "dmt" (ignoring case)
    dmt_variants = [a for a in aliases if a.lower() == "dmt"]
    assert len(dmt_variants) <= 1, f"Expected at most 1 case-variant of 'dmt', got {dmt_variants}"
    # The remove_node.title "dmt" and remove_node.aliases "Dmt" should not be added (already case-dup of keep aliases)
    assert "dmt" not in aliases or len(dmt_variants) == 1  # "dmt" is either absent or the sole variant
    assert "Dmt" not in aliases or len(dmt_variants) == 1   # "Dmt" is either absent or the sole variant


def test_merge_nodes_deduplicates_against_keep_existing_alias(graph):
    """Zwei remove-Kandidaten (Alias + Titel) collapse gegen einen keep-Alias.

    Diskriminiert gegen stale-Snapshot-Logik: Die Dedup muss gegen die LIVE,
    WACHSENDE kombinierte Liste checken, nicht gegen einen Snapshot vor der Schleife.
    Mit zwei case-Varianten als Kandidaten können wir feststellen, ob die Dedup
    frisch ist (beide werden erkannt) oder stale (erste wird hinzugefügt, zweite
    wird möglicherweise falsch erneut hinzugefügt, wenn der Snapshot nicht aktualisiert wird).
    """
    keep = Node(
        id="c1", title="Serotonin", type="concept",
        creation_time=TS, last_access=TS,
        metadata={"aliases": ["Serotonin-Rezeptor"]},
    )
    # remove_node hat ZWEI case-Varianten desselben keep-Alias:
    # - alias "serotonin-rezeptor"
    # - title "SEROTONIN-REZEPTOR"
    # Beide sollten gegen den keep-Alias "Serotonin-Rezeptor" dedupliziert werden.
    remove = Node(
        id="c2", title="SEROTONIN-REZEPTOR", type="concept",
        creation_time=TS, last_access=TS,
        metadata={"aliases": ["serotonin-rezeptor"]},
    )
    graph.add_node(keep)
    graph.add_node(remove)

    graph.merge_nodes("c1", "c2")

    aliases = graph.get_node("c1").metadata.get("aliases", [])
    variants = [a for a in aliases if a.lower() == "serotonin-rezeptor"]
    assert len(variants) == 1, f"Erwartet genau 1 Case-Variante, bekommen: {variants}"


def test_merge_nodes_deduplicates_remove_nodes_own_title_and_alias(graph):
    """Selbstreferenzieller Fall: remove_node.title und remove_node-eigener Alias sind Case-Varianten."""
    keep = Node(
        id="c1", title="Ayahuasca", type="concept",
        creation_time=TS, last_access=TS,
        metadata={"aliases": []},
    )
    remove = Node(
        id="c2", title="DMT", type="concept",
        creation_time=TS, last_access=TS,
        metadata={"aliases": ["dmt"]},
    )
    graph.add_node(keep)
    graph.add_node(remove)

    graph.merge_nodes("c1", "c2")

    aliases = graph.get_node("c1").metadata.get("aliases", [])
    variants = [a for a in aliases if a.lower() == "dmt"]
    assert len(variants) == 1, f"Erwartet genau 1 Eintrag fuer 'DMT'/'dmt', bekommen: {variants}"


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
    graph.add_edge(Edge(id="e2", source="n1", target="n2", relation_type="supports",
                         creation_time=now, last_updated=now))

    all_incoming = graph.get_incoming_edges("n2")
    only_supports = graph.get_incoming_edges("n2", relation_type="supports")

    assert len(all_incoming) == 2
    assert len(only_supports) == 1
    assert only_supports[0].id == "e2"


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
