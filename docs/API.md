# API-Referenz

Das Backend ist eine FastAPI-App. **Die verbindliche, immer aktuelle Referenz
ist das generierte OpenAPI-Schema** — bei laufendem Server:

- Swagger UI: <http://127.0.0.1:8000/docs>
- ReDoc: <http://127.0.0.1:8000/redoc>
- Schema: <http://127.0.0.1:8000/openapi.json>

Diese Seite gibt den Überblick; Request-/Response-Felder stehen im Schema.

## Authentifizierung

Ist `CBKS_API_KEY` gesetzt, verlangt **jeder** Endpunkt den Header
`X-API-Key: <key>` (globale Dependency, `backend/auth.py`). Ist die Variable
leer oder ungesetzt, ist die API offen — siehe [SECURITY.md](../SECURITY.md).

Es gibt bewusst **kein** `/api/v1`-Präfix: eine Versionierung ohne echte
Versionsstrategie wäre Alibi.

## Ingest

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/documents` | Datei hochladen (multipart). PDF/Bild werden geparst, ggf. per VLM-OCR. Antwortet mit `duplicate: true`, wenn der Content-Hash bekannt ist |
| `POST` | `/notes` | Notiz aus Text anlegen |

## Abfrage

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/ask` | RAG-Antwort über den Graphen. Unterstützt `history` für Multi-Turn |
| `GET` | `/search?q=` | Semantische Suche (FAISS) |
| `GET` | `/nodes/{id}` | Einzelnen Knoten samt Metriken laden |
| `DELETE` | `/nodes/{id}` | Knoten löschen (inkl. zugehöriger Events) |
| `GET` | `/nodes/{id}/audio` | Knoten als WAV vorlesen. **`503`, wenn TTS nicht installiert ist** |
| `GET` | `/graph` | Kompletter Graph (Knoten + Kanten) für die 3D-Ansicht |
| `GET` | `/stats` | Zähler für Knoten, Kanten und Events |
| `GET` | `/events` | Event-Log, paginierbar |

## Analyse

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/analysis/timeline` | Knotenanzahl über die Zeit |
| `GET` | `/analysis/emotions` | Emotionsverlauf über die Zeit |
| `GET` | `/analysis/patterns` | Muster-Report |
| `GET` | `/analysis/recurring` | Wiederkehrende Themen |
| `POST` | `/analyze/contradictions` | Widersprüche zwischen Notizen suchen (Pineal-Agent) |

## Wartung

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/retry` | Fehlgeschlagene Events erneut verarbeiten |
| `POST` | `/rebuild` | Graph vollständig aus dem Event-Log neu aufbauen |
| `POST` | `/dedupe` | Entitäten zusammenführen (Entity Resolution) |
| `POST` | `/backup` | Backup-Skript aus `CBKS_BACKUP_SCRIPT` ausführen |

## Vault

Datei-Endpunkte arbeiten relativ zu `CBKS_VAULT_DIR` und sind per
`vault_fs._resolve()` gegen Path-Traversal abgesichert.

| Methode | Pfad | Zweck |
|---|---|---|
| `GET` | `/vault/default-path` | Vorbelegter Vault-Pfad (`CBKS_VAULT_PATH`) |
| `POST` | `/vault/scan` | Scan-Job starten; nimmt einen Dateisystempfad entgegen |
| `GET` | `/vault/scan/{job_id}` | Fortschritt des Scan-Jobs abfragen |
| `POST` | `/vault/rescan?full=` | Bereits importierten Vault neu indexieren (`full=true` erzwingt Vollscan) |
| `GET` | `/vault/tree` | Dateibaum |
| `GET` `/` `PUT` | `/vault/file?path=` | Datei lesen / schreiben. Schreiben nutzt `content_hash` für optimistisches Locking (`409` bei Konflikt) |
| `DELETE` | `/vault/file?path=` | Datei löschen |
| `POST` | `/vault/rename` | Datei umbenennen/verschieben |
| `POST` | `/vault/attachment` | Anhang nach `attachments/` hochladen |
| `GET` | `/vault/backlinks?path=` | Ein- und ausgehende Wiki-Links einer Datei |
| `GET` | `/vault/search?q=` | Volltextsuche über die indexierten Vault-Inhalte |

## Fehlerformat

Fehler kommen im FastAPI-Standard, die Meldungen sind **deutsch**:

```json
{ "detail": "Node nicht gefunden" }
```

Gängige Codes: `401` (API-Key fehlt/falsch), `404` (nicht gefunden),
`409` (Vault-Schreibkonflikt), `422` (kein vorlesbarer Inhalt),
`503` (TTS nicht installiert).
