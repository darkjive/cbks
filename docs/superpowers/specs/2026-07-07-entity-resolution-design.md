# Entity Resolution — Design (Phase 3.3)

**Datum:** 2026-07-07
**Status:** Freigegeben
**Schließt bekannte Lücke:** „Entity Resolution" aus `CBKS_SPEC_v1.1.md` §9 — dasselbe Konzept aus verschiedenen Quellen („DMT" im Tagebuch vs. „Dimethyltryptamin" im PDF) wird bislang nicht zu einem Node zusammengeführt.

## Problem

Concept-Nodes entstehen im Dispatcher (`backend/services/dispatcher.py`). Dedup passiert heute ausschließlich über `GraphBackend.find_node_by_title()` — ein exakter, case-insensitiver Titelvergleich. Sobald das LLM dieselbe Entität leicht anders benennt, entsteht ein Duplikat-Node, und Kanten verteilen sich auf mehrere Nodes statt sich an einem zu sammeln. Das verschlechtert Nachbarschafts-Antworten (`/nodes/{id}`), RAG-Kontext und die Graph-Visualisierung.

## Entscheidungen (aus Brainstorming 2026-07-07)

| # | Entscheidung | Begründung |
|---|---|---|
| 1 | Resolution beim Ingest **und** als Batch-Befehl | Neue Duplikate verhindern + Bestand bereinigen |
| 2 | Matching: Embedding-Kandidatensuche + LLM-Bestätigung bei Grenzfällen | Schnell im Normalfall, präzise im Grenzfall |
| 3 | Merges vollautomatisch, alte Titel bleiben als Aliases erhalten | Solo-Dev-Workflow, keine Review-UI nötig |
| 4 | Titel-Vektoren als BLOB in SQLite, Cosine per numpy (kein zweiter FAISS-Index) | Boring technology; Brute-Force reicht bis ~100k Konzepte (YAGNI) |
| 5 | Bei LLM-Fehler im Grenzfall: **kein** Merge | Lieber ein Duplikat (holt der nächste dedupe-Lauf) als ein Fehl-Merge |

## Architektur

### Neuer Service: `backend/services/entity_resolver.py`

```
EntityResolver(graph: GraphBackend, temporal_agent: TemporalAgent, llm_client: LLMClient)
    resolve(title: str) -> Node | None      # Ingest-Pfad: existierendes Konzept oder None
    register(node: Node) -> None             # Titel embedden + Vektor speichern (nach add_node)
    dedupe_all() -> MergeSummary              # Batch-Pfad: {checked: int, merged: int}
```

Dependency Injection wie beim Dispatcher; `LLMClient` ist das bestehende Protocol aus `prefrontal.py`.

### Neue Tabelle (in `backend/storage/sqlite_db.py::init_db`)

```sql
CREATE TABLE IF NOT EXISTS concept_title_vectors (
    node_id TEXT PRIMARY KEY REFERENCES nodes(id),
    vector  BLOB NOT NULL
);
```

Vektor: `numpy.ndarray.tobytes()` (float32), Dimension implizit durch bge-m3.

### GraphBackend-Erweiterung

```
merge_nodes(keep_id: str, remove_id: str) -> None
```

Transaktional (Muster wie `clear_all`): Kanten von `remove_id` auf `keep_id` umhängen, Alias in Metadata des Gewinners schreiben, Verlierer-Node und dessen Titel-Vektor löschen, NetworkX-Cache aktualisieren. Rollback bei Fehler.

### Dispatcher-Änderung

In `process_event()`: `self.graph.find_node_by_title(entity_title)` → `self.resolver.resolve(entity_title)`. Bei `None`: Konzept-Node anlegen wie bisher, danach `resolver.register(node)`.

### CLI + API

- `cbks dedupe` — ruft `resolver.dedupe_all()`, gibt `checked`/`merged` aus.
- `POST /dedupe` — Route in `backend/main.py`, Response-Model `DedupeResponse {checked: int, merged: int}` in `api_models.py`. Auth wie alle Routen (globale Dependency).

## Matching-Logik (`resolve`)

1. **Schnellpfad:** exakter case-insensitiver Vergleich gegen Titel **und** `metadata["aliases"]` aller Konzept-Nodes (Unicode-Lowercase via `str.lower()`, wie in `find_node_by_title`). Diese Prüfung macht der Resolver selbst; `find_node_by_title` bleibt unverändert.
2. **Embedding:** Titel per `TemporalAgent.embed()` embedden, Cosine-Similarity per numpy gegen alle Vektoren aus `concept_title_vectors`.
3. **Entscheidung** (Schwellwerte als Modul-Konstanten, keine Config):
   - `>= 0.92` → Match, Konzept zurückgeben.
   - `0.75 – 0.92` → LLM-Bestätigung: „Bezeichnen ‚X' und ‚Y' dieselbe Entität? Antworte ausschließlich als JSON: {\"same\": true/false}". Nur bei `same: true` → Match.
   - `< 0.75` → kein Match, `None`.
4. Bei mehreren Kandidaten über dem Schwellwert gewinnt der ähnlichste.

## Merge-Semantik

- Der **ältere** Node (`creation_time`) überlebt und behält ID und Titel.
- Verlierer-Titel wird in `metadata["aliases"]` des Gewinners aufgenommen (Liste, dedupliziert); bestehende Aliases des Verlierers werden übernommen.
- Kanten des Verlierers (als source oder target) werden auf den Gewinner umgehängt. Entstünde eine Dublette (gleiche source/target/relation_type wie eine bestehende Kante), wird die Verlierer-Kante gelöscht statt umgehängt.
- Self-Loops (Kante Gewinner→Gewinner nach Umhängen) werden gelöscht.
- Verlierer-Node und sein Eintrag in `concept_title_vectors` werden gelöscht.
- Alles in einer Transaktion; Rollback bei Fehler.

## Batch-Dedupe (`dedupe_all`)

Konzept-Nodes chronologisch (nach `creation_time`) durchgehen. Für jeden Node: gegen die bereits „behaltenen" Nodes mit derselben Matching-Logik (Schritte 2–3) prüfen. Bei Match: `merge_nodes(älterer, neuerer)`. Idempotent — ein zweiter Lauf findet keine Merges mehr.

## Fehlerbehandlung

- LLM nicht erreichbar oder ungültiges JSON im Grenzfall → als „kein Match" behandeln (kein Merge). Der Ingest darf dadurch **nicht** fehlschlagen.
- Embedding-Fehler beim Ingest → Exception propagiert wie heute (Event wird `failed`, Retry/Rebuild als Recovery — bestehendes Muster).
- `clear_all()` leert auch `concept_title_vectors` (Rebuild-Kompatibilität). Beim Rebuild läuft die Resolution automatisch mit, da sie im Ingest-Pfad sitzt.

## Nicht in Scope (YAGNI)

- Kein zweiter FAISS-Index für Titel (Brute-Force-Cosine reicht).
- Keine Review-Queue / kein Undo einzelner Merges (Recovery: `cbks rebuild`).
- Keine konfigurierbaren Schwellwerte.
- Aliases fließen nicht in die Suche ein (Suche läuft über Dokument-Embeddings; Aliases sind reine Metadaten + Schnellpfad-Treffer beim Ingest).
- Kein Frontend-UI für Dedupe (CLI/API reicht; UI-Integration ggf. später mit dem Graph-Styling).

## Tests (TDD)

| Bereich | Testfälle |
|---|---|
| EntityResolver | Exakt-Match (Schnellpfad), Alias-Match, High-Similarity-Match ohne LLM, Grenzfall mit LLM-Ja/Nein, kein Match unter Schwelle, LLM-Fehler → kein Match |
| GraphBackend.merge_nodes | Kanten-Rewire (source+target), Alias-Übernahme, Dubletten-Kanten gelöscht, Self-Loop gelöscht, Vektor gelöscht, Rollback bei Teilfehler |
| Dispatcher-Integration | Zwei Ingests mit ähnlichen Entitäten → ein Konzept-Node mit zwei mentions-Kanten |
| dedupe_all | Bestand mit Duplikaten wird gemerged, Idempotenz (zweiter Lauf: merged=0) |
| API | POST /dedupe liefert 200 + checked/merged |
| CLI | cbks dedupe gibt Zusammenfassung aus |

Fake-Embeddings und Fake-LLM als Test-Doubles (bestehendes Muster aus den Dispatcher-Tests).
