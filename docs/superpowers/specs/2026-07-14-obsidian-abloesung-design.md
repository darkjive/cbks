# Design: Obsidian-Ablösung — Vault als Datenquelle + Markdown-Editor

Datum: 2026-07-14 · Status: vom Nutzer freigegeben (Design-Dialog)

## Ziel

Obsidian wird durch cbks ersetzt. Ein einfacher Ordner mit Markdown-Dateien
(`~/Dev/vault`, ein privates Git-Repo) wird die alleinige Datenquelle; cbks
bekommt einen vollwertigen Markdown-Editor mit Live-Preview. Der Übergang läuft
schrittweise: Obsidian zeigt währenddessen parallel auf denselben Ordner.

**Nutzer-Entscheidungen aus dem Design-Dialog:**

- Dateien = Wahrheit; SQLite/FAISS nur abgeleiteter, rebuildbarer Index
- Editor-Typ: Live-Preview wie Obsidian (CodeMirror 6)
- v1-Must-haves: Wiki-Links, Dateibaum + Suche, Bilder & Anhänge, Backlinks-Panel
- Migration: schrittweise (aktive Notizen zuerst, alter Vault bleibt Archiv)
- Index-Ansatz A: API-zentriert mit synchronem Re-Index + Rescan-Kommando
  (Dateisystem-Watcher bewusst NICHT in v1)

## 1. Datenmodell — Inversion der Source of Truth

Heute ist SQLite die Quelle der Wahrheit (Notiz-Content liegt in der DB, der
Export ist eine Kopie). Neu:

- **`CBKS_VAULT_DIR`** (neue Config, env-gesteuert wie alle `CBKS_*`): Wurzel
  des Vaults. Alles unterhalb ist Nutzer-Territorium; cbks indexiert es.
- **Identität über Frontmatter-`id`.** Jede Notiz-Datei trägt ihre Node-ID im
  YAML-Frontmatter. Die von `cbks export` erzeugten Dateien haben sie bereits.
  Datei ohne `id` → Indexer generiert eine UUID und schreibt sie einmalig ins
  Frontmatter (der einzige Fall, in dem der Indexer eine Datei verändert).
  Dadurch überleben Notizen Rename/Move ohne Identitätsverlust.
- **Upsert statt Insert.** Der Node speichert seinen relativen `source_path`
  (in `metadata`). Gleiche `id` = gleicher Node: Content, Titel, Embedding,
  Entities und Kanten werden neu berechnet, nicht dupliziert. Heutiges
  Verhalten (jeder Ingest = neuer Node) gilt nur noch für Nicht-Vault-Ingest.
- **Löschung:** Datei weg → Node weg (bestehendes `delete_node`), beim Rescan
  erkannt über die Menge der bekannten `source_path`s.
- **Änderungserkennung** über vorhandenen `content_hash`; unveränderte Dateien
  überspringt der Rescan.
- Entities, Kanten, Aktivierungs-/Decay-Gewichte bleiben DB-only (abgeleitete
  Daten, kein Bestandteil der Dateien).

## 2. Backend

### Neue Module

- **`services/vault_fs.py`** — Dateioperationen im Vault:
  - Pfadauflösung mit Traversal-Schutz (jeder Pfad wird gegen `CBKS_VAULT_DIR`
    resolved; Ausbruch → Fehler)
  - `list_tree()`, `read_file()`, `write_file()`, `rename()`, `delete()`,
    `save_attachment()` (Ablage unter `attachments/`)
- **`services/vault_index.py`** — Indexierung:
  - `index_file(path)`: Frontmatter parsen (vorhandenes `parse_frontmatter`),
    id sicherstellen, klassifizieren + embedden (vorhandene Pipeline), Node
    upserten, Kanten/Entities neu ableiten
  - `rescan(full: bool)`: Vault durchlaufen (Muster von `iter_vault_files`),
    nur geänderte Dateien indexieren, verwaiste Nodes löschen

### API-Endpoints (FastAPI, deutsch bleibende Fehlermeldungen)

| Endpoint | Zweck |
|---|---|
| `GET /vault/tree` | Ordner-/Dateibaum |
| `GET /vault/file?path=` | Datei lesen (liefert Content + content_hash) |
| `PUT /vault/file` | Schreiben; indexiert synchron; erwartet den gelesenen `content_hash` — extern geändert → **409** |
| `POST /vault/rename` | Umbenennen/Verschieben (Node folgt via Frontmatter-id) |
| `DELETE /vault/file` | Löschen (Datei + Node) |
| `POST /vault/attachment` | Bild/Anhang-Upload nach `attachments/` |
| `POST /vault/rescan` | Rescan anstoßen |
| `GET /vault/backlinks?path=` | Eingehende Links aus den Graph-Kanten |
| `GET /vault/search?q=` | Volltextsuche (v1: SQL LIKE über content; FTS5 später bei Bedarf) |

### Verhalten

- **Rescan beim Serverstart** (Config-Flag, default an) — fängt Obsidian-Edits
  und `git pull` ab.
- **CLI:** `cbks index [--full]`.
- **Schreibpfade umgestellt:** `cbks note`/`add` erzeugen künftig .md-Dateien
  im Vault-Ordner `inbox/` und indexieren sie. Agent-Anreicherungen
  (z. B. "## Update"-Abschnitte) schreiben in die Datei, dann Re-Index —
  damit landet alles im Git-Sync.
- `cbks export` bleibt als einmalige Migrationshilfe erhalten, verliert danach
  seinen Zweck.
- Event-Log bleibt bestehen; Vault-Events referenzieren den `source_path`.

## 3. Frontend — Editor-Tab

Vierter Tab "Editor" neben Graph/Analyse/Chat (bestehendes `view`-State-Muster
in `App.tsx`). Layout: links Dateibaum + Suche, Mitte Editor, rechts
einklappbares Backlinks-Panel.

- **CodeMirror 6** (`@codemirror/lang-markdown`); Live-Preview über
  Decorations: Markup-Zeichen (`**`, `#`, `[[`) nur auf der Cursor-Zeile
  sichtbar, sonst gerendert. Größter Einzelposten des Projekts, unabhängig vom
  Backend entwickelbar.
- **Wiki-Links:** `[[` öffnet Autocomplete (Notiz-Titel via API), Klick
  navigiert zur Notiz, toter Link bietet "Notiz anlegen".
- **Bilder & Anhänge:** Paste/Drag → Upload → `![](attachments/…)` eingefügt,
  inline gerendert (Widget-Decoration). PDFs werden verlinkt.
- **Autosave** debounced (~1 s nach Tipppause) über `PUT /vault/file`;
  409-Antwort → Hinweis "Datei extern geändert" mit Neu-laden-Option, kein
  stilles Überschreiben.
- **Suche** in der Seitenleiste über `GET /vault/search`.

## 4. Migration & Obsidian-Übergang

1. Vault enthält bereits die 25 exportierten Notizen (inkl. Frontmatter-ids —
   der Export war rückwirkend die Migration des DB-Bestands).
2. Schrittweise: aktive Notizen aus dem alten Obsidian-Vault nach
   `~/Dev/vault` kopieren; `cbks index` oder Serverstart nimmt sie auf.
   Alter Vault bleibt unangetastet als Archiv.
3. Übergangsphase: Obsidian zusätzlich auf `~/Dev/vault` zeigen lassen — beide
   Editoren arbeiten auf denselben Dateien, Rescan versöhnt.
4. Obsidian löschen, sobald der cbks-Editor im Alltag trägt.

## 5. Fehlerbehandlung

- Pfad-Traversal → 400, deutscher Fehlertext (bestehendes Muster).
- Schreibkonflikt → 409 (siehe oben).
- Nicht indexierbare Datei (kaputtes Encoding o. ä.) → Datei bleibt
  unangetastet, Fehler landet im Event-Log als failed (bestehendes
  Retry-Muster), Rescan läuft weiter.
- Ollama nicht erreichbar → Datei wird gespeichert, Indexierung als failed
  geloggt, `cbks retry`/nächster Rescan holt sie nach. Speichern darf nie am
  LLM scheitern.

## 6. Tests

Nach bestehendem Muster (Mock-Ollama-Fixtures, `CBKS_DATA_DIR`/
`CBKS_VAULT_DIR` = tmp_path):

- vault_fs: Traversal-Schutz, CRUD, Attachment-Ablage
- vault_index: id-Vergabe in Frontmatter, Upsert-Idempotenz (2× indexieren =
  1 Node), Rename behält Node, Delete räumt Node ab, Rescan überspringt
  Unverändertes
- API: alle Endpoints inkl. 409-Konflikt und Traversal-400
- CLI: `cbks index`, umgestelltes `note` (Datei entsteht in `inbox/`)
- Frontend-Gate: `npm run build` (tsc) + `npm run lint`

## 7. Phasen

| Phase | Inhalt | Lieferbar |
|---|---|---|
| 1 | Config `CBKS_VAULT_DIR`, `vault_fs`, `vault_index` (Upsert, Rescan), CLI `cbks index` | Vault wird indexiert, Graph zeigt Vault-Notizen |
| 2 | API-Endpoints (tree/file/rename/delete/attachment/rescan/backlinks/search) | Backend komplett editorfähig |
| 3 | Editor-Basis: Tab, Dateibaum, Laden/Speichern mit Autosave + 409, Syntax-Highlighting | Notizen in cbks editierbar |
| 4 | Live-Preview-Decorations + Wiki-Links (Autocomplete, Navigation, tote Links) | Obsidian-Gefühl |
| 5 | Bilder/Anhänge, Backlinks-Panel, Volltextsuche | v1-Featureset komplett |
| 6 | Schreibpfade umstellen (`note`/`add`/Agenten → Vault), Rescan bei Serverstart, Spec v1.2 aktualisieren | Obsidian löschbar |

**Bewusst nicht in v1** (Phase 2+ nach Bedarf): Dateisystem-Watcher,
Editor vom Handy (Tailscale/Binding-Thema), Git-Auto-Commit, FTS5-Suche.
