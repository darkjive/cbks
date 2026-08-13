# Spec: Obsidian-Vault-Import + modernes Import-UI

**Datum:** 2026-07-10
**Status:** Entwurf (vom Nutzer freigegeben)

## Ziel

Notizen aus einem Obsidian-Vault (z.B. `/mnt/external/Vault/`) sollen per
Knopfdruck importiert werden können, statt jede Notiz einzeln einzutippen. Der
Vault-Pfad ist konfigurierbar (nicht hart auf dieses eine Verzeichnis codiert).
Zusätzlich wird die bestehende Import-UI (`UploadForm`) modernisiert: Drag&Drop
statt reinem Datei-Button, klare Trennung der drei Eingabewege per Tabs.

## Entscheidungen (mit Nutzer geklärt)

| Frage | Entscheidung |
|---|---|
| Import-Modus | Manueller Scan-Button (kein dauerhaftes Watching) |
| Pfad-Speicherung | Nur env var `CBKS_VAULT_PATH`, kein DB-Persistieren |
| Feld-Editierbarkeit | Textfeld vorbefüllt mit env-Default, pro Scan überschreibbar |
| UI-Umfang | Alle drei Wege (Vault/Notiz/Datei) bleiben, als Tabs, Vault im Vordergrund |
| Datei-Filter | `.md`/`.markdown` + Anhänge (`.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp` — identisch zu `parse_file`) |
| Duplikat-Erkennung | Bestehende Hash-Duplikaterkennung aus `ingest_file`/`EventLog` wiederverwenden |
| Fortschrittsanzeige | Live-Fortschritt per Polling (async Job + Status-Endpoint) |

## Architektur

```
Vault-Tab (Frontend)
  → POST /vault/scan {path}         -- startet Scan als Background-Task, gibt job_id zurück
  → GET  /vault/scan/{job_id}       -- Polling alle 1s: {scanned, total, processed, duplicates, failed, done}
  → GET  /vault/default-path        -- liefert CBKS_VAULT_PATH zum Vorbefüllen
```

## Backend

### `backend/config.py`

Neues Feld `vault_path: Optional[str]`, aus `os.environ.get("CBKS_VAULT_PATH")`.

### Neuer Service `backend/services/vault_import.py`

Konstanten:

```python
_SUPPORTED_SUFFIXES = {".md", ".markdown", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}
_EXCLUDED_DIRS = {".obsidian", ".trash"}
```

Funktionen:

- `iter_vault_files(root: Path) -> list[Path]` — `root.rglob("*")`, filtert auf
  `_SUPPORTED_SUFFIXES`, überspringt Pfade mit einem Segment aus `_EXCLUDED_DIRS`
  oder die mit `.` beginnen (versteckte Dateien).
- `@dataclass VaultScanState`: `total: int`, `scanned: int`, `processed: int`,
  `duplicates: int`, `failed: int`, `done: bool`, `error: Optional[str]`.
- `async def scan_vault(root: Path, ctx: AppContext, state: VaultScanState) -> None`:
  füllt `state.total` sofort nach `iter_vault_files`, iteriert dann Datei für
  Datei, ruft synchron `ingest_file(path, ctx.event_log, source="vault", vlm_client=ctx.vlm_client)`
  auf. Exceptions pro Datei (z.B. kaputtes PDF) werden abgefangen, zählen als
  `failed`, Scan läuft weiter. Erfolgreiche Duplikate zählen als `duplicates`,
  sonst `processed`. Nach jeder Datei: `state.scanned += 1`. Am Ende (nicht pro
  Datei!) einmalig `await ctx.dispatcher.process_pending()` und
  `ctx.faiss_index.save()` — effizienter als Einzelverarbeitung bei hunderten
  Dateien. Am Schluss `state.done = True`. Wirft `scan_vault` selbst eine
  unerwartete Exception (außerhalb der Pro-Datei-Behandlung), wird `state.error`
  gesetzt und `state.done = True` — das Polling zeigt dann einen Fehler statt
  endlos weiterzulaufen.

### Neue Endpoints (`backend/main.py`)

```python
_vault_jobs: dict[str, VaultScanState] = {}

@app.get("/vault/default-path")
def get_vault_default_path() -> dict:
    ctx = build_context()
    return {"path": ctx.config.vault_path or ""}

@app.post("/vault/scan")
def start_vault_scan(body: VaultScanRequest) -> dict:
    root = Path(body.path)
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="Pfad existiert nicht oder ist kein Verzeichnis")
    ctx = build_context()
    job_id = uuid4().hex
    state = VaultScanState()
    _vault_jobs[job_id] = state
    asyncio.create_task(scan_vault(root, ctx, state))
    return {"job_id": job_id}

@app.get("/vault/scan/{job_id}")
def get_vault_scan(job_id: str) -> VaultScanState:
    state = _vault_jobs.get(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return state
```

`_vault_jobs` ist bewusst ein einfaches In-Memory-Dict im Prozess (kein
DB-Persistieren) — der Scan ist ein einmaliger, kurzlebiger UI-Vorgang, kein
Cron-Job, der einen Backend-Neustart überleben muss.

`VaultScanRequest` (neu in `backend/api_models.py`): `{path: str}`.

## Frontend

### `frontend/src/components/UploadForm.tsx`

Wird zu einem Tab-Container mit drei Tabs: **Vault** (Default-Tab) · **Notiz** · **Datei**.

```
┌─ Eingabe ──────────────────────┐
│ [ Vault ] [ Notiz ] [ Datei ]  │
│                                 │
│  Vault-Ordner                  │
│  [/mnt/external/Vault    ] │
│  [   Vault scannen & importieren   ] │
│                                 │
│  ▓▓▓▓▓▓▓▓▓░░░░░░░░  142/380     │
│  ✓ 120 importiert  ⊘ 15 Duplikate  ✕ 7 Fehler │
└─────────────────────────────────┘
```

- **State:** `activeTab: "vault" | "note" | "file"` (Default `"vault"`),
  `vaultPath: string` (initial leer, per `useEffect` einmalig aus
  `GET /vault/default-path` befüllt), `vaultJobId: string | null`,
  `vaultState: VaultScanState | null`.
- **Vault-Tab:** Textfeld (`vaultPath`) + Button „Vault scannen & importieren"
  (disabled während `vaultJobId` aktiv ist). Klick: `POST /vault/scan
  {path: vaultPath}` → `job_id` merken → `setInterval` (1000ms) auf
  `GET /vault/scan/{job_id}`, State übernehmen; bei `done === true`: Interval
  stoppen, `vaultJobId = null`, Toast mit Zusammenfassung
  (`✓ processed importiert, ⊘ duplicates Duplikate, ✕ failed Fehler`),
  `onIngested()` aufrufen. Fortschrittsbalken + Live-Zähler nur rendern,
  solange `vaultState !== null`.
- **Datei-Tab:** ersetzt `<input type="file">` durch eine Dropzone
  (`onDragOver`/`onDragLeave`/`onDrop`, gestrichelter Rahmen, Klick öffnet
  zusätzlich ein verstecktes `<input type="file">` als Fallback). Ruft die
  bestehende `submitFile(file)` unverändert auf.
- **Notiz-Tab:** bestehendes Feld + „Speichern", nur ins Tab-Layout verschoben,
  Logik (`submitNote`) unverändert.

### `frontend/src/styles/global.css`

Neue Klassen (angelehnt an bestehende Tokens/Muster wie `.event-tab`/
`.event-tab.active` und `.stat-tile`):

- `.upload-tabs`, `.upload-tab`, `.upload-tab.active`
- `.vault-progress` (Fortschrittsbalken-Track + -Fill, wie `.dist-track`/`.dist-fill`)
- `.vault-stats` (drei Inline-Zähler wie `.stat-inline`)
- `.dropzone`, `.dropzone.dragover` (gestrichelter Rahmen, Hover-/Drag-Zustand
  mit `--accent`-Farbe)

## Fehlerbehandlung

| Fall | Verhalten |
|---|---|
| Vault-Pfad existiert nicht / kein Verzeichnis | `POST /vault/scan` → 400, Toast-Fehler, kein Job angelegt |
| Vault ohne passende Dateien | Job läuft durch mit `total=0`, Info-Toast „Keine Dateien gefunden" |
| Einzelne Datei nicht parsbar | In `scan_vault` abgefangen, zählt als `failed`, Scan läuft weiter |
| Job-ID unbekannt (z.B. Backend-Neustart während Scan) | `GET /vault/scan/{id}` → 404, Frontend stoppt Polling, Toast-Fehler |
| Scan läuft bereits | „Scan starten"-Button disabled, solange `vaultJobId` gesetzt ist |
| Unerwarteter Fehler im Scan selbst (z.B. Berechtigungsfehler beim Verzeichnis-Walk) | `state.error` gesetzt, `done=true`; Frontend erkennt `error !== null`, stoppt Polling, zeigt Toast mit `state.error` statt Erfolgs-Zusammenfassung |

## Tests

- **`backend/tests/test_vault_import.py`** (neu): `iter_vault_files()` gegen ein
  temporäres Verzeichnis mit passenden/unpassenden Dateien, `.obsidian`/`.trash`-
  Unterordnern und versteckten Dateien. `scan_vault()` End-to-End mit einem
  `EventLog` gegen eine Temp-DB: prüft korrekte Zählung von
  processed/duplicates/failed (inkl. einer absichtlich kaputten Datei, die
  `parse_file` zum Scheitern bringt).
- **`backend/tests/test_api.py`** (Ergänzung): `POST /vault/scan` mit
  ungültigem Pfad → 400; mit validem Pfad → `job_id` im Body. `GET
  /vault/scan/{job_id}` liefert Fortschritt und erreicht `done=true`
  (`asyncio`-Task ggf. mit `await asyncio.sleep(0)`/Polling-Loop im Test
  abwarten). `GET /vault/default-path` spiegelt `CBKS_VAULT_PATH` (per
  `monkeypatch.setenv`) wider.
- **Frontend:** kein Testframework im Projekt vorhanden → manuelle Verifikation
  über Dev-Server/Browser (echter Vault-Scan, Dropzone-Drag&Drop, Tab-Wechsel,
  Abbruch-/Fehlerfälle wie ungültiger Pfad).

## Nicht in Scope

- Kontinuierliches Watching/automatischer Re-Import bei Dateiänderungen
- Persistieren des Vault-Pfads in der DB/Config-UI (nur env var)
- Löschen/Umbenennen von Notizen im Vault nachverfolgen (nur Import neuer/
  geänderter Inhalte über die bestehende Hash-Duplikaterkennung)
- Auswahl einzelner Dateien/Unterordner innerhalb des Vaults (immer voller
  rekursiver Scan ab dem angegebenen Pfad)
- Persistieren von Scan-Jobs über einen Backend-Neustart hinweg

## Risiken

- **Lange Scans blockieren den Event-Loop nicht**, da `scan_vault` als
  `asyncio.create_task` läuft — aber `ingest_file`/`parse_file` sind synchrone,
  potenziell CPU-/IO-lastige Aufrufe (PDF-Parsing, VLM-Aufrufe für Bilder) und
  laufen daher im selben Task ohne Thread-Offload. Bei sehr großen Vaults mit
  vielen Bildern kann das andere Requests währenddessen verzögern. Akzeptiert
  für die erste Version (YAGNI) — Threadpool-Offload wäre ein späterer
  Optimierungsschritt, falls es in der Praxis stört.
