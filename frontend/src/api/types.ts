export type NodeType =
  | "concept" | "document" | "task" | "note" | "project" | "commit" | "screenshot" | "person";

export type Hemisphere = "left" | "right" | "auto";

export interface Node {
  id: string;
  title: string;
  type: NodeType;
  hemisphere?: Hemisphere;
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

export interface ChatTurn {
  role: "user" | "assistant";
  content: string;
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

export interface DedupeResponse {
  checked: number;
  merged: number;
}

export interface BackupResponse {
  status: string;
}

export type EventStatus = "pending" | "processed" | "failed";

export interface EventItem {
  id: number;
  event_type: string;
  content_hash: string;
  payload: string;
  source: string;
  status: EventStatus;
  error?: string | null;
  created_at?: string | null;
  processed_at?: string | null;
}

export interface TimelineBucket {
  date: string;
  total: number;
  by_type: Record<string, number>;
}

export interface EmotionBucket {
  date: string;
  avg: number;
  min: number;
  max: number;
  count: number;
}

export interface ConceptStat {
  title: string;
  mentions: number;
}

export interface PatternReport {
  total_nodes: number;
  total_edges: number;
  type_distribution: Record<string, number>;
  sentiment_distribution: { positive: number; neutral: number; negative: number };
  relation_distribution: Record<string, number>;
  top_concepts: ConceptStat[];
}

export interface RecurringTopic {
  title: string;
  mentions: number;
  distinct_days: number;
  first_seen: string;
  last_seen: string;
  span_days: number;
  recurrence_score: number;
}

export interface VaultScanState {
  total: number;
  scanned: number;
  processed: number;
  duplicates: number;
  failed: number;
  processing_total: number;
  processing_done: number;
  done: boolean;
  error: string | null;
}
