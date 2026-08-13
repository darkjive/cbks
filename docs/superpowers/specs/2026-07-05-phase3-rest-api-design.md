# CBKS Phase 3.1 (REST-API) — Design

Datum: 2026-07-05

**Referenz:** CBKS_SPEC_v1.1.md §5.2 (REST-API), §Phase 3 (Zeile ~790-796). Phase 1 (Fundament) und Phase 2 (MVP-Kern mit CLI) sind abgeschlossen und liegen direkt auf `main`.

## Ziel

Phase 3 der Spec umfasst mehrere weitgehend unabhängige Themen (REST-API, Frontend, Sentiment-Analyse, Entity-Resolution, Decay-Ranking, LOD-Visualisierung). Dieses Dokument deckt nur das erste Sub-Projekt ab: die REST-API. Spec-Zitat (Zeile 383): *"Das CLI spricht intern dieselben Service-Funktionen an wie später die REST-API."* — die API ist also ein zweiter, unabhängiger Konsument der in Phase 2 gebauten Service-Schicht, kein Ersatz für die CLI und keine Abhängigkeit der CLI von der API.

## Architektur

```
backend/
├── main.py            # FastAPI-app, Routen (NEU — bisher nur Stub-Kommentar)
├── app_context.py      # AppContext + build_context() (NEU — extrahiert aus cli.py)
├── cli.py              # nutzt künftig app_context.build_context() statt eigener _build_context()
├── auth.py             # API-Key-Dependency (NEU)
├── ...                 # models/, services/, storage/ unverändert aus Phase 2
```

`AppContext` (Dataclass: config, conn, event_log, graph, faiss_index, temporal_agent, prefrontal_agent, dispatcher) und die Builder-Funktion wandern von `cli.py` nach `backend/app_context.py`, damit CLI und API keine Logik duplizieren. `cli.py` importiert von dort. Dies ist eine reine Umbenennung/Verschiebung ohne Verhaltensänderung — bestehende CLI-Tests müssen unverändert grün bleiben.

## Docker-Fix (Voraussetzung, im selben Zug behoben)

Bug in `docker/compose.yml` aus Phase 1, nie getestet (Docker blieb in Phase 2 ungenutzt): `build.context: ../backend` kopiert die Backend-Dateien direkt nach `/app` (kein `backend/`-Unterordner im Image), aber der gesamte Code nutzt absolute Imports wie `from backend.config import Config`. Das würde im Container mit `ImportError` fehlschlagen.

**Fix:**
- `docker/compose.yml`: `build.context: ..` (Repo-Root), `build.dockerfile: backend/Dockerfile`.
- `backend/Dockerfile`: `COPY requirements.txt` → `COPY backend/requirements.txt`, `COPY . .` → `COPY backend/ ./backend/` (bzw. Repo-Root-relative Pfade, sodass `/app/backend/...` im Image entsteht), `CMD` von `uvicorn main:app ...` auf `uvicorn backend.main:app --host 127.0.0.1 --port 8000`.
- `docker/compose.yml` Env-Var-Namen an die tatsächlichen `CBKS_*`-Namen aus `backend/config.py` angleichen (aktuell: `OLLAMA_HOST`/`DATABASE_PATH`/`FAISS_PATH`/`SNAPSHOT_PATH`/`BACKUP_PATH` — teils falsch benannt ggü. `config.py`s `CBKS_DATABASE_PATH`/`CBKS_FAISS_PATH`/`CBKS_BACKUP_SCRIPT`; `SNAPSHOT_PATH` entfällt, da FAISS-Snapshots laut Phase-2-Design weiterhin zurückgestellt sind).

## Endpunkte

1:1-Parität zu den 9 CLI-Befehlen:

| Methode | Pfad | CLI-Pendant | Request | Response (Erfolg) |
|---|---|---|---|---|
| POST | `/documents` | `add <datei>` | multipart file upload | `{event_id, duplicate, duplicate_since, processed, failed}` |
| POST | `/notes` | `note <text>` | JSON `{text: str}` | wie oben |
| POST | `/ask` | `ask <frage>` | JSON `{question: str}` | `{answer: str, sources: list[str]}` |
| GET | `/search` | `search <begriff>` | Query `?q=...&limit=10` | `[{node: {...}, score: float}]` |
| GET | `/nodes/{node_id}` | `show <id>` | — | `{node: {...}, neighbors: [...]}`; 404 falls unbekannt |
| GET | `/stats` | `stats` | — | `{events: {...}, graph: {...}}` |
| POST | `/retry` | `retry` | — | `{processed: int, failed: int}` |
| POST | `/rebuild` | `rebuild` | — | `{processed: int, failed: int}` |
| POST | `/backup` | `backup` | — | `{status: "ok"}` |

`/documents` und `/notes` rufen intern `ingest_file`/`ingest_note` (Task 10) gefolgt von `dispatcher.process_pending()` auf — exakt wie `cli.py`s `add`/`note`, inklusive `faiss_index.save()` danach. Bei `/documents` schreibt der Handler die hochgeladene Datei zuerst in ein temporäres Verzeichnis (`tempfile`), da `ingest_file(path, ...)` einen Dateipfad erwartet (Task 10); die Temp-Datei wird nach dem Aufruf wieder gelöscht.

Bei Duplikat (`IngestResult.duplicate=True`) wird `process_pending()` übersprungen, Response ist `{event_id, duplicate: true, duplicate_since, processed: null, failed: null}` mit HTTP 200. Bei erfolgreicher neuer Ingestion ist es `{event_id, duplicate: false, duplicate_since: null, processed, failed}` — auch wenn `failed > 0` (einzelne Events können in `process_pending()` fehlschlagen, siehe Dispatcher-Fehlerisolation aus Task 9), bleibt der HTTP-Status 200: ein `failed > 0` ist kein HTTP-Fehler, sondern fachlicher Zustand, exakt wie die CLI, die `"Verarbeitet: X, Fehlgeschlagen: Y"` ausgibt statt eine Exception zu werfen.

Alle Endpunkte sind **synchron/blockierend**: die Response kommt erst nach vollständiger Verarbeitung (kein Background-Task, kein Polling) — konsistent mit der Phase-2-Entscheidung gegen einen Dispatcher-Daemon.

## Auth

`CBKS_API_KEY` als neue Config-Option (`backend/config.py`, optional, kein Default). Ist die Env-Var gesetzt, prüft eine FastAPI-Dependency (`backend/auth.py`) den Header `X-API-Key` gegen den konfigurierten Wert bei jedem Request (`401 Unauthorized` bei Fehlen oder Mismatch). Ist die Env-Var nicht gesetzt, läuft die API ungeprüft (aktueller lokaler Nutzungsfall bleibt reibungslos, kein Breaking Change für bestehende Host-Nutzung).

## Fehlerbehandlung

- Duplikat bei `/documents`/`/notes` → `200 OK`, `duplicate: true` im Body (kein Fehler).
- Unbekannte `node_id` bei `/nodes/{id}` → `404 Not Found`, `{"detail": "Node nicht gefunden"}`.
- Fehlende/ungültige Request-Felder → `422 Unprocessable Entity` (FastAPI/Pydantic-Standard, automatisch).
- Fehlender/falscher API-Key (falls `CBKS_API_KEY` gesetzt) → `401 Unauthorized`.
- Unerwartete Exceptions (z. B. Ollama nicht erreichbar) → `500 Internal Server Error` (FastAPI-Standard, kein spezielles Error-Handling — konsistent mit CLI, die solche Fehler ebenfalls nicht speziell abfängt und stattdessen den Prozess mit Traceback beenden würde; im Server-Kontext bedeutet das einen 500 statt Prozessabbruch).
- `/backup`: `subprocess.CalledProcessError` (Skript schlägt fehl) → `500`, kein spezielles Mapping nötig.

## Response-Modelle

Pydantic-Modelle in `backend/api_models.py` (NEU), die die bestehenden Service-Dataclasses (`IngestResult`, `ProcessSummary`, `SearchHit`, `AnswerResult` aus Tasks 9-12) 1:1 in JSON-serialisierbare Schemas übersetzen. Die Service-Schicht selbst bleibt unverändert (keine Pydantic-Abhängigkeit dort einführen) — die Übersetzung passiert ausschließlich in den Routen-Handlern von `main.py`.

## Testing

- `backend/tests/test_api.py`: `fastapi.testclient.TestClient`, gleiche Fake-Ollama-Fixtures (`fake_embed`/`fake_generate` per `monkeypatch`) und `isolated_data_dir`-Fixture wie in `test_cli.py` — ein Test pro Endpunkt, analog zur CLI-Testabdeckung (inkl. Duplikat-Fall, 404-Fall, Auth-Fall).
- `backend/tests/test_e2e_api_milestone.py`: ein echter E2E-Test gegen laufendes Ollama (analog zu Task 14): `POST /documents` (PDF) → `POST /ask` → Antwort mit Quellen. Übersprungen falls Ollama nicht erreichbar (gleicher Skip-Mechanismus wie `test_e2e_milestone.py`).
- Docker-Fix wird manuell verifiziert: `docker compose build && docker compose up -d`, dann ein `curl -H "X-API-Key: ..." http://127.0.0.1:8000/stats` gegen den laufenden Container (kein Docker-Test in der pytest-Suite, da das Docker-Deployment selbst kein automatisiert getestetes CI-Artefakt in diesem Solo-Dev-Setup ist — Konsistenz mit Phase 1, wo `docker compose config` ebenfalls nur manuell validiert wurde).

## Out of Scope (bewusst, für spätere Sub-Projekte von Phase 3)

- Frontend-Anbindung (eigenes Sub-Projekt, baut auf dieser API auf).
- CORS-Konfiguration (erst relevant, sobald ein Browser-Frontend aus einem anderen Origin zugreift).
- Background-Task/Polling für lange Verarbeitungen (YAGNI, solange kein Frontend das braucht).
- Rate-Limiting, Request-Logging-Middleware (kein Bedarf für Solo-Dev-Nutzung).
