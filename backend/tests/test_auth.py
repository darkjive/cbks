from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from backend.auth import require_api_key


def _make_app() -> FastAPI:
    app = FastAPI(dependencies=[Depends(require_api_key)])

    @app.get("/ping")
    def ping() -> dict:
        return {"ok": True}

    return app


def test_no_api_key_configured_allows_request(monkeypatch):
    monkeypatch.delenv("CBKS_API_KEY", raising=False)
    client = TestClient(_make_app())

    response = client.get("/ping")

    assert response.status_code == 200


def test_missing_header_rejected_when_key_configured(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")
    client = TestClient(_make_app())

    response = client.get("/ping")

    assert response.status_code == 401


def test_correct_header_accepted(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")
    client = TestClient(_make_app())

    response = client.get("/ping", headers={"X-API-Key": "secret123"})

    assert response.status_code == 200


def test_wrong_header_rejected(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")
    client = TestClient(_make_app())

    response = client.get("/ping", headers={"X-API-Key": "wrong"})

    assert response.status_code == 401


def test_empty_string_api_key_treated_as_unset(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "")
    client = TestClient(_make_app())

    response = client.get("/ping")

    assert response.status_code == 200


def test_empty_header_rejected_when_key_configured(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")
    client = TestClient(_make_app())

    response = client.get("/ping", headers={"X-API-Key": ""})

    assert response.status_code == 401
