import asyncio
from typing import Protocol

import ollama


class EmbeddingClient(Protocol):
    def embed(self, text: str) -> list[float]: ...


class OllamaEmbeddingClient:
    def __init__(self, host: str, model: str, num_ctx: int = 8192, timeout: float = 60.0):
        self._client = ollama.Client(host=host, timeout=timeout)
        self._model = model
        self._num_ctx = num_ctx

    def embed(self, text: str) -> list[float]:
        # ollama.Client.embeddings() (legacy /api/embeddings) meldet faelschlich
        # "input length exceeds context length" bei Texten nahe der Kontextgrenze.
        # /api/embed (mit truncate=True) verarbeitet dieselben Texte korrekt.
        response = self._client.embed(
            model=self._model, input=text, truncate=True,
            options={"num_ctx": self._num_ctx},
        )
        embeddings = response["embeddings"]
        if not embeddings:
            raise ValueError("Ollama lieferte keine Embeddings (Modell gepullt?)")
        return list(embeddings[0])


class TemporalAgent:
    def __init__(self, client: EmbeddingClient):
        self._client = client

    async def embed(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._client.embed, text)
