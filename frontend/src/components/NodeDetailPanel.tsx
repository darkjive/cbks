import type { ReactNode } from "react";
import type { NodeDetailResponse, Edge } from "../api/types";
import { NODE_TYPE_COLORS } from "../graph/colors";

interface Props {
  detail: NodeDetailResponse | null;
  edges: Edge[];
  onClose: () => void;
  onDelete: () => void;
  audioState: "idle" | "loading" | "playing";
  onPlayAudio: () => void;
  onStopAudio: () => void;
}

function MetricBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="metric">
      <div className="metric-label">
        <span>{label}</span>
        <span className="metric-value">{pct}%</span>
      </div>
      <div className="metric-bar">
        <div className="metric-fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function MetricRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="metric-row">
      <span>{label}</span>
      <span>{value}</span>
    </div>
  );
}

function formatDate(iso?: string): string {
  if (!iso) return "\u2014";
  try {
    return new Date(iso).toLocaleString("de-DE", {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

export function NodeDetailPanel({
  detail, edges, onClose, onDelete, audioState, onPlayAudio, onStopAudio,
}: Props) {
  if (!detail) return null;
  const { node, neighbors } = detail;

  const neighborMap = new Map(neighbors.map((n) => [n.id, n]));
  const myEdges = edges.filter(
    (e) => e.source === node.id || e.target === node.id
  );

  const emotionalColor = node.emotional_weight >= 0 ? "#6CE07A" : "#E06C8E";

  return (
    <aside className="node-detail-panel">
      <div className="detail-title-row">
        <h2 className="detail-title">{node.title}</h2>
        {node.content && (
          audioState === "loading" ? (
            <button type="button" className="audio-btn" disabled title="Wird geladen\u2026">
              {"\u23F3"}
            </button>
          ) : audioState === "playing" ? (
            <button type="button" className="audio-btn" onClick={onStopAudio} title="Vorlesen stoppen">
              {"\u23F9"}
            </button>
          ) : (
            <button type="button" className="audio-btn" onClick={onPlayAudio} title="Vorlesen">
              {"\u25B6"}
            </button>
          )
        )}
        <button type="button" className="detail-close" onClick={onClose} aria-label="Schließen">
          {"✕"}
        </button>
      </div>
      <div
        className="node-type-tag"
        style={{ color: NODE_TYPE_COLORS[node.type] ?? "#ccc" }}
      >
        {"\u25CF"} {node.type}
      </div>

      {node.content && (
        <div className="detail-section">
          <h3>Inhalt</h3>
          <p className="detail-content">{node.content}</p>
        </div>
      )}

      <div className="detail-section">
        <h3>Neuronale Metriken</h3>
        <MetricBar label="Aktivierung" value={node.activation} />
        <MetricBar label="Konfidenz" value={node.confidence} />
        <MetricBar label="Wichtigkeit" value={node.importance} />
        <MetricRow
          label="Emotionale Gewichtung"
          value={
            <span style={{ color: emotionalColor }}>
              {node.emotional_weight.toFixed(2)}
            </span>
          }
        />
        <MetricRow label={"Decay-Rate (\u03BB)"} value={node.decay_rate.toFixed(4)} />
      </div>

      <div className="detail-section">
        <h3>Lebenslauf</h3>
        <MetricRow label="Zugriffe" value={String(node.access_counter)} />
        <MetricRow label="Erstellt" value={formatDate(node.creation_time)} />
        <MetricRow label="Letzter Zugriff" value={formatDate(node.last_access)} />
      </div>

      <div className="detail-section">
        <h3>Verbindungen ({myEdges.length})</h3>
        {myEdges.length === 0 ? (
          <p className="muted">Keine Verbindungen</p>
        ) : (
          <ul className="edge-list">
            {myEdges.map((e) => {
              const isSource = e.source === node.id;
              const otherId = isSource ? e.target : e.source;
              const other = neighborMap.get(otherId);
              const arrow = isSource ? "\u2192" : "\u2190";
              return (
                <li
                  key={e.id}
                  className={
                    e.relation_type === "contradicts"
                      ? "edge-item edge-item--contradicts"
                      : "edge-item"
                  }
                >
                  <span className="edge-arrow">{arrow}</span>
                  <span className="edge-relation">{e.relation_type}</span>
                  <span className="edge-target">
                    {other?.title ?? otherId.slice(0, 8)}
                  </span>
                  <span className="edge-strength">
                    {(e.strength * 100).toFixed(0)}%
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <button
        type="button"
        className="btn-danger delete-btn"
        onClick={() => {
          if (window.confirm(`Node "${node.title}" wirklich löschen?`)) onDelete();
        }}
      >
        Node löschen
      </button>
    </aside>
  );
}
