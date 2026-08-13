import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from backend.cli import app as cli_app
from backend.main import app
from backend.services.agents.prefrontal import OllamaLLMClient
from backend.services.agents.temporal import OllamaEmbeddingClient


@pytest.fixture(autouse=True)
def fake_ollama(monkeypatch):
    def fake_embed(self, text: str) -> list[float]:
        return [1.0] + [0.0] * 1023

    def fake_generate(self, prompt: str, format: str = "") -> str:
        if "Beantworte die letzte Frage" in prompt:
            return "Das Dokument handelt von FAISS."
        return json.dumps({"classification": "document", "entities": ["FAISS"]})

    monkeypatch.setattr(OllamaEmbeddingClient, "embed", fake_embed)
    monkeypatch.setattr(OllamaLLMClient, "generate", fake_generate)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CBKS_API_KEY", raising=False)
    return tmp_path


@pytest.fixture()
def client(isolated_data_dir):
    with TestClient(app) as c:
        yield c


def _ingest_note(client: TestClient, text: str) -> str:
    client.post("/notes", json={"text": text})
    node_id = client.get("/search", params={"q": text}).json()[0]["node"]["id"]
    return node_id


def test_delete_node_removes_node_and_event(client):
    node_id = _ingest_note(client, "Ein Text über FAISS")

    response = client.delete(f"/nodes/{node_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted_node_id"] == node_id
    assert body["removed_event_id"] is not None

    # Node ist weg
    get_response = client.get(f"/nodes/{node_id}")
    assert get_response.status_code == 404

    # Event-Log ist leer - kein Rebuild kann den Node wiederherstellen
    stats = client.get("/stats").json()
    assert stats["events"]["pending"] + stats["events"]["processed"] + stats["events"]["failed"] == 0


def test_delete_unknown_node_returns_404(client):
    response = client.delete("/nodes/does-not-exist")
    assert response.status_code == 404


def test_delete_node_no_longer_searchable(client):
    node_id = _ingest_note(client, "Spezifischer Text über FAISS")

    client.delete(f"/nodes/{node_id}")

    hits = client.get("/search", params={"q": "Spezifischer"}).json()
    assert all(h["node"]["id"] != node_id for h in hits)


def test_delete_node_vanishes_from_graph(client):
    node_id = _ingest_note(client, "Text für den Graphen")

    client.delete(f"/nodes/{node_id}")

    graph = client.get("/graph").json()
    assert all(n["id"] != node_id for n in graph["nodes"])


# --- CLI ---------------------------------------------------------------------

cli_runner = CliRunner()


def test_cli_delete_unknown_node_exits_nonzero():
    result = cli_runner.invoke(cli_app, ["delete", "does-not-exist"])
    assert result.exit_code == 1
    assert "nicht gefunden" in result.stdout.lower()


def test_cli_delete_existing_node_succeeds():
    from backend.app_context import build_context

    note_result = cli_runner.invoke(cli_app, ["note", "Ein Text über FAISS"])
    assert note_result.exit_code == 0

    ctx = build_context()
    node_ids = [n for n in ctx.graph.graph.nodes()]
    assert node_ids, "Note sollte mindestens einen Node erzeugen"
    node_id = node_ids[0]

    delete_result = cli_runner.invoke(cli_app, ["delete", node_id])
    assert delete_result.exit_code == 0
    assert "gelöscht" in delete_result.stdout.lower()

    ctx2 = build_context()
    assert ctx2.graph.get_node(node_id) is None
