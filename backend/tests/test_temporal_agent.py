import asyncio
from unittest.mock import MagicMock

from backend.services.agents.temporal import OllamaEmbeddingClient, TemporalAgent


class FakeEmbeddingClient:
    def __init__(self, vector: list[float]):
        self._vector = vector
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return self._vector


def test_temporal_agent_returns_client_vector():
    client = FakeEmbeddingClient([0.1, 0.2, 0.3])
    agent = TemporalAgent(client)

    result = asyncio.run(agent.embed("Testtext"))

    assert result == [0.1, 0.2, 0.3]
    assert client.calls == ["Testtext"]


def test_ollama_embedding_client_sets_num_ctx(monkeypatch):
    mock_client = MagicMock()
    mock_client.embed.return_value = {"embeddings": [[0.1]]}
    monkeypatch.setattr("ollama.Client", lambda host, **kwargs: mock_client)

    client = OllamaEmbeddingClient(host="http://localhost:11434", model="bge-m3")
    client.embed("Text")

    _, kwargs = mock_client.embed.call_args
    assert kwargs["options"]["num_ctx"] == 8192


def test_ollama_embedding_client_uses_embed_endpoint_with_truncate(monkeypatch):
    # Legacy /api/embeddings (ollama.Client.embeddings) liefert bei Texten nahe der
    # Kontextgrenze faelschlich "input length exceeds context length"; /api/embed
    # (mit truncate=True) verarbeitet dieselben Texte korrekt.
    mock_client = MagicMock()
    mock_client.embed.return_value = {"embeddings": [[0.1, 0.2]]}
    monkeypatch.setattr("ollama.Client", lambda host, **kwargs: mock_client)

    client = OllamaEmbeddingClient(host="http://localhost:11434", model="bge-m3")
    result = client.embed("Text")

    assert result == [0.1, 0.2]
    _, kwargs = mock_client.embed.call_args
    assert kwargs["input"] == "Text"
    assert kwargs["truncate"] is True
    assert not mock_client.embeddings.called
