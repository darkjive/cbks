import asyncio
import json
from dataclasses import dataclass

import numpy as np

from backend.models.nodes import Node
from backend.services.agents.prefrontal import LLMClient
from backend.services.agents.temporal import TemporalAgent
from backend.services.graph_backend import GraphBackend

_HIGH_SIMILARITY = 0.92
_LOW_SIMILARITY = 0.75

_SAME_ENTITY_PROMPT = (
    'Bezeichnen "{title_a}" und "{title_b}" dieselbe Entität (Konzept, Person, '
    'Technologie)? Antworte ausschließlich als JSON: {{"same": true}} oder {{"same": false}}.'
)


@dataclass
class MergeSummary:
    checked: int
    merged: int


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype="float32")
    vb = np.asarray(b, dtype="float32")
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


class EntityResolver:
    def __init__(self, graph: GraphBackend, temporal_agent: TemporalAgent, llm_client: LLMClient):
        self._graph = graph
        self._temporal_agent = temporal_agent
        self._llm_client = llm_client

    def _exact_or_alias_match(self, title: str) -> Node | None:
        normalized = title.lower()
        for node in self._graph.get_concept_nodes():
            if node.title.lower() == normalized:
                return node
            aliases = node.metadata.get("aliases", [])
            if normalized in (alias.lower() for alias in aliases):
                return node
        return None

    async def _confirm_same(self, title_a: str, title_b: str) -> bool:
        prompt = _SAME_ENTITY_PROMPT.format(title_a=title_a, title_b=title_b)
        try:
            raw = await asyncio.to_thread(self._llm_client.generate, prompt)
            return bool(json.loads(raw)["same"])
        except Exception:  # noqa: BLE001 - LLM-Fehler im Grenzfall => konservativ kein Merge
            return False

    async def resolve(self, title: str) -> Node | None:
        exact = self._exact_or_alias_match(title)
        if exact is not None:
            return exact

        vectors = self._graph.get_concept_vectors()
        if not vectors:
            return None

        query_vector = await self._temporal_agent.embed(title)
        best_id, best_score = None, -1.0
        for node_id, vector in vectors:
            score = _cosine_similarity(query_vector, vector)
            if score > best_score:
                best_id, best_score = node_id, score

        if best_score >= _HIGH_SIMILARITY:
            candidate = self._graph.get_node(best_id)
            if candidate is not None:
                self._graph.add_alias(candidate.id, title)
            return candidate
        if best_score >= _LOW_SIMILARITY:
            candidate = self._graph.get_node(best_id)
            if candidate is not None and await self._confirm_same(title, candidate.title):
                self._graph.add_alias(candidate.id, title)
                return candidate
        return None

    async def register(self, node: Node) -> None:
        vector = await self._temporal_agent.embed(node.title)
        self._graph.set_concept_vector(node.id, vector)

    async def dedupe_all(self) -> MergeSummary:
        concepts = self._graph.get_concept_nodes()
        vectors = dict(self._graph.get_concept_vectors())
        kept: list[Node] = []
        checked = merged = 0

        for node in concepts:
            checked += 1
            vector = vectors.get(node.id)
            if vector is None:
                vector = await self._temporal_agent.embed(node.title)
                self._graph.set_concept_vector(node.id, vector)

            match: Node | None = None
            best_node, best_score = None, -1.0
            for kept_node in kept:
                score = _cosine_similarity(vector, vectors.get(kept_node.id) or [])
                if score > best_score:
                    best_node, best_score = kept_node, score

            if best_score >= _HIGH_SIMILARITY:
                match = best_node
            elif best_score >= _LOW_SIMILARITY and best_node is not None:
                if await self._confirm_same(node.title, best_node.title):
                    match = best_node

            if match is not None:
                self._graph.merge_nodes(match.id, node.id)
                merged += 1
            else:
                kept.append(node)
                vectors[node.id] = vector

        return MergeSummary(checked=checked, merged=merged)
