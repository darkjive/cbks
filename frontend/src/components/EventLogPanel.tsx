import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { useToast } from "./Toast";
import type { EventItem, EventStatus } from "../api/types";

type Filter = "failed" | "pending" | "all";

interface Props {
  refreshKey: number;
  onGraphChanged: () => void;
}

const FILTERS: { key: Filter; label: string }[] = [
  { key: "failed", label: "Fehler" },
  { key: "pending", label: "Offen" },
  { key: "all", label: "Alle" },
];

function formatTime(iso?: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("de-DE", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "—";
  }
}

function payloadPreview(payload: string): string {
  try {
    const obj = JSON.parse(payload) as Record<string, unknown>;
    const text = (obj.text as string) || (obj.path as string) || (obj.title as string);
    if (typeof text === "string") return text.length > 48 ? text.slice(0, 48) + "…" : text;
    return "";
  } catch {
    return "";
  }
}

// Fehlertexte (z.B. rohe LLM-Antworten) koennen mehrere Bildschirmseiten
// fuellen - gekuerzt anzeigen, Details nur auf Wunsch.
const ERROR_PREVIEW_LEN = 140;

export function EventLogPanel({ refreshKey, onGraphChanged }: Props) {
  const { pushError, pushToast } = useToast();
  const [events, setEvents] = useState<EventItem[]>([]);
  const [filter, setFilter] = useState<Filter>("failed");
  const [loading, setLoading] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [expandedErrors, setExpandedErrors] = useState<Set<number>>(() => new Set());
  const [copiedId, setCopiedId] = useState<number | null>(null);

  const toggleError = (id: number) => {
    setExpandedErrors((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const copyError = async (id: number, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(id);
      setTimeout(() => setCopiedId((cur) => (cur === id ? null : cur)), 1500);
    } catch {
      pushToast("Fehlermeldung konnte nicht kopiert werden", "error");
    }
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const qs = filter === "all" ? "" : `?status_filter=${filter}`;
    apiFetch<EventItem[]>(`/events${qs}`)
      .then((data) => {
        if (!cancelled) setEvents(data);
      })
      .catch((err) => pushError(err, "Event-Log konnte nicht geladen werden"))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refreshKey, filter, pushError]);

  const runRetry = async () => {
    setRetrying(true);
    try {
      const res = await apiFetch<{ processed: number; failed: number }>("/retry", {
        method: "POST",
      });
      pushToast(`Verarbeitet: ${res.processed}, Fehler: ${res.failed}`, "success");
      onGraphChanged();
    } catch (err) {
      pushError(err, "Retry fehlgeschlagen");
    } finally {
      setRetrying(false);
    }
  };

  return (
    <div className="event-log-panel">
      <div className="event-log-tabs">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            className={`event-tab ${filter === f.key ? "active" : ""}`}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      <ul className="event-list">
        {loading && <li className="event-empty">lade …</li>}
        {!loading && events.length === 0 && (
          <li className="event-empty">keine Events</li>
        )}
        {events.map((ev) => (
          <li key={ev.id} className={`event-row event-${ev.status}`}>
            <div className="event-row-head">
              <span className={`event-status-dot event-status-${ev.status}`} />
              <span className="event-type">{ev.event_type}</span>
              <span className="event-id">#{ev.id}</span>
            </div>
            {payloadPreview(ev.payload) && (
              <p className="event-preview">{payloadPreview(ev.payload)}</p>
            )}
            {ev.status === "failed" && ev.error && (
              <>
                {expandedErrors.has(ev.id) ? (
                  <pre className="event-error event-error-full">{ev.error}</pre>
                ) : (
                  <p className="event-error">
                    {ev.error.length > ERROR_PREVIEW_LEN
                      ? ev.error.slice(0, ERROR_PREVIEW_LEN) + "…"
                      : ev.error}
                  </p>
                )}
                <div className="event-error-actions">
                  {ev.error.length > ERROR_PREVIEW_LEN && (
                    <button
                      type="button"
                      className="event-error-toggle"
                      onClick={() => toggleError(ev.id)}
                    >
                      {expandedErrors.has(ev.id) ? "Weniger anzeigen" : "Details anzeigen"}
                    </button>
                  )}
                  <button
                    type="button"
                    className="event-error-toggle"
                    onClick={() => copyError(ev.id, ev.error ?? "")}
                  >
                    {copiedId === ev.id ? "Kopiert!" : "Kopieren"}
                  </button>
                </div>
              </>
            )}
            <div className="event-meta">
              <span className="event-source">{ev.source}</span>
              <span className="event-time">{formatTime(ev.created_at)}</span>
            </div>
          </li>
        ))}
      </ul>

      {(filter === "failed" || filter === "pending") && events.length > 0 && (
        <button
          type="button"
          className="btn-action event-retry"
          onClick={runRetry}
          disabled={retrying}
        >
          {retrying ? "…" : "Retry"}
        </button>
      )}
    </div>
  );
}

export type { Filter as EventLogFilter };
export type { EventStatus };
