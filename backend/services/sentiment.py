from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Protocol

from backend.services.agents.prefrontal import LLMClient

logger = logging.getLogger(__name__)


class SentimentClient(Protocol):
    async def analyze(self, text: str) -> float: ...


# BERT ist schnell und lokal, deckt aber nur kurze klare Sätze sicher. Für lange
# oder mehrdeutige Texte und als Robustheitsnetz dient Qwen3 als Fallback.
_LLM_PROMPT = (
    "Bewerte die emotionale Tendenz des folgenden Textes auf einer Skala "
    "von -1.0 (sehr negativ) bis 1.0 (sehr positiv), 0.0 bedeutet neutral.\n"
    "Antworte AUSSCHLIESSLICH mit einer einzelnen Dezimalzahl, kein Text.\n\n"
    "Text:\n"
)

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _parse_llm_score(raw: str) -> float:
    match = _NUMBER_RE.search(raw.strip())
    if not match:
        return 0.0
    try:
        return max(-1.0, min(1.0, float(match.group().replace(",", "."))))
    except ValueError:
        return 0.0


class HybridSentiment:
    def __init__(self, bert_model: Any, llm_client: LLMClient):
        self._bert = bert_model
        self._llm = llm_client

    def _bert_score(self, text: str) -> float:
        _, probs = self._bert.predict_sentiment([text], output_probabilities=True)
        if not probs:
            return 0.0
        weights = {"positive": 0.0, "negative": 0.0}
        for label, prob in probs[0]:
            if label in weights:
                weights[label] = prob
        return max(-1.0, min(1.0, weights["positive"] - weights["negative"]))

    async def _llm_score(self, text: str) -> float:
        raw = await asyncio.to_thread(self._llm.generate, _LLM_PROMPT + text)
        return _parse_llm_score(raw)

    async def analyze(self, text: str) -> float:
        if not text.strip():
            return 0.0
        try:
            return await asyncio.to_thread(self._bert_score, text)
        except Exception as exc:  # noqa: BLE001 - Fallback ist Teil des Designs
            logger.warning("BERT-Sentiment fehlgeschlagen, LLM-Fallback: %s", exc)
            try:
                return await self._llm_score(text)
            except Exception as exc2:  # noqa: BLE001 -letztes Netz, 0.0 ist sicher
                logger.error("LLM-Sentiment-Fallback fehlgeschlagen: %s", exc2)
                return 0.0
