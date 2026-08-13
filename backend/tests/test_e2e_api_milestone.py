"""Echter Ende-zu-Ende-Test der REST-API gegen die laufende native Ollama-Instanz.

Voraussetzung: `systemctl --user status ollama` ist aktiv und `qwen3:8b`
sowie `bge-m3` sind gepullt. Kein Mock, keine Fakes - läuft In-Process via
FastAPI TestClient (kein Docker nötig, das API-Objekt läuft direkt im
Testprozess, spricht aber echtes HTTP mit dem echten Ollama).
"""
import ollama
import pytest
from fastapi.testclient import TestClient

from backend.main import app


def _ollama_available() -> bool:
    try:
        ollama.Client(host="http://127.0.0.1:11434").list()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_available(), reason="Natives Ollama nicht erreichbar auf 127.0.0.1:11434"
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CBKS_API_KEY", raising=False)
    with TestClient(app) as c:
        yield c


def test_add_pdf_then_ask_via_api(client, tmp_path):
    import fitz

    pdf_path = tmp_path / "papier.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "CBKS ist ein persoenliches Wissensmanagementsystem. "
        "Es nutzt FAISS fuer Vektorsuche und Ollama fuer lokale Sprachmodelle.",
    )
    doc.save(str(pdf_path))
    doc.close()

    with open(pdf_path, "rb") as fh:
        add_response = client.post(
            "/documents", files={"file": ("papier.pdf", fh, "application/pdf")}
        )
    assert add_response.status_code == 200
    assert add_response.json()["failed"] == 0

    ask_response = client.post("/ask", json={"question": "Was macht CBKS?"})
    assert ask_response.status_code == 200
    assert len(ask_response.json()["answer"].strip()) > 0
    assert len(ask_response.json()["sources"]) >= 1
