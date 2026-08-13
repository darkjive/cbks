import json
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app_context import build_context
from backend.main import app
from backend.models.nodes import Node
from backend.services import tts as tts_service
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
    # Kontextmanager noetig: erst damit feuert der Lifespan-Handler,
    # der app.state.ctx mit dem frisch gesetzten CBKS_DATA_DIR baut.
    with TestClient(app) as c:
        yield c


def test_context_is_singleton_across_requests(client):
    from backend.main import app as main_app
    ctx_before = main_app.state.ctx
    client.get("/stats")
    client.get("/stats")
    assert main_app.state.ctx is ctx_before


def test_stats_on_empty_db(client):
    response = client.get("/stats")
    assert response.status_code == 200
    assert response.json()["events"] == {"pending": 0, "processed": 0, "failed": 0}


def test_note_then_ask_end_to_end(client):
    note_response = client.post("/notes", json={"text": "Ein Text über FAISS"})
    assert note_response.status_code == 200
    assert note_response.json()["failed"] == 0

    ask_response = client.post("/ask", json={"question": "Worum geht es?"})
    assert ask_response.status_code == 200
    assert ask_response.json()["answer"] == "Das Dokument handelt von FAISS."
    assert len(ask_response.json()["sources"]) == 1


def test_search_finds_ingested_note(client):
    client.post("/notes", json={"text": "Ein Text über Graphentheorie"})

    response = client.get("/search", params={"q": "Graphentheorie"})

    assert response.status_code == 200
    hits = response.json()
    assert len(hits) == 1
    assert hits[0]["node"]["title"] == "Ein Text über Graphentheorie"


def test_add_document_duplicate_reports_conflict(client):
    content = b"Einmaliger Inhalt"
    first = client.post("/documents", files={"file": ("doc.md", content, "text/markdown")})
    assert first.status_code == 200
    assert first.json()["duplicate"] is False

    second = client.post("/documents", files={"file": ("doc.md", content, "text/markdown")})
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["duplicate_since"] is not None


def test_add_document_path_traversal_is_sanitized(client, tmp_path):
    content = b"Boesartiger Inhalt"
    escape_target = tmp_path.parent / "escaped_file.md"

    response = client.post(
        "/documents", files={"file": ("../../escaped_file.md", content, "text/markdown")}
    )

    assert response.status_code == 200
    assert not escape_target.exists()
    assert not (Path.cwd() / "escaped_file.md").exists()

    search_response = client.get("/search", params={"q": "escaped_file"})
    hits = search_response.json()
    assert any(hit["node"]["title"] == "escaped_file.md" for hit in hits)


def test_add_document_upload_is_ephemeral(client):
    content = b"Wird nach Ingest wieder geloescht"

    response = client.post(
        "/documents", files={"file": ("ephemeral.md", content, "text/markdown")}
    )

    assert response.status_code == 200
    uploads_root = Path(tempfile.gettempdir()) / "cbks-uploads"
    if uploads_root.exists():
        assert list(uploads_root.rglob("*")) == []


def test_get_unknown_node_returns_404(client):
    response = client.get("/nodes/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"] == "Node nicht gefunden"


def test_get_known_node_returns_node_and_neighbors(client):
    client.post("/notes", json={"text": "Ein Text über Graphentheorie"})
    search_response = client.get("/search", params={"q": "Graphentheorie"})
    node_id = search_response.json()[0]["node"]["id"]

    response = client.get(f"/nodes/{node_id}")

    assert response.status_code == 200
    assert response.json()["node"]["id"] == node_id
    assert len(response.json()["neighbors"]) == 1


def test_retry_reprocesses_failed_events(client, monkeypatch):
    calls = {"n": 0}

    def flaky_generate(self, prompt: str, format: str = "") -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Simulierter LLM-Fehler")
        return json.dumps({"classification": "document", "entities": ["FAISS"]})

    monkeypatch.setattr(OllamaLLMClient, "generate", flaky_generate)

    note_response = client.post("/notes", json={"text": "Text der zunächst fehlschlägt"})
    assert note_response.json()["failed"] == 1

    retry_response = client.post("/retry")
    assert retry_response.status_code == 200
    assert retry_response.json() == {"processed": 1, "failed": 0}


def test_rebuild_runs_without_error(client):
    client.post("/notes", json={"text": "Text für Rebuild"})

    response = client.post("/rebuild")

    assert response.status_code == 200
    assert response.json()["failed"] == 0


def test_backup_runs_configured_script(client, tmp_path, monkeypatch):
    script_path = tmp_path / "backup.sh"
    script_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script_path.chmod(0o755)
    monkeypatch.setenv("CBKS_BACKUP_SCRIPT", str(script_path))

    response = client.post("/backup")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_missing_api_key_rejected_when_configured(client, monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")

    response = client.get("/stats")

    assert response.status_code == 401


def test_correct_api_key_accepted(client, monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")

    response = client.get("/stats", headers={"X-API-Key": "secret123"})

    assert response.status_code == 200


def test_graph_returns_all_nodes_and_edges(client):
    client.post("/notes", json={"text": "Ein Text über Graphentheorie"})

    response = client.get("/graph")

    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) >= 1
    assert "edges" in body


def test_concurrent_requests_do_not_crash_sentiment_model(client):
    client.post("/notes", json={"text": "Ein Text über Graphentheorie"})
    node_id = client.get("/search", params={"q": "Graphentheorie"}).json()[0]["node"]["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.get(f"/nodes/{node_id}"), range(4)))

    assert all(r.status_code == 200 for r in responses)


def test_dedupe_route_returns_checked_and_merged_counts(client):
    client.post("/notes", json={"text": "Ein Text über FAISS"})

    response = client.post("/dedupe")

    assert response.status_code == 200
    body = response.json()
    assert "checked" in body
    assert "merged" in body


def test_analysis_recurring_returns_topics_with_multiple_mentions(client):
    client.post("/notes", json={"text": "Ein Text über FAISS"})
    client.post("/notes", json={"text": "Noch ein Text über FAISS"})

    response = client.get("/analysis/recurring")

    assert response.status_code == 200
    topics = response.json()
    assert any(t["title"] == "FAISS" and t["mentions"] >= 2 for t in topics)


def test_analyze_contradictions_creates_edge_between_conflicting_notes(client, monkeypatch):
    def fake_generate(self, prompt: str, format: str = "") -> str:
        if "Beantworte die letzte Frage" in prompt:
            return "Das Dokument handelt von FAISS."
        if "Prüfe, ob sich diese beiden Textaussagen" in prompt:
            return json.dumps({"contradicts": True, "confidence": 0.9})
        return json.dumps({"classification": "document", "entities": ["FAISS"]})

    monkeypatch.setattr(OllamaLLMClient, "generate", fake_generate)

    client.post("/notes", json={"text": "FAISS funktioniert einwandfrei"})
    client.post("/notes", json={"text": "FAISS ist kaputt"})

    response = client.post("/analyze/contradictions")

    assert response.status_code == 200
    body = response.json()
    assert body["found"] >= 1

    graph_response = client.get("/graph")
    edges = graph_response.json()["edges"]
    assert any(e["relation_type"] == "contradicts" for e in edges)


def test_get_node_audio_returns_wav(client, monkeypatch):
    def fake_synthesize(text, cache_dir):
        path = cache_dir / "fake.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"RIFF-fake-wav-bytes")
        return path

    monkeypatch.setattr("backend.main.tts_service.synthesize", fake_synthesize)

    client.post("/notes", json={"text": "Ein Text ueber Graphentheorie"})
    node_id = client.get("/search", params={"q": "Graphentheorie"}).json()[0]["node"]["id"]

    response = client.get(f"/nodes/{node_id}/audio")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFF-fake-wav-bytes"


def test_get_node_audio_without_tts_dependencies_returns_503(client, monkeypatch):
    # TTS ist optional (backend/requirements-tts.txt). Fehlt es, soll der Endpunkt
    # eine klare 503 liefern statt eines ImportError-Tracebacks.
    def fake_synthesize(text, cache_dir):
        raise tts_service.TTSUnavailableError("TTS ist nicht installiert.")

    monkeypatch.setattr("backend.main.tts_service.synthesize", fake_synthesize)

    client.post("/notes", json={"text": "Ein Text ueber Graphentheorie"})
    node_id = client.get("/search", params={"q": "Graphentheorie"}).json()[0]["node"]["id"]

    response = client.get(f"/nodes/{node_id}/audio")

    assert response.status_code == 503
    assert "nicht installiert" in response.json()["detail"]


def test_get_node_audio_unknown_node_returns_404(client):
    response = client.get("/nodes/does-not-exist/audio")

    assert response.status_code == 404


def test_get_node_audio_empty_content_returns_422(client):
    ctx = build_context()
    ctx.graph.add_node(Node(
        id="empty-content-node", title="Leer", type="note",
        creation_time="2026-07-09T00:00:00+00:00", last_access="2026-07-09T00:00:00+00:00",
    ))

    response = client.get("/nodes/empty-content-node/audio")

    assert response.status_code == 422


def test_get_vault_default_path_reflects_env(monkeypatch):
    # Der App-Kontext (inkl. Config) wird jetzt einmal beim Lifespan-Start gebaut -
    # die Env-Variable muss also VOR dem Aufbau des TestClient gesetzt sein, nicht
    # erst danach (die `client`-Fixture waere hier zu spaet dran).
    monkeypatch.setenv("CBKS_VAULT_PATH", "/tmp/mein-vault")

    with TestClient(app) as scoped_client:
        response = scoped_client.get("/vault/default-path")

    assert response.status_code == 200
    assert response.json()["path"] == "/tmp/mein-vault"


def test_get_vault_default_path_empty_when_unset(client, monkeypatch):
    monkeypatch.delenv("CBKS_VAULT_PATH", raising=False)

    response = client.get("/vault/default-path")

    assert response.status_code == 200
    assert response.json()["path"] == ""


def test_start_vault_scan_rejects_invalid_path(client):
    response = client.post("/vault/scan", json={"path": "/pfad/existiert/garantiert/nicht"})

    assert response.status_code == 400


def test_get_vault_scan_unknown_job_returns_404(client):
    response = client.get("/vault/scan/unbekannte-id")

    assert response.status_code == 404


def test_start_vault_scan_and_poll_until_done(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "notiz.md").write_text("Text über FAISS")

    # asyncio.create_task() im async-Endpoint braucht einen Event-Loop, der ueber
    # mehrere Requests hinweg durchlaeuft. Der modulweite `client` (ohne `with`) baut
    # pro Aufruf einen neuen, kurzlebigen Loop auf, wodurch der Background-Task nach
    # dem ersten Request verwaist und nie weiterlaeuft (empirisch verifiziert). Ein
    # lokal gescopter Context-Manager-Client haelt den Loop fuer beide Requests am
    # Leben. Im echten Server (uvicorn) existiert dieses Problem nicht, da dort ohnehin
    # nur ein durchgehend laufender Event-Loop existiert.
    with TestClient(app) as scoped_client:
        start_response = scoped_client.post("/vault/scan", json={"path": str(vault)})
        assert start_response.status_code == 200
        job_id = start_response.json()["job_id"]

        body = None
        for _ in range(50):
            poll = scoped_client.get(f"/vault/scan/{job_id}")
            assert poll.status_code == 200
            body = poll.json()
            if body["done"]:
                break
            time.sleep(0.05)
        else:
            pytest.fail("Scan wurde nicht innerhalb des Timeouts fertig")

    assert body["total"] == 1
    assert body["processed"] == 1
    assert body["duplicates"] == 0
    assert body["failed"] == 0
    assert body["error"] is None


@pytest.fixture()
def vault_client(isolated_data_dir, monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("CBKS_VAULT_DIR", str(vault))
    with TestClient(app) as c:
        yield c, vault


def test_vault_tree_reflects_filesystem(vault_client):
    client, vault = vault_client
    (vault / "notiz.md").write_text("Text")

    response = client.get("/vault/tree")

    assert response.status_code == 200
    names = {entry["name"] for entry in response.json()}
    assert names == {"notiz.md"}


def test_vault_file_get_returns_content_and_hash(vault_client):
    client, vault = vault_client
    (vault / "notiz.md").write_text("Hallo Vault")

    response = client.get("/vault/file", params={"path": "notiz.md"})

    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "Hallo Vault"
    assert len(body["content_hash"]) == 64


def test_vault_file_get_missing_returns_404(vault_client):
    client, _ = vault_client

    response = client.get("/vault/file", params={"path": "gibtsnicht.md"})

    assert response.status_code == 404


def test_vault_file_get_traversal_returns_400(vault_client):
    client, _ = vault_client

    response = client.get("/vault/file", params={"path": "../../etc/passwd"})

    assert response.status_code == 400


def test_vault_file_put_creates_and_indexes(vault_client):
    client, vault = vault_client

    response = client.put("/vault/file", json={"path": "neu.md", "content": "Text über FAISS"})

    assert response.status_code == 200
    body = response.json()
    assert body["indexed"] is True
    assert (vault / "neu.md").is_file()

    graph_response = client.get("/graph")
    assert any(n["title"] == "neu" for n in graph_response.json()["nodes"])


def test_vault_file_put_conflict_returns_409(vault_client):
    client, vault = vault_client
    (vault / "notiz.md").write_text("Original")

    response = client.put(
        "/vault/file", json={"path": "notiz.md", "content": "Überschrieben", "expected_hash": "falsch"}
    )

    assert response.status_code == 409


def test_vault_file_put_without_vault_dir_returns_400(client):
    response = client.put("/vault/file", json={"path": "notiz.md", "content": "x"})

    assert response.status_code == 400


def test_vault_rename_moves_file_and_updates_node_metadata(vault_client):
    client, vault = vault_client
    client.put("/vault/file", json={"path": "alt.md", "content": "Text über FAISS"})

    response = client.post("/vault/rename", json={"source": "alt.md", "target": "neu.md"})

    assert response.status_code == 200
    assert not (vault / "alt.md").exists()
    assert (vault / "neu.md").is_file()
    graph = client.get("/graph").json()
    assert graph["nodes"][0]["metadata"]["source_path"] == "neu.md"


def test_vault_rename_missing_source_returns_404(vault_client):
    client, _ = vault_client

    response = client.post("/vault/rename", json={"source": "weg.md", "target": "ziel.md"})

    assert response.status_code == 404


def test_vault_delete_removes_file_and_node(vault_client):
    client, vault = vault_client
    client.put("/vault/file", json={"path": "weg.md", "content": "Text über FAISS"})
    node_id = next(
        n["id"] for n in client.get("/graph").json()["nodes"]
        if n["metadata"].get("source_path") == "weg.md"
    )

    response = client.request("DELETE", "/vault/file", params={"path": "weg.md"})

    assert response.status_code == 200
    assert not (vault / "weg.md").exists()
    graph = client.get("/graph").json()
    assert all(n["id"] != node_id for n in graph["nodes"])


def test_vault_delete_missing_returns_404(vault_client):
    client, _ = vault_client

    response = client.request("DELETE", "/vault/file", params={"path": "gibtsnicht.md"})

    assert response.status_code == 404


def test_vault_delete_after_edit_does_not_resurrect_node_on_rebuild(vault_client):
    client, vault = vault_client
    client.put("/vault/file", json={"path": "n.md", "content": "Version 1"})
    # index_file schreibt beim ersten Indexieren eine id ins Frontmatter der
    # Datei zurueck (siehe vault_index._ensure_id). Diese muss beim Edit
    # erhalten bleiben, sonst entsteht ein neuer Node statt eines Upserts.
    current = client.get("/vault/file", params={"path": "n.md"}).json()
    edited_content = current["content"].replace("Version 1", "Version 2")
    client.put(
        "/vault/file",
        json={"path": "n.md", "content": edited_content, "expected_hash": current["content_hash"]},
    )
    node_id = next(
        n["id"] for n in client.get("/graph").json()["nodes"]
        if n["metadata"].get("source_path") == "n.md"
    )

    response = client.request("DELETE", "/vault/file", params={"path": "n.md"})

    assert response.status_code == 200
    assert all(n["id"] != node_id for n in client.get("/graph").json()["nodes"])

    # Regressionstest fuer Finding 1: vor dem Fix ueberlebte das aeltere der
    # beiden vault.file-Events den Delete und liess den Node bei `cbks
    # rebuild` mit veraltetem Inhalt wieder auferstehen.
    rebuild_response = client.post("/rebuild")

    assert rebuild_response.status_code == 200
    assert all(n["id"] != node_id for n in client.get("/graph").json()["nodes"])


def test_vault_attachment_upload_saves_under_attachments(vault_client):
    client, vault = vault_client

    response = client.post(
        "/vault/attachment", files={"file": ("bild.png", b"\x89PNG", "image/png")}
    )

    assert response.status_code == 200
    assert response.json()["path"] == "attachments/bild.png"
    assert (vault / "attachments" / "bild.png").read_bytes() == b"\x89PNG"


def test_vault_rescan_endpoint_reports_summary(vault_client):
    client, vault = vault_client
    (vault / "notiz.md").write_text("Text über FAISS")

    response = client.post("/vault/rescan")

    assert response.status_code == 200
    body = response.json()
    assert body["processed"] == 1
    assert body["deleted"] == 0


def test_vault_backlinks_returns_incoming_wikilinks(vault_client):
    client, vault = vault_client
    (vault / "ziel.md").write_text("---\ntitle: Ziel-Notiz\n---\n\nInhalt")
    (vault / "quelle.md").write_text("Verweist auf [[Ziel-Notiz]]")
    client.post("/vault/rescan")

    response = client.get("/vault/backlinks", params={"path": "ziel.md"})

    assert response.status_code == 200
    backlinks = response.json()["backlinks"]
    assert len(backlinks) == 1
    assert backlinks[0]["metadata"]["source_path"] == "quelle.md"


def test_vault_backlinks_empty_for_unlinked_note(vault_client):
    client, vault = vault_client
    (vault / "einsam.md").write_text("Niemand verlinkt mich")
    client.post("/vault/rescan")

    response = client.get("/vault/backlinks", params={"path": "einsam.md"})

    assert response.status_code == 200
    assert response.json()["backlinks"] == []


def test_vault_search_finds_matching_note(vault_client):
    client, vault = vault_client
    (vault / "notiz.md").write_text("Ein Text über Graphentheorie")
    client.post("/vault/rescan")

    response = client.get("/vault/search", params={"q": "Graphentheorie"})

    assert response.status_code == 200
    hits = response.json()
    assert len(hits) == 1
    assert hits[0]["node"]["metadata"]["source_path"] == "notiz.md"
