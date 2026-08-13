import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from backend.models.edges import Edge
from backend.models.events import Event
from backend.models.nodes import Node
from backend.services.agents.prefrontal import PrefrontalAgent
from backend.services.agents.temporal import TemporalAgent
from backend.services.entity_resolver import EntityResolver
from backend.services.event_log import EventLog
from backend.services.field_extractor import extract_fields
from backend.services.graph_backend import GraphBackend
from backend.services.parsing import parse_frontmatter
from backend.services.sentiment import SentimentClient
from backend.storage.faiss_index import FaissIndex

_VALID_NODE_TYPES = {
    "concept", "document", "task", "note", "project", "commit", "screenshot", "person",
}
_EVENT_TYPE_TO_NODE_TYPE = {
    "document.added": "document",
    "note.created": "note",
}


def _resolve_node_type(classification: str, event_type: str) -> str:
    if classification in _VALID_NODE_TYPES:
        return classification
    return _EVENT_TYPE_TO_NODE_TYPE.get(event_type, "document")


@dataclass
class ProcessSummary:
    processed: int
    failed: int


class Dispatcher:
    def __init__(
        self,
        event_log: EventLog,
        graph: GraphBackend,
        faiss_index: FaissIndex,
        temporal_agent: TemporalAgent,
        prefrontal_agent: PrefrontalAgent,
        entity_resolver: EntityResolver,
        embedding_model_name: str,
        sentiment: Optional[SentimentClient] = None,
    ):
        self.event_log = event_log
        self.graph = graph
        self.faiss_index = faiss_index
        self.temporal_agent = temporal_agent
        self.prefrontal_agent = prefrontal_agent
        self.entity_resolver = entity_resolver
        self.embedding_model_name = embedding_model_name
        self.sentiment = sentiment

    async def process_event(self, event: Event) -> None:
        payload = json.loads(event.payload)
        title = payload["title"]
        raw_text = payload["text"]
        now = datetime.now(timezone.utc).isoformat()

        # YAML-Frontmatter (Obsidian-Vault-Notizen) vor der Verarbeitung abtrennen:
        # Klassifizierung, Embedding und Feldextraktion arbeiten auf dem reinen Body,
        # Titel/Datum werden als echte Node-Felder übernommen.
        body, meta = parse_frontmatter(raw_text)
        # Wurde Frontmatter erkannt (meta befuellt), gilt der abgetrennte Body -
        # auch wenn er leer ist. Sonst landet bei reinen Frontmatter-Notizen das
        # rohe YAML in Klassifikation/Embedding/content. Nur ohne Frontmatter
        # (meta leer) faellt text auf den Rohtext zurueck.
        text = body if meta else raw_text
        if meta.get("title"):
            title = meta["title"]
        updated = meta.get("updated")

        # Vault-Events tragen ihre Node-Identitaet explizit im Payload (Frontmatter-id).
        # Existiert bereits ein Node mit dieser id, ist dies ein Upsert (Datei bearbeitet):
        # Content/Vektor/abgeleitete Kanten neu, Aktivierungs-/Zugriffsfelder bleiben stehen.
        explicit_node_id = payload.get("node_id")
        base_node = self.graph.get_node(explicit_node_id) if explicit_node_id else None
        node_id = explicit_node_id or str(uuid.uuid4())
        creation_time = base_node.creation_time if base_node else (meta.get("created") or now)

        tasks = [
            self.prefrontal_agent.classify_and_extract(text),
            self.temporal_agent.embed(text),
        ]
        if self.sentiment is not None:
            tasks.append(self.sentiment.analyze(text))
        results = await asyncio.gather(*tasks)
        classification_result = results[0]
        vector = results[1]
        emotional_weight = results[2] if len(results) > 2 else 0.0

        node_type = _resolve_node_type(classification_result.classification, event.event_type)
        extracted_fields = extract_fields(text)
        metadata: dict = dict(base_node.metadata) if base_node else {}
        metadata.pop("extracted_fields", None)
        metadata.pop("updated", None)
        if extracted_fields:
            metadata["extracted_fields"] = extracted_fields
        if updated:
            metadata["updated"] = updated
        if explicit_node_id:
            metadata["source_path"] = payload.get("source_path")

        doc_node = Node(
            id=node_id, title=title, type=node_type, content=text,
            content_hash=event.content_hash, creation_time=creation_time,
            last_access=base_node.last_access if base_node else creation_time,
            access_counter=base_node.access_counter if base_node else 0,
            activation=base_node.activation if base_node else 1.0,
            confidence=base_node.confidence if base_node else 1.0,
            decay_rate=base_node.decay_rate if base_node else 0.001,
            importance=base_node.importance if base_node else 0.5,
            hemisphere=base_node.hemisphere if base_node else "auto",
            emotional_weight=emotional_weight,
            metadata=metadata,
        )

        if base_node is not None:
            self.graph.upsert_node(doc_node)
            old_faiss_id = self.graph.replace_vector(doc_node.id, event.id, self.embedding_model_name)
            if old_faiss_id is not None and old_faiss_id != event.id:
                self.faiss_index.remove(old_faiss_id)
            self.graph.delete_outgoing_edges(doc_node.id)
        else:
            self.graph.add_node(doc_node)
            self.graph.link_vector(doc_node.id, event.id, self.embedding_model_name)
        self.faiss_index.add(event.id, vector)

        entity_nodes: dict[str, Node] = {}
        for entity in classification_result.entities:
            entity_node = await self._resolve_or_create_entity(
                entity.name, entity.type, now, entity.relationship
            )
            entity_nodes[entity.name] = entity_node
            edge = Edge(
                id=str(uuid.uuid4()), source=doc_node.id, target=entity_node.id,
                relation_type="mentions", creation_time=now, last_updated=now,
            )
            self.graph.add_edge(edge)

        # Zweiter Durchlauf: Hierarchie-Kanten (z.B. Krankenkasse -part_of-> Versicherung).
        # Die Parent-Entität kann implizit sein (nicht selbst in entities enthalten).
        for entity in classification_result.entities:
            if not entity.parent:
                continue
            parent_node = entity_nodes.get(entity.parent)
            if parent_node is None:
                parent_node = await self._resolve_or_create_entity(entity.parent, "concept", now)
                entity_nodes[entity.parent] = parent_node
            edge = Edge(
                id=str(uuid.uuid4()), source=entity_nodes[entity.name].id, target=parent_node.id,
                relation_type="part_of", creation_time=now, last_updated=now,
            )
            self.graph.add_edge(edge)

        self.event_log.mark_processed(event.id)

    async def _resolve_or_create_entity(
        self, name: str, entity_type: str, now: str, relationship: str | None = None
    ) -> Node:
        entity_node = await self.entity_resolver.resolve(name)
        if entity_node is None:
            node_type = "person" if entity_type == "person" else "concept"
            metadata = {"entity_type": entity_type}
            if relationship:
                metadata["relationship"] = relationship
            entity_node = Node(
                id=str(uuid.uuid4()), title=name, type=node_type,
                creation_time=now, last_access=now, metadata=metadata,
            )
            self.graph.add_node(entity_node)
            await self.entity_resolver.register(entity_node)
        return entity_node

    async def _process_one_safe(self, event: Event) -> bool:
        try:
            await self.process_event(event)
            return True
        except Exception as exc:  # noqa: BLE001 - bewusst breit, siehe Spec §4.1 Regel 4
            self.event_log.mark_failed(event.id, str(exc))
            return False

    async def process_events(
        self, events: list[Event], on_progress: Optional[Callable[[int, int], None]] = None
    ) -> ProcessSummary:
        processed = failed = 0
        total = len(events)
        for event in events:
            ok = await self._process_one_safe(event)
            processed += int(ok)
            failed += int(not ok)
            if on_progress is not None:
                on_progress(processed + failed, total)
        return ProcessSummary(processed=processed, failed=failed)

    async def process_pending(
        self, on_progress: Optional[Callable[[int, int], None]] = None
    ) -> ProcessSummary:
        return await self.process_events(self.event_log.pending(), on_progress=on_progress)
