# Architektur-Modernisierung Backend — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Backend von "Kontext pro Request" auf einen einmal beim App-Start gebauten Singleton-Kontext (FastAPI-Lifespan + DI) umstellen, Endpoints konsequent async machen, Vault-Scan-Jobs in SQLite persistieren, ein Migrations-Fundament legen, strukturiertes JSON-Logging einführen und das Docker-Setup portabel machen.

**Architecture:** `build_context()` bleibt für die CLI erhalten, bekommt aber einen `check_same_thread`-Parameter. Die API baut den Kontext einmal im Lifespan-Handler und injiziert ihn per `Depends(get_context)`. Alle `asyncio.run(...)`-Aufrufe wandern an die Prozess-Eintrittspunkte (CLI, Tests); die Service-Schicht (`rag`, `rebuild`) wird async. Blocking-I/O in async Endpoints läuft über `asyncio.to_thread`. Vault-Jobs bekommen eine eigene SQLite-Tabelle statt eines In-Memory-Dicts.

**Tech Stack:** FastAPI 0.139 (Lifespan-API), sqlite3 (stdlib, serialized mode), python-json-logger 4.1, Docker Compose.

## Global Constraints

- Tests laufen aus dem Repo-Root: `.venv/bin/python -m pytest backend/tests/<datei> -v`
- Kein Git-Worktree — direkt auf `main` arbeiten (Nutzer-Präferenz).
- **Voraussetzung vor Task 1:** Der Arbeitsbaum enthält uncommittete Vault-Import-Änderungen (siehe `git status`). Diese zuerst als eigenen Commit sichern (`git add -A && git commit -m "feat: vault-import fertigstellen"`), damit die Plan-Commits sauber getrennt sind.
- Kommentare im Code auf Deutsch, Stil der Umgebung beibehalten (z. B. ASCII-Umschreibungen wie "fuer" kommen in Bestandskommentaren vor — beim Ändern bestehender Kommentare deren Stil übernehmen).
- API-Antwortformate (Response-Models in `backend/api_models.py`) dürfen sich NICHT ändern — das Frontend (`frontend/src/api/types.ts`) verlässt sich darauf.
- `backend/tests/test_e2e_api_milestone.py` und `test_e2e_milestone.py` brauchen laufendes Ollama; sie skippen sonst automatisch. Nicht als Fehler werten.
- Multi-Prozess-Hinweis (bewusster Trade-off, in Task 2 als Kommentar dokumentieren): Nach der Singleton-Umstellung hält die API den FAISS-Index im Speicher. CLI-Ingest (`cbks add` / `cbks note`) parallel zur laufenden API führt zu divergierenden Index-Ständen. Single-Writer-Annahme: CLI-Schreibbefehle nur bei gestoppter API benutzen, oder danach `POST /rebuild` aufrufen.

---

### Task 1: SQLite-Verbindung thread-fähig machen

Der Singleton-Kontext wird im Lifespan (Event-Loop-Thread) gebaut, aber `asyncio.to_thread(...)`-Aufrufe greifen aus Worker-Threads auf die Verbindung zu. `sqlite3.connect()` bindet die Verbindung standardmäßig an den erzeugenden Thread (`check_same_thread=True`) — das würde crashen. Python 3.12 kompiliert SQLite im serialized mode (`sqlite3.threadsafety == 3`), das Teilen der Verbindung über Threads ist damit sicher.

**Files:**
- Modify: `backend/storage/sqlite_db.py` (Funktion `get_connection`, ca. Zeile 64)
- Modify: `backend/app_context.py` (Funktion `build_context`, Zeile 52)
- Test: `backend/tests/test_sqlite_db.py`

**Interfaces:**
- Produces: `get_connection(db_path: Path, check_same_thread: bool = True) -> sqlite3.Connection` und `build_context(check_same_thread: bool = True) -> AppContext`. Task 2 ruft `build_context(check_same_thread=False)` auf.

- [ ] **Step 1: Failing Test schreiben**

In `backend/tests/test_sqlite_db.py` ergänzen:

```python
import threading


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
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_sqlite_db.py::test_connection_shared_across_threads -v`
Expected: FAIL mit `TypeError: get_connection() got an unexpected keyword argument 'check_same_thread'`

- [ ] **Step 3: Implementieren**

In `backend/storage/sqlite_db.py`:

```python
def get_connection(db_path: Path, check_same_thread: bool = True) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False ist sicher: Python 3.12 liefert sqlite3.threadsafety == 3
    # (serialized mode), SQLite serialisiert Zugriffe auf dieselbe Verbindung selbst.
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
```

In `backend/app_context.py` die Signatur von `build_context` erweitern und durchreichen:

```python
def build_context(check_same_thread: bool = True) -> AppContext:
    config = Config.from_env()
    conn = get_connection(config.database_path, check_same_thread=check_same_thread)
    ...  # Rest unverändert
```

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/python -m pytest backend/tests/test_sqlite_db.py backend/tests/test_app_context.py -v`
Expected: PASS (alle)

- [ ] **Step 5: Commit**

```bash
git add backend/storage/sqlite_db.py backend/app_context.py backend/tests/test_sqlite_db.py
git commit -m "feat: SQLite-Verbindung optional thread-uebergreifend nutzbar"
```

---

### Task 2: Lifespan-Kontext + Dependency Injection in der API

Kernstück des Plans: `build_context()` läuft aktuell bei jedem Request (21 Aufrufstellen in `main.py`) und lädt dabei jedes Mal den FAISS-Index von Disk (`FaissIndex.__init__` → `faiss.read_index`). Stattdessen: einmal im Lifespan bauen, per `Depends` injizieren.

**Files:**
- Modify: `backend/main.py` (alle Endpoints)
- Modify: `backend/tests/test_api.py`
- Modify: `backend/tests/test_e2e_api_milestone.py`

**Interfaces:**
- Consumes: `build_context(check_same_thread=False)` aus Task 1.
- Produces: `get_context(request: Request) -> AppContext` in `backend/main.py`; der Kontext liegt in `app.state.ctx`. Task 4 und Task 6 hängen davon ab, dass jeder Endpoint `ctx: AppContext = Depends(get_context)` als Parameter hat.

- [ ] **Step 1: Failing Test schreiben**

In `backend/tests/test_api.py` ergänzen (nach den bestehenden Fixtures):

```python
def test_context_is_singleton_across_requests(client):
    from backend.main import app as main_app
    ctx_before = main_app.state.ctx
    client.get("/stats")
    client.get("/stats")
    assert main_app.state.ctx is ctx_before
```

Gleichzeitig die Client-Fixture einführen, die der Test braucht — in `test_api.py` das Modul-Level `client = TestClient(app)` (Zeile 16) **löschen** und ersetzen durch:

```python
@pytest.fixture()
def client(isolated_data_dir):
    # Kontextmanager noetig: erst damit feuert der Lifespan-Handler,
    # der app.state.ctx mit dem frisch gesetzten CBKS_DATA_DIR baut.
    with TestClient(app) as c:
        yield c
```

Alle Testfunktionen in `test_api.py`, die bisher das globale `client` nutzen, bekommen den Parameter `client` (rein mechanisch, Funktionskörper bleiben unverändert), z. B.:

```python
def test_stats_on_empty_db(client):
    response = client.get("/stats")
    ...
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_api.py::test_context_is_singleton_across_requests -v`
Expected: FAIL mit `AttributeError: 'State' object has no attribute 'ctx'`

- [ ] **Step 3: Lifespan + DI in main.py implementieren**

In `backend/main.py`:

```python
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status

from backend.app_context import AppContext, build_context


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ein Kontext fuer die Prozess-Lebensdauer: SQLite-Verbindung, FAISS-Index
    # (faiss.read_index ist teuer) und Ollama-Clients werden genau einmal gebaut.
    # Trade-off: CLI-Ingest parallel zur laufenden API fuehrt zu divergierenden
    # Index-Staenden (Single-Writer-Annahme, siehe Plan-Dokument).
    ctx = build_context(check_same_thread=False)
    app.state.ctx = ctx
    yield
    ctx.conn.close()


app = FastAPI(title="CBKS API", dependencies=[Depends(require_api_key)], lifespan=lifespan)


def get_context(request: Request) -> AppContext:
    return request.app.state.ctx
```

Dann in **jedem** Endpoint die Zeile `ctx = build_context()` löschen und stattdessen den Parameter `ctx: AppContext = Depends(get_context)` anhängen. Beispiel:

```python
@app.post("/notes", response_model=IngestResponse)
def create_note(body: NoteRequest, ctx: AppContext = Depends(get_context)) -> IngestResponse:
    result = ingest_note(body.text, ctx.event_log, source="api")
    ...
```

Bei Endpoints ohne Body/Query-Parameter ist `ctx` der einzige Parameter:

```python
@app.get("/stats", response_model=StatsResponse)
def stats(ctx: AppContext = Depends(get_context)) -> StatsResponse:
    return StatsResponse(events=ctx.event_log.counts(), graph=ctx.graph.counts())
```

Betroffen sind alle 20 Endpoints, die bisher `ctx = build_context()` aufrufen (`get_vault_scan` ist der einzige ohne — der bekommt seinen `ctx`-Parameter erst in Task 6). In `start_vault_scan` ersetzt der Parameter ebenfalls das `ctx = build_context()`.

- [ ] **Step 4: test_e2e_api_milestone.py auf Lifespan-Client umstellen**

Modul-Level `client = TestClient(app)` löschen; stattdessen Fixture:

```python
@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CBKS_API_KEY", raising=False)
    with TestClient(app) as c:
        yield c
```

Die Testfunktionen bekommen den Parameter `client`; ihre eigenen `monkeypatch.setenv("CBKS_DATA_DIR", ...)`/`delenv`-Zeilen entfallen (macht jetzt die Fixture). Wo ein Test `tmp_path` weiterhin direkt braucht (z. B. zum Schreiben einer PDF), bleibt `tmp_path` als Parameter stehen — pytest liefert dieselbe Instanz wie in der Fixture.

`backend/tests/test_auth.py` braucht KEINE Änderung (baut eine eigene Mini-App ohne Lifespan).

- [ ] **Step 5: Tests laufen lassen**

Run: `.venv/bin/python -m pytest backend/tests/test_api.py -v`
Expected: PASS (alle, inkl. `test_context_is_singleton_across_requests`)

Run: `.venv/bin/python -m pytest backend/tests -x -q`
Expected: PASS (E2E-Tests skippen ggf. ohne Ollama)

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_api.py backend/tests/test_e2e_api_milestone.py
git commit -m "feat: App-Kontext einmal im Lifespan bauen statt pro Request"
```

---

### Task 3: Service-Schicht async machen (rag, rebuild)

`rag.search` (Zeile 57), `rag.ask` (Zeile 180) und `rebuild.rebuild` (Zeile 18) rufen intern `asyncio.run(...)` auf. Das verhindert, dass die API-Endpoints in Task 4 `async def` werden können (nested `asyncio.run` im laufenden Loop wirft `RuntimeError`). Lösung: Diese Funktionen werden `async def`, alle `asyncio.run(X)` darin werden `await X`. Die Aufrufer (CLI, main.py, Tests) wrappen vorerst mit `asyncio.run` — main.py wird in Task 4 auf `await` umgestellt.

**Files:**
- Modify: `backend/services/rag.py:50,57,165,180`
- Modify: `backend/services/rebuild.py:9,18`
- Modify: `backend/main.py` (Endpoints `ask`, `search`, `rebuild`)
- Modify: `backend/cli.py` (Commands `ask`, `search`, `rebuild`)
- Modify: `backend/tests/test_rag.py:70,83,112,133,153`
- Modify: `backend/tests/test_rebuild.py:49,62`

**Interfaces:**
- Produces: `async def search(query, temporal_agent, faiss_index, graph, limit=10)`, `async def ask(question, temporal_agent, faiss_index, graph, prefrontal_agent, history=None)` in `backend/services/rag.py`; `async def rebuild(event_log, graph, faiss_index, dispatcher)` in `backend/services/rebuild.py`. Task 4 awaited diese direkt.

- [ ] **Step 1: Tests anpassen (werden zu failing tests)**

In `backend/tests/test_rag.py` jede direkte Aufrufstelle wrappen (`import asyncio` oben ergänzen):

```python
hits = asyncio.run(search("FAISS", temporal_agent, faiss_index, graph, limit=10))
result = asyncio.run(ask("Was steht im Dokument?", temporal_agent, faiss_index, graph, prefrontal_agent))
```

(Zeilen 70, 83, 112, 133, 153 — bei den `ask`-Aufrufen mit mehrzeiligen Argumenten das `asyncio.run(...)` um den gesamten Aufruf legen.)

In `backend/tests/test_rebuild.py` analog (Zeilen 49, 62):

```python
summary = asyncio.run(rebuild(event_log, graph, faiss_index, dispatcher))
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_rag.py backend/tests/test_rebuild.py -v`
Expected: FAIL mit `ValueError: a coroutine was expected` (asyncio.run um eine Nicht-Coroutine)

- [ ] **Step 3: Services async machen**

`backend/services/rag.py`:
- `def search(...)` → `async def search(...)`; Zeile 57: `vector = asyncio.run(temporal_agent.embed(query))` → `vector = await temporal_agent.embed(query)`
- `def ask(...)` → `async def ask(...)`; Zeile 180: `answer = asyncio.run(prefrontal_agent.answer_question(...))` → `answer = await prefrontal_agent.answer_question(...)`
- Falls `ask` intern `search` aufruft: `await` ergänzen.
- Wenn danach kein `asyncio.run` mehr im Modul vorkommt und `asyncio` sonst ungenutzt ist: `import asyncio` entfernen.

`backend/services/rebuild.py`:
- `def rebuild(...)` → `async def rebuild(...)`; Zeile 18: `return asyncio.run(dispatcher.process_events(events))` → `return await dispatcher.process_events(events)`; ungenutzten `import asyncio` entfernen.

- [ ] **Step 4: Aufrufer in main.py und cli.py anpassen**

`backend/main.py` (Endpoints sind hier noch sync — Übergangszustand bis Task 4):

```python
result = asyncio.run(rag_service.ask(
    body.question, ctx.temporal_agent, ctx.faiss_index, ctx.graph,
    ctx.prefrontal_agent, history=history or None,
))
```

```python
hits = asyncio.run(rag_service.search(q, ctx.temporal_agent, ctx.faiss_index, ctx.graph, limit=limit))
```

```python
summary = asyncio.run(rebuild_service.rebuild(ctx.event_log, ctx.graph, ctx.faiss_index, ctx.dispatcher))
```

`backend/cli.py` (Commands `ask` Zeile 43, `search` Zeile 53, `rebuild` Zeile 90): dieselben Aufrufe mit `asyncio.run(...)` wrappen.

- [ ] **Step 5: Tests laufen lassen**

Run: `.venv/bin/python -m pytest backend/tests/test_rag.py backend/tests/test_rebuild.py backend/tests/test_api.py backend/tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/services/rag.py backend/services/rebuild.py backend/main.py backend/cli.py backend/tests/test_rag.py backend/tests/test_rebuild.py
git commit -m "refactor: rag und rebuild async, asyncio.run nur noch an Eintrittspunkten"
```

---

### Task 4: API-Endpoints auf async def umstellen

Sync-`def`-Endpoints belegen pro Request einen Threadpool-Worker und nutzen `asyncio.run`. Jetzt, wo die Services async sind: Endpoints werden `async def`, awaiten direkt, und die verbleibenden blocking Aufrufe (PDF-Parsing, TTS, Backup-Skript) laufen über `asyncio.to_thread`, damit sie den Event-Loop nicht blockieren.

**Files:**
- Modify: `backend/main.py` (alle Endpoints)

**Interfaces:**
- Consumes: async `rag_service.ask/search`, async `rebuild_service.rebuild` (Task 3); `ctx: AppContext = Depends(get_context)` (Task 2).
- Produces: keine neuen Schnittstellen; Verhalten und Response-Modelle unverändert.

- [ ] **Step 1: Endpoints umstellen**

Jeden Endpoint zu `async def` machen und `asyncio.run(X)` durch `await X` ersetzen. Blocking Aufrufe in `asyncio.to_thread` wrappen. Vollständige Liste der nicht-trivialen Fälle:

```python
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
```

```python
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
```

```python
@app.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, ctx: AppContext = Depends(get_context)) -> AskResponse:
    history = [(turn.role, turn.content) for turn in body.history]
    result = await rag_service.ask(
        body.question, ctx.temporal_agent, ctx.faiss_index, ctx.graph,
        ctx.prefrontal_agent, history=history or None,
    )
    return AskResponse(answer=result.answer, sources=result.sources)
```

```python
@app.get("/search", response_model=list[SearchHitResponse])
async def search(q: str, limit: int = 10, ctx: AppContext = Depends(get_context)) -> list[SearchHitResponse]:
    hits = await rag_service.search(q, ctx.temporal_agent, ctx.faiss_index, ctx.graph, limit=limit)
    return [SearchHitResponse(node=hit.node, score=hit.score) for hit in hits]
```

```python
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
    wav_path = await asyncio.to_thread(tts_service.synthesize, text, ctx.config.data_dir)
    return FileResponse(wav_path, media_type="audio/wav")
```

```python
@app.post("/retry", response_model=ProcessSummaryResponse)
async def retry(ctx: AppContext = Depends(get_context)) -> ProcessSummaryResponse:
    summary = await ctx.dispatcher.process_events(ctx.event_log.failed())
    ctx.faiss_index.save()
    return ProcessSummaryResponse(processed=summary.processed, failed=summary.failed)
```

```python
@app.post("/rebuild", response_model=ProcessSummaryResponse)
async def rebuild(ctx: AppContext = Depends(get_context)) -> ProcessSummaryResponse:
    summary = await rebuild_service.rebuild(ctx.event_log, ctx.graph, ctx.faiss_index, ctx.dispatcher)
    ctx.faiss_index.save()
    return ProcessSummaryResponse(processed=summary.processed, failed=summary.failed)
```

```python
@app.post("/dedupe", response_model=DedupeResponse)
async def dedupe(ctx: AppContext = Depends(get_context)) -> DedupeResponse:
    summary = await ctx.entity_resolver.dedupe_all()
    return DedupeResponse(checked=summary.checked, merged=summary.merged)
```

```python
@app.post("/backup", response_model=BackupResponse)
async def backup(ctx: AppContext = Depends(get_context)) -> BackupResponse:
    await asyncio.to_thread(subprocess.run, [str(ctx.config.backup_script_path)], check=True)
    return BackupResponse(status="ok")
```

```python
@app.post("/analyze/contradictions", response_model=ContradictionResponse)
async def analyze_contradictions(ctx: AppContext = Depends(get_context)) -> ContradictionResponse:
    summary = await pineal.find_contradictions(ctx.graph, ctx.llm_client)
    return ContradictionResponse(checked=summary.checked, found=summary.found)
```

Die reinen Lese-Endpoints (`get_node`, `get_graph`, `stats`, `list_events`, `analysis_*`, `get_vault_default_path`, `get_vault_scan`) werden ebenfalls `async def`, ihr Körper bleibt unverändert (SQLite-Reads sind schnell genug für den Loop).

- [ ] **Step 2: Tests laufen lassen**

Run: `.venv/bin/python -m pytest backend/tests/test_api.py backend/tests/test_vault_import.py -v`
Expected: PASS

Run: `.venv/bin/python -m pytest backend/tests -x -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/main.py
git commit -m "refactor: API-Endpoints async, blocking I/O via to_thread"
```

---

### Task 5: Schema-Migrationen über PRAGMA user_version

`init_db` kann heute nur `CREATE TABLE IF NOT EXISTS` — die erste Spaltenänderung an Bestandsdaten (`data/cbks.db`) hätte keinen Mechanismus. Minimal-Lösung ohne Alembic: eine geordnete `MIGRATIONS`-Liste, `PRAGMA user_version` als Zähler. `SCHEMA` bleibt immer das vollständige Bild für frische DBs; `MIGRATIONS` enthält nur Änderungen, die `IF NOT EXISTS` nicht abdecken kann (ALTER TABLE, Datenmigrationen).

**Files:**
- Modify: `backend/storage/sqlite_db.py` (nach `SCHEMA`, Funktion `init_db`)
- Test: `backend/tests/test_sqlite_db.py`

**Interfaces:**
- Produces: Modul-Konstante `MIGRATIONS: list[str]` in `backend/storage/sqlite_db.py`; `init_db(conn)` wendet ausstehende Migrationen an und setzt `PRAGMA user_version`. Signatur von `init_db` unverändert.

- [ ] **Step 1: Failing Test schreiben**

In `backend/tests/test_sqlite_db.py` ergänzen:

```python
from backend.storage import sqlite_db


def test_migrations_apply_once_and_bump_user_version(tmp_path, monkeypatch):
    conn = sqlite_db.get_connection(tmp_path / "mig.db")
    sqlite_db.init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0

    monkeypatch.setattr(
        sqlite_db, "MIGRATIONS", ["ALTER TABLE nodes ADD COLUMN mig_test_col TEXT;"]
    )
    sqlite_db.init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(nodes)")]
    assert "mig_test_col" in columns

    # Zweiter Lauf darf die Migration nicht erneut anwenden
    # (wuerde sonst mit "duplicate column name" crashen)
    sqlite_db.init_db(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_sqlite_db.py::test_migrations_apply_once_and_bump_user_version -v`
Expected: FAIL mit `AttributeError: ... has no attribute 'MIGRATIONS'`

- [ ] **Step 3: Implementieren**

In `backend/storage/sqlite_db.py` nach dem `SCHEMA`-String:

```python
# Geordnete Migrationen fuer Bestandsdatenbanken. SCHEMA beschreibt immer den
# aktuellen Endzustand (fuer frische DBs); hier landet nur, was
# CREATE ... IF NOT EXISTS nicht abdeckt (ALTER TABLE, Datenmigrationen).
# Index in der Liste + 1 == Ziel-user_version. Eintraege niemals umsortieren
# oder loeschen, nur anhaengen.
MIGRATIONS: list[str] = []
```

`init_db` erweitern:

```python
def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, migration in enumerate(MIGRATIONS[current:], start=current + 1):
        conn.executescript(migration)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
```

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/python -m pytest backend/tests/test_sqlite_db.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/storage/sqlite_db.py backend/tests/test_sqlite_db.py
git commit -m "feat: Schema-Migrationen ueber PRAGMA user_version"
```

---

### Task 6: Vault-Scan-Jobs in SQLite persistieren

`_vault_jobs: dict` in `main.py:48` lebt nur im Prozessspeicher: nach Neustart 404 für laufende Jobs, kein Cleanup, wächst unbegrenzt. Stattdessen: Tabelle `vault_jobs`, Zustand wird während des Scans fortgeschrieben, beim App-Start werden verwaiste Jobs als abgebrochen markiert. Das API-Antwortformat (`VaultScanResponse`) bleibt identisch — das Frontend-Polling funktioniert unverändert.

**Files:**
- Modify: `backend/storage/sqlite_db.py` (Tabelle in `SCHEMA` ergänzen)
- Modify: `backend/services/vault_import.py`
- Modify: `backend/main.py` (Lifespan, `start_vault_scan`, `get_vault_scan`, `_vault_jobs` entfernen)
- Test: `backend/tests/test_vault_import.py`

**Interfaces:**
- Consumes: `init_db`/`SCHEMA` aus Task 5, Lifespan aus Task 2.
- Produces in `backend/services/vault_import.py`:
  - `create_job(conn: sqlite3.Connection, job_id: str) -> None`
  - `save_state(conn: sqlite3.Connection, job_id: str, state: VaultScanState) -> None`
  - `load_state(conn: sqlite3.Connection, job_id: str) -> Optional[VaultScanState]`
  - `abort_unfinished_jobs(conn: sqlite3.Connection) -> None`
  - `scan_vault(root: Path, ctx: AppContext, state: VaultScanState, job_id: str) -> None` (neuer Parameter `job_id`)

- [ ] **Step 1: Failing Tests schreiben**

In `backend/tests/test_vault_import.py` ergänzen:

```python
from backend.services.vault_import import (
    VaultScanState, abort_unfinished_jobs, create_job, load_state, save_state,
)
from backend.storage.sqlite_db import get_connection, init_db


@pytest.fixture()
def conn(tmp_path):
    conn = get_connection(tmp_path / "jobs.db")
    init_db(conn)
    return conn


def test_job_state_roundtrip(conn):
    create_job(conn, "job1")
    state = VaultScanState(total=5, scanned=3, processed=2, duplicates=1, failed=0,
                           processing_total=4, processing_done=2, done=False, error=None)
    save_state(conn, "job1", state)

    loaded = load_state(conn, "job1")

    assert loaded == state


def test_load_unknown_job_returns_none(conn):
    assert load_state(conn, "gibtsnicht") is None


def test_abort_unfinished_jobs_marks_running_jobs(conn):
    create_job(conn, "laeuft")
    create_job(conn, "fertig")
    save_state(conn, "fertig", VaultScanState(done=True))

    abort_unfinished_jobs(conn)

    aborted = load_state(conn, "laeuft")
    assert aborted.done is True
    assert aborted.error == "Durch Server-Neustart abgebrochen"
    untouched = load_state(conn, "fertig")
    assert untouched.done is True
    assert untouched.error is None
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_vault_import.py -v`
Expected: FAIL mit `ImportError: cannot import name 'create_job'`

- [ ] **Step 3: Tabelle und Persistenz-Funktionen implementieren**

In `backend/storage/sqlite_db.py` innerhalb des `SCHEMA`-Strings ergänzen (vor dem schließenden `"""`):

```sql
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
```

(Kein Eintrag in `MIGRATIONS` nötig — `IF NOT EXISTS` deckt neue Tabellen für Bestands-DBs ab.)

In `backend/services/vault_import.py` (`import sqlite3` oben ergänzen):

```python
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
```

`scan_vault` bekommt den Parameter `job_id` und schreibt den Zustand fort:

```python
async def scan_vault(root: Path, ctx: AppContext, state: VaultScanState, job_id: str) -> None:
    try:
        files = iter_vault_files(root)
        state.total = len(files)
        for path in files:
            try:
                result = ingest_file(path, ctx.event_log, source="vault", vlm_client=ctx.vlm_client)
                if result.duplicate:
                    state.duplicates += 1
                else:
                    state.processed += 1
            except Exception:
                state.failed += 1
            finally:
                state.scanned += 1
                save_state(ctx.conn, job_id, state)
            # Event-Loop zwischen Dateien freigeben: ohne diesen Yield-Punkt blockiert
            # die rein synchrone Schleife den einzigen Event-Loop-Thread bis zum
            # Scan-Ende, wodurch Live-Polling (GET /vault/scan/{job_id}) waehrend des
            # Scans nie antworten wuerde.
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
```

- [ ] **Step 4: main.py umstellen**

`_vault_jobs`-Dict (Zeile 48) löschen; `_vault_scan_tasks`-Set bleibt (GC-Schutz). Import anpassen:

```python
from backend.services.vault_import import (
    VaultScanState, abort_unfinished_jobs, create_job, load_state, scan_vault,
)
```

Im Lifespan nach `app.state.ctx = ctx`:

```python
    abort_unfinished_jobs(ctx.conn)
```

Endpoints:

```python
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
```

- [ ] **Step 5: Tests laufen lassen**

Run: `.venv/bin/python -m pytest backend/tests/test_vault_import.py backend/tests/test_api.py -v`
Expected: PASS (bestehende Vault-Scan-API-Tests in test_api.py müssen unverändert grün sein — Antwortformat ist identisch)

- [ ] **Step 6: Commit**

```bash
git add backend/storage/sqlite_db.py backend/services/vault_import.py backend/main.py backend/tests/test_vault_import.py
git commit -m "feat: Vault-Scan-Jobs in SQLite persistieren statt In-Memory-Dict"
```

---

### Task 7: Strukturiertes JSON-Logging + Request-Middleware

`python-json-logger` ist installiert, wird aber nirgends benutzt; die API loggt keine Requests. Neu: zentrales `setup_logging()` (JSON auf stderr), aufgerufen im Lifespan und in der CLI, plus eine HTTP-Middleware, die Methode, Pfad, Status und Dauer loggt.

**Files:**
- Create: `backend/logging_setup.py`
- Modify: `backend/main.py` (Lifespan, Middleware)
- Modify: `backend/cli.py` (Setup-Aufruf)
- Test: `backend/tests/test_logging_setup.py` (neu)

**Interfaces:**
- Produces: `setup_logging(level: int = logging.INFO) -> None` in `backend/logging_setup.py`. Logger-Name der Middleware: `cbks.api`.

- [ ] **Step 1: Failing Tests schreiben**

Neue Datei `backend/tests/test_logging_setup.py`:

```python
import json
import logging

from fastapi.testclient import TestClient

from backend.logging_setup import setup_logging
from backend.main import app


def test_setup_logging_emits_parseable_json():
    setup_logging()
    handler = next(
        h for h in logging.getLogger().handlers if h.formatter.__class__.__name__ == "JsonFormatter"
    )
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hallo", None, None)

    parsed = json.loads(handler.format(record))

    assert parsed["message"] == "hallo"
    assert parsed["levelname"] == "INFO"


def test_setup_logging_is_idempotent():
    setup_logging()
    count_before = len(logging.getLogger().handlers)
    setup_logging()
    assert len(logging.getLogger().handlers) == count_before


def test_request_middleware_logs_method_path_status(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CBKS_API_KEY", raising=False)
    with TestClient(app) as client:
        with caplog.at_level(logging.INFO, logger="cbks.api"):
            client.get("/stats")

    record = next(r for r in caplog.records if r.name == "cbks.api")
    assert record.method == "GET"
    assert record.path == "/stats"
    assert record.status == 200
    assert record.duration_ms >= 0
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_logging_setup.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.logging_setup'`

- [ ] **Step 3: Implementieren**

Neue Datei `backend/logging_setup.py`:

```python
import logging

from pythonjsonlogger.json import JsonFormatter

_MARKER_ATTR = "_cbks_json_handler"


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    # Idempotent: mehrfacher Aufruf (Lifespan + CLI + Tests) darf keine
    # doppelten Handler anhaengen.
    if any(getattr(h, _MARKER_ATTR, False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    setattr(handler, _MARKER_ATTR, True)
    root.addHandler(handler)
    root.setLevel(level)
```

In `backend/main.py`: `import logging`, `import time` oben ergänzen, dazu:

```python
from backend.logging_setup import setup_logging

logger = logging.getLogger("cbks.api")
```

Im Lifespan als erste Zeile `setup_logging()` aufrufen. Nach der App-Definition die Middleware:

```python
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
```

In `backend/cli.py` nach `app = typer.Typer()`:

```python
from backend.logging_setup import setup_logging

setup_logging()
```

- [ ] **Step 4: Tests laufen lassen**

Run: `.venv/bin/python -m pytest backend/tests/test_logging_setup.py backend/tests/test_api.py backend/tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/logging_setup.py backend/main.py backend/cli.py backend/tests/test_logging_setup.py
git commit -m "feat: strukturiertes JSON-Logging mit Request-Middleware"
```

---

### Task 8: Docker-Setup portabel machen

`docker/compose.yml` hat den Host-Pfad `$REPO/data` hartkodiert und reicht keinen API-Key durch. `network_mode: host` bleibt bewusst bestehen: Das native Ollama lauscht nur auf `127.0.0.1:11434` und wäre über ein Bridge-Netzwerk (`host-gateway`) nicht erreichbar, ohne die Ollama-Konfiguration auf dem Host zu ändern. Portabilität kommt über `.env`-Variablen.

**Files:**
- Modify: `docker/compose.yml`
- Modify: `docker/.env.example`

**Interfaces:**
- Produces: Compose-Variablen `CBKS_DATA_DIR` (Host-Pfad zum Datenverzeichnis, Default `../data` relativ zur compose.yml) und `CBKS_API_KEY` (Default leer = Auth aus).

- [ ] **Step 1: compose.yml parametrisieren**

`docker/compose.yml` komplett ersetzen durch:

```yaml
services:
  cbks-backend:
    build:
      context: ..
      dockerfile: backend/Dockerfile
    container_name: cbks-backend
    # Host-Networking bleibt bewusst: natives Ollama lauscht nur auf 127.0.0.1:11434
    # und waere ueber ein Bridge-Netzwerk nicht erreichbar, ohne die
    # Ollama-Konfiguration auf dem Host zu aendern.
    network_mode: host
    volumes:
      - ${CBKS_DATA_DIR:-../data}:/data
    environment:
      - OLLAMA_HOST=${OLLAMA_HOST:-http://127.0.0.1:11434}
      - CBKS_DATA_DIR=/data
      - CBKS_DATABASE_PATH=/data/cbks.db
      - CBKS_FAISS_PATH=/data/faiss_index/index.faiss
      - CBKS_BACKUP_SCRIPT=/data/backup.sh
      - CBKS_API_KEY=${CBKS_API_KEY:-}
    restart: unless-stopped
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

- [ ] **Step 2: .env.example aktualisieren**

`docker/.env.example` komplett ersetzen durch:

```bash
# Kopie als docker/.env anlegen und Werte anpassen.
#
# Natives Ollama muss bereits laufen (systemctl --user status ollama)
# und auf 127.0.0.1:11434 erreichbar sein — dieses Compose-Setup startet
# keinen eigenen Ollama-Container.

# Host-Pfad zum Datenverzeichnis (SQLite, FAISS, Backups).
# Relativ zur compose.yml oder absolut.
CBKS_DATA_DIR=../data

# Ollama-Endpoint aus Container-Sicht (bei network_mode: host = Host-Localhost).
OLLAMA_HOST=http://127.0.0.1:11434

# API-Key fuer X-API-Key-Header. Leer = Auth deaktiviert.
CBKS_API_KEY=
```

- [ ] **Step 3: Verifizieren**

Run: `cd $REPO/docker && docker compose config`
Expected: Rendert ohne Fehler; im Output ist der Volume-Eintrag der aufgelöste absolute Pfad zu `data` (bzw. der Wert aus `docker/.env`, falls vorhanden), nicht mehr der hartkodierte `$REPO/data`-String aus der Datei. Falls Docker lokal nicht läuft: `docker compose -f compose.yml config --no-interpolate` zeigt zumindest gültiges YAML.

- [ ] **Step 4: Commit**

```bash
git add docker/compose.yml docker/.env.example
git commit -m "feat: Docker-Setup ueber .env parametrisieren"
```

---

## Abschlussverifikation (nach Task 8)

- [ ] Gesamte Testsuite: `.venv/bin/python -m pytest backend/tests -q` → PASS (E2E skippt ohne Ollama)
- [ ] App startet real: `.venv/bin/uvicorn backend.main:app --port 8000` → Startlog erscheint als JSON, `curl -s localhost:8000/stats` antwortet, im Log erscheint ein `cbks.api`-Request-Eintrag mit `duration_ms`
- [ ] Frontend-Smoke: `cd frontend && npm run dev`, Upload-Tab öffnen, Notiz anlegen — Antwortformate unverändert
