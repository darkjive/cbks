"""Echter Ende-zu-Ende-Test gegen die laufende native Ollama-Instanz.

Voraussetzung: `systemctl --user status ollama` ist aktiv und `qwen3:8b`
sowie `bge-m3` sind gepullt (siehe Phase 1, Task 5+6). Kein Mock, keine Fakes -
das ist bewusst der einzige Test in Phase 2, der echte LLM-Latenz akzeptiert.
"""
from pathlib import Path

import ollama
import pytest
from typer.testing import CliRunner

from backend.cli import app

runner = CliRunner()


def _ollama_available() -> bool:
    try:
        ollama.Client(host="http://127.0.0.1:11434").list()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _ollama_available(), reason="Natives Ollama nicht erreichbar auf 127.0.0.1:11434"
)


def test_add_pdf_then_ask_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))

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

    add_result = runner.invoke(app, ["add", str(pdf_path)])
    assert add_result.exit_code == 0
    assert "Fehlgeschlagen: 0" in add_result.stdout

    ask_result = runner.invoke(app, ["ask", "Was macht CBKS?"])
    assert ask_result.exit_code == 0
    assert len(ask_result.stdout.strip()) > 0
    assert "Quellen:" in ask_result.stdout


def test_rebuild_restores_graph_from_event_log(tmp_path, monkeypatch):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))

    runner.invoke(app, ["note", "Eine Notiz über Graphentheorie und DAGs."])
    stats_before = runner.invoke(app, ["stats"])
    assert "'processed': 1" in stats_before.stdout

    rebuild_result = runner.invoke(app, ["rebuild"])
    assert rebuild_result.exit_code == 0
    assert "0 fehlgeschlagen" in rebuild_result.stdout

    stats_after = runner.invoke(app, ["stats"])
    assert "'processed': 1" in stats_after.stdout
