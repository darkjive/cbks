import asyncio
import json
from unittest.mock import MagicMock

import pytest

from backend.services.agents.prefrontal import OllamaLLMClient, PrefrontalAgent


class FakeLLMClient:
    def __init__(self, response: str):
        self._response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str, format: str = "") -> str:
        self.prompts.append(prompt)
        return self._response


def test_classify_and_extract_parses_json_response():
    response = json.dumps({"entities": ["FAISS", "Ollama"], "classification": "document"})
    agent = PrefrontalAgent(FakeLLMClient(response))

    result = asyncio.run(agent.classify_and_extract("Text über FAISS und Ollama"))

    assert result.classification == "document"
    assert [e.name for e in result.entities] == ["FAISS", "Ollama"]
    assert all(e.parent is None for e in result.entities)


def test_classify_and_extract_parses_person_relationship():
    response = json.dumps({
        "entities": [
            {"name": "Erika Mustermann", "type": "person", "parent": None, "relationship": "Ehefrau"},
            {"name": "Max Mustermann", "type": "person", "parent": None, "relationship": None},
        ],
        "classification": "document",
    })
    agent = PrefrontalAgent(FakeLLMClient(response))

    result = asyncio.run(agent.classify_and_extract("Heiratsurkunde Erika und Max Mustermann"))

    by_name = {e.name: e for e in result.entities}
    assert by_name["Erika Mustermann"].relationship == "Ehefrau"
    assert by_name["Erika Mustermann"].type == "person"
    assert by_name["Max Mustermann"].relationship is None


def test_classify_and_extract_parses_hierarchical_entities():
    response = json.dumps({
        "entities": [
            {"name": "Versicherung", "type": "concept", "parent": None},
            {"name": "MUSTERKASSE", "type": "organization", "parent": "Versicherung"},
            {"name": "333444555", "type": "value", "parent": "MUSTERKASSE"},
        ],
        "classification": "document",
    })
    agent = PrefrontalAgent(FakeLLMClient(response))

    result = asyncio.run(agent.classify_and_extract("Gesundheitskarte MUSTERKASSE 333444555"))

    by_name = {e.name: e for e in result.entities}
    assert by_name["Versicherung"].parent is None
    assert by_name["MUSTERKASSE"].parent == "Versicherung"
    assert by_name["MUSTERKASSE"].type == "organization"
    assert by_name["333444555"].parent == "MUSTERKASSE"


def test_classify_and_extract_raises_on_invalid_json():
    agent = PrefrontalAgent(FakeLLMClient("das ist kein JSON"))

    with pytest.raises(ValueError):
        asyncio.run(agent.classify_and_extract("Text"))


def test_classify_and_extract_raises_on_valid_json_non_object():
    agent = PrefrontalAgent(FakeLLMClient(json.dumps(["FAISS", "Ollama"])))

    with pytest.raises(ValueError):
        asyncio.run(agent.classify_and_extract("Text"))


def test_ollama_llm_client_sets_num_ctx(monkeypatch):
    mock_client = MagicMock()
    mock_client.generate.return_value = {"response": "ok"}
    monkeypatch.setattr("ollama.Client", lambda host, **kwargs: mock_client)

    client = OllamaLLMClient(host="http://localhost:11434", model="qwen3:8b")
    client.generate("Prompt")

    _, kwargs = mock_client.generate.call_args
    assert kwargs["options"]["num_ctx"] == 8192


def test_answer_question_returns_client_response():
    agent = PrefrontalAgent(FakeLLMClient("Die Antwort lautet 42."))

    answer = asyncio.run(agent.answer_question("Wie lautet die Antwort?", "Kontext: 42"))

    assert answer == "Die Antwort lautet 42."
