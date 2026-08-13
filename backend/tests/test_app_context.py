import sqlite3

from backend.app_context import AppContext, build_context
from backend.services.dispatcher import Dispatcher
from backend.services.event_log import EventLog
from backend.services.graph_backend import GraphBackend
from backend.storage.faiss_index import FaissIndex


def test_build_context_returns_wired_appcontext(tmp_path, monkeypatch):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))

    ctx = build_context()

    assert isinstance(ctx, AppContext)
    assert isinstance(ctx.conn, sqlite3.Connection)
    assert isinstance(ctx.event_log, EventLog)
    assert isinstance(ctx.graph, GraphBackend)
    assert isinstance(ctx.faiss_index, FaissIndex)
    assert isinstance(ctx.dispatcher, Dispatcher)
