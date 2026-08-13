# CBKS Frontend — UI-Redesign (Design)

**Referenz:** Nutzer-Feedback nach visueller Prüfung der Phase-3.2-UI (`docs/superpowers/specs/2026-07-06-frontend-grundgeruest-design.md`): die UI ist nicht intuitiv genug. Konkrete Punkte: Makro/Meso/Micro-Buttons wirken funktionslos, „Frage stellen" soll ein typischer AI-Chat werden, die Sidebar soll per Icon einklappbar sein, es fehlt ein Logo, die Schrift wirkt nicht technisch genug, „CBKS" ist nirgends ausgeschrieben, und die Analyse-Seite nutzt ihren Platz nicht (einspaltige Liste statt Mehrspalten-Dashboard).

Alle Design-Entscheidungen wurden im Brainstorming mit visuellem Begleiter (Browser-Mockups) getroffen und vom Nutzer per Klick/Text bestätigt.

## 1. Makro/Meso/Micro — keine Änderung

**Befund:** Kein Bug. `GraphCanvas.tsx` filtert korrekt Top-N nach Knotengrad (`LOD_LIMITS = { macro: 25, meso: 80, micro: 0 }`). Bei aktuell 21 Knoten im Graph (`GET /stats` → `graph.nodes: 21`) liegt die Gesamtmenge unter dem Makro-Limit — alle drei Stufen zeigen zwangsläufig identische Knoten. Der Effekt wird sichtbar, sobald der Graph wächst.

**Entscheidung:** Nutzer hat explizit „nichts ändern" gewählt. Kein Implementierungs-Task für diesen Punkt.

## 2. Chat — eigener Haupt-Tab statt Sidebar-Sektion

**Aktuell:** `AskPanel.tsx` lebt als Sidebar-Sektion „Frage stellen" (280px Thread-Höhe, Inline-Eingabezeile).

**Neu:**
- Dritter Tab im `main-toolbar` neben „Gehirn"/„Analyse": **„Chat"**. `App.tsx`s `view`-State wird von `"graph" | "analysis"` zu `"graph" | "analysis" | "chat"` erweitert.
- Sidebar-Sektion „Frage stellen" entfällt vollständig aus `App.tsx` (kein Rest-Button).
- `AskPanel.tsx` wird strukturell umgebaut, State-Logik (turns, ask, clear, sources) bleibt gleich, nur Layout/Markup ändert sich:
  - Volle Höhe/Breite des `app-main`-Bereichs.
  - Nachrichten als flache Liste mit Avatar (kein Bubble-Hintergrund für Assistant-Nachrichten) — Stil B aus dem Mockup: kleines Avatar-Quadrat („Du" / Brain-Icon für CBKS), Rolle als kleines Label darüber, Inhalt in voller Breite darunter.
  - Eingabefeld unten fixiert (sticky footer innerhalb des Tabs), mit Send-Button.
  - „Verlauf löschen" bleibt als kleiner Button, oben im Tab statt zwischen Thread und Eingabe.
  - Auto-Scroll-Verhalten (bestehender `scrollRef`-Effekt) bleibt erhalten.
  - Quellen-Liste (`lastSources`) bleibt, als kleine Zeile unterhalb der letzten Assistant-Antwort statt globaler Liste am Tab-Ende.

## 3. Sidebar — einklappbar per Icon-Leiste

**Neu:**
- Icon oben in der Sidebar selbst (eigene Zeile über den Sektionen), togglet zwischen ausgeklappt (300px, wie heute) und eingeklappt (~48px Icon-Leiste).
- Eingeklappter Zustand zeigt ein Icon pro verbleibender Sektion: Eingabe (Upload-Icon), Suche (Lupe), Aktionen (Blitz/Zahnrad), Event-Log (Liste). ApiKeyPrompt-Sektion (nur sichtbar wenn kein Key gesetzt) bekommt ebenfalls ein Icon, falls sichtbar.
- Klick auf ein Icon in der eingeklappten Leiste klappt die Sidebar wieder voll auf und scrollt zur entsprechenden Sektion (`scrollIntoView`).
- Zustand (`collapsed: boolean`) lokal in `App.tsx` via `useState`, kein Persistieren über Reload hinweg nötig (nicht gefordert — YAGNI).
- CSS: `.app` Grid-Spalte wechselt zwischen `300px` und `48px` je nach Collapse-State (`grid-template-columns` dynamisch über CSS-Variable oder Modifier-Klasse).

## 4. Branding — Logo, Schrift, ausgeschriebener Name

- **Schrift:** [Space Grotesk](https://fonts.google.com/specimen/Space+Grotesk) (Google Fonts, per `@import` in `global.css` oder `<link>` in `index.html`) für Marke, Überschriften (`h1`, `h2`, `.sidebar-section h2`, Tab-Labels) und Labels. Fließtext (Inhalte, Eingabefelder, Chat-Nachrichten) bleibt beim bestehenden `system-ui`-Stack für Lesbarkeit bei kleinen Schriftgrößen.
- **Logo:** Neues minimalistisches Umriss-Gehirn-Icon als Inline-SVG-Komponente (kein PNG/Webp), Farbe `var(--accent)` (`#6C8EF5`), ca. 26×26px, links neben dem „CBKS"-Schriftzug im Header.
- **Ausgeschriebener Name:** Unterzeile im Header direkt unter „CBKS": „Cognitive Brain Knowledge System" (kleine, gedämpfte Schrift, `--fg-muted`, uppercase, letter-spacing) — gemäß `CBKS_SPEC_v1.1.md` §1.1 kanonischer Name.
- Kein Austausch von `frontend/public/HAL9000.svg.webp` (Browser-Favicon) oder der PWA-Manifest-Icons — nicht Teil der Anfrage, nur der In-App-Header bekommt das neue Logo.

## 5. Analyse-Seite — 2-Spalten-Grid

**Aktuell:** `AnalysisPanel.tsx` rendert 7 `<section className="analysis-section">` sequenziell untereinander (einspaltig), Charts sind bereits vorhanden (`TimelineChart`, `EmotionChart`, `DistBars`×3, `TopConcepts`, `RecurringTopics`) — es fehlt nur ein Grid-Layout.

**Neu:** `.analysis-panel` wird zu einem CSS-Grid (`grid-template-columns: 1fr 1fr`, `gap`) statt Flex-Column:
- Timeline-Chart und Sentiment-Kurve jeweils `grid-column: 1 / -1` (volle Breite) oben.
- Knoten-Typen, Sentiment-Verteilung, Relations-Typen, Top-Konzepte als vier Kacheln im 2×2-Raster darunter (je eine Grid-Zelle).
- Wiederkehrende Themen wieder `grid-column: 1 / -1` (volle Breite) am Ende.
- Responsive: Media-Query unter einer Breakpoint-Grenze (z.B. `768px`, an bestehende Breakpoints in `global.css` anlehnen falls vorhanden, sonst neu definieren) faltet das Grid auf eine Spalte.
- Keine neuen Chart-Typen, keine Backend-Änderungen — reines Layout-Rework der bestehenden `analysis-section`-Kacheln.

## Betroffene Dateien

- `frontend/index.html` — Font-Link (Space Grotesk)
- `frontend/src/App.tsx` — Tab-State (`chat`), Sidebar-Collapse-State, Sidebar-Sektion „Frage stellen" entfernen, Header-Markup (Logo + Unterzeile)
- `frontend/src/components/AskPanel.tsx` — Layout-Umbau (Tab statt Sidebar-Box, Avatar-Liste statt Bubble-losem Thread-Div)
- `frontend/src/components/AnalysisPanel.tsx` — Grid statt Flex-Column
- `frontend/src/styles/global.css` — neue/geänderte Klassen: Header-Logo/Unterzeile, Sidebar-Collapse (Icon-Leiste), Chat-Tab-Styles, Analyse-Grid, Space-Grotesk-Font-Anwendung
- Neu: `frontend/src/components/BrainLogo.tsx` (oder inline in `App.tsx`) — SVG-Icon-Komponente

## Nicht im Scope

- Makro/Meso/Micro-Verhalten (siehe Abschnitt 1)
- Backend-/API-Änderungen
- Neue Analyse-Metriken oder Chart-Typen
- Persistenz des Sidebar-Collapse-Zustands über Reloads hinweg
- Austausch von Browser-Favicon/PWA-Icons
