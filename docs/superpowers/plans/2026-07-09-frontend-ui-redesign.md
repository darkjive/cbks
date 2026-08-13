# Frontend-UI-Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CBKS-Frontend intuitiver machen: Branding (Logo, technische Schrift, ausgeschriebener Name), „Frage stellen" wird ein eigener Chat-Tab im Hauptbereich, Sidebar per Icon-Leiste einklappbar, Analyse-Seite als 2-Spalten-Grid statt einspaltiger Liste.

**Architecture:** Reines Frontend-Rework ohne Backend-Änderungen. Vier unabhängige, sequenziell aufeinander aufbauende Tasks in `frontend/src/`: (1) Branding im Header inkl. neuer `icons.tsx`-Komponentendatei, (2) Chat als dritter Haupt-Tab statt Sidebar-Sektion, (3) Sidebar-Collapse mit Icon-Leiste (baut auf der um „Frage stellen" bereinigten Sidebar aus Task 2 auf), (4) Analyse-Seite als CSS-Grid. Jeder Task ändert `App.tsx`/`global.css` inkrementell — Reihenfolge einhalten, da Task 3 die Sidebar-Struktur aus Task 2 voraussetzt.

**Tech Stack:** React 19, TypeScript, Vite. Kein Test-Runner im Frontend (nur `tsc -b` via `npm run build` als Typecheck/Build-Gate). Verifikation der visuellen/interaktiven Korrektheit erfolgt manuell im Browser (`npm run dev`, http://localhost:5173) — kein automatisierter UI-Test vorhanden oder gefordert.

## Global Constraints

- Makro/Meso/Micro-Verhalten (`GraphCanvas.tsx`) bleibt unverändert — kein Task in diesem Plan berührt es (Nutzer-Entscheidung: „nichts ändern").
- Keine Backend-/API-Änderungen.
- Keine neuen Analyse-Metriken oder Chart-Typen — nur Layout-Rework bestehender Charts.
- Kein Persistieren des Sidebar-Collapse-Zustands über Reloads hinweg (lokaler `useState`, kein localStorage).
- Kein Austausch von `frontend/public/HAL9000.svg.webp` oder PWA-Manifest-Icons.
- Jeder Task endet mit `npm run build` (muss fehlerfrei durchlaufen) und einem manuellen Browser-Check über `npm run dev`.
- Referenz-Design: `docs/superpowers/specs/2026-07-09-frontend-ui-redesign-design.md`.

---

## Task 1: Branding — Logo, Space-Grotesk-Schrift, ausgeschriebener Name im Header

**Files:**
- Create: `frontend/src/components/icons.tsx`
- Modify: `frontend/index.html`
- Modify: `frontend/src/styles/global.css`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Produces: `BrainLogo({ size?: number }): JSX.Element` in `frontend/src/components/icons.tsx` — wird in Task 2 (Chat-Avatar) wiederverwendet.
- Produces: CSS-Variable `--font-display` in `:root` (`global.css`) — wird in Task 2 (`.chat-header h2`) genutzt.

- [ ] **Step 1: Google-Fonts-Link für Space Grotesk einbinden**

In `frontend/index.html`, ersetze:

```html
    <meta name="mobile-web-app-capable" content="yes" />
    <title>CBKS</title>
  </head>
```

durch:

```html
    <meta name="mobile-web-app-capable" content="yes" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&display=swap"
      rel="stylesheet"
    />
    <title>CBKS</title>
  </head>
```

- [ ] **Step 2: `icons.tsx` mit `BrainLogo` erstellen**

Erstelle `frontend/src/components/icons.tsx`:

```tsx
interface IconProps {
  size?: number;
}

export function BrainLogo({ size = 24 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path
        d="M9 20c-3-1-4-5-2-8 1-3 1-6 4-7 2-2 6-2 8 0 3-1 6 1 6 4 2 2 2 6-1 8 0 3-3 5-6 4-3 2-7 1-9-1z"
        fill="none"
        stroke="var(--accent)"
        strokeWidth={1.6}
      />
      <path
        d="M16 5v20M11 9c2 1 2 4 0 5M21 9c-2 1-2 4 0 5"
        stroke="var(--accent)"
        strokeWidth={1}
        opacity={0.6}
        fill="none"
      />
    </svg>
  );
}
```

- [ ] **Step 3: `--font-display` und Header/Brand-Styles in `global.css` ergänzen**

Ersetze das `:root`-Block:

```css
:root {
  --bg: #0f1115;
  --bg-panel: #14161c;
  --bg-elevated: #1b1e26;
  --border: #2a2d36;
  --border-strong: #3a3d46;
  --fg: #e6e6e6;
  --fg-muted: #8b8f99;
  --accent: #6C8EF5;
  --header-height: 48px;
}
```

durch:

```css
:root {
  --bg: #0f1115;
  --bg-panel: #14161c;
  --bg-elevated: #1b1e26;
  --border: #2a2d36;
  --border-strong: #3a3d46;
  --fg: #e6e6e6;
  --fg-muted: #8b8f99;
  --accent: #6C8EF5;
  --header-height: 48px;
  --font-display: "Space Grotesk", system-ui, sans-serif;
}
```

Ersetze:

```css
.app-header h1 {
  font-size: 1rem;
  font-weight: 600;
  margin: 0;
  letter-spacing: 0.02em;
}
```

durch:

```css
.app-header h1 {
  font-family: var(--font-display);
  font-size: 1.1rem;
  font-weight: 700;
  margin: 0;
  letter-spacing: 0.01em;
}

.brand {
  display: flex;
  align-items: center;
  gap: 0.65rem;
}

.brand-text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.brand-sub {
  font-size: 0.62rem;
  color: var(--fg-muted);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-top: 1px;
}
```

Ersetze:

```css
.sidebar-section h2 {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-muted);
  margin: 0;
}
```

durch:

```css
.sidebar-section h2 {
  font-family: var(--font-display);
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--fg-muted);
  margin: 0;
}
```

Ersetze:

```css
.toolbar-tab {
  padding: 0.25rem 0.75rem;
  font-size: 0.72rem;
  background: var(--bg-elevated);
  color: var(--fg-muted);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
```

durch:

```css
.toolbar-tab {
  font-family: var(--font-display);
  font-weight: 600;
  padding: 0.25rem 0.75rem;
  font-size: 0.72rem;
  background: var(--bg-elevated);
  color: var(--fg-muted);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
```

- [ ] **Step 4: Header-Markup in `App.tsx` auf Logo + Wortmarke + Unterzeile umstellen**

Füge den Import hinzu (nach der letzten bestehenden Component-Import-Zeile, vor `import { apiFetch }`):

```tsx
import { BrainLogo } from "./components/icons";
```

Ersetze:

```tsx
      <header className="app-header">
        <h1>CBKS</h1>
        <div className="header-right">
```

durch:

```tsx
      <header className="app-header">
        <div className="brand">
          <BrainLogo size={26} />
          <div className="brand-text">
            <h1>CBKS</h1>
            <span className="brand-sub">Cognitive Brain Knowledge System</span>
          </div>
        </div>
        <div className="header-right">
```

- [ ] **Step 5: Build verifizieren**

Run: `cd frontend && npm run build`
Expected: `✓ built in ...` ohne TypeScript-Fehler.

- [ ] **Step 6: Manueller Browser-Check**

Run: `cd frontend && npm run dev` (Port 5173), im Browser öffnen.
Prüfen:
- Header zeigt Umriss-Gehirn-Icon links, „CBKS" in Space-Grotesk-Schrift, darunter klein „Cognitive Brain Knowledge System".
- Sidebar-Überschriften (z.B. „Eingabe") und die Tab-Buttons „Gehirn"/„Analyse" wirken in der neuen technischen Schrift.
- Kein Layout-Bruch im Header (Badge „verbunden"/„API-Key fehlt" bleibt rechts, keine Überlappung).

Dev-Server danach mit Ctrl+C beenden.

- [ ] **Step 7: Commit**

```bash
git add frontend/index.html frontend/src/styles/global.css frontend/src/App.tsx frontend/src/components/icons.tsx
git commit -m "feat: CBKS-Branding im Header (Logo, Space-Grotesk-Schrift, ausgeschriebener Name)"
```

---

## Task 2: Chat als eigener Haupt-Tab

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/AskPanel.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**
- Consumes: `BrainLogo({ size?: number })` aus `frontend/src/components/icons.tsx` (Task 1).
- Consumes: `--font-display` CSS-Variable (Task 1).
- Produces: `view`-State erweitert um `"chat"` in `App.tsx` — keine weiteren Tasks konsumieren dies direkt.

- [ ] **Step 1: `view`-State um `"chat"` erweitern**

In `frontend/src/App.tsx`, ersetze:

```tsx
  const [view, setView] = useState<"graph" | "analysis">("graph");
```

durch:

```tsx
  const [view, setView] = useState<"graph" | "analysis" | "chat">("graph");
```

- [ ] **Step 2: Sidebar-Sektion „Frage stellen" entfernen**

In `frontend/src/App.tsx`, ersetze:

```tsx
        <div className="sidebar-section">
          <h2>Frage stellen</h2>
          <AskPanel />
        </div>

        <div className="sidebar-section">
          <h2>Aktionen</h2>
```

durch:

```tsx
        <div className="sidebar-section">
          <h2>Aktionen</h2>
```

- [ ] **Step 3: Chat-Tab-Button und Main-Umschaltung ergänzen**

In `frontend/src/App.tsx`, ersetze:

```tsx
        <div className="main-toolbar">
          <button
            className={`toolbar-tab ${view === "graph" ? "active" : ""}`}
            onClick={() => setView("graph")}
          >
            Gehirn
          </button>
          <button
            className={`toolbar-tab ${view === "analysis" ? "active" : ""}`}
            onClick={() => setView("analysis")}
          >
            Analyse
          </button>
        </div>
        {view === "graph" ? (
          <GraphCanvas
            nodes={nodes}
            edges={edges}
            highlightedNodeIds={searchHits.map((hit) => hit.node.id)}
            onNodeSelect={handleNodeSelect}
          />
        ) : (
          <AnalysisPanel refreshKey={refreshKey} />
        )}
```

durch:

```tsx
        <div className="main-toolbar">
          <button
            className={`toolbar-tab ${view === "graph" ? "active" : ""}`}
            onClick={() => setView("graph")}
          >
            Gehirn
          </button>
          <button
            className={`toolbar-tab ${view === "analysis" ? "active" : ""}`}
            onClick={() => setView("analysis")}
          >
            Analyse
          </button>
          <button
            className={`toolbar-tab ${view === "chat" ? "active" : ""}`}
            onClick={() => setView("chat")}
          >
            Chat
          </button>
        </div>
        {view === "graph" ? (
          <GraphCanvas
            nodes={nodes}
            edges={edges}
            highlightedNodeIds={searchHits.map((hit) => hit.node.id)}
            onNodeSelect={handleNodeSelect}
          />
        ) : view === "analysis" ? (
          <AnalysisPanel refreshKey={refreshKey} />
        ) : (
          <AskPanel />
        )}
```

(`AskPanel` ist bereits importiert, keine Import-Änderung nötig.)

- [ ] **Step 4: `AskPanel.tsx` komplett auf Chat-Tab-Layout umbauen**

Ersetze den kompletten Inhalt von `frontend/src/components/AskPanel.tsx` durch:

```tsx
import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../api/client";
import { useToast } from "./Toast";
import { BrainLogo } from "./icons";
import type { AskResponse, ChatTurn } from "../api/types";

export function AskPanel() {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [lastSources, setLastSources] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const { pushError } = useToast();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns, loading]);

  const ask = async () => {
    const trimmed = question.trim();
    if (!trimmed || loading) return;
    setLoading(true);
    setTurns((prev) => [...prev, { role: "user", content: trimmed }]);
    setQuestion("");
    try {
      const history = turns.map((t) => ({ role: t.role, content: t.content }));
      const result = await apiFetch<AskResponse>("/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed, history }),
      });
      setTurns((prev) => [...prev, { role: "assistant", content: result.answer }]);
      setLastSources(result.sources);
    } catch (err) {
      setTurns((prev) => [
        ...prev,
        { role: "assistant", content: "(keine Antwort erhalten)" },
      ]);
      pushError(err, "Frage konnte nicht beantwortet werden");
    } finally {
      setLoading(false);
    }
  };

  const clear = () => {
    setTurns([]);
    setLastSources([]);
  };

  return (
    <div className="chat-tab">
      <div className="chat-header">
        <h2>Chat</h2>
        {turns.length > 0 && (
          <button className="chat-clear" onClick={clear}>
            Verlauf löschen
          </button>
        )}
      </div>

      <div className="chat-thread" ref={scrollRef}>
        {turns.length === 0 && !loading && (
          <p className="chat-empty">
            Noch keine Fragen gestellt. Stell CBKS eine Frage zu deinem Wissensgraphen.
          </p>
        )}
        {turns.map((turn, i) => (
          <div key={i} className="chat-msg">
            <div className={`chat-avatar chat-avatar-${turn.role}`}>
              {turn.role === "user" ? "Du" : <BrainLogo size={14} />}
            </div>
            <div className="chat-msg-body">
              <span className="chat-msg-role">{turn.role === "user" ? "Du" : "CBKS"}</span>
              <p className="chat-msg-content">{turn.content}</p>
              {turn.role === "assistant" && i === turns.length - 1 && lastSources.length > 0 && (
                <ul className="chat-sources">
                  {lastSources.map((source) => (
                    <li key={source} title={source}>
                      {source.slice(0, 8)}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="chat-msg">
            <div className="chat-avatar chat-avatar-assistant">
              <BrainLogo size={14} />
            </div>
            <div className="chat-msg-body">
              <span className="chat-msg-role">CBKS</span>
              <p className="chat-msg-content chat-msg-loading">denkt nach…</p>
            </div>
          </div>
        )}
      </div>

      <div className="chat-input-row">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && ask()}
          placeholder="Frage an CBKS stellen..."
          disabled={loading}
        />
        <button className="chat-send" onClick={ask} disabled={loading || !question.trim()}>
          {loading ? "…" : "Senden"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Alte `.ask-*`-CSS entfernen, neue `.chat-*`-CSS ergänzen**

In `frontend/src/styles/global.css`, ersetze den kompletten Block von `.search-bar,\n.upload-form,\n.ask-panel,` bis zum Ende von `.ask-sources li { ... }` — also:

```css
.search-bar,
.upload-form,
.ask-panel,
.stats-bar,
.api-key-prompt {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.search-bar > div,
.upload-form > div {
  display: flex;
  gap: 0.5rem;
}

.search-bar input,
.upload-form input[type="text"],
.ask-panel input {
  flex: 1;
}

/* AskPanel: Gesprächsverlauf */

.ask-thread {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.5rem;
  max-height: 280px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.ask-empty {
  margin: 0;
  color: var(--fg-muted);
  font-size: 0.8rem;
  text-align: center;
}

.ask-turn {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.ask-turn-role {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--fg-muted);
}

.ask-turn-content {
  margin: 0;
  white-space: pre-wrap;
  font-size: 0.82rem;
  line-height: 1.35;
}

.ask-turn-user .ask-turn-content {
  color: var(--fg);
}

.ask-turn-assistant .ask-turn-content,
.ask-turn-loading {
  color: #cfd2d8;
  background: var(--bg-elevated);
  border-left: 2px solid var(--accent);
  padding: 0.35rem 0.5rem;
  border-radius: 0 4px 4px 0;
}

.ask-turn-loading {
  font-size: 0.78rem;
  color: var(--fg-muted);
  font-style: italic;
}

.ask-clear {
  align-self: flex-start;
  font-size: 0.7rem;
  padding: 0.2rem 0.5rem;
  color: var(--fg-muted);
}

.ask-sources {
  margin: 0;
  padding-left: 1.1rem;
}

.ask-sources li {
  font-size: 0.7rem;
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
}
```

durch:

```css
.search-bar,
.upload-form,
.stats-bar,
.api-key-prompt {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.search-bar > div,
.upload-form > div {
  display: flex;
  gap: 0.5rem;
}

.search-bar input,
.upload-form input[type="text"] {
  flex: 1;
}

/* Chat-Tab */

.chat-tab {
  height: 100%;
  display: flex;
  flex-direction: column;
  padding: 3rem 1.25rem 1.25rem;
  max-width: 760px;
  margin: 0 auto;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 0.75rem;
}

.chat-header h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: 1.05rem;
}

.chat-clear {
  font-size: 0.7rem;
  padding: 0.25rem 0.6rem;
  color: var(--fg-muted);
}

.chat-thread {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1.1rem;
  padding-bottom: 1rem;
}

.chat-empty {
  margin: auto;
  color: var(--fg-muted);
  font-size: 0.85rem;
  text-align: center;
}

.chat-msg {
  display: flex;
  gap: 0.65rem;
}

.chat-avatar {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.62rem;
  font-weight: 600;
}

.chat-avatar-user {
  background: var(--border-strong);
  color: var(--fg);
}

.chat-avatar-assistant {
  background: var(--bg-elevated);
  border: 1px solid var(--border);
}

.chat-msg-body {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  min-width: 0;
}

.chat-msg-role {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fg-muted);
}

.chat-msg-content {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.5;
  color: var(--fg);
  white-space: pre-wrap;
}

.chat-msg-loading {
  color: var(--fg-muted);
  font-style: italic;
}

.chat-sources {
  margin: 0.3rem 0 0;
  padding-left: 1.1rem;
}

.chat-sources li {
  font-size: 0.68rem;
  color: var(--fg-muted);
  font-variant-numeric: tabular-nums;
}

.chat-input-row {
  display: flex;
  gap: 0.5rem;
  border-top: 1px solid var(--border);
  padding-top: 0.75rem;
}

.chat-input-row input {
  flex: 1;
}
```

- [ ] **Step 6: Build verifizieren**

Run: `cd frontend && npm run build`
Expected: `✓ built in ...` ohne TypeScript-Fehler.

- [ ] **Step 7: Manueller Browser-Check**

Run: `cd frontend && npm run dev`, im Browser öffnen.
Prüfen:
- Sidebar hat keine „Frage stellen"-Sektion mehr.
- Neuer Tab „Chat" existiert neben „Gehirn"/„Analyse", volle Höhe/Breite im Hauptbereich.
- Eine Frage stellen (falls Backend mit gültigem API-Key läuft): Nachricht erscheint als flache Zeile mit Avatar „Du" links, Antwort mit Gehirn-Icon-Avatar „CBKS", kein Sprechblasen-Hintergrund.
- Eingabefeld bleibt unten fixiert, Thread scrollt automatisch nach unten bei neuer Nachricht.
- „Verlauf löschen" erscheint nur, wenn mindestens eine Nachricht vorhanden ist.

Dev-Server danach mit Ctrl+C beenden.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/AskPanel.tsx frontend/src/styles/global.css
git commit -m "feat: Chat als eigener Haupt-Tab statt Sidebar-Sektion"
```

---

## Task 3: Sidebar per Icon-Leiste einklappbar

**Files:**
- Modify: `frontend/src/components/icons.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**
- Consumes: bestehende `icons.tsx`-Datei aus Task 1 (wird erweitert, nicht ersetzt).
- Consumes: bereinigte Sidebar-Struktur aus Task 2 (vier Sektionen: Eingabe, Suche, Aktionen, Event-Log, plus optionale ApiKeyPrompt-Sektion).

- [ ] **Step 1: Icons für Collapse-Toggle und Sidebar-Rail ergänzen**

In `frontend/src/components/icons.tsx`, ersetze das Ende der Datei:

```tsx
      <path
        d="M16 5v20M11 9c2 1 2 4 0 5M21 9c-2 1-2 4 0 5"
        stroke="var(--accent)"
        strokeWidth={1}
        opacity={0.6}
        fill="none"
      />
    </svg>
  );
}
```

durch:

```tsx
      <path
        d="M16 5v20M11 9c2 1 2 4 0 5M21 9c-2 1-2 4 0 5"
        stroke="var(--accent)"
        strokeWidth={1}
        opacity={0.6}
        fill="none"
      />
    </svg>
  );
}

export function ColumnsIcon({ size = 16 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      aria-hidden="true"
    >
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <line x1="9" y1="4" x2="9" y2="20" />
    </svg>
  );
}

export function UploadIcon({ size = 16 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      aria-hidden="true"
    >
      <path d="M12 16V4M7 9l5-5 5 5" />
      <path d="M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3" />
    </svg>
  );
}

export function SearchIcon({ size = 16 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      aria-hidden="true"
    >
      <circle cx="10.5" cy="10.5" r="6.5" />
      <line x1="20" y1="20" x2="15.3" y2="15.3" />
    </svg>
  );
}

export function BoltIcon({ size = 16 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      aria-hidden="true"
    >
      <path d="M13 3L4 14h6l-1 7 9-11h-6l1-7z" />
    </svg>
  );
}

export function ListIcon({ size = 16 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      aria-hidden="true"
    >
      <line x1="8" y1="6" x2="20" y2="6" />
      <line x1="8" y1="12" x2="20" y2="12" />
      <line x1="8" y1="18" x2="20" y2="18" />
      <circle cx="4" cy="6" r="1" />
      <circle cx="4" cy="12" r="1" />
      <circle cx="4" cy="18" r="1" />
    </svg>
  );
}

export function KeyIcon({ size = 16 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      aria-hidden="true"
    >
      <circle cx="7.5" cy="15.5" r="4.5" />
      <path d="M11 12l9-9M16 7l3 3M19 4l1 1" />
    </svg>
  );
}
```

- [ ] **Step 2: Collapse-State und `expandTo`-Helper in `App.tsx` ergänzen**

Ersetze den Import:

```tsx
import { BrainLogo } from "./components/icons";
```

durch:

```tsx
import {
  BrainLogo,
  ColumnsIcon,
  UploadIcon,
  SearchIcon,
  BoltIcon,
  ListIcon,
  KeyIcon,
} from "./components/icons";
```

Ersetze:

```tsx
  const [view, setView] = useState<"graph" | "analysis" | "chat">("graph");
```

durch:

```tsx
  const [view, setView] = useState<"graph" | "analysis" | "chat">("graph");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
```

Ersetze:

```tsx
  const triggerRefresh = useCallback(() => {
    setRefreshKey((k) => k + 1);
    loadGraph();
  }, [loadGraph]);
```

durch:

```tsx
  const triggerRefresh = useCallback(() => {
    setRefreshKey((k) => k + 1);
    loadGraph();
  }, [loadGraph]);

  const expandTo = useCallback((sectionId: string) => {
    setSidebarCollapsed(false);
    requestAnimationFrame(() => {
      document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, []);
```

- [ ] **Step 3: Sidebar-Markup auf Collapse/Rail umstellen**

Ersetze das äußere `<div className="app">`:

```tsx
    <div className="app">
```

durch:

```tsx
    <div className="app" style={{ gridTemplateColumns: sidebarCollapsed ? "56px 1fr" : "300px 1fr" }}>
```

Ersetze den kompletten `<aside>`-Block:

```tsx
      <aside className="app-sidebar">
        {apiKey === null && (
          <div className="sidebar-section">
            <ApiKeyPrompt />
          </div>
        )}

        <div className="sidebar-section">
          <h2>Eingabe</h2>
          <UploadForm onIngested={triggerRefresh} />
        </div>

        <div className="sidebar-section">
          <h2>Suche</h2>
          <SearchBar onResults={setSearchHits} />
        </div>

        <div className="sidebar-section">
          <h2>Aktionen</h2>
          <StatsBar refreshKey={refreshKey} onGraphChanged={triggerRefresh} />
        </div>

        <div className="sidebar-section">
          <h2>Event-Log</h2>
          <EventLogPanel refreshKey={refreshKey} onGraphChanged={triggerRefresh} />
        </div>
      </aside>
```

durch:

```tsx
      <aside className={`app-sidebar ${sidebarCollapsed ? "collapsed" : ""}`}>
        <button
          className="sidebar-toggle"
          onClick={() => setSidebarCollapsed((c) => !c)}
          title={sidebarCollapsed ? "Sidebar ausklappen" : "Sidebar einklappen"}
        >
          <ColumnsIcon />
        </button>

        {sidebarCollapsed ? (
          <div className="sidebar-rail">
            {apiKey === null && (
              <button className="rail-icon" onClick={() => expandTo("api-key-section")} title="API-Key">
                <KeyIcon />
              </button>
            )}
            <button className="rail-icon" onClick={() => expandTo("upload-section")} title="Eingabe">
              <UploadIcon />
            </button>
            <button className="rail-icon" onClick={() => expandTo("search-section")} title="Suche">
              <SearchIcon />
            </button>
            <button className="rail-icon" onClick={() => expandTo("actions-section")} title="Aktionen">
              <BoltIcon />
            </button>
            <button className="rail-icon" onClick={() => expandTo("log-section")} title="Event-Log">
              <ListIcon />
            </button>
          </div>
        ) : (
          <>
            {apiKey === null && (
              <div className="sidebar-section" id="api-key-section">
                <ApiKeyPrompt />
              </div>
            )}

            <div className="sidebar-section" id="upload-section">
              <h2>Eingabe</h2>
              <UploadForm onIngested={triggerRefresh} />
            </div>

            <div className="sidebar-section" id="search-section">
              <h2>Suche</h2>
              <SearchBar onResults={setSearchHits} />
            </div>

            <div className="sidebar-section" id="actions-section">
              <h2>Aktionen</h2>
              <StatsBar refreshKey={refreshKey} onGraphChanged={triggerRefresh} />
            </div>

            <div className="sidebar-section" id="log-section">
              <h2>Event-Log</h2>
              <EventLogPanel refreshKey={refreshKey} onGraphChanged={triggerRefresh} />
            </div>
          </>
        )}
      </aside>
```

- [ ] **Step 4: CSS für Collapse/Rail ergänzen**

In `frontend/src/styles/global.css`, ersetze:

```css
.app-sidebar {
  grid-area: sidebar;
  background: var(--bg-panel);
  border-right: 1px solid var(--border);
  padding: 0.75rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
```

durch:

```css
.app-sidebar {
  grid-area: sidebar;
  background: var(--bg-panel);
  border-right: 1px solid var(--border);
  padding: 0.75rem;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.app-sidebar.collapsed {
  padding: 0.75rem 0.4rem;
  align-items: center;
}

.sidebar-toggle {
  align-self: flex-end;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.3rem;
  color: var(--fg-muted);
  display: flex;
}

.app-sidebar.collapsed .sidebar-toggle {
  align-self: center;
}

.sidebar-toggle:hover {
  color: var(--fg);
  border-color: var(--border-strong);
}

.sidebar-rail {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  align-items: center;
}

.rail-icon {
  width: 32px;
  height: 32px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--fg-muted);
}

.rail-icon:hover {
  color: var(--fg);
  border-color: var(--border-strong);
}
```

- [ ] **Step 5: Build verifizieren**

Run: `cd frontend && npm run build`
Expected: `✓ built in ...` ohne TypeScript-Fehler.

- [ ] **Step 6: Manueller Browser-Check**

Run: `cd frontend && npm run dev`, im Browser öffnen.
Prüfen:
- Klick auf das Spalten-Icon oben in der Sidebar klappt sie auf eine schmale Icon-Leiste (~56px Spaltenbreite) zusammen, der Graph nutzt den gewonnenen Platz.
- Eingeklappt sind Icons für Eingabe/Suche/Aktionen/Event-Log sichtbar (plus Key-Icon falls kein API-Key gesetzt).
- Klick auf z.B. das Such-Icon klappt die Sidebar wieder voll auf und scrollt zur Suche-Sektion.
- Erneuter Klick auf das Spalten-Icon (jetzt oben in der vollen Sidebar) klappt wieder ein.

Dev-Server danach mit Ctrl+C beenden.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/icons.tsx frontend/src/App.tsx frontend/src/styles/global.css
git commit -m "feat: Sidebar per Icon-Leiste einklappbar"
```

---

## Task 4: Analyse-Seite als 2-Spalten-Grid

**Files:**
- Modify: `frontend/src/components/AnalysisPanel.tsx`
- Modify: `frontend/src/styles/global.css`

**Interfaces:**
- Keine Abhängigkeiten zu Task 1–3, kann unabhängig verifiziert werden.

- [ ] **Step 1: `analysis-section--wide`-Klassen für volle Breite ergänzen**

In `frontend/src/components/AnalysisPanel.tsx`, ersetze den `return`-Block:

```tsx
  return (
    <div className="analysis-panel">
      <section className="analysis-section">
        <h2>Aktivität über Zeit</h2>
        <TimelineChart data={timeline} />
      </section>
      <section className="analysis-section">
        <h2>Sentiment-Kurve</h2>
        <EmotionChart data={emotions} />
      </section>
      {patterns && (
        <>
          <section className="analysis-section">
            <h2>Knoten-Typen</h2>
            <DistBars data={patterns.type_distribution} colors={typeColors} />
          </section>
          <section className="analysis-section">
            <h2>Sentiment-Verteilung</h2>
            <DistBars data={patterns.sentiment_distribution} colors={SENTIMENT_COLORS} />
          </section>
          <section className="analysis-section">
            <h2>Relations-Typen</h2>
            <DistBars data={patterns.relation_distribution} />
          </section>
          <section className="analysis-section">
            <h2>Top-Konzepte</h2>
            <TopConcepts data={patterns.top_concepts} />
          </section>
        </>
      )}
      <section className="analysis-section">
        <h2>Wiederkehrende Themen</h2>
        <RecurringTopics data={recurring} />
      </section>
    </div>
  );
```

durch:

```tsx
  return (
    <div className="analysis-panel">
      <section className="analysis-section analysis-section--wide">
        <h2>Aktivität über Zeit</h2>
        <TimelineChart data={timeline} />
      </section>
      <section className="analysis-section analysis-section--wide">
        <h2>Sentiment-Kurve</h2>
        <EmotionChart data={emotions} />
      </section>
      {patterns && (
        <>
          <section className="analysis-section">
            <h2>Knoten-Typen</h2>
            <DistBars data={patterns.type_distribution} colors={typeColors} />
          </section>
          <section className="analysis-section">
            <h2>Sentiment-Verteilung</h2>
            <DistBars data={patterns.sentiment_distribution} colors={SENTIMENT_COLORS} />
          </section>
          <section className="analysis-section">
            <h2>Relations-Typen</h2>
            <DistBars data={patterns.relation_distribution} />
          </section>
          <section className="analysis-section">
            <h2>Top-Konzepte</h2>
            <TopConcepts data={patterns.top_concepts} />
          </section>
        </>
      )}
      <section className="analysis-section analysis-section--wide">
        <h2>Wiederkehrende Themen</h2>
        <RecurringTopics data={recurring} />
      </section>
    </div>
  );
```

- [ ] **Step 2: `.analysis-panel` von Flex-Column auf Grid umstellen**

In `frontend/src/styles/global.css`, ersetze:

```css
.analysis-panel {
  height: 100%;
  overflow-y: auto;
  padding: 3rem 1.25rem 1.25rem;
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}
```

durch:

```css
.analysis-panel {
  height: 100%;
  overflow-y: auto;
  padding: 3rem 1.25rem 1.25rem;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1.5rem;
  align-content: start;
}

.analysis-section--wide {
  grid-column: 1 / -1;
}

@media (max-width: 768px) {
  .analysis-panel {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 3: `.analysis-chart` Breitenlimit entfernen, damit Charts die Grid-Kachel füllen**

Ersetze:

```css
.analysis-chart {
  width: 100%;
  max-width: 560px;
  height: auto;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.5rem;
}
```

durch:

```css
.analysis-chart {
  width: 100%;
  height: auto;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.5rem;
}
```

- [ ] **Step 4: Build verifizieren**

Run: `cd frontend && npm run build`
Expected: `✓ built in ...` ohne TypeScript-Fehler.

- [ ] **Step 5: Manueller Browser-Check**

Run: `cd frontend && npm run dev`, im Browser öffnen, Tab „Analyse" öffnen.
Prüfen:
- Timeline-Chart und Sentiment-Kurve stehen jeweils über die volle Breite oben.
- Knoten-Typen/Sentiment-Verteilung/Relations-Typen/Top-Konzepte bilden darunter ein 2×2-Kachel-Raster.
- Wiederkehrende Themen steht wieder über die volle Breite am Ende.
- Browserfenster schmaler als ~768px ziehen: Grid fällt auf eine Spalte zusammen, kein horizontales Scrollen.

Dev-Server danach mit Ctrl+C beenden.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/AnalysisPanel.tsx frontend/src/styles/global.css
git commit -m "feat: Analyse-Seite als 2-Spalten-Grid statt einspaltiger Liste"
```
