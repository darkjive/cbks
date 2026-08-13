import sqlite3
import threading

from backend.storage import sqlite_db
from backend.storage.sqlite_db import get_connection, init_db


def test_init_db_creates_all_tables(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)

    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {row["name"] for row in rows}

    assert {"events", "nodes", "edges", "node_vectors"}.issubset(table_names)


def test_init_db_is_idempotent(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    init_db(conn)  # darf nicht fehlschlagen

    conn.execute("INSERT INTO events (event_type, content_hash, payload, source) VALUES (?,?,?,?)",
                 ("note.created", "abc123", "{}", "cli"))
    conn.commit()
    row = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
    assert row["n"] == 1


def test_events_unique_hash_and_type(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    conn.execute("INSERT INTO events (event_type, content_hash, payload, source) VALUES (?,?,?,?)",
                 ("note.created", "abc123", "{}", "cli"))
    conn.commit()

    import sqlite3 as sqlite3_module
    try:
        conn.execute("INSERT INTO events (event_type, content_hash, payload, source) VALUES (?,?,?,?)",
                     ("note.created", "abc123", "{}", "cli"))
        conn.commit()
        assert False, "sollte IntegrityError werfen"
    except sqlite3_module.IntegrityError:
        pass


def test_events_created_at_iso8601_default(tmp_path):
    """Verify that created_at default produces ISO-8601 UTC format when not explicitly supplied."""
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)

    # Insert WITHOUT specifying created_at to use the SQL default
    conn.execute("INSERT INTO events (event_type, content_hash, payload, source) VALUES (?,?,?,?)",
                 ("note.created", "abc123", "{}", "cli"))
    conn.commit()

    # Retrieve the row and check the timestamp format
    row = conn.execute("SELECT created_at FROM events WHERE content_hash = 'abc123'").fetchone()
    created_at = row["created_at"]

    # ISO-8601 format with UTC suffix: "2026-07-05T22:15:00.123Z"
    assert "T" in created_at, f"created_at missing 'T' separator: {created_at}"
    assert created_at.endswith("Z"), f"created_at missing UTC 'Z' suffix: {created_at}"


def test_init_db_creates_concept_title_vectors_table(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)

    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {row["name"] for row in rows}

    assert "concept_title_vectors" in table_names


def test_connection_shared_across_threads(tmp_path):
    conn = get_connection(tmp_path / "threads.db", check_same_thread=False)
    init_db(conn)
    errors: list[Exception] = []

    def use_connection() -> None:
        try:
            conn.execute("SELECT count(*) FROM events").fetchone()
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=use_connection)
    thread.start()
    thread.join()
    assert errors == []


def test_migrations_apply_once_and_bump_user_version(tmp_path, monkeypatch):
    conn = sqlite_db.get_connection(tmp_path / "mig.db")
    sqlite_db.init_db(conn)
    # Startet bereits mit den realen Migrationen (z.B. hemisphere v1.2).
    baseline = conn.execute("PRAGMA user_version").fetchone()[0]
    assert baseline == len(sqlite_db.MIGRATIONS)

    monkeypatch.setattr(
        sqlite_db, "MIGRATIONS",
        sqlite_db.MIGRATIONS + ["ALTER TABLE nodes ADD COLUMN mig_test_col TEXT;"],
    )
    sqlite_db.init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == baseline + 1
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(nodes)")]
    assert "mig_test_col" in columns

    # Zweiter Lauf darf die Migration nicht erneut anwenden
    # (wuerde sonst mit "duplicate column name" crashen)
    sqlite_db.init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == baseline + 1
