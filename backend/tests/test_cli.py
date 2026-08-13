import json
import os

import pytest
from typer.testing import CliRunner

from backend.cli import app
from backend.services.agents.prefrontal import OllamaLLMClient
from backend.services.agents.temporal import OllamaEmbeddingClient

runner = CliRunner()


@pytest.fixture(autouse=True)
def fake_ollama(monkeypatch):
    def fake_embed(self, text: str) -> list[float]:
        return [1.0] + [0.0] * 1023

    def fake_generate(self, prompt: str, format: str = "") -> str:
        if "Beantworte die folgende Frage" in prompt:
            return "Das Dokument handelt von FAISS."
        return json.dumps({"classification": "document", "entities": ["FAISS"]})

    monkeypatch.setattr(OllamaEmbeddingClient, "embed", fake_embed)
    monkeypatch.setattr(OllamaLLMClient, "generate", fake_generate)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    return tmp_path


def test_stats_on_empty_db():
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "0" in result.stdout


def test_note_then_ask_end_to_end(tmp_path):
    note_result = runner.invoke(app, ["note", "Ein Text über FAISS"])
    assert note_result.exit_code == 0

    ask_result = runner.invoke(app, ["ask", "Worum geht es?"])
    assert ask_result.exit_code == 0
    assert "FAISS" in ask_result.stdout


def test_add_duplicate_reports_conflict(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("Einmaliger Inhalt", encoding="utf-8")

    first = runner.invoke(app, ["add", str(path)])
    assert first.exit_code == 0

    second = runner.invoke(app, ["add", str(path)])
    assert second.exit_code == 0
    assert "bereits bekannt" in second.stdout.lower()


def test_show_unknown_node_reports_not_found():
    result = runner.invoke(app, ["show", "does-not-exist"])
    assert result.exit_code == 1
    assert "nicht gefunden" in result.stdout.lower()


def test_rebuild_runs_without_error(tmp_path):
    runner.invoke(app, ["note", "Text für Rebuild"])
    result = runner.invoke(app, ["rebuild"])
    assert result.exit_code == 0


def test_search_finds_ingested_note():
    note_result = runner.invoke(app, ["note", "Ein Text über Graphentheorie"])
    assert note_result.exit_code == 0

    search_result = runner.invoke(app, ["search", "Graphentheorie"])
    assert search_result.exit_code == 0
    assert "Graphentheorie" in search_result.stdout


def test_retry_reprocesses_failed_events(monkeypatch):
    calls = {"n": 0}

    def flaky_generate(self, prompt: str, format: str = "") -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporärer Fehler")
        return json.dumps({"classification": "note", "entities": []})

    monkeypatch.setattr(OllamaLLMClient, "generate", flaky_generate)

    note_result = runner.invoke(app, ["note", "Text der zunächst fehlschlägt"])
    assert note_result.exit_code == 0
    assert "Verarbeitet: 0, Fehlgeschlagen: 1" in note_result.stdout

    retry_result = runner.invoke(app, ["retry"])
    assert retry_result.exit_code == 0
    assert "Erneut verarbeitet: 1, weiterhin fehlgeschlagen: 0" in retry_result.stdout


def test_backup_runs_configured_script(tmp_path, monkeypatch):
    script_path = tmp_path / "backup.sh"
    script_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(script_path, 0o755)
    monkeypatch.setenv("CBKS_BACKUP_SCRIPT", str(script_path))

    result = runner.invoke(app, ["backup"])
    assert result.exit_code == 0
    assert "Backup abgeschlossen" in result.stdout


def test_dedupe_runs_without_error(tmp_path):
    runner.invoke(app, ["note", "Ein Text über FAISS"])

    result = runner.invoke(app, ["dedupe"])

    assert result.exit_code == 0
    assert "Geprüft" in result.stdout


def test_export_writes_markdown_with_frontmatter(tmp_path):
    runner.invoke(app, ["note", "Ein Text über FAISS"])
    ziel = tmp_path / "vault"

    result = runner.invoke(app, ["export", str(ziel)])

    assert result.exit_code == 0
    dateien = list(ziel.glob("*.md"))
    assert len(dateien) == 1
    inhalt = dateien[0].read_text(encoding="utf-8")
    assert inhalt.startswith("---\n")
    assert "title:" in inhalt
    assert "created:" in inhalt
    assert "Ein Text über FAISS" in inhalt


def test_export_skips_nodes_without_content(tmp_path):
    # note erzeugt zusätzlich den Entity-Node "FAISS" ohne content —
    # der darf nicht als leere Markdown-Datei landen.
    runner.invoke(app, ["note", "Ein Text über FAISS"])
    ziel = tmp_path / "vault"

    result = runner.invoke(app, ["export", str(ziel)])

    assert result.exit_code == 0
    assert len(list(ziel.glob("*.md"))) == 1
    assert "1" in result.stdout


def test_export_is_idempotent(tmp_path):
    runner.invoke(app, ["note", "Ein Text über FAISS"])
    ziel = tmp_path / "vault"

    runner.invoke(app, ["export", str(ziel)])
    result = runner.invoke(app, ["export", str(ziel)])

    assert result.exit_code == 0
    assert len(list(ziel.glob("*.md"))) == 1


def test_index_command_without_vault_dir_exits_with_error(monkeypatch, tmp_path):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CBKS_VAULT_DIR", raising=False)
    runner = CliRunner()

    result = runner.invoke(app, ["index"])

    assert result.exit_code == 1
    assert "CBKS_VAULT_DIR" in result.stdout


def test_index_command_indexes_vault(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "notiz.md").write_text("Text über FAISS")
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CBKS_VAULT_DIR", str(vault))
    runner = CliRunner()

    result = runner.invoke(app, ["index"])

    assert result.exit_code == 0
    assert "Verarbeitet: 1" in result.stdout
