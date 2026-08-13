import asyncio
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

from pathlib import Path

from backend.app_context import AppContext
from backend.services.ingestion import ingest_file

_SUPPORTED_SUFFIXES = {".md", ".markdown", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}
_EXCLUDED_DIRS = {".obsidian", ".trash", "node_modules", "dist", "build"}


def iter_vault_files(root: Path) -> list[Path]:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() in _SUPPORTED_SUFFIXES:
                files.append(path)
    return files


@dataclass
class VaultScanState:
    total: int = 0
    scanned: int = 0
    processed: int = 0
    duplicates: int = 0
    failed: int = 0
    processing_total: int = 0
    processing_done: int = 0
    done: bool = False
    error: Optional[str] = None


def create_job(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute("INSERT INTO vault_jobs (id) VALUES (?)", (job_id,))
    conn.commit()


def save_state(conn: sqlite3.Connection, job_id: str, state: VaultScanState) -> None:
    conn.execute(
        """UPDATE vault_jobs
           SET total = ?, scanned = ?, processed = ?, duplicates = ?, failed = ?,
               processing_total = ?, processing_done = ?, done = ?, error = ?
           WHERE id = ?""",
        (state.total, state.scanned, state.processed, state.duplicates, state.failed,
         state.processing_total, state.processing_done, int(state.done), state.error, job_id),
    )
    conn.commit()


def load_state(conn: sqlite3.Connection, job_id: str) -> Optional[VaultScanState]:
    row = conn.execute("SELECT * FROM vault_jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return VaultScanState(
        total=row["total"], scanned=row["scanned"], processed=row["processed"],
        duplicates=row["duplicates"], failed=row["failed"],
        processing_total=row["processing_total"], processing_done=row["processing_done"],
        done=bool(row["done"]), error=row["error"],
    )


def abort_unfinished_jobs(conn: sqlite3.Connection) -> None:
    # Nach einem Neustart kann kein Scan-Task mehr laufen - offene Jobs sind tot.
    conn.execute(
        "UPDATE vault_jobs SET done = 1, error = 'Durch Server-Neustart abgebrochen' WHERE done = 0"
    )
    conn.commit()


async def scan_vault(root: Path, ctx: AppContext, state: VaultScanState, job_id: str) -> None:
    try:
        files = iter_vault_files(root)
        state.total = len(files)
        for path in files:
            try:
                # to_thread: PDF-/Bild-Parsing und VLM-Aufruf sind blocking und
                # wuerden sonst den Event-Loop fuer die Dauer jeder Datei blockieren.
                result = await asyncio.to_thread(
                    ingest_file, path, ctx.event_log, source="vault", vlm_client=ctx.vlm_client
                )
                if result.duplicate:
                    state.duplicates += 1
                else:
                    state.processed += 1
            except Exception:
                state.failed += 1
            finally:
                state.scanned += 1
                save_state(ctx.conn, job_id, state)
            await asyncio.sleep(0)

        def _on_progress(done: int, pending_total: int) -> None:
            state.processing_done = done
            state.processing_total = pending_total
            save_state(ctx.conn, job_id, state)

        await ctx.dispatcher.process_pending(on_progress=_on_progress)
        ctx.faiss_index.save()
    except Exception as exc:
        state.error = str(exc)
    finally:
        state.done = True
        save_state(ctx.conn, job_id, state)
