import { Fragment, memo, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import type { ThreeEvent } from "@react-three/fiber";
import { OrbitControls, Html } from "@react-three/drei";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { EffectComposer, Bloom, DepthOfField } from "@react-three/postprocessing";
import * as THREE from "three";
import { forceSimulation, forceLink, forceManyBody, forceCenter } from "d3-force-3d";
import type { Node, Edge, NodeType } from "../api/types";
import { NODE_TYPE_COLORS } from "../graph/colors";
import {
  generateBrainHull,
  BRAIN_AXES,
  BRAIN_REGION_NAMES,
  REGION_BOUNDS,
  CEREBELLUM_AXES,
  CEREBELLUM_OFFSET,
  BRAINSTEM_TOP,
  BRAINSTEM_BOTTOM,
  BRAINSTEM_RADIUS,
} from "../graph/brainHull";
import type { BrainRegionName } from "../graph/brainHull";

// Statische Gehirn-Huelle: einmal generiert, fuer alle Renders identisch.
const BRAIN = generateBrainHull(4800);

const HULL_COLOR: [number, number, number] = [1, 1, 1];

// Level of Detail: Makro zeigt nur Hubs (Ueberblick mit Labels),
// Meso die gut verbundenen Knoten, Micro alles. limit=0 = kein Limit.
type LODLevel = "macro" | "meso" | "micro";
const LOD_LIMITS: Record<LODLevel, number> = { macro: 25, meso: 80, micro: 0 };
const LOD_LABELS_ALWAYS: Record<LODLevel, boolean> = {
  macro: true,
  meso: false,
  micro: false,
};

interface Props {
  nodes: Node[];
  edges: Edge[];
  highlightedNodeIds: string[];
  visibleTypes: Set<NodeType>;
  onNodeSelect: (id: string) => void;
  selectedNodeId?: string | null;
  onDeselectNode?: () => void;
}

// Kamera-Distanz der Ausgangsposition [0, 1.2, 6.5] - fuer den
// Zoom-Out/Reset-Button, der zu dieser Ansicht zurueckspringt.
const DEFAULT_CAMERA_DISTANCE = Math.hypot(0, 1.2, 6.5);

function truncate(title: string, max = 26): string {
  // Dateiendungen sind im Graph nur Rauschen - der Titel zaehlt.
  const clean = title.replace(/\.(md|txt|pdf|jpe?g|png|webp)$/i, "");
  return clean.length > max ? clean.slice(0, max - 1) + "\u2026" : clean;
}

// Makro zeigt Labels nur fuer die wichtigsten Hubs - alle 25 gleichzeitig
// ueberlappen sich auf kleinen Screens zu einem unlesbaren Klumpen.
const MACRO_LABEL_LIMIT = 12;

// Hex-Farbe -> THREE.Color, skaliert damit Bloom es zum Leuchten bringt.
function glowColor(hex: string, intensity = 1.9): THREE.Color {
  return new THREE.Color(hex).multiplyScalar(intensity);
}

type Vec3 = [number, number, number];

interface Layout {
  positions: Map<string, Vec3>;
  visibleNodes: Node[];
  visibleEdges: Edge[];
}

type SimNode3D = Node & {
  x?: number;
  y?: number;
  z?: number;
  vx?: number;
  vy?: number;
  vz?: number;
};

// Areal-Mapping: jeder NodeType hat einen groben Anker in einer Gehirn-Region.
// Orientierung: +z frontal (zum Betrachter), -z occipital, +y dorsal/oben,
// x < 0 linke Hemisphaere (logisch/analytisch: task, commit, concept),
// x > 0 rechte Hemisphaere (visuell/kreativ: note, screenshot) - passend zur
// scene_x<0="Left"-Konvention der echten Mesh-Regionen in brainHull.ts.
// document/project bleiben mittig, da sie beides sein koennen. Eine moderate
// Force zieht jeden Typ in sein Areal, ohne die Topologie (link/charge) zu
// dominieren.
const AREA_ANCHORS: Partial<Record<NodeType, Vec3>> = {
  project: [0.0, 0.15, 0.95],
  task: [-0.6, 0.35, 0.6],
  concept: [-0.6, 0.6, 0.15],
  document: [0.0, 0.0, 0.1],
  note: [0.8, 0.0, 0.1],
  commit: [-0.55, -0.1, -0.7],
  screenshot: [0.6, 0.15, -1.0],
};

// Explizite Hemisphaeren-Anker, wenn n.hemisphere === "left"|"right" gesetzt ist.
// Sie uebersteuern das typbasierte AREA_ANCHORS-Mapping. y/z bleiben nahe 0,
// damit das Areal-Mapping (frontal/occipital/dorsal) weiterhin greifen kann -
// nur die x-Komponente wird explizit auf die Hemisphaere gezwungen.
const LEFT_ANCHOR: Vec3 = [-0.7, 0.0, 0.0];
const RIGHT_ANCHOR: Vec3 = [0.7, 0.0, 0.0];

// Liefert den Areal-Anker fuer einen Node. Explizite hemisphere-Werte
// ("left"/"right") schlagen das typbasierte Mapping, "auto" (Default) nutzt
// AREA_ANCHORS wie bisher. Node ohne passendes AREA_ANCHORS (z.B. person)
// bekommt keinen Anker und wird von den uebrigen Kraeften verteilt.
function anchorFor(n: Pick<Node, "hemisphere" | "type">): Vec3 | undefined {
  if (n.hemisphere === "left") return LEFT_ANCHOR;
  if (n.hemisphere === "right") return RIGHT_ANCHOR;
  return AREA_ANCHORS[n.type];
}

function areaForce(nodes: SimNode3D[], strength = 0.08) {
  return (alpha: number) => {
    for (const n of nodes) {
      const anchor = anchorFor(n);
      if (!anchor) continue;
      n.vx = (n.vx ?? 0) + (anchor[0] - (n.x ?? 0)) * strength * alpha;
      n.vy = (n.vy ?? 0) + (anchor[1] - (n.y ?? 0)) * strength * alpha;
      n.vz = (n.vz ?? 0) + (anchor[2] - (n.z ?? 0)) * strength * alpha;
    }
  };
}

// Stabile Positionen: die Force-Simulation laeuft einmal ueber ALLE Nodes
// (unabhaengig vom Filter), damit Knoten beim Filter-/LOD-Wechsel nicht
// umherspringen und das raeumliche Gedaechtnis des Users erhalten bleibt.
function computePositions(nodes: Node[], edges: Edge[]): Map<string, Vec3> {
  // d3-force-3d: gleiche API wie d3-force, aber mit 3 Dimensionen.
  const sim: SimNode3D[] = nodes.map((n) => ({ ...n }));
  const links = edges.map((e) => ({ source: e.source, target: e.target }));
  const simulation = forceSimulation<SimNode3D>(sim, 3)
    .force(
      "link",
      forceLink<SimNode3D, { source: string; target: string }>(links)
        .id((d) => d.id)
        .distance(5)
    )
    .force("charge", forceManyBody<SimNode3D>().strength(-16))
    .force("center", forceCenter<SimNode3D>(0, 0, 0))
    .force("areas", areaForce(sim, 0.045));

  // Synchrone Stabilisierung - reicht fuer ein stabiles 3D-Layout.
  for (let i = 0; i < 320; i++) simulation.tick();
  simulation.stop();

  // Knotenwolke ins Gehirn-Innere passen: ellipsoid-normalisierte Skalierung,
  // sodass der am weitesten aussen liegende Knoten ~FIT_FACTOR der
  // Gehirn-Halbachsen erreicht (mit Padding fuer die zerklueftete Oberflaeche).
  const { x: ax, y: ay, z: az } = BRAIN_AXES;
  let maxNorm = 1e-6;
  for (const n of sim) {
    const nx = (n.x ?? 0) / ax;
    const ny = (n.y ?? 0) / ay;
    const nz = (n.z ?? 0) / az;
    const nr = Math.sqrt(nx * nx + ny * ny + nz * nz);
    if (nr > maxNorm) maxNorm = nr;
  }
  const FIT_FACTOR = 0.8;
  const k = FIT_FACTOR / maxNorm;

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

  return positions;
}

// Reine Sichtbarkeits-Auswahl auf den stabilen Positionen - kein Re-Simulieren
// mehr, nur Grad-Ranking (fuer LOD) und Filter nach Typ/Kanten.
function selectVisible(
  positions: Map<string, Vec3>,
  nodes: Node[],
  edges: Edge[],
  visible: Set<NodeType>,
  limit: number,
  forceShow: Set<string>
): Layout {
  const typeVisible = nodes.filter((n) => visible.has(n.type));
  const typeIds = new Set(typeVisible.map((n) => n.id));

  // Grad jedes Knotens innerhalb der typ-sichtbaren Menge - echter
  // Struktursignal, denn Konzepte mit vielen mentions werden zu Hubs.
  const degree = new Map<string, number>();
  for (const n of typeVisible) degree.set(n.id, 0);
  for (const e of edges) {
    if (typeIds.has(e.source) && typeIds.has(e.target)) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }
  }

  // LOD: Top-N nach Grad (limit=0 = alle). Suchtreffer (forceShow)
  // umgehen das Limit, damit Treffer stets sichtbar bleiben.
  const ranked = [...typeVisible].sort(
    (a, b) =>
      (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0) ||
      (a.id < b.id ? -1 : 1)
  );
  const visibleNodes: Node[] =
    limit > 0 && ranked.length > limit ? ranked.slice(0, limit) : ranked;
  const visibleIds = new Set(visibleNodes.map((n) => n.id));
  for (const n of typeVisible) {
    if (forceShow.has(n.id) && !visibleIds.has(n.id)) {
      visibleNodes.push(n);
      visibleIds.add(n.id);
    }
  }

  const visibleEdges = edges.filter(
    (e) => visibleIds.has(e.source) && visibleIds.has(e.target)
  );

  return { positions, visibleNodes, visibleEdges };
}

const HULL_COLOR_BASE = new THREE.Color(...HULL_COLOR);
const HULL_COLOR_HOVER = new THREE.Color(...HULL_COLOR).multiplyScalar(2.2);

// Ein Points-Objekt pro Gehirn-Region, damit Hover-Zustand nur den
// betroffenen Bereich heller/groesser macht statt der ganzen Huelle.
// memo() ist hier wichtig: ohne ihn rendern bei jedem Hover-Wechsel alle elf
// Regionen (inkl. Material-Neuallokation) statt nur der betroffenen, was auf
// schwaecherer GPU/GC-Kombination (v.a. Firefox) zu Rucklern fuehrt.
const BrainRegionPoints = memo(function BrainRegionPoints({
  positions,
  colors,
  hovered,
}: {
  positions: Float32Array;
  colors: Float32Array;
  hovered: boolean;
}) {
  return (
    <points>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[colors, 3]} />
      </bufferGeometry>
      <pointsMaterial
        vertexColors
        size={hovered ? 0.015 : 0.009}
        sizeAttenuation
        transparent
        opacity={hovered ? 1 : 0.8}
        color={hovered ? HULL_COLOR_HOVER : HULL_COLOR_BASE}
        toneMapped={false}
        depthWrite={false}
      />
    </points>
  );
});

// Kamera-Fokus-Anfrage: Klick auf eine Hitbox setzt ein neues Ziel mit
// eindeutigem key, CameraRig animiert einmalig dorthin (siehe unten).
interface FocusRequest {
  center: Vec3;
  distance: number;
  key: number;
}

function BrainHull({
  onRegionFocus,
  onRegionSelect,
}: {
  onRegionFocus: (center: Vec3, distance: number) => void;
  onRegionSelect: (region: BrainRegionName) => void;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const [hovered, setHovered] = useState<BrainRegionName | null>(null);

  useFrame((state) => {
    if (!groupRef.current) return;
    const s = 1 + Math.sin(state.clock.elapsedTime * 0.6) * 0.015;
    groupRef.current.scale.setScalar(s);
  });

  // Kapsel-Mittelpunkt/-Laenge/-Rotation fuer die unsichtbare
  // Brainstem-Hitbox, aus BRAINSTEM_TOP/BOTTOM abgeleitet.
  const brainstemCapsule = useMemo(() => {
    const top = new THREE.Vector3(...BRAINSTEM_TOP);
    const bottom = new THREE.Vector3(...BRAINSTEM_BOTTOM);
    const mid = top.clone().lerp(bottom, 0.5);
    const dir = bottom.clone().sub(top);
    const length = dir.length();
    const quaternion = new THREE.Quaternion().setFromUnitVectors(
      new THREE.Vector3(0, 1, 0),
      dir.normalize()
    );
    return { mid, length, quaternion };
  }, []);

  // Einmalig geslict statt pro Render: subarray() liefert sonst bei jedem
  // Render neue View-Objekte, wodurch memo() auf BrainRegionPoints trotz
  // gleicher Daten immer ein Re-Render ausloesen wuerde (siehe oben).
  const regionSlices = useMemo(
    () =>
      Object.fromEntries(
        BRAIN_REGION_NAMES.map((name) => [
          name,
          {
            positions: BRAIN.positions.subarray(
              BRAIN.regions[name].start * 3,
              (BRAIN.regions[name].start + BRAIN.regions[name].count) * 3
            ),
            colors: BRAIN.colors.subarray(
              BRAIN.regions[name].start * 3,
              (BRAIN.regions[name].start + BRAIN.regions[name].count) * 3
            ),
          },
        ])
      ) as Record<BrainRegionName, { positions: Float32Array; colors: Float32Array }>,
    []
  );

  // Liegt ein Graph-Knoten irgendwo im Raycast-Pfad, hat er Vorrang: die
  // Region-Hitbox greift sonst vor den (viel kleineren) Node-Kugeln, die
  // dadurch teilweise nicht mehr klickbar waeren.
  const hasNodeIntersection = (e: ThreeEvent<PointerEvent> | ThreeEvent<MouseEvent>) =>
    e.intersections.some((i) => i.object.userData?.isGraphNode);

  const setRegionHover = (region: BrainRegionName) => (e: ThreeEvent<PointerEvent>) => {
    if (hasNodeIntersection(e)) return;
    e.stopPropagation();
    setHovered(region);
    document.body.style.cursor = "pointer";
  };
  const clearRegionHover = (region: BrainRegionName) => () => {
    setHovered((h) => (h === region ? null : h));
    document.body.style.cursor = "auto";
  };
  const focusRegion =
    (region: BrainRegionName, center: Vec3, radius: number) => (e: ThreeEvent<MouseEvent>) => {
      if (hasNodeIntersection(e)) return;
      e.stopPropagation();
      onRegionFocus(center, Math.max(1.4, radius * 2.6));
      onRegionSelect(region);
    };

  return (
    <group ref={groupRef}>
      {BRAIN_REGION_NAMES.map((name) => (
        <BrainRegionPoints
          key={name}
          {...regionSlices[name]}
          hovered={hovered === name}
        />
      ))}

      {/* Unsichtbare Hitboxen fuer sauberes Hover-/Klick-Erkennen pro Region -
          Raycasting direkt auf die duennen Punktwolken waere zu fummelig. */}
      {BRAIN_REGION_NAMES.filter((name) => name !== "brainstem").map((name) => {
        const { axes, offset } = REGION_BOUNDS[name];
        return (
          <mesh
            key={name}
            position={offset}
            scale={[axes.x, axes.y, axes.z]}
            onPointerOver={setRegionHover(name)}
            onPointerOut={clearRegionHover(name)}
            onClick={focusRegion(name, offset, Math.max(axes.x, axes.y, axes.z))}
          >
            <sphereGeometry args={[1, 16, 16]} />
            <meshBasicMaterial transparent opacity={0} depthWrite={false} />
          </mesh>
        );
      })}
      <mesh
        position={brainstemCapsule.mid}
        quaternion={brainstemCapsule.quaternion}
        onPointerOver={setRegionHover("brainstem")}
        onPointerOut={clearRegionHover("brainstem")}
        onClick={focusRegion(
          "brainstem",
          [brainstemCapsule.mid.x, brainstemCapsule.mid.y, brainstemCapsule.mid.z],
          brainstemCapsule.length / 2 + BRAINSTEM_RADIUS
        )}
      >
        <cylinderGeometry args={[BRAINSTEM_RADIUS, BRAINSTEM_RADIUS, brainstemCapsule.length, 12]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
    </group>
  );
}

// Animiert Kamera + OrbitControls-Target einmalig (ease-out, ~0.8s) zu einer
// Fokus-Anfrage. Nach Abschluss wird die Kamera nicht mehr angefasst, damit
// der User danach wieder frei orbiten/zoomen kann (kein "Kampf" mit Input).
function CameraRig({
  request,
  controlsRef,
}: {
  request: FocusRequest | null;
  controlsRef: RefObject<OrbitControlsImpl | null>;
}) {
  const anim = useRef<{
    key: number;
    startTarget: THREE.Vector3;
    startPos: THREE.Vector3;
    endTarget: THREE.Vector3;
    endDist: number;
    t: number;
  } | null>(null);

  if (request && anim.current?.key !== request.key && controlsRef.current) {
    const controls = controlsRef.current;
    anim.current = {
      key: request.key,
      startTarget: controls.target.clone(),
      startPos: controls.object.position.clone(),
      endTarget: new THREE.Vector3(...request.center),
      endDist: request.distance,
      t: 0,
    };
  }

  useFrame((_, delta) => {
    const a = anim.current;
    const controls = controlsRef.current;
    if (!a || !controls || a.t >= 1) return;
    a.t = Math.min(1, a.t + delta / 0.8);
    const ease = 1 - Math.pow(1 - a.t, 3);
    const target = a.startTarget.clone().lerp(a.endTarget, ease);
    const dir = a.startPos.clone().sub(a.startTarget);
    const dist = THREE.MathUtils.lerp(dir.length(), a.endDist, ease);
    dir.setLength(dist);
    controls.target.copy(target);
    controls.object.position.copy(target).add(dir);
    controls.update();
  });

  return null;
}

const PARTICLE_COUNT = 600;

// Schwebende Staub-/Neuron-Partikel in einer Kugelschale um die Szene,
// rotieren langsam fuer Tiefenwahrnehmung.
function FloatingParticles() {
  const ref = useRef<THREE.Points>(null);
  const positions = useMemo(() => {
    const arr = new Float32Array(PARTICLE_COUNT * 3);
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const r = 2.6 + Math.random() * 3.9;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      arr[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      arr[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      arr[i * 3 + 2] = r * Math.cos(phi);
    }
    return arr;
  }, []);
  useFrame((_, delta) => {
    if (!ref.current) return;
    ref.current.rotation.y += delta * 0.03;
    ref.current.rotation.x += delta * 0.01;
  });
  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial
        size={0.01}
        sizeAttenuation
        transparent
        opacity={0.5}
        color={new THREE.Color(0.1, 0.6, 0.9)}
        toneMapped={false}
        depthWrite={false}
      />
    </points>
  );
}

function GraphEdges({
  layout,
  hoveredId,
  focusedRegion,
  selectedNodeId,
}: {
  layout: Layout;
  hoveredId: string | null;
  focusedRegion: BrainRegionName | null;
  selectedNodeId: string | null;
}) {
  const { base, bright, contradicts } = useMemo(() => {
    const baseArr: number[] = [];
    const brightArr: number[] = [];
    const contradictsArr: number[] = [];
    for (const e of layout.visibleEdges) {
      const a = layout.positions.get(e.source);
      const b = layout.positions.get(e.target);
      if (!a || !b) continue;
      if (
        focusedRegion !== null &&
        (nearestRegion(a) !== focusedRegion || nearestRegion(b) !== focusedRegion)
      ) {
        continue;
      }
      const isSelectedEdge =
        selectedNodeId !== null && (e.source === selectedNodeId || e.target === selectedNodeId);
      // Bei aktivem Node nur dessen eigene Kanten zeigen - Rest komplett
      // ausblenden statt nur zu dimmen (konsistent zum Ego-Netzwerk-Fokus).
      if (selectedNodeId !== null && !isSelectedEdge) continue;
      if (e.relation_type === "contradicts") {
        contradictsArr.push(...a, ...b);
        continue;
      }
      baseArr.push(...a, ...b);
      if ((hoveredId && (e.source === hoveredId || e.target === hoveredId)) || isSelectedEdge) {
        brightArr.push(...a, ...b);
      }
    }
    return {
      base: new Float32Array(baseArr),
      bright: new Float32Array(brightArr),
      contradicts: new Float32Array(contradictsArr),
    };
  }, [layout, hoveredId, focusedRegion, selectedNodeId]);

  return (
    <>
      <lineSegments>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[base, 3]} />
        </bufferGeometry>
        <lineBasicMaterial
          color={new THREE.Color(0.1, 0.35, 0.45)}
          transparent
          opacity={hoveredId ? 0.03 : 0.1}
          toneMapped={false}
        />
      </lineSegments>
      {bright.length > 0 && (
        <lineSegments>
          <bufferGeometry>
            <bufferAttribute attach="attributes-position" args={[bright, 3]} />
          </bufferGeometry>
          <lineBasicMaterial
            color={new THREE.Color(0.1, 1.4, 1.7)}
            transparent
            opacity={0.9}
            toneMapped={false}
          />
        </lineSegments>
      )}
      {contradicts.length > 0 && (
        <lineSegments>
          <bufferGeometry>
            <bufferAttribute attach="attributes-position" args={[contradicts, 3]} />
          </bufferGeometry>
          <lineBasicMaterial
            color={new THREE.Color(1.6, 0.15, 0.15)}
            transparent
            opacity={0.85}
            toneMapped={false}
          />
        </lineSegments>
      )}
    </>
  );
}

// Radar-Ping fuer den aktiven Node: mehrere flache Ringe expandieren aus dem
// Node heraus und faden dabei aus, versetzt gestartet fuer einen Sonar-Effekt.
// Die Ring-Gruppe wird jeden Frame zur Kamera hin ausgerichtet (Billboard),
// da ringGeometry sonst nur aus einem Blickwinkel als Kreis erscheint.
const RADAR_RING_COUNT = 3;
const RADAR_CYCLE_SECONDS = 1.6;
const RADAR_MIN_RADIUS = 0.02;
const RADAR_MAX_RADIUS = 0.16;

function RadarRings({ position, color }: { position: Vec3; color: string }) {
  const groupRef = useRef<THREE.Group>(null);
  const ringRefs = useRef<(THREE.Mesh | null)[]>([]);
  const matRefs = useRef<(THREE.MeshBasicMaterial | null)[]>([]);

  useFrame((state) => {
    if (groupRef.current) {
      groupRef.current.quaternion.copy(state.camera.quaternion);
    }
    const t = state.clock.elapsedTime;
    for (let i = 0; i < RADAR_RING_COUNT; i++) {
      const mesh = ringRefs.current[i];
      const mat = matRefs.current[i];
      if (!mesh || !mat) continue;
      const offset = (i / RADAR_RING_COUNT) * RADAR_CYCLE_SECONDS;
      const phase = ((t + offset) % RADAR_CYCLE_SECONDS) / RADAR_CYCLE_SECONDS;
      const radius = RADAR_MIN_RADIUS + phase * (RADAR_MAX_RADIUS - RADAR_MIN_RADIUS);
      mesh.scale.setScalar(radius);
      mat.opacity = (1 - phase) * 0.7;
    }
  });

  return (
    <group ref={groupRef} position={position}>
      {Array.from({ length: RADAR_RING_COUNT }).map((_, i) => (
        <mesh
          key={i}
          ref={(el) => {
            ringRefs.current[i] = el;
          }}
        >
          <ringGeometry args={[0.85, 1, 32]} />
          <meshBasicMaterial
            ref={(el) => {
              matRefs.current[i] = el;
            }}
            color={color}
            transparent
            opacity={0}
            side={THREE.DoubleSide}
            toneMapped={false}
            depthWrite={false}
          />
        </mesh>
      ))}
    </group>
  );
}

// Ordnet eine Position der naechstgelegenen Hirn-Region zu (per
// Zentrums-Distanz) - garantiert eine luecken- und ueberlappungsfreie
// Zuordnung, im Gegensatz zu einem strikten Ellipsoid-Containment-Test, bei
// dem Knoten in den Zwischenraeumen zweier Lappen bei keiner Region landen
// wuerden.
function nearestRegion(pos: Vec3): BrainRegionName {
  let best: BrainRegionName = BRAIN_REGION_NAMES[0];
  let bestDist = Infinity;
  for (const name of BRAIN_REGION_NAMES) {
    const { offset } = REGION_BOUNDS[name];
    const dx = pos[0] - offset[0];
    const dy = pos[1] - offset[1];
    const dz = pos[2] - offset[2];
    const d = dx * dx + dy * dy + dz * dz;
    if (d < bestDist) {
      bestDist = d;
      best = name;
    }
  }
  return best;
}

function GraphNodes({
  layout,
  highlighted,
  hoveredId,
  setHoveredId,
  onSelect,
  labelsAlways,
  focusedRegion,
  selectedNodeId,
}: {
  layout: Layout;
  highlighted: Set<string>;
  hoveredId: string | null;
  setHoveredId: (id: string | null) => void;
  onSelect: (id: string) => void;
  labelsAlways: boolean;
  focusedRegion: BrainRegionName | null;
  selectedNodeId: string | null;
}) {
  // Verbundene Knoten beim Hover fuer Hervorhebung.
  const connected = useMemo(() => {
    const set = new Set<string>();
    if (!hoveredId) return set;
    set.add(hoveredId);
    for (const e of layout.visibleEdges) {
      if (e.source === hoveredId) set.add(e.target);
      if (e.target === hoveredId) set.add(e.source);
    }
    return set;
  }, [hoveredId, layout.visibleEdges]);

  // Ego-Netzwerk des aktiven (ausgewaehlten) Knotens: er selbst + direkte
  // Nachbarn bleiben sichtbar, alles andere wird ausgeblendet - konsistent
  // zum Areal-Fokus, der ebenfalls alles ausserhalb komplett versteckt statt
  // nur zu dimmen.
  const selectedNeighbors = useMemo(() => {
    const set = new Set<string>();
    if (!selectedNodeId) return set;
    set.add(selectedNodeId);
    for (const e of layout.visibleEdges) {
      if (e.source === selectedNodeId) set.add(e.target);
      if (e.target === selectedNodeId) set.add(e.source);
    }
    return set;
  }, [selectedNodeId, layout.visibleEdges]);

  // visibleNodes ist nach Grad sortiert, der Index ist also der Hub-Rang.
  const showLabelFor = (id: string, rank: number): boolean =>
    (labelsAlways && rank < MACRO_LABEL_LIMIT) || highlighted.has(id) || id === hoveredId;

  // Dezent atmende Knoten: ein useFrame updated alle Mesh-Skalen direkt
  // (kein React-Re-Render), baseScales wird pro Render aus hover/hi-State neu
  // gesetzt und mit einer sinus-Pulsation ueberlagert.
  const meshRefs = useRef<(THREE.Mesh | null)[]>([]);
  const baseScales = useRef<number[]>([]);
  const phases = useMemo(
    () => layout.visibleNodes.map(() => Math.random() * Math.PI * 2),
    [layout.visibleNodes]
  );
  useFrame((state) => {
    const t = state.clock.elapsedTime;
    const refs = meshRefs.current;
    const bases = baseScales.current;
    for (let i = 0; i < refs.length; i++) {
      const mesh = refs[i];
      const base = bases[i];
      if (mesh && base) {
        const isSelected = layout.visibleNodes[i]?.id === selectedNodeId;
        const amplitude = isSelected ? 0.4 : 0.05;
        const speed = isSelected ? 2.4 : 1.6;
        const pulse = 1 + Math.sin(t * speed + phases[i]) * amplitude;
        mesh.scale.setScalar(base * pulse);
      }
    }
  });

  return (
    <>
      {layout.visibleNodes.map((n, i) => {
        const pos = layout.positions.get(n.id) ?? ([0, 0, 0] as Vec3);
        const isHi = highlighted.has(n.id);
        const isHover = hoveredId === n.id;
        const outsideFocus = focusedRegion !== null && nearestRegion(pos) !== focusedRegion;
        const hiddenBySelection = selectedNodeId !== null && !selectedNeighbors.has(n.id);
        if (outsideFocus || hiddenBySelection) return null;
        const dimmed = hoveredId !== null && !connected.has(n.id);
        const r = Math.max(0.005, 0.0045 + (n.importance ?? 0.3) * 0.01);
        const scale = isHover ? 1.6 : isHi ? 1.3 : 1;
        const intensity = isHover || isHi ? 2.6 : dimmed ? 0.5 : 1.8;
        baseScales.current[i] = scale * r;
        return (
          <Fragment key={n.id}>
            <mesh
              position={pos}
              userData={{ isGraphNode: true }}
              ref={(el) => {
                meshRefs.current[i] = el;
              }}
              onPointerOver={(e) => {
                e.stopPropagation();
                setHoveredId(n.id);
                document.body.style.cursor = "pointer";
              }}
              onPointerOut={() => {
                setHoveredId(null);
                document.body.style.cursor = "auto";
              }}
              onClick={(e) => {
                e.stopPropagation();
                onSelect(n.id);
              }}
            >
              <sphereGeometry args={[1, 16, 16]} />
              <meshBasicMaterial
                color={glowColor(NODE_TYPE_COLORS[n.type] ?? "#888", intensity)}
                transparent
                opacity={dimmed ? 0.2 : 1}
                toneMapped={false}
              />
              {showLabelFor(n.id, i) && (
                <Html
                  position={[0, 1.4, 0]}
                  zIndexRange={[4, 0]}
                  style={{ pointerEvents: "none" }}
                >
                  <div className="graph-node-label">
                    <span
                      className="graph-node-label-dot"
                      style={{ background: NODE_TYPE_COLORS[n.type] }}
                    />
                    {truncate(n.title)}
                  </div>
                </Html>
              )}
            </mesh>
            {n.id === selectedNodeId && (
              <RadarRings position={pos} color={NODE_TYPE_COLORS[n.type] ?? "#888"} />
            )}
          </Fragment>
        );
      })}
    </>
  );
}

export function GraphCanvas({
  nodes,
  edges,
  highlightedNodeIds,
  visibleTypes,
  onNodeSelect,
  selectedNodeId = null,
  onDeselectNode,
}: Props) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [lod, setLod] = useState<LODLevel>("micro");
  const [focusRequest, setFocusRequest] = useState<FocusRequest | null>(null);
  const [focusedRegion, setFocusedRegion] = useState<BrainRegionName | null>(null);
  const [autoRotateOn, setAutoRotateOn] = useState(true);
  const controlsRef = useRef<OrbitControlsImpl | null>(null);

  const handleRegionFocus = (center: Vec3, distance: number) => {
    setFocusRequest({ center, distance, key: Date.now() });
  };
  // Erneuter Klick auf dieselbe Region hebt die Abdunklung wieder auf.
  const handleRegionSelect = (region: BrainRegionName) => {
    setFocusedRegion((prev) => (prev === region ? null : region));
  };
  // Zoom-Out/Reset: Areal-Fokus und Node-Auswahl aufheben, LOD auf "alle
  // Knoten" und Kamera zur Ausgangsposition zurueckfahren - zeigt wieder das
  // komplette Hirn mit allen Knoten.
  const handleResetView = () => {
    setFocusedRegion(null);
    setLod("micro");
    onDeselectNode?.();
    handleRegionFocus([0, 0, 0], DEFAULT_CAMERA_DISTANCE);
  };

  const highlighted = useMemo(
    () => new Set(highlightedNodeIds),
    [highlightedNodeIds]
  );

  // Positionen nur bei echten Graph-Aenderungen neu berechnen (neue/entfernte
  // Nodes/Edges) - NICHT bei Filter-/LOD-Wechsel, damit Knoten am Platz
  // bleiben und das raeumliche Gedaechtnis erhalten bleibt.
  const positions = useMemo(() => computePositions(nodes, edges), [nodes, edges]);
  const layout = useMemo(
    () => selectVisible(positions, nodes, edges, visibleTypes, LOD_LIMITS[lod], highlighted),
    [positions, nodes, edges, visibleTypes, lod, highlighted]
  );

  return (
    <div className="graph-wrapper">
      <Canvas
        camera={{ position: [0, 1.2, 6.5], fov: 55 }}
        gl={{ antialias: true }}
        onPointerMissed={() => setHoveredId(null)}
      >
        <color attach="background" args={["#05070d"]} />
        <fog attach="fog" args={["#05070d", 7, 16]} />

        <BrainHull onRegionFocus={handleRegionFocus} onRegionSelect={handleRegionSelect} />
        <FloatingParticles />
        <GraphEdges
          layout={layout}
          hoveredId={hoveredId}
          focusedRegion={focusedRegion}
          selectedNodeId={selectedNodeId}
        />
        <GraphNodes
          layout={layout}
          highlighted={highlighted}
          focusedRegion={focusedRegion}
          hoveredId={hoveredId}
          setHoveredId={setHoveredId}
          onSelect={onNodeSelect}
          labelsAlways={LOD_LABELS_ALWAYS[lod]}
          selectedNodeId={selectedNodeId}
        />
        <CameraRig request={focusRequest} controlsRef={controlsRef} />

        <OrbitControls
          ref={controlsRef}
          enableDamping
          dampingFactor={0.08}
          rotateSpeed={0.7}
          zoomSpeed={0.8}
          minDistance={0.15}
          maxDistance={14}
          autoRotate={autoRotateOn}
          autoRotateSpeed={0.1}
        />
        <EffectComposer>
          <Bloom
            mipmapBlur
            intensity={1.4}
            luminanceThreshold={0.1}
            luminanceSmoothing={0.5}
            radius={0.8}
          />
          <DepthOfField
            target={[0, 0, 0]}
            bokehScale={0.7}
            focalLength={0.08}
          />
        </EffectComposer>
      </Canvas>

      <div className="graph-controls">
        <button
          type="button"
          className="graph-icon-btn"
          onClick={() => setAutoRotateOn((on) => !on)}
          title={autoRotateOn ? "Rotation stoppen" : "Rotation starten"}
        >
          {autoRotateOn ? "⏸" : "▶"}
        </button>
        <button
          type="button"
          className="graph-icon-btn"
          onClick={handleResetView}
          title="Ansicht zuruecksetzen: komplettes Hirn, alle Knoten"
        >
          ⤢
        </button>
      </div>

      <div className="graph-lod">
        {(["macro", "meso", "micro"] as LODLevel[]).map((lvl) => (
          <button
            key={lvl}
            type="button"
            className={`lod-btn ${lod === lvl ? "active" : ""}`}
            onClick={() => setLod(lvl)}
            title={
              lvl === "macro"
                ? "Übersicht: nur die wichtigsten Knoten"
                : lvl === "meso"
                  ? "Cluster: gut verbundene Knoten"
                  : "Detail: alle Knoten"
            }
          >
            {lvl === "macro" ? "Übersicht" : lvl === "meso" ? "Cluster" : "Detail"}
          </button>
        ))}
      </div>

    </div>
  );
}
