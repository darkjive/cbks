// Gehirn-Huelle als Punktewolke, gesampelt aus einem echten anatomischen
// Mesh (siehe brainMeshData.ts fuer Quelle/Regenerierung). Elf Regionen
// (vier Lappen x links/rechts, Cerebellum links/rechts, Brainstem) werden
// aus vorab gebackenen Punktpools zusammengesetzt - keine prozedurale
// Annaeherung mehr noetig, da die Geometrie bereits die echten
// Lappen/Faltungen/Proportionen enthaelt.

import {
  FRONTALLEFT_POINTS_B64,
  FRONTALLEFT_POINT_COUNT,
  FRONTALRIGHT_POINTS_B64,
  FRONTALRIGHT_POINT_COUNT,
  PARIETALLEFT_POINTS_B64,
  PARIETALLEFT_POINT_COUNT,
  PARIETALRIGHT_POINTS_B64,
  PARIETALRIGHT_POINT_COUNT,
  TEMPORALLEFT_POINTS_B64,
  TEMPORALLEFT_POINT_COUNT,
  TEMPORALRIGHT_POINTS_B64,
  TEMPORALRIGHT_POINT_COUNT,
  OCCIPITALLEFT_POINTS_B64,
  OCCIPITALLEFT_POINT_COUNT,
  OCCIPITALRIGHT_POINTS_B64,
  OCCIPITALRIGHT_POINT_COUNT,
  CEREBELLUMLEFT_POINTS_B64,
  CEREBELLUMLEFT_POINT_COUNT,
  CEREBELLUMRIGHT_POINTS_B64,
  CEREBELLUMRIGHT_POINT_COUNT,
  BRAINSTEM_POINTS_B64,
  BRAINSTEM_POINT_COUNT,
} from "./brainMeshData";

export type BrainRegionName =
  | "frontalLeft"
  | "frontalRight"
  | "parietalLeft"
  | "parietalRight"
  | "temporalLeft"
  | "temporalRight"
  | "occipitalLeft"
  | "occipitalRight"
  | "cerebellumLeft"
  | "cerebellumRight"
  | "brainstem";

export interface BrainHullRegion {
  start: number;
  count: number;
}

export interface RegionBounds {
  axes: { x: number; y: number; z: number };
  offset: [number, number, number];
}

export interface BrainHull {
  positions: Float32Array;
  colors: Float32Array;
  regions: Record<BrainRegionName, BrainHullRegion>;
}

// Farbverlauf entlang der x-Achse (Hemisphaeren): linke Hemisphaere
// (logisch, x<0) in Blau, rechte Hemisphaere (emotional, x>0) im Spektrum
// Lila -> Rot -> Orange -> Gelb -> Gruen. Werte >1 erzeugen Bloom-Glow.
const BLUE: [number, number, number] = [0.1, 0.3, 1.05];
const PURPLE: [number, number, number] = [0.65, 0.18, 1.0];
const RED: [number, number, number] = [1.05, 0.18, 0.15];
const ORANGE: [number, number, number] = [1.1, 0.5, 0.05];
const YELLOW: [number, number, number] = [1.0, 0.95, 0.1];
const GREEN: [number, number, number] = [0.2, 1.0, 0.3];
const BOOST = 1.0;

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

// Positionierte Farbstops (t in [0,1] entlang x): linke Haelfte bleibt
// durchgehend blau, an der Hemisphaeren-Grenze (x=0 -> t=0.5) Uebergang ins
// Spektrum, das sich ueber die rechte Haelfte entfaltet.
const GRADIENT_STOPS: { pos: number; c: [number, number, number] }[] = [
  { pos: 0.0, c: BLUE },
  { pos: 0.48, c: BLUE },
  { pos: 0.55, c: PURPLE },
  { pos: 0.66, c: RED },
  { pos: 0.77, c: ORANGE },
  { pos: 0.88, c: YELLOW },
  { pos: 1.0, c: GREEN },
];

function gradient(t: number): [number, number, number] {
  const clamped = t < 0 ? 0 : t > 1 ? 1 : t;
  let i = 0;
  while (i < GRADIENT_STOPS.length - 2 && clamped > GRADIENT_STOPS[i + 1].pos) i++;
  const a = GRADIENT_STOPS[i];
  const b = GRADIENT_STOPS[i + 1];
  const u = (clamped - a.pos) / (b.pos - a.pos);
  return [
    lerp(a.c[0], b.c[0], u) * BOOST,
    lerp(a.c[1], b.c[1], u) * BOOST,
    lerp(a.c[2], b.c[2], u) * BOOST,
  ];
}

function decodeF32(b64: string): Float32Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Float32Array(bytes.buffer);
}

interface RegionPool {
  points: Float32Array;
  count: number;
}

const REGION_POOLS: Record<BrainRegionName, RegionPool> = {
  frontalLeft: { points: decodeF32(FRONTALLEFT_POINTS_B64), count: FRONTALLEFT_POINT_COUNT },
  frontalRight: { points: decodeF32(FRONTALRIGHT_POINTS_B64), count: FRONTALRIGHT_POINT_COUNT },
  parietalLeft: { points: decodeF32(PARIETALLEFT_POINTS_B64), count: PARIETALLEFT_POINT_COUNT },
  parietalRight: { points: decodeF32(PARIETALRIGHT_POINTS_B64), count: PARIETALRIGHT_POINT_COUNT },
  temporalLeft: { points: decodeF32(TEMPORALLEFT_POINTS_B64), count: TEMPORALLEFT_POINT_COUNT },
  temporalRight: { points: decodeF32(TEMPORALRIGHT_POINTS_B64), count: TEMPORALRIGHT_POINT_COUNT },
  occipitalLeft: { points: decodeF32(OCCIPITALLEFT_POINTS_B64), count: OCCIPITALLEFT_POINT_COUNT },
  occipitalRight: { points: decodeF32(OCCIPITALRIGHT_POINTS_B64), count: OCCIPITALRIGHT_POINT_COUNT },
  cerebellumLeft: { points: decodeF32(CEREBELLUMLEFT_POINTS_B64), count: CEREBELLUMLEFT_POINT_COUNT },
  cerebellumRight: { points: decodeF32(CEREBELLUMRIGHT_POINTS_B64), count: CEREBELLUMRIGHT_POINT_COUNT },
  brainstem: { points: decodeF32(BRAINSTEM_POINTS_B64), count: BRAINSTEM_POINT_COUNT },
};

export const BRAIN_REGION_NAMES = Object.keys(REGION_POOLS) as BrainRegionName[];

const TOTAL_POOL_COUNT = BRAIN_REGION_NAMES.reduce(
  (sum, name) => sum + REGION_POOLS[name].count,
  0
);

// Symmetrische x-Ausdehnung der gesamten Form; die Hemisphaeren-Grenze liegt
// bei x=0, daher wird auf t=0.5 in der Mitte normalisiert (siehe brainMeshData.ts
// fuer die scene_x<0='Left'-Konvention).
const GLOBAL_X_EXTENT = (() => {
  let max = 0;
  for (const name of BRAIN_REGION_NAMES) {
    const { points, count } = REGION_POOLS[name];
    for (let i = 0; i < count; i++) {
      const ax = Math.abs(points[i * 3]);
      if (ax > max) max = ax;
    }
  }
  return max || 1;
})();

function globalGradient(px: number): [number, number, number] {
  // px in [-EXTENT, +EXTENT] -> t in [0, 1], symmetrisch um x=0.
  const t = (px + GLOBAL_X_EXTENT) / (2 * GLOBAL_X_EXTENT);
  return gradient(t);
}

function computeBounds(names: BrainRegionName[]): RegionBounds {
  let minx = Infinity,
    maxx = -Infinity,
    miny = Infinity,
    maxy = -Infinity,
    minz = Infinity,
    maxz = -Infinity;
  for (const name of names) {
    const { points, count } = REGION_POOLS[name];
    for (let i = 0; i < count; i++) {
      const x = points[i * 3];
      const y = points[i * 3 + 1];
      const z = points[i * 3 + 2];
      if (x < minx) minx = x;
      if (x > maxx) maxx = x;
      if (y < miny) miny = y;
      if (y > maxy) maxy = y;
      if (z < minz) minz = z;
      if (z > maxz) maxz = z;
    }
  }
  return {
    axes: { x: (maxx - minx) / 2, y: (maxy - miny) / 2, z: (maxz - minz) / 2 },
    offset: [(maxx + minx) / 2, (maxy + miny) / 2, (maxz + minz) / 2],
  };
}

// Bounding-Box (Halbachsen + Mittelpunkt) pro Einzelregion, fuer
// Hover-Hitboxen und Kamera-Fokus in GraphCanvas.tsx.
export const REGION_BOUNDS: Record<BrainRegionName, RegionBounds> = Object.fromEntries(
  BRAIN_REGION_NAMES.map((name) => [name, computeBounds([name])])
) as Record<BrainRegionName, RegionBounds>;

// Cerebrum-Ellipsoid (alle vier Lappen x beide Hemisphaeren) fuer das
// Graph-Knoten-Layout-Fitting, siehe GraphCanvas.tsx computeLayout.
export const BRAIN_AXES = computeBounds([
  "frontalLeft",
  "frontalRight",
  "parietalLeft",
  "parietalRight",
  "temporalLeft",
  "temporalRight",
  "occipitalLeft",
  "occipitalRight",
]).axes;

const cerebellumBounds = computeBounds(["cerebellumLeft", "cerebellumRight"]);
export const CEREBELLUM_AXES = cerebellumBounds.axes;
export const CEREBELLUM_OFFSET = cerebellumBounds.offset;

// Brainstem-Kapsel-Achse: aus Top-/Bottom-Perzentil-Zentroiden des Pools
// hand-abgeleitet (nicht die generische Bbox, da der Brainstem in y+z
// schraeg verlaeuft und ein reiner Bbox-Mittelpunkt die Neigung verlieren
// wuerde).
export const BRAINSTEM_RADIUS = 0.35;
export const BRAINSTEM_TOP: [number, number, number] = [0, 0.22, 0.25];
export const BRAINSTEM_BOTTOM: [number, number, number] = [0, -2.14, -0.42];

function fillFromPool(
  pool: Float32Array,
  poolCount: number,
  count: number,
  positions: Float32Array,
  colors: Float32Array,
  offset: number
): void {
  for (let i = 0; i < count; i++) {
    // Pool ist bereits zufaellig sortiert -> zyklisches Wiederverwenden
    // liefert bei count > poolCount weiterhin eine plausible Verteilung.
    const srcIdx = i % poolCount;
    const px = pool[srcIdx * 3];
    const py = pool[srcIdx * 3 + 1];
    const pz = pool[srcIdx * 3 + 2];

    const idx = (offset + i) * 3;
    positions[idx] = px;
    positions[idx + 1] = py;
    positions[idx + 2] = pz;

    const [cr, cg, cb] = globalGradient(px);
    colors[idx] = cr;
    colors[idx + 1] = cg;
    colors[idx + 2] = cb;
  }
}

export function generateBrainHull(count: number): BrainHull {
  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);
  const regions = {} as Record<BrainRegionName, BrainHullRegion>;

  let offset = 0;
  let allocated = 0;
  BRAIN_REGION_NAMES.forEach((name, i) => {
    const pool = REGION_POOLS[name];
    const isLast = i === BRAIN_REGION_NAMES.length - 1;
    const regionCount = isLast
      ? count - allocated
      : Math.round((count * pool.count) / TOTAL_POOL_COUNT);
    fillFromPool(pool.points, pool.count, regionCount, positions, colors, offset);
    regions[name] = { start: offset, count: regionCount };
    offset += regionCount;
    allocated += regionCount;
  });

  return { positions, colors, regions };
}
