# CBKS Phase 3.2 — Frontend-Grundgerüst (Design)

## Kontext

Phase 3.1 (REST-API, 9 Routen) ist abgeschlossen und getestet (93 Tests grün). Laut `CBKS_SPEC_v1.1.md` ist der nächste Phase-3-Sub-Schritt das Frontend-Grundgerüst: React + D3.js, 2D-Graph zuerst. `frontend/` existiert bisher nur als leeres Verzeichnis (`.gitkeep`).

Die bestehende REST-API hat **keinen Endpunkt, der den gesamten Graphen (Nodes + Edges) liefert** — nur `/nodes/{id}` (1-Hop-Nachbarn), `/search`, `/stats`. Für eine D3-Graph-Visualisierung wird ein neuer Endpoint benötigt.

## Ziel

Ein funktionierendes Dashboard, das alle 9 bestehenden REST-Routen plus einen neuen `/graph`-Endpoint bedient:
- 2D-Force-Directed-Graph des gesamten Wissensgraphen (Zoom/Pan, Klick → Detail-Panel)
- Suche (`GET /search`), Ergebnisse im Graph hervorgehoben
- Ask-Panel (`POST /ask`), zeigt Antwort + Quellen
- Upload (Dokument via `POST /documents`, Notiz via `POST /notes`)
- Stats-Anzeige (`GET /stats`)
- Retry/Rebuild/Backup als einfache Buttons

## Architektur

- **Backend-Erweiterung:** Neuer Endpoint `GET /graph` liefert `{nodes: Node[], edges: Edge[]}` komplett (kein Filter/Pagination — für aktuellen Datenumfang unkritisch).
- **Frontend:** Eigenständige SPA unter `frontend/` — Vite + React + TypeScript.
- **Dev-Workflow:** Nur Vite-Dev-Server (Port 5173), Proxy leitet alle API-Pfade an `http://127.0.0.1:8000` weiter. Kein Production-Build, kein Docker/nginx-Service, keine CORS-Konfiguration im Backend in diesem Schritt.
- **D3-Integration:** `GraphCanvas`-Komponente hält einen Ref auf `<svg>`; D3 rendert Force-Simulation, Nodes, Edges, Zoom/Pan komplett imperativ hinein (useEffect bei Datenänderung). React kontrolliert nicht die einzelnen SVG-Kindelemente. Klicks auf Nodes rufen einen React-Callback (`onNodeSelect`).

## Backend-Änderung: `GET /graph`

- `backend/services/graph_backend.py`: neue Methoden `get_all_nodes() -> list[Node]`, `get_all_edges() -> list[Edge]` (direktes `SELECT * FROM nodes` / `SELECT * FROM edges`, analog zu bestehenden Methoden).
- `backend/api_models.py`: neues Modell
  ```python
  class GraphResponse(BaseModel):
      nodes: list[Node]
      edges: list[Edge]
  ```
- `backend/main.py`: neue Route
  ```python
  @app.get("/graph", response_model=GraphResponse)
  def get_graph() -> GraphResponse: ...
  ```
- TDD: Test zuerst (analog zu bestehenden Route-Tests in `backend/tests/`), dann Implementierung.

## Frontend-Projektstruktur

```
frontend/
├── index.html
├── vite.config.ts          # Dev-Proxy → 127.0.0.1:8000
├── package.json
├── tsconfig.json
└── src/
    ├── main.tsx
    ├── App.tsx              # Layout: Sidebar (Suche/Ask/Upload/Stats) + GraphCanvas
    ├── api/
    │   ├── client.ts        # fetch-Wrapper: X-API-Key aus localStorage, wirft ApiError bei Non-2xx
    │   └── types.ts         # Node, Edge, GraphResponse, AskResponse, SearchHitResponse, StatsResponse — gespiegelt aus api_models.py
    ├── components/
    │   ├── GraphCanvas.tsx       # D3-Force-Graph (imperativ)
    │   ├── NodeDetailPanel.tsx   # Node + Neighbors nach Klick (GET /nodes/{id})
    │   ├── SearchBar.tsx         # GET /search, Treffer im Graph hervorheben
    │   ├── AskPanel.tsx          # POST /ask, Antwort + Sources
    │   ├── UploadForm.tsx        # POST /documents (Datei) und POST /notes (Text)
    │   ├── StatsBar.tsx          # GET /stats
    │   └── ApiKeyPrompt.tsx      # einmaliges Eingabefeld, speichert in localStorage
    └── styles/
        └── global.css       # eigenes, schlankes CSS — kein UI-Framework
```

Node-Typ-Farbcodierung (7 Typen: `concept, document, task, note, project, commit, screenshot`) nutzt bei der Implementierung die `dataviz`-Skill-Palette für kategoriale Farben.

## Datenfluss & State

- Kein globaler State-Manager (Redux/Zustand) — React State + Context nur für den API-Key.
- `App.tsx` lädt beim Mount einmal `GET /graph`; nach `POST /documents`, `POST /notes`, `POST /retry`, `POST /rebuild` wird `/graph` neu geladen (kein Diffing).
- `SearchBar`/`AskPanel` haben eigenen lokalen State; Hervorhebung im Graph läuft über Prop `highlightedNodeIds: string[]` an `GraphCanvas`, das D3 als CSS-Klasse umsetzt.
- Klick auf Node → `onNodeSelect(id)` → `App` lädt `GET /nodes/{id}` → State für `NodeDetailPanel`.

## Error Handling & Auth

- `api/client.ts`: zentraler Wrapper wirft bei Non-2xx einen typisierten `ApiError`; jede Komponente fängt lokal ab, zeigt einfache Fehlermeldung (kein globales Toast-System).
- API-Key: `ApiKeyPrompt` erscheint bei 401/403-Antwort oder wenn `localStorage` beim Start leer ist. Key wird bei jedem Request als `X-API-Key`-Header mitgeschickt, falls vorhanden.

## Testing

- Backend: neuer Test für `GET /graph` (TDD, analog zu bestehenden Route-Tests).
- Frontend: kein Test-Setup (kein Vitest/RTL) in diesem Grundgerüst-Schritt — bewusste Scope-Entscheidung. Nachziehbar, sobald Frontend-Logik komplexer wird (z.B. D3-Datenverarbeitung).

## Out of Scope (bewusst zurückgestellt)

- Production-Build/Docker-Integration des Frontends, CORS-Konfiguration
- Pagination/Filterung im `/graph`-Endpoint
- Frontend-Tests
- LOD-Visualisierung (Makro/Meso/Micro), Decay-Ranking im UI, Entity Resolution — spätere Phase-3-Aufgaben laut `CBKS_SPEC_v1.1.md`
