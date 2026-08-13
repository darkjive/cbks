import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { StatsResponse } from "../api/types";

interface Props {
  refreshKey: number;
  onGraphChanged: () => void;
  ttsEnabled: boolean;
  onToggleTts: () => void;
}

interface BusyState {
  retry: boolean;
  rebuild: boolean;
  dedupe: boolean;
  contradictions: boolean;
  backup: boolean;
}

export function StatsBar({ refreshKey, onGraphChanged, ttsEnabled, onToggleTts }: Props) {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [busy, setBusy] = useState<BusyState>({
    retry: false,
    rebuild: false,
    dedupe: false,
    contradictions: false,
    backup: false,
  });
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<StatsResponse>("/stats")
      .then(setStats)
      .catch(() => setStats(null));
  }, [refreshKey]);

  const runAction = async (
    key: keyof BusyState,
    path: string,
    label: string,
    refreshAfter: boolean
  ) => {
    setBusy((b) => ({ ...b, [key]: true }));
    setMessage(null);
    try {
      const result = await apiFetch<{ processed?: number; failed?: number; checked?: number; merged?: number; found?: number; status?: string }>(
        path,
        { method: "POST" }
      );
      if (path === "/backup") {
        setMessage(`${label}: OK`);
      } else if (path === "/dedupe") {
        setMessage(`${label}: ${result?.checked ?? 0} geprüft, ${result?.merged ?? 0} verschmolzen`);
      } else if (path === "/analyze/contradictions") {
        setMessage(`${label}: ${result?.checked ?? 0} geprüft, ${result?.found ?? 0} Widersprüche`);
      } else {
        setMessage(`${label}: ${result?.processed ?? 0} verarbeitet, ${result?.failed ?? 0} fehlgeschlagen`);
      }
      if (refreshAfter) onGraphChanged();
    } catch {
      setMessage(`${label} fehlgeschlagen`);
    } finally {
      setBusy((b) => ({ ...b, [key]: false }));
    }
  };

  const pending = stats?.events?.pending ?? 0;
  const processed = stats?.events?.processed ?? 0;
  const failed = stats?.events?.failed ?? 0;
  const nodeCount = stats?.graph?.nodes ?? 0;
  const edgeCount = stats?.graph?.edges ?? 0;

  return (
    <div className="stats-bar">
      <div className="stats-events">
        <div className="stat-tile stat-pending">
          <span className="stat-value">{pending}</span>
          <span className="stat-label">offen</span>
        </div>
        <div className="stat-tile stat-processed">
          <span className="stat-value">{processed}</span>
          <span className="stat-label">fertig</span>
        </div>
        <div className="stat-tile stat-failed">
          <span className="stat-value">{failed}</span>
          <span className="stat-label">fehler</span>
        </div>
      </div>

      <div className="stats-graph">
        <span className="stat-inline"><strong>{nodeCount}</strong> Knoten</span>
        <span className="stat-inline"><strong>{edgeCount}</strong> Kanten</span>
      </div>

      {message && <p className="stats-message">{message}</p>}

      <div className="stats-actions">
        <button
          className="btn-action"
          disabled={busy.retry}
          onClick={() => runAction("retry", "/retry", "Erneut verarbeiten", true)}
        >
          {busy.retry ? "…" : "Erneut verarbeiten"}
        </button>
        <button
          className="btn-action"
          disabled={busy.dedupe}
          onClick={() => runAction("dedupe", "/dedupe", "Duplikate", true)}
        >
          {busy.dedupe ? "…" : "Duplikate zusammenführen"}
        </button>
        <button
          className="btn-action"
          disabled={busy.contradictions}
          onClick={() => runAction("contradictions", "/analyze/contradictions", "Widersprüche", true)}
        >
          {busy.contradictions ? "…" : "Widersprüche finden"}
        </button>
        <button
          className="btn-action"
          disabled={busy.backup}
          onClick={() => runAction("backup", "/backup", "Backup", false)}
        >
          {busy.backup ? "…" : "Backup erstellen"}
        </button>
        <button
          className="btn-action btn-danger"
          disabled={busy.rebuild}
          onClick={() => {
            if (window.confirm("Graph komplett neu aufbauen? Das kann dauern und verändert den Graphen."))
              runAction("rebuild", "/rebuild", "Neu aufbauen", true);
          }}
        >
          {busy.rebuild ? "…" : "Graph neu aufbauen"}
        </button>
      </div>

      <button
        type="button"
        className="tts-toggle"
        role="switch"
        aria-checked={ttsEnabled}
        onClick={onToggleTts}
      >
        <span>Notizen vorlesen</span>
        <span className={`switch ${ttsEnabled ? "on" : ""}`} aria-hidden="true" />
      </button>
    </div>
  );
}
