from backend.api_models import (
    AskRequest,
    AskResponse,
    BackupResponse,
    IngestResponse,
    NodeResponse,
    NoteRequest,
    ProcessSummaryResponse,
    SearchHitResponse,
    StatsResponse,
)
from backend.models.nodes import Node


def _make_node() -> Node:
    return Node(
        id="n1", title="Test", type="document",
        creation_time="2026-07-05T00:00:00Z", last_access="2026-07-05T00:00:00Z",
    )


def test_ingest_response_duplicate_fields_optional():
    response = IngestResponse(event_id=-1, duplicate=True, duplicate_since="2026-07-01T00:00:00Z")
    assert response.processed is None
    assert response.failed is None


def test_ingest_response_success_fields():
    response = IngestResponse(event_id=1, duplicate=False, processed=1, failed=0)
    assert response.duplicate_since is None


def test_note_request_requires_text():
    body = NoteRequest(text="Hallo")
    assert body.text == "Hallo"


def test_ask_request_response_roundtrip():
    request = AskRequest(question="Was?")
    response = AskResponse(answer="Antwort", sources=["n1"])
    assert request.question == "Was?"
    assert response.sources == ["n1"]


def test_search_hit_response_wraps_node():
    hit = SearchHitResponse(node=_make_node(), score=0.9)
    assert hit.node.title == "Test"
    assert hit.score == 0.9


def test_node_response_wraps_node_and_neighbors():
    node = _make_node()
    response = NodeResponse(node=node, neighbors=[node])
    assert response.neighbors == [node]


def test_stats_response_shape():
    response = StatsResponse(
        events={"pending": 0, "processed": 1, "failed": 0}, graph={"nodes": 2, "edges": 1}
    )
    assert response.events["processed"] == 1


def test_process_summary_response_shape():
    response = ProcessSummaryResponse(processed=2, failed=1)
    assert response.processed == 2


def test_backup_response_status():
    response = BackupResponse(status="ok")
    assert response.status == "ok"
