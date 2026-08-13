# Obsidian-Vault-Import + modernes Import-UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notizen aus einem Obsidian-Vault lassen sich per Knopfdruck importieren (manueller Scan-Button, Live-Fortschritt per Polling), statt jede Notiz einzeln einzutippen. Zusätzlich wird `UploadForm` zu einem Tab-Container (Vault/Notiz/Datei) mit Drag&Drop-Dateiupload modernisiert.

**Architecture:** Neuer Backend-Service `backend/services/vault_import.py` durchsucht rekursiv ein Verzeichnis, filtert unterstützte Dateitypen und ruft pro Datei die bestehende `ingest_file()`-Pipeline auf (identische Hash-Duplikaterkennung wie bei Einzel-Upload). Der Scan läuft als asyncio-Background-Task; ein In-Memory-Dict hält den Fortschritt pro `job_id`, den das Frontend per 1s-Polling gegen `GET /vault/scan/{job_id}` abfragt. `UploadForm.tsx` wird zu drei Tabs (Vault im Vordergrund, Notiz, Datei mit Dropzone).

**Tech Stack:** Backend: FastAPI, asyncio, pytest (`.venv/bin/pytest`). Frontend: React 19, TypeScript, kein Test-Runner (Verifikation via `npm run build` + manueller Browser-Test).

## Global Constraints

- Referenz-Design: `docs/superpowers/specs/2026-07-10-vault-import-design.md`.
- Import-Modus: nur manueller Scan-Button, **kein** dauerhaftes Watching.
- Vault-Pfad wird **nur** aus env var `CBKS_VAULT_PATH` gelesen, **nicht** in der DB persistiert.
- Datei-Filter identisch zu `parse_file`: `.md`, `.markdown`, `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp`.
- Duplikat-Erkennung ausschließlich über die bestehende `ingest_file()`/`EventLog`-Hash-Logik — keine eigene Implementierung.
- **`POST /vault/scan` muss `async def` sein** (Abweichung vom Pseudocode in der Spec): alle bestehenden Endpoints in `backend/main.py` sind synchrone `def`-Handler, die FastAPI/Starlette in einem Worker-Thread ohne eigenen laufenden Event-Loop ausführt. `asyncio.create_task(...)` in einem solchen Thread scheitert mit `RuntimeError: no running event loop`. Nur als `async def` läuft der Handler direkt auf dem App-Event-Loop, wo `create_task` funktioniert.
- **`scan_vault()` muss nach jeder verarbeiteten Datei `await asyncio.sleep(0)` aufrufen** (Ergänzung zur Spec): ohne einen Yield-Punkt pro Datei blockiert die komplette, rein synchrone Verarbeitungsschleife den einzigen Event-Loop-Thread für die gesamte Scan-Dauer — das im Ziel geforderte Live-Polling würde bis zum Scan-Ende überhaupt keine Zwischenstände liefern. `await asyncio.sleep(0)` gibt die Kontrolle minimal-invasiv zwischen den Dateien zurück, ohne den in der Spec bewusst abgelehnten Threadpool-Offload einzuführen.
- Response-Modelle der drei neuen Endpoints werden als `pydantic.BaseModel` in `backend/api_models.py` definiert und über `response_model=` gebunden — Projektkonvention, jeder bestehende Endpoint macht das so (siehe `backend/main.py`).
- Kein git worktree — direkt auf `main` implementieren (Nutzerpräferenz für dieses Repo).
- Python-Umgebung für alle Backend-Befehle: `.venv/bin/python`, `.venv/bin/pytest`.
- Nicht in Scope: kontinuierliches Watching, Persistieren des Vault-Pfads in DB/Config-UI, Löschungs-/Umbenennungs-Tracking, Auswahl einzelner Dateien/Unterordner, Persistieren von Scan-Jobs über einen Backend-Neustart hinweg.

---

## Task 1: Backend — Vault-Konfiguration und Scan-Service

**Files:**
- Modify: `backend/config.py`
- Modify: `backend/tests/test_config.py`
- Create: `backend/services/vault_import.py`
- Create: `backend/tests/test_vault_import.py`

**Interfaces:**
- Produces: `Config.vault_path: Optional[str]` (aus `CBKS_VAULT_PATH`) — Task 2 liest `ctx.config.vault_path`.
- Produces: `iter_vault_files(root: Path) -> list[Path]` in `backend/services/vault_import.py`.
- Produces: `@dataclass VaultScanState` mit Feldern `total: int = 0`, `scanned: int = 0`, `processed: int = 0`, `duplicates: int = 0`, `failed: int = 0`, `done: bool = False`, `error: Optional[str] = None` — Task 2 liest diese Felder für die Response und legt Instanzen im `_vault_jobs`-Dict ab.
- Produces: `async def scan_vault(root: Path, ctx: AppContext, state: VaultScanState) -> None` — Task 2 ruft das über `asyncio.create_task(scan_vault(root, ctx, state))` auf.
- Consumes: `ingest_file(path, event_log, source="cli", vlm_client=None) -> IngestResult` aus `backend/services/ingestion.py` (Felder: `event_id: int`, `duplicate: bool`, `duplicate_since: Optional[str]`).
- Consumes: `AppContext` aus `backend/app_context.py` (Felder u.a. `event_log`, `dispatcher`, `faiss_index`, `vlm_client`).

- [ ] **Step 1: Fehlschlagende Tests für `Config.vault_path` schreiben**

Füge in `backend/tests/test_config.py` am Ende an:

```python


def test_from_env_vault_path_default_none(monkeypatch):
    monkeypatch.delenv("CBKS_VAULT_PATH", raising=False)

    config = Config.from_env()

    assert config.vault_path is None


def test_from_env_vault_path_set(monkeypatch):
    monkeypatch.setenv("CBKS_VAULT_PATH", "/mnt/external/Vault")

    config = Config.from_env()

    assert config.vault_path == "/mnt/external/Vault"
```

- [ ] **Step 2: Test ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_config.py -v`
Expected: die beiden neuen Tests FAIL mit `AttributeError: 'Config' object has no attribute 'vault_path'`.

- [ ] **Step 3: `vault_path` in `Config` implementieren**

In `backend/config.py`, füge im `Config`-Dataclass nach `api_key: Optional[str]` (letztes Feld) an:

```python
    vault_path: Optional[str]
```

In `from_env()`, füge nach `api_key=os.environ.get("CBKS_API_KEY"),` an:

```python
            vault_path=os.environ.get("CBKS_VAULT_PATH"),
```

- [ ] **Step 4: Tests ausführen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_config.py -v`
Expected: alle Tests PASS.

- [ ] **Step 5: Fehlschlagende Tests für `iter_vault_files` schreiben**

Erstelle `backend/tests/test_vault_import.py`:

```python
import asyncio
import json
from dataclasses import dataclass
from typing import Optional

import pytest

from backend.services.agents.prefrontal import PrefrontalAgent
from backend.services.agents.temporal import TemporalAgent
from backend.services.dispatcher import Dispatcher
from backend.services.entity_resolver import EntityResolver
from backend.services.event_log import EventLog
from backend.services.graph_backend import GraphBackend
from backend.services.ingestion import ingest_file
from backend.services.vault_import import VaultScanState, iter_vault_files, scan_vault
from backend.storage.faiss_index import FaissIndex
from backend.storage.sqlite_db import get_connection, init_db


class FakeLLMClient:
    def generate(self, prompt: str) -> str:
        return json.dumps({"classification": "document", "entities": []})


class FakeEmbeddingClient:
    def embed(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 1.0]


@dataclass
class FakeContext:
    event_log: EventLog
    dispatcher: Dispatcher
    faiss_index: FaissIndex
    vlm_client: Optional[object] = None


def make_context(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    init_db(conn)
    event_log = EventLog(conn)
    graph = GraphBackend(conn)
    faiss_index = FaissIndex(dim=4, index_path=tmp_path / "index.faiss")
    llm = FakeLLMClient()
    prefrontal = PrefrontalAgent(llm)
    temporal = TemporalAgent(FakeEmbeddingClient())
    resolver = EntityResolver(graph, temporal, llm)
    dispatcher = Dispatcher(event_log, graph, faiss_index, temporal, prefrontal, resolver, "bge-m3")
    return FakeContext(event_log=event_log, dispatcher=dispatcher, faiss_index=faiss_index), graph


def test_iter_vault_files_finds_supported_and_skips_unsupported(tmp_path):
    (tmp_path / "notiz.md").write_text("hallo")
    (tmp_path / "notiz.markdown").write_text("hallo")
    (tmp_path / "bild.png").write_bytes(b"\x89PNG")
    (tmp_path / "irrelevant.txt").write_text("nope")
    (tmp_path / "irrelevant.mp3").write_bytes(b"id3")

    files = iter_vault_files(tmp_path)

    names = {p.name for p in files}
    assert names == {"notiz.md", "notiz.markdown", "bild.png"}


def test_iter_vault_files_skips_excluded_dirs_and_hidden_files(tmp_path):
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "config.md").write_text("x")
    (tmp_path / ".trash").mkdir()
    (tmp_path / ".trash" / "geloescht.md").write_text("x")
    (tmp_path / ".hidden.md").write_text("x")
    (tmp_path / "sichtbar.md").write_text("x")
    nested = tmp_path / "unterordner"
    nested.mkdir()
    (nested / "tief.md").write_text("x")

    files = iter_vault_files(tmp_path)

    names = {p.name for p in files}
    assert names == {"sichtbar.md", "tief.md"}
```

- [ ] **Step 6: Test ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_vault_import.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.services.vault_import'`.

- [ ] **Step 7: `iter_vault_files` implementieren**

Erstelle `backend/services/vault_import.py`:

```python
from pathlib import Path

_SUPPORTED_SUFFIXES = {".md", ".markdown", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}
_EXCLUDED_DIRS = {".obsidian", ".trash"}


def iter_vault_files(root: Path) -> list[Path]:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
            continue
        relative_parts = path.relative_to(root).parts
        if any(part in _EXCLUDED_DIRS or part.startswith(".") for part in relative_parts):
            continue
        files.append(path)
    return files
```

- [ ] **Step 8: Tests ausführen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_vault_import.py -v`
Expected: die beiden `iter_vault_files`-Tests PASS.

- [ ] **Step 9: Fehlschlagende Tests für `scan_vault` schreiben**

Füge in `backend/tests/test_vault_import.py` am Ende an:

```python


def test_scan_vault_counts_processed_and_sets_total(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "eins.md").write_text("Text über FAISS")
    (vault / "zwei.md").write_text("Text über Ollama")
    ctx, graph = make_context(tmp_path)

    state = VaultScanState()
    asyncio.run(scan_vault(vault, ctx, state))

    assert state.total == 2
    assert state.scanned == 2
    assert state.processed == 2
    assert state.duplicates == 0
    assert state.failed == 0
    assert state.done is True
    assert state.error is None
    assert len(graph.get_all_nodes()) >= 2


def test_scan_vault_counts_duplicate(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "eins.md"
    note.write_text("Identischer Text")
    ctx, graph = make_context(tmp_path)
    ingest_file(note, ctx.event_log, source="vault")

    state = VaultScanState()
    asyncio.run(scan_vault(vault, ctx, state))

    assert state.total == 1
    assert state.duplicates == 1
    assert state.processed == 0
    assert state.done is True


def test_scan_vault_counts_failed_file_and_continues(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "kaputt.pdf").write_bytes(b"das ist kein echtes PDF")
    (vault / "gut.md").write_text("Text über FAISS")
    ctx, graph = make_context(tmp_path)

    state = VaultScanState()
    asyncio.run(scan_vault(vault, ctx, state))

    assert state.total == 2
    assert state.scanned == 2
    assert state.failed == 1
    assert state.processed == 1
    assert state.done is True
    assert state.error is None


def test_scan_vault_handles_empty_vault(tmp_path):
    vault = tmp_path / "leer"
    vault.mkdir()
    ctx, graph = make_context(tmp_path)

    state = VaultScanState()
    asyncio.run(scan_vault(vault, ctx, state))

    assert state.total == 0
    assert state.scanned == 0
    assert state.done is True
    assert state.error is None
```

- [ ] **Step 10: Test ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_vault_import.py -v -k scan_vault`
Expected: FAIL mit `ImportError: cannot import name 'VaultScanState'` (o.ä., da `scan_vault`/`VaultScanState` noch nicht existieren).

- [ ] **Step 11: `VaultScanState` und `scan_vault` implementieren**

Füge in `backend/services/vault_import.py` am Anfang der Importe an (vor `from pathlib import Path`):

```python
import asyncio
from dataclasses import dataclass
from typing import Optional
```

Füge nach der bestehenden `from pathlib import Path`-Zeile an:

```python

from backend.app_context import AppContext
from backend.services.ingestion import ingest_file
```

Füge am Ende der Datei an:

```python


@dataclass
class VaultScanState:
    total: int = 0
    scanned: int = 0
    processed: int = 0
    duplicates: int = 0
    failed: int = 0
    done: bool = False
    error: Optional[str] = None


async def scan_vault(root: Path, ctx: AppContext, state: VaultScanState) -> None:
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
            # Event-Loop zwischen Dateien freigeben: ohne diesen Yield-Punkt blockiert
            # die rein synchrone Schleife den einzigen Event-Loop-Thread bis zum
            # Scan-Ende, wodurch Live-Polling (GET /vault/scan/{job_id}) waehrend des
            # Scans nie antworten wuerde.
            await asyncio.sleep(0)
        await ctx.dispatcher.process_pending()
        ctx.faiss_index.save()
    except Exception as exc:
        state.error = str(exc)
    finally:
        state.done = True
```

`AppContext` kann hier gefahrlos top-level importiert werden: `backend/app_context.py` importiert weder `vault_import.py` noch `main.py`, es entsteht kein Zirkelimport (verifiziert durch Prüfung aller Imports in `app_context.py`).

- [ ] **Step 12: Tests ausführen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_vault_import.py -v`
Expected: alle Tests PASS (6 Tests insgesamt: 2× `iter_vault_files`, 4× `scan_vault`).

- [ ] **Step 13: Commit**

```bash
git add backend/config.py backend/tests/test_config.py backend/services/vault_import.py backend/tests/test_vault_import.py
git commit -m "feat: Vault-Konfiguration und Scan-Service fuer Obsidian-Import"
```

---

## Task 2: Backend — API-Endpoints für Vault-Scan

**Files:**
- Modify: `backend/api_models.py`
- Modify: `backend/main.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `Config.vault_path` (Task 1), `VaultScanState`, `scan_vault(root, ctx, state)` (Task 1) aus `backend/services/vault_import.py`.
- Produces: `POST /vault/scan` → `{"job_id": str}`, `GET /vault/scan/{job_id}` → `{total, scanned, processed, duplicates, failed, done, error}`, `GET /vault/default-path` → `{"path": str}` — Task 3 konsumiert exakt diese drei Felder-Sets.

- [ ] **Step 1: Fehlschlagende API-Tests schreiben**

Füge in `backend/tests/test_api.py` nach der Import-Zeile `from backend.services.agents.temporal import OllamaEmbeddingClient` an:

```python
import time
```

(als eigene Zeile direkt unter dem bestehenden `import json` / `import tempfile`-Block am Dateianfang, nicht zwischen den anderen `from`-Imports — `import time` gehört zu den Standardbibliotheks-Importen oben in der Datei.)

Füge am Ende der Datei an:

```python


def test_get_vault_default_path_reflects_env(monkeypatch):
    monkeypatch.setenv("CBKS_VAULT_PATH", "/tmp/mein-vault")

    response = client.get("/vault/default-path")

    assert response.status_code == 200
    assert response.json()["path"] == "/tmp/mein-vault"


def test_get_vault_default_path_empty_when_unset(monkeypatch):
    monkeypatch.delenv("CBKS_VAULT_PATH", raising=False)

    response = client.get("/vault/default-path")

    assert response.status_code == 200
    assert response.json()["path"] == ""


def test_start_vault_scan_rejects_invalid_path():
    response = client.post("/vault/scan", json={"path": "/pfad/existiert/garantiert/nicht"})

    assert response.status_code == 400


def test_get_vault_scan_unknown_job_returns_404():
    response = client.get("/vault/scan/unbekannte-id")

    assert response.status_code == 404


def test_start_vault_scan_and_poll_until_done(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "notiz.md").write_text("Text über FAISS")

    # asyncio.create_task() im async-Endpoint braucht einen Event-Loop, der ueber
    # mehrere Requests hinweg durchlaeuft. Der modulweite `client` (ohne `with`) baut
    # pro Aufruf einen neuen, kurzlebigen Loop auf, wodurch der Background-Task nach
    # dem ersten Request verwaist und nie weiterlaeuft (empirisch verifiziert). Ein
    # lokal gescopter Context-Manager-Client haelt den Loop fuer beide Requests am
    # Leben. Im echten Server (uvicorn) existiert dieses Problem nicht, da dort ohnehin
    # nur ein durchgehend laufender Event-Loop existiert.
    with TestClient(app) as scoped_client:
        start_response = scoped_client.post("/vault/scan", json={"path": str(vault)})
        assert start_response.status_code == 200
        job_id = start_response.json()["job_id"]

        body = None
        for _ in range(50):
            poll = scoped_client.get(f"/vault/scan/{job_id}")
            assert poll.status_code == 200
            body = poll.json()
            if body["done"]:
                break
            time.sleep(0.05)
        else:
            pytest.fail("Scan wurde nicht innerhalb des Timeouts fertig")

    assert body["total"] == 1
    assert body["processed"] == 1
    assert body["duplicates"] == 0
    assert body["failed"] == 0
    assert body["error"] is None
```

- [ ] **Step 2: Tests ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/pytest backend/tests/test_api.py -v -k vault`
Expected: alle 5 neuen Tests FAIL mit `404 Not Found` (Routen existieren noch nicht) bzw. Assertion-Fehlern.

- [ ] **Step 3: Response-/Request-Modelle ergänzen**

Füge in `backend/api_models.py` am Ende der Datei an:

```python


class VaultScanRequest(BaseModel):
    path: str


class VaultScanStartResponse(BaseModel):
    job_id: str


class VaultScanResponse(BaseModel):
    total: int
    scanned: int
    processed: int
    duplicates: int
    failed: int
    done: bool
    error: Optional[str] = None


class VaultDefaultPathResponse(BaseModel):
    path: str
```

- [ ] **Step 4: Endpoints in `backend/main.py` ergänzen**

Erweitere den Importblock: ersetze

```python
from backend.api_models import (
    AskRequest,
    AskResponse,
    BackupResponse,
    ConceptStat,
    ContradictionResponse,
    DedupeResponse,
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
)
```

durch

```python
from backend.api_models import (
    AskRequest,
    AskResponse,
    BackupResponse,
    ConceptStat,
    ContradictionResponse,
    DedupeResponse,
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
    VaultDefaultPathResponse,
    VaultScanRequest,
    VaultScanResponse,
    VaultScanStartResponse,
)
```

Ersetze

```python
from backend.services.ingestion import ingest_file, ingest_note
```

durch

```python
from backend.services.ingestion import ingest_file, ingest_note
from backend.services.vault_import import VaultScanState, scan_vault
from uuid import uuid4
```

Füge nach der Zeile `app = FastAPI(title="CBKS API", dependencies=[Depends(require_api_key)])` an:

```python

_vault_jobs: dict[str, VaultScanState] = {}
```

Füge am Ende der Datei (nach dem letzten bestehenden Endpoint `analyze_contradictions`) an:

```python


@app.get("/vault/default-path", response_model=VaultDefaultPathResponse)
def get_vault_default_path() -> VaultDefaultPathResponse:
    ctx = build_context()
    return VaultDefaultPathResponse(path=ctx.config.vault_path or "")


@app.post("/vault/scan", response_model=VaultScanStartResponse)
async def start_vault_scan(body: VaultScanRequest) -> VaultScanStartResponse:
    root = Path(body.path)
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="Pfad existiert nicht oder ist kein Verzeichnis")
    ctx = build_context()
    job_id = uuid4().hex
    state = VaultScanState()
    _vault_jobs[job_id] = state
    asyncio.create_task(scan_vault(root, ctx, state))
    return VaultScanStartResponse(job_id=job_id)


@app.get("/vault/scan/{job_id}", response_model=VaultScanResponse)
def get_vault_scan(job_id: str) -> VaultScanResponse:
    state = _vault_jobs.get(job_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job nicht gefunden")
    return VaultScanResponse(
        total=state.total, scanned=state.scanned, processed=state.processed,
        duplicates=state.duplicates, failed=state.failed, done=state.done, error=state.error,
    )
```

- [ ] **Step 5: Tests ausführen, Erfolg verifizieren**

Run: `.venv/bin/pytest backend/tests/test_api.py -v -k vault`
Expected: alle 5 Tests PASS.

- [ ] **Step 6: Vollständige Backend-Testsuite ausführen**

Run: `.venv/bin/pytest backend/tests/ -q`
Expected: alle Tests PASS (keine Regression durch die Import-/Endpoint-Änderungen).

- [ ] **Step 7: Commit**

```bash
git add backend/api_models.py backend/main.py backend/tests/test_api.py
git commit -m "feat: Vault-Scan-Endpoints (POST /vault/scan, GET /vault/scan/{id}, GET /vault/default-path)"
```

---

## Task 3: Frontend — UploadForm als Tab-Container mit Vault-Scan und Dropzone

**Files:**
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/components/UploadForm.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**
- Consumes: `GET /vault/default-path` → `{path: string}`, `POST /vault/scan` → `{job_id: string}`, `GET /vault/scan/{job_id}` → `VaultScanState` (Task 2).
- Consumes: `apiFetch<T>(path, init) -> Promise<T>` aus `frontend/src/api/client.ts` (unverändert), `useToast()` aus `frontend/src/components/Toast.tsx` (unverändert, liefert `pushToast(message, type?)` und `pushError(err, fallback?)`).
- `UploadForm`s Props (`{ onIngested: () => void }`) bleiben unverändert — `frontend/src/App.tsx:216` ruft weiterhin `<UploadForm onIngested={triggerRefresh} />` unangetastet auf.

- [ ] **Step 1: `VaultScanState`-Typ ergänzen**

Füge in `frontend/src/api/types.ts` am Ende der Datei an:

```typescript

export interface VaultScanState {
  total: number;
  scanned: number;
  processed: number;
  duplicates: number;
  failed: number;
  done: boolean;
  error: string | null;
}
```

- [ ] **Step 2: `UploadForm.tsx` komplett ersetzen**

Ersetze den kompletten Inhalt von `frontend/src/components/UploadForm.tsx` durch:

```tsx
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../api/client";
import { useToast } from "./Toast";
import type { VaultScanState } from "../api/types";

interface Props {
  onIngested: () => void;
}

type Tab = "vault" | "note" | "file";

const POLL_INTERVAL_MS = 1000;

export function UploadForm({ onIngested }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>("vault");
  const [noteText, setNoteText] = useState("");
  const [noteBusy, setNoteBusy] = useState(false);
  const [fileBusy, setFileBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [vaultPath, setVaultPath] = useState("");
  const [vaultJobId, setVaultJobId] = useState<string | null>(null);
  const [vaultState, setVaultState] = useState<VaultScanState | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { pushError, pushToast } = useToast();

  useEffect(() => {
    apiFetch<{ path: string }>("/vault/default-path")
      .then((result) => setVaultPath(result.path))
      .catch(() => {
        // Vorbefüllung ist best-effort; ohne Default bleibt das Feld leer.
      });
  }, []);

  useEffect(() => {
    if (!vaultJobId) return;
    const interval = window.setInterval(async () => {
      try {
        const state = await apiFetch<VaultScanState>(`/vault/scan/${vaultJobId}`);
        setVaultState(state);
        if (state.done) {
          window.clearInterval(interval);
          setVaultJobId(null);
          if (state.error) {
            pushToast(`Vault-Scan fehlgeschlagen: ${state.error}`, "error");
          } else {
            if (state.total === 0) {
              pushToast("Keine Dateien gefunden", "info");
            } else {
              pushToast(
                `✓ ${state.processed} importiert, ⊘ ${state.duplicates} Duplikate, ✕ ${state.failed} Fehler`,
                "success"
              );
            }
            onIngested();
          }
        }
      } catch (err) {
        window.clearInterval(interval);
        setVaultJobId(null);
        pushError(err, "Vault-Scan-Status konnte nicht abgerufen werden");
      }
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [vaultJobId, onIngested, pushError, pushToast]);

  const startVaultScan = async () => {
    if (!vaultPath.trim() || vaultJobId !== null) return;
    setVaultState(null);
    try {
      const result = await apiFetch<{ job_id: string }>("/vault/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: vaultPath }),
      });
      setVaultJobId(result.job_id);
    } catch (err) {
      pushError(err, "Vault-Scan konnte nicht gestartet werden");
    }
  };

  const submitNote = async () => {
    if (!noteText.trim()) return;
    setNoteBusy(true);
    try {
      const result = await apiFetch<{ duplicate?: boolean; processed?: number; failed?: number }>(
        "/notes",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: noteText }),
        }
      );
      if (result?.duplicate) {
        pushToast("Notiz bereits vorhanden (Duplikat)", "info");
      } else {
        pushToast(
          `Verarbeitet: ${result?.processed ?? 0}, Fehler: ${result?.failed ?? 0}`,
          "success"
        );
      }
      setNoteText("");
      onIngested();
    } catch (err) {
      pushError(err, "Notiz konnte nicht gespeichert werden");
    } finally {
      setNoteBusy(false);
    }
  };

  const submitFile = async (file: File) => {
    setFileBusy(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const result = await apiFetch<{ duplicate?: boolean; processed?: number; failed?: number }>(
        "/documents",
        { method: "POST", body: formData }
      );
      if (result?.duplicate) {
        pushToast("Dokument bereits vorhanden (Duplikat)", "info");
      } else {
        pushToast(
          `Verarbeitet: ${result?.processed ?? 0}, Fehler: ${result?.failed ?? 0}`,
          "success"
        );
      }
      onIngested();
    } catch (err) {
      pushError(err, "Datei konnte nicht hochgeladen werden");
    } finally {
      setFileBusy(false);
    }
  };

  return (
    <div className="upload-form">
      <div className="upload-tabs">
        <button
          className={`upload-tab ${activeTab === "vault" ? "active" : ""}`}
          onClick={() => setActiveTab("vault")}
        >
          Vault
        </button>
        <button
          className={`upload-tab ${activeTab === "note" ? "active" : ""}`}
          onClick={() => setActiveTab("note")}
        >
          Notiz
        </button>
        <button
          className={`upload-tab ${activeTab === "file" ? "active" : ""}`}
          onClick={() => setActiveTab("file")}
        >
          Datei
        </button>
      </div>

      {activeTab === "vault" && (
        <div className="upload-tab-content">
          <input
            type="text"
            value={vaultPath}
            onChange={(e) => setVaultPath(e.target.value)}
            placeholder="/pfad/zum/vault"
          />
          <button onClick={startVaultScan} disabled={vaultJobId !== null}>
            {vaultJobId !== null ? "Scan läuft…" : "Vault scannen & importieren"}
          </button>
          {vaultState !== null && (
            <>
              <div className="vault-progress">
                <div className="dist-track">
                  <div
                    className="dist-fill"
                    style={{
                      width:
                        vaultState.total > 0
                          ? `${(vaultState.scanned / vaultState.total) * 100}%`
                          : "0%",
                      background: "#6C8EF5",
                    }}
                  />
                </div>
                <span className="dist-value">
                  {vaultState.scanned}/{vaultState.total}
                </span>
              </div>
              <div className="vault-stats">
                <span className="stat-inline">
                  ✓ <strong>{vaultState.processed}</strong> importiert
                </span>
                <span className="stat-inline">
                  ⊘ <strong>{vaultState.duplicates}</strong> Duplikate
                </span>
                <span className="stat-inline">
                  ✕ <strong>{vaultState.failed}</strong> Fehler
                </span>
              </div>
            </>
          )}
        </div>
      )}

      {activeTab === "note" && (
        <div className="upload-tab-content">
          <div className="upload-row">
            <input
              type="text"
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitNote()}
              placeholder="Notiz eintippen..."
            />
            <button onClick={submitNote} disabled={noteBusy}>
              {noteBusy ? "…" : "Speichern"}
            </button>
          </div>
        </div>
      )}

      {activeTab === "file" && (
        <div className="upload-tab-content">
          <div
            className={`dropzone ${dragOver ? "dragover" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              const file = e.dataTransfer.files?.[0];
              if (file) submitFile(file);
            }}
            onClick={() => fileInputRef.current?.click()}
          >
            {fileBusy ? "Wird hochgeladen…" : "Datei hierher ziehen oder klicken"}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            hidden
            disabled={fileBusy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) submitFile(file);
              e.target.value = "";
            }}
          />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: CSS-Klassen ergänzen**

In `frontend/src/styles/global.css`, ersetze

```css
.search-bar > div,
.upload-form > div {
  display: flex;
  gap: 0.5rem;
}
```

durch

```css
.search-bar > div {
  display: flex;
  gap: 0.5rem;
}

.upload-row {
  display: flex;
  gap: 0.5rem;
}
```

(Grund: `UploadForm` hat nach diesem Umbau keine bloßen `<div>`-Kinder mehr direkt unter `.upload-form` — Tabs und Tab-Inhalt brauchen eigene, explizite Klassen statt der alten generischen Kind-Selektor-Regel, die auf die frühere Zwei-Zeilen-Struktur zugeschnitten war.)

Füge am Ende der Datei an:

```css

/* ---------- Upload-Tabs (Vault/Notiz/Datei) ---------- */

.upload-tabs {
  display: flex;
  gap: 0.25rem;
}

.upload-tab {
  flex: 1;
  padding: 0.3rem 0.4rem;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--fg-muted);
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.75rem;
  transition: all 0.12s;
}

.upload-tab:hover {
  color: var(--fg);
  border-color: var(--border-strong);
}

.upload-tab.active {
  color: var(--fg);
  border-color: var(--accent);
  background: rgba(108, 142, 245, 0.12);
}

.upload-tab-content {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.vault-progress {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.vault-progress .dist-track {
  flex: 1;
}

.vault-stats {
  display: flex;
  gap: 0.75rem;
  font-size: 0.8rem;
  color: var(--fg-muted);
}

.dropzone {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem 1rem;
  border: 1px dashed var(--border);
  border-radius: 6px;
  color: var(--fg-muted);
  font-size: 0.8rem;
  text-align: center;
  cursor: pointer;
  transition: all 0.12s;
}

.dropzone:hover,
.dropzone.dragover {
  border-color: var(--accent);
  color: var(--fg);
  background: rgba(108, 142, 245, 0.08);
}
```

- [ ] **Step 4: TypeScript-Build verifizieren**

Run: `cd frontend && npm run build`
Expected: `tsc -b` und `vite build` laufen ohne Fehler durch.

- [ ] **Step 5: Manuelle Verifikation im Dev-Server**

Run: `cd frontend && npm run dev` (Backend muss parallel laufen, z.B. `.venv/bin/uvicorn backend.main:app --reload` mit gesetztem `CBKS_VAULT_PATH`).

Prüfe im Browser:
- Tab-Wechsel Vault/Notiz/Datei funktioniert, Vault ist der Default-Tab.
- Vault-Textfeld ist mit `CBKS_VAULT_PATH` vorbefüllt.
- Klick auf „Vault scannen & importieren" startet den Scan, Fortschrittsbalken und Zähler aktualisieren sich alle ~1s, am Ende erscheint ein Erfolgs-Toast.
- Ungültiger Vault-Pfad → Fehler-Toast, kein Fortschrittsbalken.
- Datei-Tab: Drag&Drop einer Datei löst Upload aus, Klick auf die Dropzone öffnet den Datei-Dialog.
- Notiz-Tab: Eingabe + Enter/Speichern funktioniert wie vorher.

Notiere das Ergebnis (bestanden/Probleme) im Report — kein automatisierter Test verfügbar für diesen Schritt.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/components/UploadForm.tsx frontend/src/styles/global.css
git commit -m "feat: UploadForm als Vault/Notiz/Datei-Tabs mit Live-Scan-Fortschritt und Dropzone"
```

---

## Nach allen Tasks

Nach Abschluss aller drei Tasks: finaler Whole-Branch-Review über `superpowers:requesting-code-review`, danach `superpowers:finishing-a-development-branch`. Kein Worktree vorhanden (direkt auf `main` gearbeitet) — die Abschluss-Optionen entsprechend anpassen (kein Merge/PR nötig, nur Zusammenfassung).
