import { useEffect, useMemo, useState } from "react";
import { scaleBand, scaleLinear } from "d3";
import { apiFetch } from "../api/client";
import { useToast } from "./Toast";
import { NODE_TYPE_COLORS } from "../graph/colors";
import type {
  ConceptStat,
  EmotionBucket,
  PatternReport,
  RecurringTopic,
  TimelineBucket,
} from "../api/types";

const ALL_NODE_TYPES = Object.keys(NODE_TYPE_COLORS) as (keyof typeof NODE_TYPE_COLORS)[];

function shortDate(iso: string): string {
  return iso.slice(5);
}

function TimelineChart({ data }: { data: TimelineBucket[] }) {
  const width = 520;
  const height = 160;
  const margin = { top: 12, right: 12, bottom: 24, left: 28 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const maxY = Math.max(1, ...data.map((d) => d.total));
  const x = scaleBand().domain(data.map((d) => d.date)).range([0, innerW]).padding(0.3);
  const y = scaleLinear().domain([0, maxY]).range([innerH, 0]);

  if (data.length === 0) return <p className="analysis-empty">Keine Aktivität.</p>;

  // Nur Typen in die Legende, die auch vorkommen.
  const usedTypes = ALL_NODE_TYPES.filter((t) =>
    data.some((d) => (d.by_type[t] ?? 0) > 0)
  );

  return (
    <>
    <svg className="analysis-chart" viewBox={`0 0 ${width} ${height}`}>
      <g transform={`translate(${margin.left},${margin.top})`}>
        {y.ticks(3).map((t) => (
          <g key={t}>
            <line x1={0} x2={innerW} y1={y(t)} y2={y(t)} stroke="#2a2d36" strokeWidth={1} />
            <text x={-6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="#8b8f99">
              {t}
            </text>
          </g>
        ))}
        {data.map((d) => {
          let offset = 0;
          const bw = x.bandwidth();
          return (
            <g key={d.date} transform={`translate(${x(d.date)},0)`}>
              {ALL_NODE_TYPES.filter((t) => (d.by_type[t] ?? 0) > 0).map((t) => {
                const v = d.by_type[t] ?? 0;
                const h = y(0) - y(v);
                const seg = (
                  <rect
                    key={t}
                    x={0}
                    y={y(offset + v)}
                    width={bw}
                    height={Math.max(0, h)}
                    fill={NODE_TYPE_COLORS[t]}
                    opacity={0.85}
                  />
                );
                offset += v;
                return seg;
              })}
              <text
                x={bw / 2}
                y={innerH + 16}
                textAnchor="middle"
                fontSize={10}
                fill="#8b8f99"
              >
                {shortDate(d.date)}
              </text>
            </g>
          );
        })}
      </g>
    </svg>
    <div className="chart-legend">
      {usedTypes.map((t) => (
        <span key={t} className="chart-legend-item">
          <span className="legend-dot" style={{ background: NODE_TYPE_COLORS[t] }} />
          {t}
        </span>
      ))}
    </div>
    </>
  );
}

function EmotionChart({ data }: { data: EmotionBucket[] }) {
  const width = 520;
  const height = 160;
  const margin = { top: 12, right: 12, bottom: 24, left: 28 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const x = scaleBand().domain(data.map((d) => d.date)).range([0, innerW]).padding(0.3);
  const y = scaleLinear().domain([-1, 1]).range([innerH, 0]);

  if (data.length === 0) return <p className="analysis-empty">Keine Sentiment-Daten.</p>;
  // Eine durchgehend neutrale Linie traegt keine Information.
  if (data.every((d) => d.avg === 0)) {
    return <p className="analysis-empty">Bisher nur neutrales Sentiment – noch keine Ausschläge.</p>;
  }

  const line = (points: { x: number; y: number }[]) =>
    points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");
  const path = line(
    data.map((d) => ({ x: (x(d.date) ?? 0) + x.bandwidth() / 2, y: y(d.avg) }))
  );

  return (
    <svg className="analysis-chart" viewBox={`0 0 ${width} ${height}`}>
      <g transform={`translate(${margin.left},${margin.top})`}>
        {[-1, -0.5, 0, 0.5, 1].map((t) => (
          <g key={t}>
            <line
              x1={0}
              x2={innerW}
              y1={y(t)}
              y2={y(t)}
              stroke={t === 0 ? "#3a3d46" : "#2a2d36"}
              strokeWidth={t === 0 ? 1.5 : 1}
            />
            <text x={-6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="#8b8f99">
              {t}
            </text>
          </g>
        ))}
        <path d={path} fill="none" stroke="#6C8EF5" strokeWidth={2} opacity={0.9} />
        {data.map((d) => (
          <circle
            key={d.date}
            cx={(x(d.date) ?? 0) + x.bandwidth() / 2}
            cy={y(d.avg)}
            r={3}
            fill={d.avg >= 0 ? "#6CE07A" : "#E06C8E"}
          />
        ))}
        {data.map((d, i) =>
          i % Math.ceil(data.length / 8 || 1) === 0 ? (
            <text
              key={d.date}
              x={(x(d.date) ?? 0) + x.bandwidth() / 2}
              y={innerH + 16}
              textAnchor="middle"
              fontSize={10}
              fill="#8b8f99"
            >
              {shortDate(d.date)}
            </text>
          ) : null
        )}
      </g>
    </svg>
  );
}

function DistBars({
  data,
  colors,
}: {
  data: Record<string, number>;
  colors?: Record<string, string>;
}) {
  const entries = Object.entries(data).filter(([, v]) => v > 0);
  if (entries.length === 0) return <p className="analysis-empty">Keine Daten.</p>;
  const max = Math.max(...entries.map(([, v]) => v));
  return (
    <div className="dist-bars">
      {entries
        .sort((a, b) => b[1] - a[1])
        .map(([k, v]) => (
          <div key={k} className="dist-row">
            <span className="dist-label">{k}</span>
            <div className="dist-track">
              <div
                className="dist-fill"
                style={{
                  width: `${(v / max) * 100}%`,
                  background: colors?.[k] ?? "#6C8EF5",
                }}
              />
            </div>
            <span className="dist-value">{v}</span>
          </div>
        ))}
    </div>
  );
}

function TopConcepts({ data }: { data: ConceptStat[] }) {
  if (data.length === 0) return <p className="analysis-empty">Keine Konzepte verknüpft.</p>;
  const max = Math.max(...data.map((c) => c.mentions));
  return (
    <div className="dist-bars">
      {data.map((c) => (
        <div key={c.title} className="dist-row">
          <span className="dist-label" title={c.title}>
            {c.title}
          </span>
          <div className="dist-track">
            <div
              className="dist-fill"
              style={{ width: `${(c.mentions / max) * 100}%`, background: "#5FD0C0" }}
            />
          </div>
          <span className="dist-value">{c.mentions}</span>
        </div>
      ))}
    </div>
  );
}

function RecurringTopics({ data }: { data: RecurringTopic[] }) {
  if (data.length === 0) return <p className="analysis-empty">Keine wiederkehrenden Themen.</p>;
  const max = Math.max(...data.map((t) => t.recurrence_score));
  return (
    <div className="recurring-list">
      {data.map((t) => (
        <div key={t.title} className="recurring-row">
          <div className="recurring-head">
            <span className="recurring-title" title={t.title}>{t.title}</span>
            <span className="recurring-score">{t.recurrence_score.toFixed(1)}</span>
          </div>
          <div className="dist-track">
            <div
              className="dist-fill"
              style={{ width: `${(t.recurrence_score / max) * 100}%`, background: "#C792EA" }}
            />
          </div>
          <div className="recurring-meta">
            <span>{t.mentions}× erwähnt</span>
            <span>{t.distinct_days} Tage</span>
            <span>Spanne {t.span_days}d</span>
            <span>{shortDate(t.first_seen)}–{shortDate(t.last_seen)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

const SENTIMENT_COLORS: Record<string, string> = {
  positive: "#6CE07A",
  neutral: "#8b8f99",
  negative: "#E06C8E",
};

export function AnalysisPanel({ refreshKey }: { refreshKey: number }) {
  const [timeline, setTimeline] = useState<TimelineBucket[]>([]);
  const [emotions, setEmotions] = useState<EmotionBucket[]>([]);
  const [patterns, setPatterns] = useState<PatternReport | null>(null);
  const [recurring, setRecurring] = useState<RecurringTopic[]>([]);
  const [loading, setLoading] = useState(true);
  const { pushError } = useToast();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      apiFetch<TimelineBucket[]>("/analysis/timeline"),
      apiFetch<EmotionBucket[]>("/analysis/emotions"),
      apiFetch<PatternReport>("/analysis/patterns"),
      apiFetch<RecurringTopic[]>("/analysis/recurring"),
    ])
      .then(([tl, em, pa, re]) => {
        if (cancelled) return;
        setTimeline(tl ?? []);
        setEmotions(em ?? []);
        setPatterns(pa ?? null);
        setRecurring(re ?? []);
      })
      .catch((err) => !cancelled && pushError(err, "Analyse konnte nicht geladen werden"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [refreshKey, pushError]);

  const typeColors = useMemo(
    () =>
      Object.fromEntries(
        ALL_NODE_TYPES.map((t) => [t, NODE_TYPE_COLORS[t]])
      ) as Record<string, string>,
    []
  );

  if (loading) return <div className="analysis-panel">Lade Analyse…</div>;

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
      <section className="analysis-section analysis-section--wide">
        <h2>Wiederkehrende Themen</h2>
        <RecurringTopics data={recurring} />
      </section>
    </div>
  );
}
