# CBKS Phase 2 (MVP-Kern mit CLI) — Design

Datum: 2026-07-05

**Referenz:** CBKS_SPEC_v1.1.md §3–§6, §8 (Phase 2). Phase 1 (Fundament) ist abgeschlossen: Docker/Backend-Image, natives Ollama, Modelle gepullt, Modellwahl `qwen3:8b` (siehe `docs/benchmark_results.md`), Backup+Timer.

## Ziel

Meilenstein aus Spec §8: `cbks add papier.pdf && cbks ask "Was steht drin?"` funktioniert Ende-zu-Ende. `cbks rebuild` stellt Graph + FAISS-Index vollständig aus dem Event-Log wieder her.

## Abweichungen / Entscheidungen ggü. Spec (mit Nutzer abgestimmt, 2026-07-05)

1. **Kein eigenständiger Hermes-Dispatcher-Daemon.** Statt eines separaten asyncio-Hintergrundprozesses, der kontinuierlich die `events`-Tabelle pollt, ist `process_pending()` eine synchrone Funktion, die von CLI-Befehlen direkt aufgerufen wird (intern per `asyncio.gather()` an die drei Agenten parallelisiert, aber ohne separaten Prozess). Begründung: Solo-Dev-CLI-Tool, kein Bedarf an einem laufenden Server für die MVP-Nutzung; passt zum Meilenstein-Muster "`cbks add X && cbks ask Y`" (beide Befehle laufen synchron durch und kehren erst nach Abschluss zurück).
2. **Ausführung direkt auf dem Host (venv), nicht im Docker-Container.** Der `docker/compose.yml`-Container aus Phase 1 bleibt für später (REST-API/Deployment) bestehen, wird für die CLI-Entwicklung in Phase 2 aber nicht verwendet — schnellere Iteration ohne Rebuild bei jeder Codeänderung.
3. **FAISS-Snapshots verschoben auf später.** Nicht Teil von Phase 2 (Spec-Priorität ohnehin niedriger als der Rest, ⭐⭐⭐). `cbks rebuild` ist der Recovery-Mechanismus fürs MVP; Snapshots kommen erst bei Bedarf (z. B. wenn `rebuild` bei wachsendem Event-Log spürbar lange dauert).
4. **Entity→Graph-Regel (von der Spec nicht spezifiziert):** Jede vom Prefrontal-Agenten erkannte Entität wird — falls noch kein `concept`-Node mit exakt (case-insensitive) demselben Titel existiert — als neuer `concept`-Node angelegt und per `mentions`-Edge mit dem Quell-Dokument-Node verknüpft. Nur exakter Titel-Match als Dedup-Kriterium; echte Entity-Resolution/Deduplizierung auf semantischer Ebene ist laut Spec §9 explizit erst Phase 3.

## Bekannte Limitation (aus finalem Whole-Branch-Review, bewusst zurückgestellt)

**`cbks retry` ist bei einem partiellen Fehler nach dem Node-Write nicht idempotent.** `process_event` erzeugt für den Dokument-Node bei jedem Aufruf eine frische UUID, ohne Dedup über `content_hash`. Schlägt die Verarbeitung erst NACH dem Schreiben des Dokument-Node fehl (z. B. `add_edge` scheitert), bleibt ein Orphan-Node stehen, während das Event auf `failed` bleibt. Ein erneuter `cbks retry` würde einen zweiten Dokument-Node erzeugen — schlägt der Fehler nach `link_vector` auf, sogar dauerhaft (`node_vectors.faiss_id UNIQUE`-Konflikt, Event bleibt für `retry` für immer `failed`). Der korrekte Recovery-Weg ist ausschließlich **`cbks rebuild`** (leert Graph+FAISS, spielt das gesamte Event-Log neu ab — idempotent per Design). Ein echter Fix für `retry` selbst bräuchte Dedup über `content_hash` (nicht Titel) plus idempotente FAISS/`node_vectors`-Behandlung — für den Solo-Dev-MVP-Scope (YAGNI) bewusst zurückgestellt; bei Bedarf in Phase 3 aufgreifen.

## Architektur

```
backend/
├── cli.py                    # Typer-CLI: add/note/ask/search/show/stats/retry/rebuild/backup
├── config.py                 # Pfade, Ollama-Host, Modellname (qwen3:8b)
├── models/
│   ├── events.py / nodes.py / edges.py   # Pydantic-Schemas
├── services/
│   ├── event_log.py          # append, pending(), mark_processed, replay_all
│   ├── hashing.py            # SHA-256 Content-Hash
│   ├── dispatcher.py         # process_pending() – synchron, intern asyncio.gather() über Agenten
│   ├── agents/
│   │   ├── prefrontal.py     # qwen3:8b – Klassifikation + Entity-Extraktion
│   │   ├── temporal.py       # bge-m3 Embedding + FAISS
│   │   └── graph_engine.py   # Schreibt Nodes/Edges (Write-Through SQLite→NetworkX)
│   ├── parsing.py            # PyMuPDF (PDF), Markdown
│   └── graph_backend.py      # SQLite = Wahrheit, NetworkX = Cache
├── storage/
│   ├── sqlite_db.py
│   └── faiss_index.py        # IndexFlatIP + IndexIDMap
└── tests/
```

Datenmodell (SQLite-Schema, Node/Edge-Struktur, FAISS-Index-Typ) wird unverändert aus Spec §4 übernommen — dort bereits vollständig spezifiziert.

## Verarbeitungs-Pipeline

Pro `cbks add`/`cbks note`-Aufruf:

1. **Parsing** (bei Datei): PyMuPDF für PDF, Markdown-Parser für `.md` → Rohtext.
2. **Hashing + Dedup-Check**: SHA-256 über Rohinhalt. Existiert `(content_hash, event_type)` bereits → Event wird verworfen, Hinweis "bereits bekannt seit `<Datum>`".
3. **Event anhängen** (`status=pending`).
4. **`process_pending()`** — pro pending Event:
   - Prefrontal-Agent (qwen3:8b) und Temporal-Agent (bge-m3) laufen parallel via `asyncio.gather()` — beide nehmen nur den Rohtext als Eingabe, sind also unabhängig voneinander:
     - Prefrontal: Klassifikation + bis zu 5 Entitäten (Prompt-Stil wie Phase-1-Benchmark).
     - Temporal: Embedding → FAISS `add_with_ids` (Vektor vorher L2-normalisiert).
   - Graph-Engine läuft danach sequentiell (braucht die Ergebnisse beider Agenten): Dokument-Node anlegen; pro Entität `concept`-Node finden-oder-anlegen + `mentions`-Edge.
   - Event → `processed`.
   - Exception in einem Schritt → Event → `failed` + Fehlermeldung, Pipeline läuft mit nächstem Event weiter (Spec §4.1 Regel 4).

`cbks rebuild`: leert `nodes`/`edges`/`node_vectors`/FAISS-Index, spielt danach das gesamte Event-Log erneut durch dieselbe Verarbeitungslogik. Event-Log selbst bleibt unberührt (Append-only).

`cbks ask "frage"`: Embedding der Frage (bge-m3) → FAISS-Suche Top-k → Kontext + Frage an qwen3:8b → `{answer, sources}`.

`cbks search "begriff"`: reine semantische Suche, Top-10, ohne LLM-Antwort.

## CLI-Befehle (Spec §5.1)

| Befehl | Funktion |
|---|---|
| `cbks add <datei>` | Parsing → Hash-Check → Event anhängen → `process_pending()` |
| `cbks note "text"` | Wie `add`, Text direkt statt Datei |
| `cbks ask "frage"` | RAG-Antwort mit Quellen |
| `cbks search "begriff"` | Semantische Suche, Top-10 |
| `cbks show <node_id>` | Node-Details + direkte Nachbarn |
| `cbks stats` | Anzahl Nodes/Edges/Events, pending/failed-Zähler |
| `cbks retry` | Alle `failed`-Events erneut durch `process_pending()` |
| `cbks rebuild` | Graph + FAISS aus Event-Log neu aufbauen |
| `cbks backup` | Ruft `data/backup.sh` (Phase 1) auf |

## Fehlerbehandlung

- Append-only Event-Log, nie geändert/gelöscht außer `status`/`error`/`processed_at`.
- Fehlgeschlagene Events blockieren nicht die Pipeline (siehe oben), `cbks retry` verarbeitet sie erneut.
- Bei Neustart: keine offene Frage — es gibt keinen Hintergrundprozess (Entscheidung 1), Events werden ausschließlich durch CLI-Aufrufe verarbeitet.

## Testing-Strategie

- TDD pro Service-Modul (`event_log`, `hashing`, `graph_backend`, `faiss_index`) mit echten SQLite-/FAISS-Testdateien (keine Mocks für Storage-Schicht).
- Ollama-Aufrufe (Prefrontal/Temporal) hinter einer dünnen Interface-Abstraktion, in Unit-Tests mit Fake-Implementierung getestet (echte LLM-Calls wären zu langsam/nicht-deterministisch für den regulären Testlauf).
- Ein echter End-to-End-Test läuft gegen die laufende native Ollama-Instanz (analog zum Benchmark-Skript aus Phase 1) und deckt exakt den Meilenstein ab: `cbks add <Testdokument>` gefolgt von `cbks ask <Frage zum Dokument>`.

## Out of Scope (Phase 2)

- FAISS-Snapshots (siehe Entscheidung 3).
- REST-API-Routen (Phase 3, Grundgerüst existiert nicht, `api/`-Ordner bleibt vorerst leer).
- Entity-Resolution/Deduplizierung auf semantischer Ebene (Phase 3, bekannte Lücke aus Spec §9).
- Frontend, Sentiment-Analyse, Decay-Ranking-UI (Phase 3+).
- Hermes-Dispatcher als eigenständiger Hintergrundprozess (siehe Entscheidung 1 — ggf. relevant für Phase 3, wenn die REST-API asynchrone Ingestion braucht).
