# Anatomisch erkennbare Gehirn-Huelle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `frontend/src/graph/brainHull.ts` erzeugt statt einer einzelnen Ellipsoid-Punktwolke drei zusammengesetzte Regionen (Cerebrum mit Temporallappen, Cerebellum, Brainstem), und `GraphCanvas.tsx` mappt die unterste Knotenschicht in Cerebellum/Brainstem statt ausschliesslich ins Cerebrum-Ellipsoid.

**Architecture:** `brainHull.ts` bekommt drei interne Generator-Funktionen (`generateCerebrum`, `generateCerebellum`, `generateBrainstem`), die direkt in gemeinsam allokierte `Float32Array`-Buffer schreiben (kein Concat). Die Farbgebung wechselt von lokalem Fibonacci-`dy` auf eine globale, an der zusammengesetzten Form normalisierte y-Position, damit der Farbverlauf ueber alle drei Regionen hinweg durchgaengig bleibt. `GraphCanvas.tsx` fittet Knoten wie bisher ins Cerebrum-Ellipsoid und mappt danach die unterste Schicht in zwei weiteren Schritten in Cerebellum- bzw. Brainstem-Zielkoordinaten um. Cerebellum-Achsen/-Offset und Brainstem-Kapselparameter werden aus `brainHull.ts` exportiert (Abweichung von der Spec-Formulierung "muessen nicht exportiert werden" — noetig, damit Node-Mapping und Punktwolke exakt denselben Raum referenzieren, DRY).

**Tech Stack:** React, Three.js/`@react-three/fiber`, TypeScript, d3-force-3d. Kein Test-Runner im Frontend (nur `tsc -b` als Build/Typecheck).

## Global Constraints

- Kamera (`position=[0,1.2,6.5]`, `fov=55`) und `OrbitControls` (`maxDistance=14`) bleiben unveraendert.
- `HULL_COLOR`, Punktgroesse/-opacity im `<pointsMaterial>` bleiben unveraendert.
- `FloatingParticles` und uebrige Szene bleiben unveraendert.
- Gesamt-Punktezahl bleibt bei 2400 (nur intern auf drei Regionen aufgeteilt: ~75% Cerebrum, ~20% Cerebellum, ~5% Brainstem).
- Keine anatomische Praezision noetig, nur klar als Gehirn erkennbare stilisierte Punktwolke.
- Kraefte-Simulation (`forceLink`/`forceManyBody`/`forceCenter`/`areaForce`) bleibt unveraendert; nur der finale Mapping-Schritt nach dem Fitting aendert sich.
- `BRAIN_AXES` bleibt als Export bestehen (wird weiter fuer das Cerebrum-Fitting gebraucht).
- Kein automatisierter Test fuer die stilisierte 3D-Form sinnvoll (Spec-Vorgabe) — Verifikation erfolgt visuell im Browser.

---

## Task 1: brainHull.ts — Cerebrum-Details, Cerebellum, Brainstem, Komposition

**Files:**
- Modify: `frontend/src/graph/brainHull.ts` (komplett)

**Interfaces:**
- Produces (unveraendert): `generateBrainHull(count: number): BrainHull` mit `BrainHull { positions: Float32Array; colors: Float32Array }`; `BRAIN_AXES: { x: number; y: number; z: number }`.
- Produces (neu, fuer Task 2): `CEREBELLUM_AXES: { x: number; y: number; z: number }`, `CEREBELLUM_OFFSET: [number, number, number]`, `BRAINSTEM_TOP: [number, number, number]`, `BRAINSTEM_BOTTOM: [number, number, number]`, `BRAINSTEM_RADIUS: number`.

- [ ] **Step 1: Datei komplett ersetzen**

Ersetze den kompletten Inhalt von `frontend/src/graph/brainHull.ts` durch:

```typescript
// Prozedurale Gehirn-Huelle als Punktewolke.
// Drei Teil-Punktwolken (Cerebrum, Cerebellum, Brainstem) werden zu einem
// gemeinsamen Buffer zusammengesetzt. Zwei Lappen (Hemisphaeren) als
// Spiegelung an der Medianebene (x=0), zentrale Laengsfurche, laterale
// Fissur, haengende Temporallappen und Noise-verzerrte Oberflaeche
// (Falten-Anmutung). Keine anatomische Korrektheit, sondern eine klar als
// Gehirn erkennbare leuchtende Kontur fuer das 3D-Layout.

export interface BrainHull {
  positions: Float32Array;
  colors: Float32Array;
}

// Farbverlauf entlang der y-Achse: unten tiefes Kobaltblau/Cyan,
// oben Neon-Magenta/Violett - Werte >1 damit Bloom zum Leuchten kommt.
const KOBALT: [number, number, number] = [0.0, 0.16, 0.6];
const CYAN: [number, number, number] = [0.09, 0.95, 1.1];
const MAGENTA: [number, number, number] = [0.85, 0.18, 1.1];
const BOOST = 1.45;

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function gradient(dy: number): [number, number, number] {
  // dy in [-1,1] -> t in [0,1]; 3-Stopp Kobalt->Cyan->Magenta.
  const t = (dy + 1) / 2;
  let r: number, g: number, b: number;
  if (t < 0.5) {
    const u = t * 2;
    r = lerp(KOBALT[0], CYAN[0], u);
    g = lerp(KOBALT[1], CYAN[1], u);
    b = lerp(KOBALT[2], CYAN[2], u);
  } else {
    const u = (t - 0.5) * 2;
    r = lerp(CYAN[0], MAGENTA[0], u);
    g = lerp(CYAN[1], MAGENTA[1], u);
    b = lerp(CYAN[2], MAGENTA[2], u);
  }
  return [r * BOOST, g * BOOST, b * BOOST];
}

// Globale y-Normalisierung: der Farbverlauf soll ueber die gesamte
// zusammengesetzte Form (Cerebrum bis Brainstem-Unterkante) durchgaengig
// sein, nicht pro Region neu bei -1..1 anfangen.
const GLOBAL_Y_MIN = -1.8;
const GLOBAL_Y_MAX = 1.35;

function globalGradient(py: number): [number, number, number] {
  const t = Math.max(0, Math.min(1, (py - GLOBAL_Y_MIN) / (GLOBAL_Y_MAX - GLOBAL_Y_MIN)));
  return gradient(t * 2 - 1);
}

// Multi-Oktaven Sinus-Pseudonoise in [-1,1] - gut genug fuer Falten-Anmutung.
// abs(x) erzwingt Spiegelsymmetrie an der Medianebene (beide Hemisphaeren identisch).
function foldNoise(x: number, y: number, z: number): number {
  const ax = Math.abs(x);
  let v = Math.sin(ax * 1.0 + 0.3) * Math.sin(y * 1.0 + 1.7) * Math.sin(z * 1.0 + 2.9);
  v += Math.sin(ax * 2.1 + 4.1) * Math.sin(y * 2.3 + 0.7) * Math.sin(z * 1.9 + 5.3) * 0.5;
  v += Math.sin(ax * 4.3 + 2.2) * Math.sin(y * 4.1 + 6.6) * Math.sin(z * 3.9 + 1.1) * 0.25;
  return v / 1.75;
}

// Feineres Multi-Oktaven-Noise (kuerzere Wellenlaenge) fuer die dichte
// Kleinhirn-Textur.
function cerebellumNoise(x: number, y: number, z: number): number {
  const ax = Math.abs(x);
  let v = Math.sin(ax * 5.0 + 0.3) * Math.sin(y * 5.0 + 1.7) * Math.sin(z * 5.0 + 2.9);
  v += Math.sin(ax * 9.0 + 4.1) * Math.sin(y * 8.6 + 0.7) * Math.sin(z * 8.2 + 5.3) * 0.5;
  return v / 1.5;
}

// Gehirn-Proportionen: laenger in z, etwas breiter in x, abgeflacht in y.
const SCALE_X = 1.45;
const SCALE_Y = 1.05;
const SCALE_Z = 1.85;
const OVERALL = 1.25;

// Halbachsen des Gehirn-Ellipsoids (fuer Layout-Fit), siehe generateBrainHull.
export const BRAIN_AXES = {
  x: SCALE_X * OVERALL,
  y: SCALE_Y * OVERALL,
  z: SCALE_Z * OVERALL,
};

// Laterale Fissur (Falte oberhalb der Temporallappen): schmale Kerbe knapp
// oberhalb der Aequatorlinie, wirkt nur seitlich (grosses |dx|).
const LAT_FISSURE_Y = 0.08;
const LAT_FISSURE_WIDTH = 0.02;
const LAT_FISSURE_DEPTH = 0.16;

// Temporallappen-Bulge: seitlich-untere Zone wird nach aussen/unten gezogen,
// analog zur topDip/groove-Technik, nur unten statt oben.
const TEMPORAL_Y = -0.4;
const TEMPORAL_WIDTH = 0.05;
const TEMPORAL_OUT = 0.22;
const TEMPORAL_DOWN = 0.16;

// Cerebellum: eigene kleinere Halbachsen, Position unterhalb/hinter dem
// Cerebrum (-y unten, -z occipital, siehe AREA_ANCHORS-Konvention in
// GraphCanvas.tsx: +z frontal, -z occipital, +y dorsal/oben).
export const CEREBELLUM_AXES = {
  x: 0.85,
  y: 0.55,
  z: 0.75,
};
export const CEREBELLUM_OFFSET: [number, number, number] = [0, -0.95, -1.55];

// Brainstem: duenne Kapsel zwischen Cerebrum-Unterseite und einem Punkt
// unterhalb des Cerebellums.
export const BRAINSTEM_RADIUS = 0.18;
export const BRAINSTEM_TOP: [number, number, number] = [0, -1.15, -0.2];
export const BRAINSTEM_BOTTOM: [number, number, number] = [0, -1.75, -1.35];

function generateCerebrum(
  count: number,
  positions: Float32Array,
  colors: Float32Array,
  offset: number
): void {
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    // Fibonacci-Kugel: gleichmaessige Verteilung auf der Einheitskugel.
    const y = 1 - (i / Math.max(1, count - 1)) * 2;
    const ringRadius = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = goldenAngle * i;
    const dx = Math.cos(theta) * ringRadius;
    const dy = y;
    const dz = Math.sin(theta) * ringRadius;

    // Falten: Radius wird leicht von Noise moduliert.
    const n = foldNoise(dx * 2.2, dy * 2.2, dz * 2.2);
    let r = 1 + 0.11 * n;

    // Zentrale Laengsfurche (longitudinal fissure): Punkte nahe x=0
    // werden leicht nach innen gezogen und oben etwas abgesenkt.
    const ax = Math.abs(dx);
    const groove = Math.exp(-(ax * ax) / 0.02);
    const fissure = 1 - 0.2 * groove;
    const topDip = 0.12 * groove * Math.max(0, dy);

    // Laterale Fissur: Kerbe knapp oberhalb der Aequatorlinie, nur seitlich.
    const lateralWeight = Math.min(1, ax * 2.2);
    const latBand = Math.exp(
      -((dy - LAT_FISSURE_Y) * (dy - LAT_FISSURE_Y)) / LAT_FISSURE_WIDTH
    );
    r *= 1 - LAT_FISSURE_DEPTH * latBand * lateralWeight;

    // Temporallappen-Bulge: seitlich-unten nach aussen und unten gezogen.
    const temporalBand = Math.exp(
      -((dy - TEMPORAL_Y) * (dy - TEMPORAL_Y)) / TEMPORAL_WIDTH
    );
    const temporalPull = temporalBand * lateralWeight;
    r += TEMPORAL_OUT * temporalPull;
    const temporalDown = TEMPORAL_DOWN * temporalPull;

    const px = dx * r * fissure * SCALE_X * OVERALL;
    const py = (dy * r * fissure - topDip - temporalDown) * SCALE_Y * OVERALL;
    const pz = dz * r * fissure * SCALE_Z * OVERALL;

    const idx = (offset + i) * 3;
    positions[idx] = px;
    positions[idx + 1] = py;
    positions[idx + 2] = pz;

    const [cr, cg, cb] = globalGradient(py);
    colors[idx] = cr;
    colors[idx + 1] = cg;
    colors[idx + 2] = cb;
  }
}

function generateCerebellum(
  count: number,
  positions: Float32Array,
  colors: Float32Array,
  offset: number
): void {
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const y = 1 - (i / Math.max(1, count - 1)) * 2;
    const ringRadius = Math.sqrt(Math.max(0, 1 - y * y));
    const theta = goldenAngle * i;
    const dx = Math.cos(theta) * ringRadius;
    const dy = y;
    const dz = Math.sin(theta) * ringRadius;

    const n = cerebellumNoise(dx * 3.0, dy * 3.0, dz * 3.0);
    const r = 1 + 0.09 * n;

    // Eigene, flachere Mittelrille.
    const ax = Math.abs(dx);
    const groove = Math.exp(-(ax * ax) / 0.03);
    const rille = 1 - 0.12 * groove;

    const px = dx * r * rille * CEREBELLUM_AXES.x + CEREBELLUM_OFFSET[0];
    const py = dy * r * rille * CEREBELLUM_AXES.y + CEREBELLUM_OFFSET[1];
    const pz = dz * r * rille * CEREBELLUM_AXES.z + CEREBELLUM_OFFSET[2];

    const idx = (offset + i) * 3;
    positions[idx] = px;
    positions[idx + 1] = py;
    positions[idx + 2] = pz;

    const [cr, cg, cb] = globalGradient(py);
    colors[idx] = cr;
    colors[idx + 1] = cg;
    colors[idx + 2] = cb;
  }
}

function generateBrainstem(
  count: number,
  positions: Float32Array,
  colors: Float32Array,
  offset: number
): void {
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const t = count <= 1 ? 0 : i / (count - 1);
    const theta = goldenAngle * i;
    const cx = BRAINSTEM_TOP[0] + (BRAINSTEM_BOTTOM[0] - BRAINSTEM_TOP[0]) * t;
    const cy = BRAINSTEM_TOP[1] + (BRAINSTEM_BOTTOM[1] - BRAINSTEM_TOP[1]) * t;
    const cz = BRAINSTEM_TOP[2] + (BRAINSTEM_BOTTOM[2] - BRAINSTEM_TOP[2]) * t;

    const px = cx + Math.cos(theta) * BRAINSTEM_RADIUS;
    const py = cy;
    const pz = cz + Math.sin(theta) * BRAINSTEM_RADIUS;

    const idx = (offset + i) * 3;
    positions[idx] = px;
    positions[idx + 1] = py;
    positions[idx + 2] = pz;

    const [cr, cg, cb] = globalGradient(py);
    colors[idx] = cr;
    colors[idx + 1] = cg;
    colors[idx + 2] = cb;
  }
}

export function generateBrainHull(count: number): BrainHull {
  const cerebrumCount = Math.round(count * 0.75);
  const cerebellumCount = Math.round(count * 0.2);
  const brainstemCount = count - cerebrumCount - cerebellumCount;

  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);

  generateCerebrum(cerebrumCount, positions, colors, 0);
  generateCerebellum(cerebellumCount, positions, colors, cerebrumCount);
  generateBrainstem(brainstemCount, positions, colors, cerebrumCount + cerebellumCount);

  return { positions, colors };
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: keine Ausgabe / Exit-Code 0 (keine Typfehler).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/graph/brainHull.ts
git commit -m "feat: Gehirn-Huelle mit Temporallappen, Kleinhirn und Hirnstamm"
```

---

## Task 2: GraphCanvas.tsx — Node-Remapping in Cerebellum/Brainstem

**Files:**
- Modify: `frontend/src/components/GraphCanvas.tsx:9` (Import)
- Modify: `frontend/src/components/GraphCanvas.tsx:162-171` (Ende von `computeLayout`)

**Interfaces:**
- Consumes (aus Task 1): `CEREBELLUM_AXES: { x: number; y: number; z: number }`, `CEREBELLUM_OFFSET: [number, number, number]`, `BRAINSTEM_TOP: [number, number, number]`, `BRAINSTEM_BOTTOM: [number, number, number]`, `BRAINSTEM_RADIUS: number` aus `../graph/brainHull`.
- Nutzt bereits vorhandene lokale Variablen aus `computeLayout`: `sim: SimNode3D[]`, `k: number`, `ax, ay, az` (aus `BRAIN_AXES` destrukturiert).

- [ ] **Step 1: Import erweitern**

In `frontend/src/components/GraphCanvas.tsx` Zeile 9 ersetzen:

```typescript
import { generateBrainHull, BRAIN_AXES } from "../graph/brainHull";
```

durch:

```typescript
import {
  generateBrainHull,
  BRAIN_AXES,
  CEREBELLUM_AXES,
  CEREBELLUM_OFFSET,
  BRAINSTEM_TOP,
  BRAINSTEM_BOTTOM,
  BRAINSTEM_RADIUS,
} from "../graph/brainHull";
```

- [ ] **Step 2: Mapping-Logik am Ende von `computeLayout` ersetzen**

Den folgenden Block (aktuell Zeilen 162-171):

```typescript
  const positions = new Map<string, Vec3>();
  for (const n of sim) {
    positions.set(n.id, [
      (n.x ?? 0) * k,
      (n.y ?? 0) * k,
      (n.z ?? 0) * k,
    ]);
  }
  return { positions, visibleNodes, visibleEdges };
}
```

ersetzen durch:

```typescript
  // 1. Fitting ins Cerebrum-Ellipsoid wie bisher.
  const fitted = sim.map((n) => ({
    id: n.id,
    x: (n.x ?? 0) * k,
    y: (n.y ?? 0) * k,
    z: (n.z ?? 0) * k,
  }));

  const positions = new Map<string, Vec3>();
  for (const f of fitted) positions.set(f.id, [f.x, f.y, f.z]);

  // 2. Unterstes ~20% nach y-Position ins Cerebellum-Ellipsoid umgemappt:
  // gleiche normalisierte Richtung, skaliert auf die kleineren
  // Cerebellum-Achsen plus dessen Positions-Offset.
  const byY = [...fitted].sort((a, b) => a.y - b.y);
  const cerebellumCount = Math.round(byY.length * 0.2);
  const cerebellumSubset = byY.slice(0, cerebellumCount);
  for (const f of cerebellumSubset) {
    const nx = f.x / ax;
    const ny = f.y / ay;
    const nz = f.z / az;
    positions.set(f.id, [
      nx * CEREBELLUM_AXES.x + CEREBELLUM_OFFSET[0],
      ny * CEREBELLUM_AXES.y + CEREBELLUM_OFFSET[1],
      nz * CEREBELLUM_AXES.z + CEREBELLUM_OFFSET[2],
    ]);
  }

  // 3. Von der Cerebellum-Teilmenge werden die untersten ~3% der
  // urspruenglichen Gesamtmenge zusaetzlich in die Brainstem-Kapsel
  // umgemappt: kleiner Radius um die Stem-Achse, Position entlang der
  // Kapsel-Laenge proportional zur urspruenglichen Hoehe.
  const brainstemCount = Math.round(byY.length * 0.03);
  const brainstemSubset = byY.slice(0, brainstemCount);
  if (brainstemSubset.length > 0 && cerebellumSubset.length > 0) {
    const cerebellumMinY = cerebellumSubset[0].y;
    const cerebellumMaxY = cerebellumSubset[cerebellumSubset.length - 1].y;
    const yRange = cerebellumMaxY - cerebellumMinY || 1e-6;
    for (const f of brainstemSubset) {
      const t = 1 - (f.y - cerebellumMinY) / yRange;
      const theta = Math.atan2(f.z, f.x);
      positions.set(f.id, [
        BRAINSTEM_TOP[0] +
          (BRAINSTEM_BOTTOM[0] - BRAINSTEM_TOP[0]) * t +
          Math.cos(theta) * BRAINSTEM_RADIUS * 0.5,
        BRAINSTEM_TOP[1] + (BRAINSTEM_BOTTOM[1] - BRAINSTEM_TOP[1]) * t,
        BRAINSTEM_TOP[2] +
          (BRAINSTEM_BOTTOM[2] - BRAINSTEM_TOP[2]) * t +
          Math.sin(theta) * BRAINSTEM_RADIUS * 0.5,
      ]);
    }
  }

  return { positions, visibleNodes, visibleEdges };
}
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: keine Ausgabe / Exit-Code 0 (keine Typfehler).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/GraphCanvas.tsx
git commit -m "feat: Knoten-Layout mappt unterste Schicht in Kleinhirn und Hirnstamm"
```

---

## Task 3: Visuelle Verifikation

**Files:** keine Code-Aenderungen (nur ggf. Nachjustierung der Konstanten aus Task 1, falls die Form optisch nicht ueberzeugt).

- [ ] **Step 1: Dev-Server starten**

Run: `cd frontend && npm run dev`
Expected: Vite startet, gibt eine lokale URL aus (typischerweise `http://localhost:5173`).

- [ ] **Step 2: Graph-Ansicht im Browser oeffnen und aus mehreren Winkeln pruefen**

Browser oeffnen, zur Graph-Ansicht navigieren, Modell per Maus (OrbitControls) aus Front-, Ruecken- und Seitenansicht betrachten und mit den drei Referenzbildern (BioDigital-Anatomiemodell) aus der Spec vergleichen.

- [ ] **Step 3: Checkliste pruefen**

- Temporallappen sichtbar als haengende seitliche Woelbung (nicht nur ein laenglicher Blob).
- Kleinhirn als eigene, dichter gefaltete Struktur unterhalb/hinten klar vom Cerebrum abgesetzt erkennbar.
- Hirnstamm als duenne Verbindung zwischen Cerebrum-Unterseite und Kleinhirn sichtbar.
- Ein sichtbarer Teil der Graph-Knoten (Kugeln) liegt erkennbar im Kleinhirn-Bereich (unterhalb/hinter dem Cerebrum-Hauptvolumen).

- [ ] **Step 4: Bei Bedarf nachjustieren**

Falls die Form nicht ueberzeugt: Konstanten in `frontend/src/graph/brainHull.ts` anpassen (`LAT_FISSURE_*`, `TEMPORAL_*` fuer Cerebrum-Details; `CEREBELLUM_AXES`/`CEREBELLUM_OFFSET` fuer Groesse/Position des Kleinhirns; `BRAINSTEM_TOP`/`BRAINSTEM_BOTTOM`/`BRAINSTEM_RADIUS` fuer den Hirnstamm). Nach jeder Aenderung Schritt 1-3 wiederholen.

- [ ] **Step 5: Commit (nur falls in Step 4 etwas geaendert wurde)**

```bash
git add frontend/src/graph/brainHull.ts
git commit -m "fix: Gehirn-Huelle-Proportionen nach visueller Pruefung nachjustiert"
```
