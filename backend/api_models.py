from typing import Optional

from pydantic import BaseModel

from backend.models.edges import Edge
from backend.models.nodes import Node


class IngestResponse(BaseModel):
    event_id: int
    duplicate: bool
    duplicate_since: Optional[str] = None
    processed: Optional[int] = None
    failed: Optional[int] = None


class NoteRequest(BaseModel):
    text: str


class ChatTurn(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    question: str
    history: list[ChatTurn] = []


class AskResponse(BaseModel):
    answer: str
    sources: list[str]


class SearchHitResponse(BaseModel):
    node: Node
    score: float


class NodeResponse(BaseModel):
    node: Node
    neighbors: list[Node]


class StatsResponse(BaseModel):
    events: dict[str, int]
    graph: dict[str, int]


class ProcessSummaryResponse(BaseModel):
    processed: int
    failed: int


class BackupResponse(BaseModel):
    status: str


class GraphResponse(BaseModel):
    nodes: list[Node]
    edges: list[Edge]


class DedupeResponse(BaseModel):
    checked: int
    merged: int


class DeleteResponse(BaseModel):
    deleted_node_id: str
    removed_event_id: Optional[int] = None


class EventResponse(BaseModel):
    id: int
    event_type: str
    content_hash: str
    payload: str
    source: str
    status: str
    error: Optional[str] = None
    created_at: Optional[str] = None
    processed_at: Optional[str] = None


class TimelineBucket(BaseModel):
    date: str
    total: int
    by_type: dict[str, int]


class EmotionBucket(BaseModel):
    date: str
    avg: float
    min: float
    max: float
    count: int


class ConceptStat(BaseModel):
    title: str
    mentions: int


class PatternReport(BaseModel):
    total_nodes: int
    total_edges: int
    type_distribution: dict[str, int]
    sentiment_distribution: dict[str, int]
    relation_distribution: dict[str, int]
    top_concepts: list[ConceptStat]


class ContradictionResponse(BaseModel):
    checked: int
    found: int


class RecurringTopic(BaseModel):
    title: str
    mentions: int
    distinct_days: int
    first_seen: str
    last_seen: str
    span_days: int
    recurrence_score: float


class VaultScanRequest(BaseModel):
    path: str


class VaultScanStartResponse(BaseModel):
    job_id: str


class VaultScanResponse(BaseModel):
    total: int
    scanned: int
    processed: int
    duplicates: int
    failed: int
    processing_total: int
    processing_done: int
    done: bool
    error: Optional[str] = None


class VaultDefaultPathResponse(BaseModel):
    path: str


class VaultTreeEntry(BaseModel):
    name: str
    path: str
    is_dir: bool
    children: Optional[list["VaultTreeEntry"]] = None


VaultTreeEntry.model_rebuild()


class VaultFileResponse(BaseModel):
    path: str
    content: str
    content_hash: str


class VaultFileWriteRequest(BaseModel):
    path: str
    content: str
    expected_hash: Optional[str] = None


class VaultFileWriteResponse(BaseModel):
    path: str
    content_hash: str
    indexed: bool


class VaultRenameRequest(BaseModel):
    source: str
    target: str


class VaultAttachmentResponse(BaseModel):
    path: str


class VaultRescanResponse(BaseModel):
    processed: int
    skipped: int
    failed: int
    deleted: int


class VaultBacklinksResponse(BaseModel):
    backlinks: list[Node]


class VaultSearchHitResponse(BaseModel):
    node: Node
