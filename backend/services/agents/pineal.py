import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from backend.models.edges import Edge
from backend.services.agents.prefrontal import LLMClient
from backend.services.graph_backend import GraphBackend

# Inhaltlicher Reasoning-Agent (Zirbeldrüse / Metakognition).
# Findet Widersprüche zwischen inhaltlich verbundenen Nodes und legt
# `contradicts`-Edges an. Bewusst auf direkte Nachbarn + Text-Nodes
# begrenzt, damit die Paarung nicht ausuert (kein O(n^2) über alles).

_MAX_TEXT = 600
_MIN_CONFIDENCE = 0.5

_CONTRADICTION_PROMPT = (
    'Prüfe, ob sich diese beiden Textaussagen inhaltlich widersprechen.\n\n'
    'Text A:\n"{text_a}"\n\n'
    'Text B:\n"{text_b}"\n\n'
    'Ein Widerspruch liegt nur vor, wenn eine Aussage die andere direkt '
    'verneint oder ihr faktisch entgegensteht (z.B. "X funktioniert" vs '
    '"X ist kaputt"). Unterschiedliche Themen oder Ergänzungen sind '
    'kein Widerspruch.\n'
    'Antworte AUSSCHLIESSLICH als JSON: '
    '{{"contradicts": true, "confidence": 0.8}} oder '
    '{{"contradicts": false, "confidence": 0.2}}'
)


@dataclass
class ContradictionSummary:
    checked: int
    found: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(text: str) -> str:
    return (text or "").strip()[:_MAX_TEXT]


async def _check_pair(
    llm_client: LLMClient, text_a: str, text_b: str
) -> tuple[bool, float]:
    prompt = _CONTRADICTION_PROMPT.format(text_a=text_a, text_b=text_b)
    try:
        raw = await asyncio.to_thread(llm_client.generate, prompt)
        result = json.loads(raw)
        return bool(result.get("contradicts")), float(result.get("confidence", 0.5))
    except Exception:  # noqa: BLE001 - LLM-Fehler => konservativ kein Widerspruch
        return False, 0.0


async def find_contradictions(
    graph: GraphBackend,
    llm_client: LLMClient,
    max_nodes: int = 20,
) -> ContradictionSummary:
    existing: set[frozenset[str]] = set()
    for edge in graph.get_all_edges():
        if edge.relation_type == "contradicts":
            existing.add(frozenset((edge.source, edge.target)))

    candidates = [
        n
        for n in graph.get_all_nodes()
        if n.content and n.type in ("document", "note")
    ]
    candidates.sort(key=lambda n: n.creation_time or "", reverse=True)
    candidates = candidates[:max_nodes]

    checked = 0
    found = 0
    for node in candidates:
        # depth=2, da Text-Nodes nur ueber ein gemeinsames Concept verbunden
        # sind (Note -> Concept <- Note), nie direkt.
        for neighbor in graph.get_neighbors(node.id, depth=2):
            if neighbor.id == node.id or not neighbor.content:
                continue
            key = frozenset((node.id, neighbor.id))
            if key in existing:
                continue
            checked += 1
            contradicts, confidence = await _check_pair(
                llm_client, _truncate(node.content), _truncate(neighbor.content)
            )
            if contradicts and confidence >= _MIN_CONFIDENCE:
                edge = Edge(
                    id=str(uuid.uuid4()),
                    source=node.id,
                    target=neighbor.id,
                    relation_type="contradicts",
                    strength=confidence,
                    creation_time=_now(),
                    last_updated=_now(),
                )
                graph.add_edge(edge)
                existing.add(key)
                found += 1
    return ContradictionSummary(checked=checked, found=found)
