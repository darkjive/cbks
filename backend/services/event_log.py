import json
import sqlite3
from datetime import datetime, timezone
from typing import Iterator, Optional

from backend.models.events import Event
from backend.services.hashing import content_hash


class DuplicateEventError(Exception):
    def __init__(self, event_type: str, existing_created_at: str, existing_status: str = "unbekannt"):
        self.event_type = event_type
        self.existing_created_at = existing_created_at
        self.existing_status = existing_status
        super().__init__(
            f"Event bereits bekannt seit {existing_created_at} (event_type={event_type})"
        )


class EventLog:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def append(self, event_type: str, payload: dict, source: str) -> int:
        payload_json = json.dumps(payload, ensure_ascii=False)
        digest = content_hash(payload_json)

        # ON CONFLICT DO NOTHING schuetzt vor der TOCTOU-Race (SELECT-then-INSERT):
        # zwei konkurrierende append-Aufrufe mit gleichem Payload wuerden sonst
        # beide das SELECT passieren und der zweite INSERT schlaegt mit
        # IntegrityError fehl. Wir pruefen danach cursor.rowcount.
        cursor = self._conn.execute(
            "INSERT INTO events (event_type, content_hash, payload, source, status) "
            "VALUES (?, ?, ?, ?, 'pending') "
            "ON CONFLICT(content_hash, event_type) DO NOTHING",
            (event_type, digest, payload_json, source),
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            existing = self._conn.execute(
                "SELECT created_at, status FROM events WHERE content_hash = ? AND event_type = ?",
                (digest, event_type),
            ).fetchone()
            # Race: ein konkurrierender DELETE kann die Zeile zwischen dem
            # INSERT-NOOP und diesem SELECT entfernt haben. Dann unknown statt
            # TypeError ('NoneType' ist nicht subskriptierbar).
            created_at = existing["created_at"] if existing is not None else "unbekannt"
            status = existing["status"] if existing is not None else "unbekannt"
            raise DuplicateEventError(event_type, created_at, status)
        return cursor.lastrowid

    def get(self, event_id: int) -> Optional[Event]:
        row = self._conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return None if row is None else self._row_to_event(row)

    def pending(self) -> list[Event]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE status = 'pending' ORDER BY id"
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def failed(self) -> list[Event]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE status = 'failed' ORDER BY id"
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def mark_processed(self, event_id: int) -> None:
        self._conn.execute(
            "UPDATE events SET status = 'processed', processed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), event_id),
        )
        self._conn.commit()

    def mark_failed(self, event_id: int, error: str) -> None:
        self._conn.execute(
            "UPDATE events SET status = 'failed', error = ?, processed_at = ? WHERE id = ?",
            (error, datetime.now(timezone.utc).isoformat(), event_id),
        )
        self._conn.commit()

    def delete(self, event_id: int) -> None:
        # Hard-Delete statt Tombstone: fuer manuell geloeschte Nodes soll ein
        # Rebuild sie nicht wiederherstellen. Wer die Loesch-Historie braucht,
        # muss ein separates Audit-Log fuehren (YAGNI aktuell).
        self._conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        self._conn.commit()

    def delete_by_vault_node_id(self, node_id: str) -> None:
        # Ein bearbeiteter Vault-Node hat mehrere vault.file-Events (eins pro
        # Edit, da EventLog.append nach content_hash dedupt, nicht nach
        # node_id). event_log.delete(faiss_id) allein loescht nur das
        # neueste Event - aeltere Events desselben node_id ueberleben und
        # wuerden bei cbks rebuild den geloeschten Node mit veraltetem
        # Inhalt wiederherstellen.
        # node_id stammt aus dem Frontmatter der Vault-Datei (vault_index._ensure_id)
        # und ist damit nutzerkontrolliert - LIKE-Wildcards muessen escaped werden,
        # sonst wuerde etwa "id: %" saemtliche vault.file-Events loeschen.
        escaped = node_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        self._conn.execute(
            "DELETE FROM events WHERE event_type = 'vault.file' "
            "AND payload LIKE ? ESCAPE '\\'",
            (f'%"node_id": "{escaped}"%',),
        )
        self._conn.commit()

    def recent(self, limit: int = 100, status: str | None = None) -> list[Event]:
        if status is not None:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def replay_all(self) -> Iterator[Event]:
        rows = self._conn.execute("SELECT * FROM events ORDER BY id").fetchall()
        for row in rows:
            yield self._row_to_event(row)

    def counts(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM events GROUP BY status"
        ).fetchall()
        result = {"pending": 0, "processed": 0, "failed": 0}
        for row in rows:
            result[row["status"]] = row["n"]
        return result

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            id=row["id"],
            event_type=row["event_type"],
            content_hash=row["content_hash"],
            payload=row["payload"],
            source=row["source"],
            status=row["status"],
            error=row["error"],
            created_at=row["created_at"],
            processed_at=row["processed_at"],
        )
