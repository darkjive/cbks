import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.models.nodes import Node
from backend.services.agents.prefrontal import PrefrontalAgent
from backend.services.agents.temporal import TemporalAgent
from backend.services.graph_backend import GraphBackend
from backend.storage.faiss_index import FaissIndex

# Decay-Rate λ: nur UI-Ranking, keine persistente Veränderung (Spec).
# Gewicht = Basis × e^(-λ·t), t in Tagen seit letztem Zugriff.
_DECAY_LAMBDA = 0.001

_MIN_TITLE_LEN = 3
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# Deutsche Beziehungsphrasen ("meine Frau") → kanonisches relationship-Label,
# das der Dispatcher als Node-Metadata setzt (siehe prefrontal._CLASSIFY_PROMPT).
_RELATIONSHIP_ALIASES = {
    "meine frau": "Ehefrau", "meiner frau": "Ehefrau", "meine ehefrau": "Ehefrau",
    "mein mann": "Ehemann", "meinem mann": "Ehemann", "mein ehemann": "Ehemann",
    "meine tochter": "Tochter", "meiner tochter": "Tochter",
    "mein sohn": "Sohn", "meinem sohn": "Sohn",
    "meine mutter": "Mutter", "meiner mutter": "Mutter",
    "mein vater": "Vater", "meinem vater": "Vater",
    "meine eltern": "Eltern",
}


def _age_days(node: Node) -> float:
    ref = node.last_access or node.creation_time
    if not ref:
        return 0.0
    try:
        then = datetime.fromisoformat(ref.replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return 0.0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return max(0.0, (now - then).total_seconds() / 86400.0)


@dataclass
class SearchHit:
    node: Node
    score: float


async def search(
    query: str,
    temporal_agent: TemporalAgent,
    faiss_index: FaissIndex,
    graph: GraphBackend,
    limit: int = 10,
) -> list[SearchHit]:
    vector = await temporal_agent.embed(query)
    # Over-fetch: FAISS kann Orphan-IDs liefern (verwaiste Vektoren nach
    # Teilfehlern/Retry) und Duplikate (gleiche faiss_id durch mehrfaches add).
    # Wir holen grosszuegig mehr Kandidaten, dedupen und fuellen so verlaesslich
    # bis zu `limit` echte Treffer auf.
    fetch_k = max(limit * 3, limit + 10)
    hits = faiss_index.search(vector, k=fetch_k)
    results: list[SearchHit] = []
    seen_faiss: set[int] = set()
    seen_nodes: set[str] = set()
    for faiss_id, score in hits:
        if faiss_id in seen_faiss:
            continue
        seen_faiss.add(faiss_id)
        node = graph.get_node_by_faiss_id(faiss_id)
        if node is None or node.id in seen_nodes:
            continue
        seen_nodes.add(node.id)
        decayed = score * math.exp(-_DECAY_LAMBDA * _age_days(node))
        results.append(SearchHit(node=node, score=decayed))
        if len(results) >= limit:
            break
    results.sort(key=lambda h: h.score, reverse=True)
    return results


def _find_mentioned_nodes(question: str, graph: GraphBackend) -> list[Node]:
    matches = []
    for node in graph.get_all_nodes():
        if len(node.title) < _MIN_TITLE_LEN:
            continue
        if re.search(rf"\b{re.escape(node.title)}\b", question, re.IGNORECASE):
            matches.append(node)
    return matches


def _graph_context_hits(question: str, graph: GraphBackend) -> list["SearchHit"]:
    mentioned = _find_mentioned_nodes(question, graph)
    seen = {n.id for n in mentioned}
    result = list(mentioned)
    for node in mentioned:
        for neighbor in graph.get_neighbors(node.id, depth=1):
            if neighbor.id not in seen:
                seen.add(neighbor.id)
                result.append(neighbor)
    return [SearchHit(node=n, score=1.0) for n in result]


def _year_context_hits(question: str, graph: GraphBackend) -> list["SearchHit"]:
    match = _YEAR_RE.search(question)
    if match is None:
        return []
    year = match.group(0)
    return [
        SearchHit(node=n, score=1.0)
        for n in graph.get_all_nodes()
        if n.type == "document" and n.content and year in n.content
    ]


def _relationship_context_hits(question: str, graph: GraphBackend) -> list["SearchHit"]:
    q = question.lower()
    labels = {label for phrase, label in _RELATIONSHIP_ALIASES.items() if phrase in q}
    if not labels:
        return []
    hits = []
    for node in graph.get_all_nodes():
        if node.metadata.get("relationship") not in labels:
            continue
        hits.append(SearchHit(node=node, score=1.0))
        for neighbor in graph.get_neighbors(node.id, depth=1):
            hits.append(SearchHit(node=neighbor, score=0.9))
    return hits


def _merge_hits(*groups: list["SearchHit"], max_total: int = 10) -> list["SearchHit"]:
    seen: set[str] = set()
    merged: list[SearchHit] = []
    for group in groups:
        for hit in group:
            if hit.node.id in seen:
                continue
            seen.add(hit.node.id)
            merged.append(hit)
            if len(merged) >= max_total:
                return merged
    return merged


def _describe_node(node: Node, graph: GraphBackend) -> str:
    lines = [f"[{node.title}]"]
    if node.content:
        lines.append(node.content)
    entity_type = node.metadata.get("entity_type")
    if entity_type:
        lines.append(f"Typ: {entity_type}")
    relationship = node.metadata.get("relationship")
    if relationship:
        lines.append(f"Beziehung zum Dokumenteninhaber: {relationship}")
    fields = node.metadata.get("extracted_fields")
    if fields:
        lines.append("Erkannte Felder: " + ", ".join(f"{k}={v}" for k, v in fields.items()))
    for edge in graph.get_all_edges():
        if edge.relation_type != "part_of":
            continue
        if edge.source == node.id:
            target = graph.get_node(edge.target)
            if target:
                lines.append(f"Teil von: {target.title}")
        elif edge.target == node.id:
            src = graph.get_node(edge.source)
            if src:
                lines.append(f"Enthält: {src.title}")
    return "\n".join(lines)


@dataclass
class AnswerResult:
    answer: str
    sources: list[str]


async def ask(
    question: str,
    temporal_agent: TemporalAgent,
    faiss_index: FaissIndex,
    graph: GraphBackend,
    prefrontal_agent: PrefrontalAgent,
    limit: int = 5,
    history: list[tuple[str, str]] | None = None,
) -> AnswerResult:
    vector_hits = await search(question, temporal_agent, faiss_index, graph, limit=limit)
    graph_hits = _graph_context_hits(question, graph)
    relationship_hits = _relationship_context_hits(question, graph)
    year_hits = _year_context_hits(question, graph)
    # Reihenfolge = Prioritaet: _merge_hits dedupt nach node.id ("erste Quelle
    # gewinnt") und cappt bei max_total. Praeziser Graph-/Beziehungs-/Jahr-Kontext
    # geht vor die unschaerfere Vektor-Aehnlichkeit.
    hits = _merge_hits(graph_hits, relationship_hits, year_hits, vector_hits)
    # ask() ist ein echter Lesezugriff (Nutzer konsumiert Inhalt), daher hier den
    # Zugriff zaehlen. search() oben bleibt idempotent - eine reine Filter-Query
    # darf den Decay-Status der Treffer nicht schon verschieben.
    graph.touch_access([hit.node.id for hit in hits])
    context = "\n\n".join(_describe_node(hit.node, graph) for hit in hits)
    answer = await prefrontal_agent.answer_question(question, context, history=history)
    return AnswerResult(answer=answer, sources=[hit.node.id for hit in hits])
