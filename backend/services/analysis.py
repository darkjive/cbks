from collections import defaultdict
from datetime import datetime
from typing import Optional

from backend.models.edges import Edge
from backend.models.nodes import Node

# Schwelle ab der ein emotional_weight als signifikant positiv/negativ zählt.
_EMOTION_THRESHOLD = 0.15


def _date_of(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "")).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def timeline(nodes: list[Node]) -> list[dict]:
    totals: dict[str, int] = defaultdict(int)
    by_type: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for n in nodes:
        d = _date_of(n.creation_time)
        if d is None:
            continue
        totals[d] += 1
        by_type[d][n.type] += 1
    return [
        {"date": d, "total": totals[d], "by_type": dict(by_type[d])}
        for d in sorted(totals)
    ]


def emotions(nodes: list[Node]) -> list[dict]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for n in nodes:
        d = _date_of(n.creation_time)
        if d is None:
            continue
        buckets[d].append(n.emotional_weight)
    result = []
    for d in sorted(buckets):
        vals = buckets[d]
        result.append(
            {
                "date": d,
                "avg": round(sum(vals) / len(vals), 3),
                "min": round(min(vals), 3),
                "max": round(max(vals), 3),
                "count": len(vals),
            }
        )
    return result


def _sentiment_bucket(ew: float) -> str:
    if ew > _EMOTION_THRESHOLD:
        return "positive"
    if ew < -_EMOTION_THRESHOLD:
        return "negative"
    return "neutral"


def patterns(nodes: list[Node], edges: list[Edge]) -> dict:
    type_dist: dict[str, int] = defaultdict(int)
    sent_dist: dict[str, int] = {"positive": 0, "neutral": 0, "negative": 0}
    for n in nodes:
        type_dist[n.type] += 1
        sent_dist[_sentiment_bucket(n.emotional_weight)] += 1

    relation_dist: dict[str, int] = defaultdict(int)
    mention_count: dict[str, int] = defaultdict(int)
    for e in edges:
        relation_dist[e.relation_type] += 1
        if e.relation_type == "mentions":
            mention_count[e.target] += 1

    concepts = [n for n in nodes if n.type == "concept"]
    top_concepts = [
        {"title": c.title, "mentions": mention_count.get(c.id, 0)}
        for c in sorted(concepts, key=lambda n: mention_count.get(n.id, 0), reverse=True)
        if mention_count.get(c.id, 0) > 0
    ][:10]

    return {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "type_distribution": dict(type_dist),
        "sentiment_distribution": sent_dist,
        "relation_distribution": dict(relation_dist),
        "top_concepts": top_concepts,
    }


def _span_days(first: str, last: str) -> int:
    try:
        a = datetime.strptime(first, "%Y-%m-%d")
        b = datetime.strptime(last, "%Y-%m-%d")
        return abs((b - a).days)
    except ValueError:
        return 0


def recurring(nodes: list[Node], edges: list[Edge], top_k: int = 10) -> list[dict]:
    # mentions-Edges verlinken document/note (source) -> concept (target).
    # Ein Konzept gilt als wiederkehrend, wenn es über mehrere Tage hinweg erwähnt wird.
    concept_ids = {n.id for n in nodes if n.type == "concept"}
    title_by_id = {n.id: n.title for n in nodes}
    days_by_concept: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e.relation_type == "mentions" and e.target in concept_ids:
            d = _date_of(e.creation_time)
            if d is not None:
                days_by_concept[e.target].append(d)

    topics: list[dict] = []
    for cid, days in days_by_concept.items():
        distinct = sorted(set(days))
        if not distinct:
            continue
        mentions = len(days)
        distinct_days = len(distinct)
        first_seen = distinct[0]
        last_seen = distinct[-1]
        span_days = _span_days(first_seen, last_seen)
        # Verteilung über Tage gewichtet stärker als bloße Menge.
        recurrence_score = round(distinct_days + mentions * 0.2, 2)
        topics.append(
            {
                "title": title_by_id.get(cid, cid),
                "mentions": mentions,
                "distinct_days": distinct_days,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "span_days": span_days,
                "recurrence_score": recurrence_score,
            }
        )

    topics.sort(key=lambda t: t["recurrence_score"], reverse=True)
    return topics[:top_k]
