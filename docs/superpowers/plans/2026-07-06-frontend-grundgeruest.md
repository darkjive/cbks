# Frontend-Grundgerüst (Phase 3.2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Vite+React+TypeScript-Dashboard (`frontend/`), das den CBKS-Wissensgraphen als 2D-Force-Graph (D3) zeigt und alle 9 bestehenden REST-Routen plus einen neuen `GET /graph`-Endpoint bedient.

**Architecture:** Neuer `GET /graph`-Endpoint im FastAPI-Backend liefert den kompletten Graphen. Das Frontend ist eine SPA, die per Vite-Dev-Proxy gegen das laufende Backend (`127.0.0.1:8000`) spricht. D3 rendert den Graphen komplett imperativ in ein `<svg>` (React kontrolliert nur den Container-Ref), alle anderen Panels (Suche, Ask, Upload, Stats) sind normale React-Komponenten, die den zentralen `fetch`-Wrapper aus `src/api/client.ts` nutzen.

**Tech Stack:** Backend: FastAPI, Pydantic, sqlite3 (bestehend). Frontend: Vite, React 18, TypeScript, d3 (d3-force, d3-zoom, d3-selection). Kein UI-Framework, kein State-Manager, kein Frontend-Test-Setup (bewusste Scope-Entscheidung laut Spec).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-06-frontend-grundgeruest-design.md`
- Kein Production-Build/Docker/CORS für das Frontend in diesem Schritt — nur `npm run dev` gegen das lokale Backend.
- Backend-Änderungen folgen TDD (Test zuerst, wie in allen bisherigen Phase-3.1-Tasks).
- Frontend-Tasks haben kein automatisiertes Test-Setup; jeder Frontend-Task endet mit einem manuellen Verifikationsschritt im Browser (`npm run dev`, Backend muss laufen).
- Alle Backend-Tests müssen am Ende weiterhin grün sein: `.venv/bin/python -m pytest backend -q`.
- Node-Typen (`type`-Feld): `concept, document, task, note, project, commit, screenshot`. Farbcodierung nutzt die `dataviz`-Skill-Palette.

---

### Task 1: GraphBackend — `get_all_nodes` / `get_all_edges`

**Files:**
- Modify: `backend/services/graph_backend.py`
- Test: `backend/tests/test_graph_backend.py`

**Interfaces:**
- Produces: `GraphBackend.get_all_nodes() -> list[Node]`, `GraphBackend.get_all_edges() -> list[Edge]`

- [ ] **Step 1: Fehlschlagenden Test schreiben**

Füge in `backend/tests/test_graph_backend.py` an das Ende an:

```python
def test_get_all_nodes_returns_every_node(graph):
    graph.add_node(make_node("n1", "FAISS"))
    graph.add_node(make_node("n2", "NetworkX"))

    nodes = graph.get_all_nodes()

    assert {n.id for n in nodes} == {"n1", "n2"}


def test_get_all_edges_returns_every_edge(graph):
    graph.add_node(make_node("n1", "FAISS"))
    graph.add_node(make_node("n2", "NetworkX"))
    graph.add_edge(make_edge("e1", "n1", "n2"))

    edges = graph.get_all_edges()

    assert len(edges) == 1
    assert edges[0].id == "e1"
    assert edges[0].source == "n1"
    assert edges[0].target == "n2"
```

- [ ] **Step 2: Test ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_graph_backend.py -v -k "get_all"`
Expected: FAIL mit `AttributeError: 'GraphBackend' object has no attribute 'get_all_nodes'`

- [ ] **Step 3: Implementieren**

In `backend/services/graph_backend.py` nach `get_node` (nach Zeile 55) einfügen:

```python
    def get_all_nodes(self) -> list[Node]:
        rows = self._conn.execute("SELECT * FROM nodes").fetchall()
        return [self._row_to_node(row) for row in rows]
```

Nach `add_edge` (nach Zeile 51, vor `get_node`) einfügen:

```python
    def get_all_edges(self) -> list[Edge]:
        rows = self._conn.execute("SELECT * FROM edges").fetchall()
        return [self._row_to_edge(row) for row in rows]
```

Und am Ende der Klasse (nach `_row_to_node`, Zeile 116-124) einen analogen Helper ergänzen:

```python
    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> Edge:
        return Edge(
            id=row["id"], source=row["source"], target=row["target"],
            relation_type=row["relation_type"], strength=row["strength"],
            temporal_score=row["temporal_score"], emotional_score=row["emotional_score"],
            reinforcement_count=row["reinforcement_count"], creation_time=row["creation_time"],
            last_updated=row["last_updated"], metadata=json.loads(row["metadata"]),
        )
```

- [ ] **Step 4: Test ausführen, Erfolg verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_graph_backend.py -v -k "get_all"`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/services/graph_backend.py backend/tests/test_graph_backend.py
git commit -m "feat: GraphBackend.get_all_nodes/get_all_edges für Graph-Export"
```

---

### Task 2: `GET /graph`-Route

**Files:**
- Modify: `backend/api_models.py`
- Modify: `backend/main.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: `GraphBackend.get_all_nodes() -> list[Node]`, `GraphBackend.get_all_edges() -> list[Edge]` (Task 1)
- Produces: `GET /graph` → `GraphResponse { nodes: Node[], edges: Edge[] }`

- [ ] **Step 1: Fehlschlagenden Test schreiben**

Füge in `backend/tests/test_api.py` an das Ende an:

```python
def test_graph_returns_all_nodes_and_edges():
    client.post("/notes", json={"text": "Ein Text über Graphentheorie"})

    response = client.get("/graph")

    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) >= 1
    assert "edges" in body
```

- [ ] **Step 2: Test ausführen, Fehlschlag verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_api.py -v -k test_graph_returns`
Expected: FAIL mit 404 (Route existiert nicht)

- [ ] **Step 3: Implementieren**

In `backend/api_models.py` ergänzen (Imports `Edge` hinzufügen, ans Ende der Datei):

```python
from backend.models.edges import Edge


class GraphResponse(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
```

(Der `Edge`-Import gehört zu den bestehenden Imports oben, nicht als zweiter `from`-Block mittendrin — also `from backend.models.edges import Edge` zusammen mit `from backend.models.nodes import Node` in Zeile 5 ergänzen, und `GraphResponse` als neue Klasse ans Ende der Datei nach `BackupResponse` anhängen.)

In `backend/main.py`:
- Import ergänzen: `GraphResponse` zur bestehenden `from backend.api_models import (...)`-Liste hinzufügen (alphabetisch einsortiert).
- Route ergänzen (z.B. nach `stats()`, vor `retry()`):

```python
@app.get("/graph", response_model=GraphResponse)
def get_graph() -> GraphResponse:
    ctx = build_context()
    return GraphResponse(nodes=ctx.graph.get_all_nodes(), edges=ctx.graph.get_all_edges())
```

- [ ] **Step 4: Test ausführen, Erfolg verifizieren**

Run: `.venv/bin/python -m pytest backend/tests/test_api.py -v -k test_graph_returns`
Expected: PASS

- [ ] **Step 5: Gesamte Suite laufen lassen**

Run: `.venv/bin/python -m pytest backend -q`
Expected: alle Tests grün (94 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/api_models.py backend/main.py backend/tests/test_api.py
git commit -m "feat: GET /graph liefert kompletten Wissensgraphen (Nodes+Edges)"
```

---

### Task 3: Frontend-Projekt-Scaffold (Vite + React + TypeScript)

**Files:**
- Create: `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/index.html`
- Create: `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/styles/global.css`
- Delete: `frontend/.gitkeep` (wird durch echte Dateien ersetzt)

**Interfaces:**
- Produces: lauffähiges `npm run dev` in `frontend/`, Proxy `/documents,/notes,/ask,/search,/nodes,/stats,/retry,/rebuild,/backup,/graph` → `http://127.0.0.1:8000`

- [ ] **Step 1: Vite-Projekt scaffolden**

```bash
cd frontend
npm create vite@latest . -- --template react-ts
```
(Bei Nachfrage "Current directory is not empty" mit "Ignore files and continue" bestätigen — nur `.gitkeep` ist vorhanden.)

- [ ] **Step 2: D3 installieren**

```bash
npm install d3
npm install -D @types/d3
```

- [ ] **Step 3: Dev-Proxy in `vite.config.ts` konfigurieren**

`frontend/vite.config.ts` komplett ersetzen mit:

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_PATHS = [
  "/documents", "/notes", "/ask", "/search", "/nodes",
  "/stats", "/retry", "/rebuild", "/backup", "/graph",
];

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: Object.fromEntries(
      API_PATHS.map((path) => [path, "http://127.0.0.1:8000"])
    ),
  },
});
```

- [ ] **Step 4: Platzhalter-App schreiben**

`frontend/src/App.tsx`:

```tsx
export function App() {
  return (
    <div className="app">
      <h1>CBKS</h1>
      <p>Grundgerüst geladen.</p>
    </div>
  );
}
```

`frontend/src/styles/global.css`:

```css
* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: system-ui, sans-serif;
  background: #0f1115;
  color: #e6e6e6;
}

.app {
  padding: 1rem;
}
```

`frontend/src/main.tsx` (vom Scaffold generierte Version anpassen, damit sie `App` und `global.css` importiert):

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/global.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

- [ ] **Step 5: `.gitkeep` entfernen**

```bash
rm frontend/.gitkeep
```

- [ ] **Step 6: Manuell verifizieren**

```bash
npm run dev
```
Expected: Vite startet auf `http://localhost:5173`, Browser zeigt "CBKS" / "Grundgerüst geladen." ohne Konsolenfehler.
Danach Dev-Server mit Ctrl+C stoppen.

- [ ] **Step 7: Commit**

```bash
cd $REPO
git add frontend/
git commit -m "feat: Vite+React+TypeScript-Scaffold für Frontend-Grundgerüst"
```

---

### Task 4: API-Client-Layer + API-Key-Handling

**Files:**
- Create: `frontend/src/api/types.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/ApiKeyContext.tsx`
- Create: `frontend/src/components/ApiKeyPrompt.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: Backend-Routen aus Task 2 und Phase 3.1 (`/graph`, `/nodes/{id}`, `/search`, `/ask`, `/documents`, `/notes`, `/stats`, `/retry`, `/rebuild`, `/backup`)
- Produces: `apiFetch<T>(path: string, init?: RequestInit) -> Promise<T>` (wirft `ApiError`), `useApiKey() -> [string | null, (key: string) => void]`, `<ApiKeyProvider>`, `<ApiKeyPrompt>`

- [ ] **Step 1: Typen definieren**

`frontend/src/api/types.ts`:

```typescript
export type NodeType =
  | "concept" | "document" | "task" | "note" | "project" | "commit" | "screenshot";

export interface Node {
  id: string;
  title: string;
  type: NodeType;
  content?: string | null;
  content_hash?: string | null;
  activation: number;
  confidence: number;
  emotional_weight: number;
  decay_rate: number;
  importance: number;
  creation_time: string;
  last_access: string;
  access_counter: number;
  metadata: Record<string, unknown>;
}

export interface Edge {
  id: string;
  source: string;
  target: string;
  relation_type: string;
  strength: number;
  temporal_score: number;
  emotional_score: number;
  reinforcement_count: number;
  creation_time: string;
  last_updated: string;
  metadata: Record<string, unknown>;
}

export interface GraphResponse {
  nodes: Node[];
  edges: Edge[];
}

export interface NodeDetailResponse {
  node: Node;
  neighbors: Node[];
}

export interface SearchHit {
  node: Node;
  score: number;
}

export interface AskResponse {
  answer: string;
  sources: string[];
}

export interface StatsResponse {
  events: Record<string, number>;
  graph: Record<string, number>;
}

export interface ProcessSummary {
  processed: number;
  failed: number;
}
```

- [ ] **Step 2: `ApiError` + `apiFetch` schreiben**

`frontend/src/api/client.ts`:

```typescript
export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    super(`API-Fehler ${status}`);
    this.status = status;
    this.body = body;
  }
}

let apiKey: string | null = null;

export function setApiKey(key: string | null): void {
  apiKey = key;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  const response = await fetch(path, { ...init, headers });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // kein JSON-Body vorhanden
    }
    throw new ApiError(response.status, body);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
```

- [ ] **Step 3: API-Key-Context schreiben**

`frontend/src/api/ApiKeyContext.tsx`:

```tsx
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { setApiKey } from "./client";

const STORAGE_KEY = "cbks-api-key";

interface ApiKeyContextValue {
  apiKey: string | null;
  setKey: (key: string) => void;
}

const ApiKeyContext = createContext<ApiKeyContextValue | null>(null);

export function ApiKeyProvider({ children }: { children: ReactNode }) {
  const [apiKey, setKeyState] = useState<string | null>(
    () => localStorage.getItem(STORAGE_KEY)
  );

  useEffect(() => {
    setApiKey(apiKey);
  }, [apiKey]);

  const setKey = (key: string) => {
    localStorage.setItem(STORAGE_KEY, key);
    setKeyState(key);
  };

  return (
    <ApiKeyContext.Provider value={{ apiKey, setKey }}>
      {children}
    </ApiKeyContext.Provider>
  );
}

export function useApiKey(): ApiKeyContextValue {
  const ctx = useContext(ApiKeyContext);
  if (!ctx) {
    throw new Error("useApiKey muss innerhalb von ApiKeyProvider verwendet werden");
  }
  return ctx;
}
```

- [ ] **Step 4: `ApiKeyPrompt`-Komponente schreiben**

`frontend/src/components/ApiKeyPrompt.tsx`:

```tsx
import { useState } from "react";
import { useApiKey } from "../api/ApiKeyContext";

export function ApiKeyPrompt() {
  const { setKey } = useApiKey();
  const [value, setValue] = useState("");

  return (
    <form
      className="api-key-prompt"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) {
          setKey(value.trim());
        }
      }}
    >
      <label htmlFor="api-key-input">CBKS_API_KEY (falls konfiguriert):</label>
      <input
        id="api-key-input"
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="leer lassen, falls kein Key gesetzt ist"
      />
      <button type="submit">Speichern</button>
    </form>
  );
}
```

- [ ] **Step 5: In `App.tsx` einhängen**

`frontend/src/App.tsx` ersetzen mit:

```tsx
import { ApiKeyProvider, useApiKey } from "./api/ApiKeyContext";
import { ApiKeyPrompt } from "./components/ApiKeyPrompt";

function Dashboard() {
  const { apiKey } = useApiKey();

  return (
    <div className="app">
      <h1>CBKS</h1>
      {apiKey === null && <ApiKeyPrompt />}
      <p>Grundgerüst geladen.</p>
    </div>
  );
}

export function App() {
  return (
    <ApiKeyProvider>
      <Dashboard />
    </ApiKeyProvider>
  );
}
```

- [ ] **Step 6: Manuell verifizieren**

Backend starten (separates Terminal): `.venv/bin/uvicorn backend.main:app --port 8000`
Frontend starten: `npm run dev`
Im Browser: Da `CBKS_API_KEY` lokal nicht gesetzt ist, erscheint das Prompt-Feld (weil `apiKey` initial `null` ist); Wert eingeben, "Speichern" klicken, Seite neu laden → Feld bleibt aus, `localStorage.getItem("cbks-api-key")` in der DevTools-Konsole zeigt den gespeicherten Wert.

- [ ] **Step 7: Commit**

```bash
cd $REPO
git add frontend/src/api frontend/src/components/ApiKeyPrompt.tsx frontend/src/App.tsx
git commit -m "feat: API-Client-Layer mit ApiError und API-Key-Handling"
```

---

### Task 5: GraphCanvas (D3-Force-Graph)

**Files:**
- Create: `frontend/src/components/GraphCanvas.tsx`
- Create: `frontend/src/graph/colors.ts`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `apiFetch<GraphResponse>("/graph")` (Task 4), `Node`/`Edge`/`GraphResponse` (Task 4)
- Produces: `<GraphCanvas nodes={Node[]} edges={Edge[]} highlightedNodeIds={string[]} onNodeSelect={(id: string) => void} />`

- [ ] **Step 1: Farbzuordnung schreiben**

`frontend/src/graph/colors.ts` — kategoriale Palette für die 7 Node-Typen (nutzt die `dataviz`-Skill-Palette als Grundlage, hier mit den 7 tatsächlich benötigten Werten):

```typescript
import type { NodeType } from "../api/types";

export const NODE_TYPE_COLORS: Record<NodeType, string> = {
  concept: "#6C8EF5",
  document: "#5FD0C0",
  task: "#F5A65C",
  note: "#C792EA",
  project: "#6CE07A",
  commit: "#E0B76C",
  screenshot: "#E06C8E",
};
```

- [ ] **Step 2: `GraphCanvas` mit D3-Force-Simulation schreiben**

`frontend/src/components/GraphCanvas.tsx`:

```tsx
import { useEffect, useRef } from "react";
import * as d3 from "d3";
import type { Node, Edge } from "../api/types";
import { NODE_TYPE_COLORS } from "../graph/colors";

interface SimNode extends Node, d3.SimulationNodeDatum {}
interface SimEdge extends d3.SimulationLinkDatum<SimNode> {
  id: string;
  relation_type: string;
}

interface Props {
  nodes: Node[];
  edges: Edge[];
  highlightedNodeIds: string[];
  onNodeSelect: (id: string) => void;
}

export function GraphCanvas({ nodes, edges, highlightedNodeIds, onNodeSelect }: Props) {
  const svgRef = useRef<SVGSVGElement | null>(null);

  useEffect(() => {
    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const width = svgRef.current?.clientWidth ?? 800;
    const height = svgRef.current?.clientHeight ?? 600;

    const simNodes: SimNode[] = nodes.map((n) => ({ ...n }));
    const simEdges: SimEdge[] = edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      relation_type: e.relation_type,
    }));

    const container = svg.append("g");

    svg.call(
      d3.zoom<SVGSVGElement, unknown>().on("zoom", (event) => {
        container.attr("transform", event.transform);
      })
    );

    const link = container
      .append("g")
      .attr("stroke", "#555")
      .selectAll("line")
      .data(simEdges)
      .join("line")
      .attr("stroke-width", 1.5);

    const highlighted = new Set(highlightedNodeIds);

    const node = container
      .append("g")
      .selectAll("circle")
      .data(simNodes)
      .join("circle")
      .attr("r", (d) => (highlighted.has(d.id) ? 10 : 7))
      .attr("fill", (d) => NODE_TYPE_COLORS[d.type])
      .attr("stroke", (d) => (highlighted.has(d.id) ? "#fff" : "none"))
      .attr("stroke-width", 2)
      .style("cursor", "pointer")
      .on("click", (_event, d) => onNodeSelect(d.id))
      .call(
        d3
          .drag<SVGCircleElement, SimNode>()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          })
      );

    node.append("title").text((d) => d.title);

    const simulation = d3
      .forceSimulation(simNodes)
      .force("link", d3.forceLink<SimNode, SimEdge>(simEdges).id((d) => d.id).distance(60))
      .force("charge", d3.forceManyBody().strength(-200))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .on("tick", () => {
        link
          .attr("x1", (d) => (d.source as SimNode).x ?? 0)
          .attr("y1", (d) => (d.source as SimNode).y ?? 0)
          .attr("x2", (d) => (d.target as SimNode).x ?? 0)
          .attr("y2", (d) => (d.target as SimNode).y ?? 0);
        node.attr("cx", (d) => d.x ?? 0).attr("cy", (d) => d.y ?? 0);
      });

    return () => {
      simulation.stop();
    };
  }, [nodes, edges, highlightedNodeIds, onNodeSelect]);

  return <svg ref={svgRef} className="graph-canvas" />;
}
```

`frontend/src/styles/global.css` ergänzen:

```css
.graph-canvas {
  width: 100%;
  height: 70vh;
  background: #14161c;
  border: 1px solid #2a2d36;
}
```

- [ ] **Step 3: In `App.tsx` einbinden**

`frontend/src/App.tsx` ersetzen mit:

```tsx
import { useEffect, useState, useCallback } from "react";
import { ApiKeyProvider, useApiKey } from "./api/ApiKeyContext";
import { ApiKeyPrompt } from "./components/ApiKeyPrompt";
import { GraphCanvas } from "./components/GraphCanvas";
import { apiFetch } from "./api/client";
import type { GraphResponse, Node, Edge } from "./api/types";

function Dashboard() {
  const { apiKey } = useApiKey();
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);

  const loadGraph = useCallback(async () => {
    const graph = await apiFetch<GraphResponse>("/graph");
    setNodes(graph.nodes);
    setEdges(graph.edges);
  }, []);

  useEffect(() => {
    loadGraph().catch((err) => console.error("Graph konnte nicht geladen werden", err));
  }, [loadGraph]);

  const handleNodeSelect = useCallback((id: string) => {
    console.log("Node ausgewählt:", id);
  }, []);

  return (
    <div className="app">
      <h1>CBKS</h1>
      {apiKey === null && <ApiKeyPrompt />}
      <GraphCanvas
        nodes={nodes}
        edges={edges}
        highlightedNodeIds={[]}
        onNodeSelect={handleNodeSelect}
      />
    </div>
  );
}

export function App() {
  return (
    <ApiKeyProvider>
      <Dashboard />
    </ApiKeyProvider>
  );
}
```

- [ ] **Step 4: Manuell verifizieren**

Backend + Frontend laufen lassen (wie in Task 4). Vorher mindestens einen Node anlegen:
```bash
curl -X POST http://127.0.0.1:8000/notes -H "Content-Type: application/json" -d '{"text": "Testnotiz für Graph-Rendering"}'
```
Im Browser: Graph zeigt mindestens einen farbigen Kreis, Zoom (Scrollrad) und Pan (Ziehen im Leerraum) funktionieren, Ziehen an einem Node bewegt ihn, Konsole zeigt "Node ausgewählt: ..." bei Klick.

- [ ] **Step 5: Commit**

```bash
cd $REPO
git add frontend/src/components/GraphCanvas.tsx frontend/src/graph frontend/src/App.tsx frontend/src/styles/global.css
git commit -m "feat: D3-Force-Graph-Rendering des Wissensgraphen (GraphCanvas)"
```

---

### Task 6: NodeDetailPanel

**Files:**
- Create: `frontend/src/components/NodeDetailPanel.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `apiFetch<NodeDetailResponse>("/nodes/{id}")` (Task 4), `GraphCanvas.onNodeSelect` (Task 5)
- Produces: `<NodeDetailPanel detail={NodeDetailResponse | null} onClose={() => void} />`

- [ ] **Step 1: `NodeDetailPanel` schreiben**

`frontend/src/components/NodeDetailPanel.tsx`:

```tsx
import type { NodeDetailResponse } from "../api/types";

interface Props {
  detail: NodeDetailResponse | null;
  onClose: () => void;
}

export function NodeDetailPanel({ detail, onClose }: Props) {
  if (!detail) return null;

  return (
    <aside className="node-detail-panel">
      <button onClick={onClose}>Schließen</button>
      <h2>{detail.node.title}</h2>
      <p><strong>Typ:</strong> {detail.node.type}</p>
      {detail.node.content && <p>{detail.node.content}</p>}
      <h3>Nachbarn ({detail.neighbors.length})</h3>
      <ul>
        {detail.neighbors.map((n) => (
          <li key={n.id}>{n.title}</li>
        ))}
      </ul>
    </aside>
  );
}
```

`frontend/src/styles/global.css` ergänzen:

```css
.node-detail-panel {
  position: fixed;
  top: 0;
  right: 0;
  width: 300px;
  height: 100vh;
  background: #1b1e26;
  border-left: 1px solid #2a2d36;
  padding: 1rem;
  overflow-y: auto;
}
```

- [ ] **Step 2: In `App.tsx` einbinden**

In `frontend/src/App.tsx`: Import ergänzen (`NodeDetailPanel`, `NodeDetailResponse`), in `Dashboard` State und Handler ergänzen:

```tsx
import { NodeDetailPanel } from "./components/NodeDetailPanel";
import type { NodeDetailResponse } from "./api/types";
```

```tsx
  const [selectedNode, setSelectedNode] = useState<NodeDetailResponse | null>(null);

  const handleNodeSelect = useCallback(async (id: string) => {
    const detail = await apiFetch<NodeDetailResponse>(`/nodes/${id}`);
    setSelectedNode(detail);
  }, []);
```

Und im JSX nach `<GraphCanvas ... />` ergänzen:

```tsx
      <NodeDetailPanel detail={selectedNode} onClose={() => setSelectedNode(null)} />
```

(Die alte `console.log`-Variante von `handleNodeSelect` und die dazugehörige `useCallback`-Deklaration ersetzen, nicht doppelt anlegen.)

- [ ] **Step 3: Manuell verifizieren**

Klick auf einen Node im Graph öffnet rechts das Panel mit Titel, Typ, Inhalt und Nachbarliste; "Schließen" blendet es aus.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/NodeDetailPanel.tsx frontend/src/App.tsx frontend/src/styles/global.css
git commit -m "feat: NodeDetailPanel zeigt Node+Nachbarn nach Klick im Graph"
```

---

### Task 7: SearchBar mit Highlighting

**Files:**
- Create: `frontend/src/components/SearchBar.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `apiFetch<SearchHit[]>("/search?q=...")` (Task 4), `GraphCanvas.highlightedNodeIds` (Task 5)
- Produces: `<SearchBar onResults={(hits: SearchHit[]) => void} />`

- [ ] **Step 1: `SearchBar` schreiben**

`frontend/src/components/SearchBar.tsx`:

```tsx
import { useState } from "react";
import { apiFetch } from "../api/client";
import type { SearchHit } from "../api/types";

interface Props {
  onResults: (hits: SearchHit[]) => void;
}

export function SearchBar({ onResults }: Props) {
  const [query, setQuery] = useState("");

  const runSearch = async () => {
    if (!query.trim()) {
      onResults([]);
      return;
    }
    const hits = await apiFetch<SearchHit[]>(`/search?q=${encodeURIComponent(query)}`);
    onResults(hits);
  };

  return (
    <div className="search-bar">
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && runSearch()}
        placeholder="Suche..."
      />
      <button onClick={runSearch}>Suchen</button>
    </div>
  );
}
```

- [ ] **Step 2: In `App.tsx` einbinden**

Import ergänzen: `SearchBar`, `SearchHit`. State und Handler in `Dashboard`:

```tsx
  const [searchHits, setSearchHits] = useState<SearchHit[]>([]);
```

Im JSX vor `<GraphCanvas ... />` ergänzen:

```tsx
      <SearchBar onResults={setSearchHits} />
```

`highlightedNodeIds`-Prop von `GraphCanvas` ändern von `[]` auf:

```tsx
        highlightedNodeIds={searchHits.map((hit) => hit.node.id)}
```

- [ ] **Step 3: Manuell verifizieren**

Notiz mit bekanntem Suchbegriff anlegen (siehe Task 5, Step 4), im Suchfeld denselben Begriff eingeben und Enter drücken → passender Node wird im Graph größer/weiß umrandet dargestellt.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/SearchBar.tsx frontend/src/App.tsx
git commit -m "feat: SearchBar hebt Treffer im Graph hervor"
```

---

### Task 8: AskPanel

**Files:**
- Create: `frontend/src/components/AskPanel.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `apiFetch<AskResponse>("/ask", {method: "POST", body})` (Task 4)
- Produces: `<AskPanel />` (eigenständig, kein Callback nötig)

- [ ] **Step 1: `AskPanel` schreiben**

`frontend/src/components/AskPanel.tsx`:

```tsx
import { useState } from "react";
import { apiFetch } from "../api/client";
import type { AskResponse } from "../api/types";

export function AskPanel() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const ask = async () => {
    if (!question.trim()) return;
    setLoading(true);
    try {
      const result = await apiFetch<AskResponse>("/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      setAnswer(result);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="ask-panel">
      <input
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && ask()}
        placeholder="Frage stellen..."
      />
      <button onClick={ask} disabled={loading}>
        {loading ? "..." : "Fragen"}
      </button>
      {answer && (
        <div className="ask-answer">
          <p>{answer.answer}</p>
          <ul>
            {answer.sources.map((source) => (
              <li key={source}>{source}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: In `App.tsx` einbinden**

Import ergänzen: `AskPanel`. Im JSX nach `<SearchBar ... />` ergänzen:

```tsx
      <AskPanel />
```

- [ ] **Step 3: Manuell verifizieren**

Frage eintippen, Enter drücken → Antwort + Quellenliste erscheinen unterhalb des Eingabefelds.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/AskPanel.tsx frontend/src/App.tsx
git commit -m "feat: AskPanel für RAG-Fragen an /ask"
```

---

### Task 9: UploadForm

**Files:**
- Create: `frontend/src/components/UploadForm.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `apiFetch<IngestResponse-artige Daten>("/documents"/"/notes", {method:"POST"})` (Task 4)
- Produces: `<UploadForm onIngested={() => void} />`

- [ ] **Step 1: `UploadForm` schreiben**

`frontend/src/components/UploadForm.tsx`:

```tsx
import { useState } from "react";
import { apiFetch } from "../api/client";

interface Props {
  onIngested: () => void;
}

export function UploadForm({ onIngested }: Props) {
  const [noteText, setNoteText] = useState("");

  const submitNote = async () => {
    if (!noteText.trim()) return;
    await apiFetch("/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: noteText }),
    });
    setNoteText("");
    onIngested();
  };

  const submitFile = async (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    await apiFetch("/documents", { method: "POST", body: formData });
    onIngested();
  };

  return (
    <div className="upload-form">
      <div>
        <input
          value={noteText}
          onChange={(e) => setNoteText(e.target.value)}
          placeholder="Notiz eintippen..."
        />
        <button onClick={submitNote}>Notiz speichern</button>
      </div>
      <div>
        <input
          type="file"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) submitFile(file);
          }}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: In `App.tsx` einbinden**

Import ergänzen: `UploadForm`. Im JSX nach `<AskPanel />` ergänzen:

```tsx
      <UploadForm onIngested={loadGraph} />
```

(`loadGraph` existiert bereits aus Task 5 und lädt `/graph` neu.)

- [ ] **Step 3: Manuell verifizieren**

Notiztext eingeben und speichern → Graph aktualisiert sich mit neuem Node. Datei über den Datei-Input hochladen → ebenfalls neuer Node im Graph.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/UploadForm.tsx frontend/src/App.tsx
git commit -m "feat: UploadForm für Dokument- und Notiz-Ingest"
```

---

### Task 10: StatsBar + Retry/Rebuild/Backup

**Files:**
- Create: `frontend/src/components/StatsBar.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: `apiFetch<StatsResponse>("/stats")`, `apiFetch<ProcessSummary>("/retry"|"/rebuild", {method:"POST"})`, `apiFetch("/backup", {method:"POST"})` (Task 4)
- Produces: `<StatsBar refreshKey={number} onGraphChanged={() => void} />`

- [ ] **Step 1: `StatsBar` schreiben**

`frontend/src/components/StatsBar.tsx`:

```tsx
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { StatsResponse } from "../api/types";

interface Props {
  refreshKey: number;
  onGraphChanged: () => void;
}

export function StatsBar({ refreshKey, onGraphChanged }: Props) {
  const [stats, setStats] = useState<StatsResponse | null>(null);

  useEffect(() => {
    apiFetch<StatsResponse>("/stats").then(setStats);
  }, [refreshKey]);

  const runRetry = async () => {
    await apiFetch("/retry", { method: "POST" });
    onGraphChanged();
  };

  const runRebuild = async () => {
    await apiFetch("/rebuild", { method: "POST" });
    onGraphChanged();
  };

  const runBackup = async () => {
    await apiFetch("/backup", { method: "POST" });
  };

  return (
    <div className="stats-bar">
      {stats && (
        <span>
          Events: {JSON.stringify(stats.events)} | Graph: {JSON.stringify(stats.graph)}
        </span>
      )}
      <button onClick={runRetry}>Retry</button>
      <button onClick={runRebuild}>Rebuild</button>
      <button onClick={runBackup}>Backup</button>
    </div>
  );
}
```

- [ ] **Step 2: In `App.tsx` einbinden**

Import ergänzen: `StatsBar`. In `Dashboard` einen einfachen Zähler für erzwungene Neuladung ergänzen:

```tsx
  const [refreshKey, setRefreshKey] = useState(0);
```

Im JSX ganz oben (vor `<SearchBar ... />`) ergänzen:

```tsx
      <StatsBar
        refreshKey={refreshKey}
        onGraphChanged={() => {
          setRefreshKey((k) => k + 1);
          loadGraph();
        }}
      />
```

Und in `submitNote`/`submitFile` bzw. `onIngested={loadGraph}` beim `UploadForm` ebenfalls `refreshKey` erhöhen, damit `StatsBar` nach Upload aktuelle Zahlen zeigt:

```tsx
      <UploadForm
        onIngested={() => {
          setRefreshKey((k) => k + 1);
          loadGraph();
        }}
      />
```

- [ ] **Step 3: Manuell verifizieren**

Stats-Zeile zeigt Event-/Graph-Zähler; nach Notiz-Upload steigen die Zahlen. "Rebuild" und "Retry" laufen ohne Fehler durch (Netzwerk-Tab zeigt 200 OK), "Backup" liefert 200 (sofern `CBKS_BACKUP_SCRIPT` lokal konfiguriert ist — sonst wird ein Serverfehler im Netzwerk-Tab sichtbar, was für das Grundgerüst kein Blocker ist, da Backup-Konfiguration außerhalb des Frontend-Scopes liegt).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/StatsBar.tsx frontend/src/App.tsx
git commit -m "feat: StatsBar mit Retry/Rebuild/Backup-Aktionen"
```

---

## Abschluss-Kriterium für Phase 3.2

1. `.venv/bin/python -m pytest backend -q` läuft vollständig grün durch (inkl. neuer `/graph`-Tests).
2. `npm run dev` im `frontend/`-Verzeichnis startet ohne Fehler; gegen ein laufendes Backend (`.venv/bin/uvicorn backend.main:app --port 8000`) zeigt das Dashboard den Graphen, erlaubt Suche, Ask, Upload (Datei+Notiz) und Stats/Retry/Rebuild/Backup.
3. Klick auf einen Node öffnet das Detail-Panel mit Nachbarn.
4. Alle zehn Tasks sind committet.
