import json
import logging

from fastapi.testclient import TestClient

from backend.logging_setup import setup_logging
from backend.main import app


def test_setup_logging_emits_parseable_json():
    setup_logging()
    handler = next(
        h for h in logging.getLogger().handlers if h.formatter.__class__.__name__ == "JsonFormatter"
    )
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "hallo", None, None)

    parsed = json.loads(handler.format(record))

    assert parsed["message"] == "hallo"
    assert parsed["levelname"] == "INFO"


def test_setup_logging_is_idempotent():
    setup_logging()
    count_before = len(logging.getLogger().handlers)
    setup_logging()
    assert len(logging.getLogger().handlers) == count_before


def test_request_middleware_logs_method_path_status(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CBKS_API_KEY", raising=False)
    with TestClient(app) as client:
        with caplog.at_level(logging.INFO, logger="cbks.api"):
            client.get("/stats")

    record = next(r for r in caplog.records if r.name == "cbks.api")
    assert record.method == "GET"
    assert record.path == "/stats"
    assert record.status == 200
    assert record.duration_ms >= 0
