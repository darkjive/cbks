import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.app_context import AppContext
from backend.models.edges import Edge
from backend.services.event_log import DuplicateEventError
from backend.services.hashing import content_hash
from backend.services.parsing import parse_frontmatter

_NOTE_SUFFIXES = {".md", ".markdown"}
_EXCLUDED_DIRS = {"attachments", "node_modules", "dist", "build"}
_OPENING_FRONTMATTER_RE = re.compile(r"\A---\s*\n")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def iter_vault_notes(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in _EXCLUDED_DIRS and not d.startswith(".")
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            if path.suffix.lower() in _NOTE_SUFFIXES:
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
    try:
        staged = stage_file(path, root, ctx)
    except DuplicateEventError as exc:
        if exc.existing_status == "processed":
            return
        raise
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
            try:
                staged.append(stage_file(path, root, ctx))
            except DuplicateEventError as exc:
                if exc.existing_status == "processed":
                    # full=True rescan von Inhalt, der byte-identisch zur
                    # zuletzt erfolgreich verarbeiteten Version ist: kein
                    # neues Event, daher normalerweise nichts zu tun. Ausnahme:
                    # der Node existiert, aber seine metadata["file_hash"]
                    # weicht vom aktuellen Inhalt ab (z.B. nach cbks rebuild,
                    # das Graph/FAISS leert, aber den Event-Log unangetastet
                    # laesst - siehe rebuild.py). In dem Fall file_hash und
                    # Wiki-Link-Kanten direkt nachziehen, statt auf ein neues
                    # Event zu warten, das wegen des Dedups nie kommt. Sobald
                    # file_hash wieder uebereinstimmt, greift dieser Zweig
                    # nicht mehr - kein Risiko doppelter Kanten bei
                    # wiederholtem `cbks index --full`.
                    if existing is not None and existing.metadata.get("file_hash") != file_hash:
                        body, _ = parse_frontmatter(raw)
                        ctx.graph.update_metadata_fields(node_id, {"file_hash": file_hash})
                        _sync_wikilinks(node_id, body, ctx)
                    summary.processed += 1
                else:
                    raise
        except Exception:
            summary.failed += 1

    if staged:
        process_summary = await ctx.dispatcher.process_pending()
        summary.processed += process_summary.processed
        summary.failed += process_summary.failed
        _finalize(staged, ctx)

    for node in ctx.graph.get_all_nodes():
        source_path = node.metadata.get("source_path")
        if source_path is not None and source_path not in known_paths:
            ctx.event_log.delete_by_vault_node_id(node.id)
            faiss_id = ctx.graph.delete_node(node.id)
            if faiss_id is not None:
                ctx.faiss_index.remove(faiss_id)
                ctx.event_log.delete(faiss_id)
            summary.deleted += 1

    ctx.faiss_index.save()
    return summary
