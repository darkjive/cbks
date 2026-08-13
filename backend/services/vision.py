from __future__ import annotations

import base64
from pathlib import Path
from typing import Protocol

import fitz
import ollama

_OCR_PROMPT = (
    "Extrahiere den gesamten sichtbaren Text aus diesem Bild. "
    "Gib ausschließlich den erkannten Text zurück, ohne Erklärungen, "
    "Kommentare oder Einleitungen. Behalte Absätze und Zeilenumbrüche bei."
)


class VLMClient(Protocol):
    def extract_text(self, image_bytes: bytes) -> str: ...


class OllamaVLMClient:
    def __init__(self, host: str, model: str, num_ctx: int = 8192):
        self._client = ollama.Client(host=host)
        self._model = model
        self._num_ctx = num_ctx

    def extract_text(self, image_bytes: bytes) -> str:
        b64 = base64.b64encode(image_bytes).decode("ascii")
        response = self._client.generate(
            model=self._model, prompt=_OCR_PROMPT, images=[b64],
            options={"num_ctx": self._num_ctx},
        )
        return response["response"].strip()


def render_pdf_pages(path: Path, dpi: int = 200) -> list[bytes]:
    doc = fitz.open(str(path))
    try:
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        return [page.get_pixmap(matrix=matrix).tobytes("png") for page in doc]
    finally:
        doc.close()
