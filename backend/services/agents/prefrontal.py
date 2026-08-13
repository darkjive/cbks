import asyncio
import json
import re
from dataclasses import dataclass
from typing import Protocol

import ollama

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

_CLASSIFY_PROMPT = """Extrahiere aus folgendem Text ein strukturiertes Wissensnetz.

1. Bis zu 15 benannte Entitäten (Organisationen, Personen, Konzepte, konkrete Werte wie
   Nummern/IDs/Daten). Für jede Entität: "name", "type"
   (organization|person|concept|value) und "parent" - der Name einer übergeordneten
   Entität, falls diese Entität fachlich zu einer anderen gehört (z.B. eine
   Versicherungsnummer gehört zur ausstellenden Organisation, eine Versicherung
   gehört zur Kategorie "Versicherung"), sonst null.
   Wichtig: Erstelle KEINE eigene Entität für reine Feld-Beschriftungen wie
   "Versichertennummer", "Mitgliedsnummer" oder "Kundennummer" - das sind nur
   Labels, keine Entitäten. Nimm stattdessen direkt den Wert als Entität (type
   "value") und setze deren "parent" auf die zugehörige Organisation/Person
   (nicht auf das Label). Bei "Label: Wert" nimm als "name" nur den Wert.
   Hinweis: Der Text stammt oft aus OCR von Karten/Formularen, dort können Label und
   Wert in beliebiger Reihenfolge nah beieinander stehen (z.B. steht "333444555"
   über der Beschriftung "Versichertennummer" und gehört zur darüberstehenden
   Organisation, z.B. "Musterkasse").
2. Für Entitäten vom Typ "person": Falls der Text erkennen lässt, dass diese Person in
   einer familiären/persönlichen Beziehung zum Dokumenteninhaber steht, setze
   "relationship" auf eine kurze deutsche Bezeichnung dieser Beziehung (z.B. "Ehefrau",
   "Ehemann", "Sohn", "Tochter", "Mutter", "Vater"), sonst null. Bei anderen Entitäten
   immer null.
3. Eine Klassifikation des Event-Typs: eines von [document, commit, note, task, screenshot].

Antworte ausschließlich als JSON:
{{"entities": [{{"name": "...", "type": "...", "parent": "..." oder null,
                "relationship": "..." oder null}}, ...],
  "classification": "..."}}

Text:
{text}
"""

_ANSWER_PROMPT = """Beantworte die letzte Frage ausschließlich anhand des gegebenen Kontexts.
Wenn der Kontext die Antwort nicht enthält, sag das ehrlich.

Kontext:
{context}
{history}
Letzte Frage:
{question}
"""

_HISTORY_BLOCK = """
Bisheriger Gesprächsverlauf:
{turns}
"""


class LLMClient(Protocol):
    def generate(self, prompt: str, format: str = "") -> str: ...


class OllamaLLMClient:
    def __init__(self, host: str, model: str, num_ctx: int = 8192, timeout: float = 120.0):
        # think=False: Standard-LLM-Modelle (z.B. qwen3) starten sonst im
        # Thinking-Modus und brauchen 15-60 s+ Chain-of-Thought bevor die erste
        # Antwort kommt -> Chat wirkt eingefroren. Thinking wird hier nirgends
        # gebraucht (Antworten + JSON-Klassifikation).
        self._client = ollama.Client(host=host, timeout=timeout)
        self._model = model
        self._num_ctx = num_ctx

    def generate(self, prompt: str, format: str = "") -> str:
        response = self._client.generate(
            model=self._model, prompt=prompt, format=format, think=False,
            options={"num_ctx": self._num_ctx},
        )
        return _strip_think(response["response"])


def _strip_think(text: str) -> str:
    # Aeltere Ollama-Server bzw. Modelle ohne think-Feld inline-denken als
    # <think>...</think> in den Response-Text. think=False oben verhindert das
    # bei aktuellen Servern, aber defensiv strippen fuer Robustheit.
    return _THINK_RE.sub("", text).strip()


@dataclass
class ExtractedEntity:
    name: str
    type: str = "concept"
    parent: str | None = None
    relationship: str | None = None


@dataclass
class ClassificationResult:
    classification: str
    entities: list[ExtractedEntity]


def _parse_entity(item: object) -> ExtractedEntity:
    if isinstance(item, str):
        return ExtractedEntity(name=item)
    return ExtractedEntity(
        name=item["name"],
        type=item.get("type") or "concept",
        parent=item.get("parent") or None,
        relationship=item.get("relationship") or None,
    )


class PrefrontalAgent:
    def __init__(self, client: LLMClient):
        self._client = client

    async def classify_and_extract(self, text: str) -> ClassificationResult:
        prompt = _CLASSIFY_PROMPT.format(text=text)
        raw = await asyncio.to_thread(self._client.generate, prompt, format="json")
        try:
            data = json.loads(raw)
            entities = [_parse_entity(item) for item in data["entities"]]
            return ClassificationResult(classification=data["classification"], entities=entities)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"Ungültige LLM-Antwort (kein valides JSON): {raw!r}") from exc

    async def answer_question(
        self,
        question: str,
        context: str,
        history: list[tuple[str, str]] | None = None,
    ) -> str:
        if history:
            turns = "\n".join(f"{role}: {content}" for role, content in history)
            history_block = _HISTORY_BLOCK.format(turns=turns)
        else:
            history_block = ""
        prompt = _ANSWER_PROMPT.format(
            context=context, question=question, history=history_block
        )
        return await asyncio.to_thread(self._client.generate, prompt)
