from backend.models.events import Event
from backend.models.nodes import Node
from backend.models.edges import Edge


def test_event_defaults():
    event = Event(event_type="note.created", content_hash="abc", payload="{}", source="cli")
    assert event.status == "pending"
    assert event.id is None


def test_node_roundtrip():
    node = Node(
        id="n1", title="Graphentheorie", type="concept",
        creation_time="2026-07-05T00:00:00+00:00",
        last_access="2026-07-05T00:00:00+00:00",
    )
    assert node.activation == 1.0
    assert node.metadata == {}


def test_edge_roundtrip():
    edge = Edge(
        id="e1", source="n1", target="n2", relation_type="mentions",
        creation_time="2026-07-05T00:00:00+00:00",
        last_updated="2026-07-05T00:00:00+00:00",
    )
    assert edge.strength == 1.0
    assert edge.reinforcement_count == 1
