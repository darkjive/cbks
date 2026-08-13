// Minimal-Typen fuer d3-force-3d (fork von d3-force mit n-dimensionalem Support).
// Modul liefert keine eigenen Typen aus.
declare module "d3-force-3d" {
  export interface Force<NodeDatum> {}

  export interface Simulation<NodeDatum> {
    force<K extends string>(key: K, force: Force<NodeDatum> | undefined): this;
    force<K extends string>(key: K): Force<NodeDatum> | undefined;
    tick(iterations?: number): this;
    restart(): this;
    stop(): this;
    alpha(alpha?: number): this;
    alphaTarget(target?: number): this;
    on(typenames: string, listener: () => void): this;
  }

  export function forceSimulation<NodeDatum = unknown>(
    nodes?: NodeDatum[],
    numDimensions?: number
  ): Simulation<NodeDatum>;

  export interface ForceLink<NodeDatum, LinkDatum> extends Force<NodeDatum> {
    id(fn: (d: NodeDatum, i: number, data: NodeDatum[]) => string | number): this;
    distance(distance: number): this;
    strength(strength: number): this;
  }

  export function forceLink<NodeDatum = unknown, LinkDatum = unknown>(
    links?: LinkDatum[]
  ): ForceLink<NodeDatum, LinkDatum>;

  export interface ForceManyBody<NodeDatum> extends Force<NodeDatum> {
    strength(strength: number): this;
  }

  export function forceManyBody<NodeDatum = unknown>(): ForceManyBody<NodeDatum>;

  export function forceCenter<NodeDatum = unknown>(
    x?: number,
    y?: number,
    z?: number
  ): Force<NodeDatum>;
}
