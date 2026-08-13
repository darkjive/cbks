import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    payload      TEXT NOT NULL,
    source       TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    error        TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    processed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_hash ON events(content_hash, event_type);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);

CREATE TABLE IF NOT EXISTS nodes (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    type             TEXT NOT NULL,
    hemisphere       TEXT NOT NULL DEFAULT 'auto',
    content          TEXT,
    content_hash     TEXT,
    activation       REAL NOT NULL DEFAULT 1.0,
    confidence       REAL NOT NULL DEFAULT 1.0,
    emotional_weight REAL NOT NULL DEFAULT 0.0,
    decay_rate       REAL NOT NULL DEFAULT 0.001,
    importance       REAL NOT NULL DEFAULT 0.5,
    creation_time    TEXT NOT NULL,
    last_access      TEXT NOT NULL,
    access_counter   INTEGER NOT NULL DEFAULT 0,
    metadata         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_nodes_title ON nodes(title COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS edges (
    id                  TEXT PRIMARY KEY,
    source              TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target              TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    relation_type       TEXT NOT NULL,
    strength            REAL NOT NULL DEFAULT 1.0,
    temporal_score      REAL NOT NULL DEFAULT 1.0,
    emotional_score     REAL NOT NULL DEFAULT 0.0,
    reinforcement_count INTEGER NOT NULL DEFAULT 1,
    creation_time       TEXT NOT NULL,
    last_updated        TEXT NOT NULL,
    metadata            TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS node_vectors (
    node_id  TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
    faiss_id INTEGER UNIQUE NOT NULL,
    model    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS concept_title_vectors (
    node_id TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
    vector  BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS vault_jobs (
    id               TEXT PRIMARY KEY,
    total            INTEGER NOT NULL DEFAULT 0,
    scanned          INTEGER NOT NULL DEFAULT 0,
    processed        INTEGER NOT NULL DEFAULT 0,
    duplicates       INTEGER NOT NULL DEFAULT 0,
    failed           INTEGER NOT NULL DEFAULT 0,
    processing_total INTEGER NOT NULL DEFAULT 0,
    processing_done  INTEGER NOT NULL DEFAULT 0,
    done             INTEGER NOT NULL DEFAULT 0,
    error            TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
"""

# Geordnete Migrationen fuer Bestandsdatenbanken. SCHEMA beschreibt immer den
# aktuellen Endzustand (fuer frische DBs); hier landet nur, was
# CREATE ... IF NOT EXISTS nicht abdeckt (ALTER TABLE, Datenmigrationen).
# Index in der Liste + 1 == Ziel-user_version. Eintraege niemals umsortieren
# oder loeschen, nur anhaengen.
MIGRATIONS: list[str] = [
    # v1.2: explizite Gehirnhaelften-Zuweisung am Node. Default 'auto' =
    # typbasiertes Anchor-Mapping im Frontend bleibt erhalten.
    "ALTER TABLE nodes ADD COLUMN hemisphere TEXT NOT NULL DEFAULT 'auto';",
]


def get_connection(db_path: Path, check_same_thread: bool = True) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL: erlaubt gleichzeitige Leser neben einem Schreiber (CLI-Ingest parallel
    # zur API). busy_timeout=30 s: bei konkurrierenden Schreibern warten statt
    # sofort "database is locked" zu werfen. foreign_keys pflicht fuer CASCADE.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    is_fresh = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
    ).fetchone()[0] == 0
    conn.executescript(SCHEMA)
    if is_fresh:
        # Frische DB: SCHEMA beschreibt bereits den vollstaendigen Endzustand,
        # alle bisherigen Migrationen sind darin abgebildet. user_version direkt
        # auf den Endstand stempeln, statt ALTER-TABLE-Migrationen erneut gegen
        # bereits vorhandene Spalten laufen zu lassen (crasht sonst mit
        # "duplicate column name").
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")
        conn.commit()
        return
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, migration in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(migration)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
