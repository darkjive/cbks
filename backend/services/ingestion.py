from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.services.event_log import DuplicateEventError, EventLog
from backend.services.parsing import parse_file
from backend.services.vision import VLMClient


@dataclass
class IngestResult:
    event_id: int
    duplicate: bool
    duplicate_since: Optional[str] = None


def ingest_file(
    path: Path,
    event_log: EventLog,
    source: str = "cli",
    vlm_client: Optional[VLMClient] = None,
) -> IngestResult:
    text = parse_file(path, vlm_client=vlm_client)
    payload = {"title": path.name, "text": text, "source_path": str(path)}
    try:
        event_id = event_log.append("document.added", payload, source)
        return IngestResult(event_id=event_id, duplicate=False)
    except DuplicateEventError as exc:
        return IngestResult(event_id=-1, duplicate=True, duplicate_since=exc.existing_created_at)


def ingest_note(text: str, event_log: EventLog, source: str = "cli") -> IngestResult:
    title = text[:60]
    payload = {"title": title, "text": text, "source_path": None}
    try:
        event_id = event_log.append("note.created", payload, source)
        return IngestResult(event_id=event_id, duplicate=False)
    except DuplicateEventError as exc:
        return IngestResult(event_id=-1, duplicate=True, duplicate_since=exc.existing_created_at)
