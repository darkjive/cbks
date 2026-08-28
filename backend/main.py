import asyncio
import hashlib
import logging
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse

from backend.api_models import (
    AskRequest,
    AskResponse,
    BackupResponse,
    ConceptStat,
    ContradictionResponse,
    DedupeResponse,
    DeleteResponse,
    EmotionBucket,
    EventResponse,
    GraphResponse,
    IngestResponse,
    NodeResponse,
    NoteRequest,
    PatternReport,
    ProcessSummaryResponse,
    RecurringTopic,
    SearchHitResponse,
    StatsResponse,
    TimelineBucket,
    VaultAttachmentResponse,
    VaultBacklinksResponse,
    VaultDefaultPathResponse,
    VaultFileResponse,
    VaultFileWriteRequest,
    VaultFileWriteResponse,
    VaultRenameRequest,
    VaultRescanResponse,
    VaultScanRequest,
    VaultScanResponse,
    VaultScanStartResponse,
    VaultSearchHitResponse,
    VaultTreeEntry,
)
from backend.app_context import AppContext, build_context
from backend.auth import require_api_key
from backend.logging_setup import setup_logging
from backend.services import analysis as analysis_service
from backend.services import rag as rag_service
from backend.services import rebuild as rebuild_service
from backend.services import tts as tts_service
from backend.services import vault_fs, vault_index
from backend.services.agents import pineal
from backend.services.ingestion import ingest_file, ingest_note
from backend.services.parsing import parse_frontmatter
from backend.services.vault_fs import VaultConflictError, VaultPathError
from backend.services.vault_import import (
    VaultScanState, abort_unfinished_jobs, create_job, load_state, scan_vault,
)
from uuid import uuid4

logger = logging.getLogger("cbks.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # Ein Kontext fuer die Prozess-Lebensdauer: SQLite-Verbindung, FAISS-Index
    # (faiss.read_index ist teuer) und Ollama-Clients werden genau einmal gebaut.
    # Trade-off: CLI-Ingest parallel zur laufenden API fuehrt zu divergierenden
    # Index-Staenden (Single-Writer-Annahme, siehe Plan-Dokument).
    ctx = build_context(check_same_thread=False)
    app.state.ctx = ctx
    if not ctx.config.api_key:
        logger.warning(
            "CBKS_API_KEY ist nicht gesetzt — die API laeuft ohne Authentifizierung. "
            "Nur unbedenklich, solange ausschliesslich auf 127.0.0.1 gebunden wird "
            "(siehe SECURITY.md); bei jeder Weiterleitung/Exposition nach aussen "
            "CBKS_API_KEY setzen."
        )
    abort_unfinished_jobs(ctx.conn)
    yield
    ctx.conn.close()


app = FastAPI(title="CBKS API", dependencies=[Depends(require_api_key)], lifespan=lifespan)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    logger.info(
        "request",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx


def _vault_root(ctx: AppContext) -> Path:
    if ctx.config.vault_dir is None:
        raise HTTPException(status_code=400, detail="CBKS_VAULT_DIR ist nicht konfiguriert")
    return ctx.config.vault_dir


def _to_tree_response(entry: vault_fs.TreeEntry) -> VaultTreeEntry:
    return VaultTreeEntry(
        name=entry.name, path=entry.path, is_dir=entry.is_dir,
        children=[_to_tree_response(c) for c in entry.children] if entry.children is not None else None,
    )


# asyncio haelt fuer create_task() nur eine schwache Referenz - ohne dieses Set
# koennte der Scan-Task vom GC eingesammelt werden, bevor er fertig ist.
_vault_scan_tasks: set[asyncio.Task] = set()


@app.post("/documents", response_model=IngestResponse)
async def create_document(
    file: UploadFile = File(...), ctx: AppContext = Depends(get_context)
) -> IngestResponse:
    content = await file.read()
    digest = hashlib.sha256(content).hexdigest()
    safe_name = Path(file.filename).name  # nur Basename, keine ../-Segmente
    tmp_dir = Path(tempfile.gettempdir()) / "cbks-uploads" / digest
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / safe_name
    tmp_path.write_bytes(content)
    try:
        # to_thread: PDF-/Bild-Parsing und VLM-Aufruf sind blocking
        result = await asyncio.to_thread(
            ingest_file, tmp_path, ctx.event_log, source="api", vlm_client=ctx.vlm_client
        )
    finally:
        tmp_path.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass  # von einem parallelen Request mit identischem Inhalt bereits entfernt
    if result.duplicate:
        return IngestResponse(
            event_id=result.event_id, duplicate=True, duplicate_since=result.duplicate_since
        )
    summary = await ctx.dispatcher.process_pending()
    ctx.faiss_index.save()
    return IngestResponse(
        event_id=result.event_id, duplicate=False,
        processed=summary.processed, failed=summary.failed,
    )


@app.post("/notes", response_model=IngestResponse)
async def create_note(body: NoteRequest, ctx: AppContext = Depends(get_context)) -> IngestResponse:
    result = ingest_note(body.text, ctx.event_log, source="api")
    if result.duplicate:
        return IngestResponse(
            event_id=result.event_id, duplicate=True, duplicate_since=result.duplicate_since
        )
    summary = await ctx.dispatcher.process_pending()
    ctx.faiss_index.save()
    return IngestResponse(
        event_id=result.event_id, duplicate=False,
        processed=summary.processed, failed=summary.failed,
    )


@app.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, ctx: AppContext = Depends(get_context)) -> AskResponse:
    history = [(turn.role, turn.content) for turn in body.history]
    try:
        result = await rag_service.ask(
            body.question,
            ctx.temporal_agent,
            ctx.faiss_index,
            ctx.graph,
            ctx.prefrontal_agent,
            history=history or None,
        )
    except Exception as exc:  # noqa: BLE001 - LLM-/Embedding-Fehler -> 4xx/5xx mit deutschem Text
        _raise_ask_error(exc)
        raise  # unerreichbar; nur damit der Typechecker result als gebunden ansieht
    return AskResponse(answer=result.answer, sources=result.sources)


def _raise_ask_error(exc: Exception) -> None:
    import httpx
    import ollama

    # ollama nutzt httpx: Read-Timeout waehrend der Generierung -> httpx.TimeoutException
    # (kein asyncio.TimeoutError), Ollama nicht erreichbar -> httpx.ConnectError
    # (kein builtin ConnectionError, Name enthaelt kein "Connection"). Beide muessen
    # explizit gegen die httpx-Typen geprueft werden, sonst greift nur der 502-Fallback.
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        raise HTTPException(status_code=504, detail="Antwort-Generierung hat das Zeitlimit überschritten")
    if isinstance(exc, ollama.ResponseError):
        raise HTTPException(
            status_code=502,
            detail=f"Ollama-Fehler ({exc.status_code}): {_humanize_ollama(str(exc.error))}",
        )
    if isinstance(exc, (ConnectionError, httpx.ConnectError)):
        raise HTTPException(status_code=502, detail="Ollama ist nicht erreichbar")
    raise HTTPException(status_code=502, detail=f"Chat-Fehler: {exc}")


def _humanize_ollama(msg: str) -> str:
    if "model" in msg.lower() and "not found" in msg.lower():
        return "Modell nicht gepullt – siehe CBKS_LLM_MODEL / 'ollama pull'"
    return msg


@app.get("/search", response_model=list[SearchHitResponse])
async def search(
    q: str = Query(..., min_length=1, description="Suchanfrage, darf nicht leer sein"),
    limit: int = Query(default=10, ge=1, le=100, description="Max. Anzahl Treffer"),
    ctx: AppContext = Depends(get_context),
) -> list[SearchHitResponse]:
    hits = await rag_service.search(q, ctx.temporal_agent, ctx.faiss_index, ctx.graph, limit=limit)
    return [SearchHitResponse(node=hit.node, score=hit.score) for hit in hits]


@app.get("/nodes/{node_id}", response_model=NodeResponse)
async def get_node(node_id: str, ctx: AppContext = Depends(get_context)) -> NodeResponse:
    node = ctx.graph.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node nicht gefunden")
    neighbors = ctx.graph.get_neighbors(node_id)
    return NodeResponse(node=node, neighbors=neighbors)


@app.delete("/nodes/{node_id}", response_model=DeleteResponse)
async def delete_node(node_id: str, ctx: AppContext = Depends(get_context)) -> DeleteResponse:
    node = ctx.graph.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node nicht gefunden")
    faiss_id = ctx.graph.delete_node(node_id)
    removed_event_id: Optional[int] = None
    if faiss_id is not None:
        # FAISS-Bereinigung best-effort: schlaegt sie fehl, bleibt ein harmloser
        # Orphan-Vektor (rag.search ueberspringt ihn). Das Origin-Event wird
        # aber immer geloescht, damit ein Rebuild den Node nicht wiederherstellt.
        try:
            ctx.faiss_index.remove(faiss_id)
            ctx.faiss_index.save()
        except Exception:
            logger.exception("FAISS-Cleanup fehlgeschlagen (faiss_id=%s)", faiss_id)
        ctx.event_log.delete(faiss_id)
        removed_event_id = faiss_id
    return DeleteResponse(deleted_node_id=node_id, removed_event_id=removed_event_id)


@app.get("/nodes/{node_id}/audio")
async def get_node_audio(node_id: str, ctx: AppContext = Depends(get_context)) -> FileResponse:
    node = ctx.graph.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node nicht gefunden")
    text = (node.content or "").strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Node hat keinen vorlesbaren Inhalt")
    # to_thread: Kokoro-Synthese blockiert sonst den Event-Loop
    speech_text = tts_service.build_speech_text(node)
    try:
        wav_path = await asyncio.to_thread(tts_service.synthesize, speech_text, ctx.config.data_dir)
    except tts_service.TTSUnavailableError as exc:
        # TTS ist eine optionale Abhaengigkeit — ohne sie ist der Rest der API nutzbar.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    return FileResponse(wav_path, media_type="audio/wav")


@app.get("/graph", response_model=GraphResponse)
async def get_graph(ctx: AppContext = Depends(get_context)) -> GraphResponse:
    return GraphResponse(nodes=ctx.graph.get_all_nodes(), edges=ctx.graph.get_all_edges())


@app.get("/stats", response_model=StatsResponse)
async def stats(ctx: AppContext = Depends(get_context)) -> StatsResponse:
    return StatsResponse(events=ctx.event_log.counts(), graph=ctx.graph.counts())


@app.get("/events", response_model=list[EventResponse])
async def list_events(
    status_filter: Optional[str] = None,
    limit: int = 100,
    ctx: AppContext = Depends(get_context),
) -> list[EventResponse]:
    events = ctx.event_log.recent(limit=limit, status=status_filter)
    return [EventResponse(**e.model_dump()) for e in events]


@app.post("/retry", response_model=ProcessSummaryResponse)
async def retry(ctx: AppContext = Depends(get_context)) -> ProcessSummaryResponse:
    summary = await ctx.dispatcher.process_events(ctx.event_log.failed())
    ctx.faiss_index.save()
    return ProcessSummaryResponse(processed=summary.processed, failed=summary.failed)


@app.post("/rebuild", response_model=ProcessSummaryResponse)
async def rebuild(ctx: AppContext = Depends(get_context)) -> ProcessSummaryResponse:
    summary = await rebuild_service.rebuild(ctx.event_log, ctx.graph, ctx.faiss_index, ctx.dispatcher)
    ctx.faiss_index.save()
    return ProcessSummaryResponse(processed=summary.processed, failed=summary.failed)


@app.post("/dedupe", response_model=DedupeResponse)
async def dedupe(ctx: AppContext = Depends(get_context)) -> DedupeResponse:
    summary = await ctx.entity_resolver.dedupe_all()
    return DedupeResponse(checked=summary.checked, merged=summary.merged)


@app.post("/backup", response_model=BackupResponse)
async def backup(ctx: AppContext = Depends(get_context)) -> BackupResponse:
    await asyncio.to_thread(subprocess.run, [str(ctx.config.backup_script_path)], check=True)
    return BackupResponse(status="ok")


@app.get("/analysis/timeline", response_model=list[TimelineBucket])
async def analysis_timeline(ctx: AppContext = Depends(get_context)) -> list[TimelineBucket]:
    return [TimelineBucket(**b) for b in analysis_service.timeline(ctx.graph.get_all_nodes())]


@app.get("/analysis/emotions", response_model=list[EmotionBucket])
async def analysis_emotions(ctx: AppContext = Depends(get_context)) -> list[EmotionBucket]:
    return [EmotionBucket(**b) for b in analysis_service.emotions(ctx.graph.get_all_nodes())]


@app.get("/analysis/patterns", response_model=PatternReport)
async def analysis_patterns(ctx: AppContext = Depends(get_context)) -> PatternReport:
    report = analysis_service.patterns(ctx.graph.get_all_nodes(), ctx.graph.get_all_edges())
    report["top_concepts"] = [ConceptStat(**c) for c in report["top_concepts"]]
    return PatternReport(**report)


@app.get("/analysis/recurring", response_model=list[RecurringTopic])
async def analysis_recurring(ctx: AppContext = Depends(get_context)) -> list[RecurringTopic]:
    return [RecurringTopic(**t) for t in analysis_service.recurring(ctx.graph.get_all_nodes(), ctx.graph.get_all_edges())]


@app.post("/analyze/contradictions", response_model=ContradictionResponse)
async def analyze_contradictions(ctx: AppContext = Depends(get_context)) -> ContradictionResponse:
    summary = await pineal.find_contradictions(ctx.graph, ctx.llm_client)
    return ContradictionResponse(checked=summary.checked, found=summary.found)


@app.get("/vault/default-path", response_model=VaultDefaultPathResponse)
async def get_vault_default_path(ctx: AppContext = Depends(get_context)) -> VaultDefaultPathResponse:
    return VaultDefaultPathResponse(path=ctx.config.vault_path or "")


@app.post("/vault/scan", response_model=VaultScanStartResponse)
async def start_vault_scan(
    body: VaultScanRequest, ctx: AppContext = Depends(get_context)
) -> VaultScanStartResponse:
    root = Path(body.path)
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="Pfad existiert nicht oder ist kein Verzeichnis")
    job_id = uuid4().hex
    create_job(ctx.conn, job_id)
    state = VaultScanState()
    task = asyncio.create_task(scan_vault(root, ctx, state, job_id))
    _vault_scan_tasks.add(task)
    task.add_done_callback(_vault_scan_tasks.discard)
    return VaultScanStartResponse(job_id=job_id)


@app.get("/vault/scan/{job_id}", response_model=VaultScanResponse)
async def get_vault_scan(job_id: str, ctx: AppContext = Depends(get_context)) -> VaultScanResponse:
    state = load_state(ctx.conn, job_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job nicht gefunden")
    return VaultScanResponse(
        total=state.total, scanned=state.scanned, processed=state.processed,
        duplicates=state.duplicates, failed=state.failed,
        processing_total=state.processing_total, processing_done=state.processing_done,
        done=state.done, error=state.error,
    )


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
        ctx.event_log.delete_by_vault_node_id(node_id)
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
