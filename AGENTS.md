# AGENTS.md — CBKS

CBKS (Cognitive Brain Knowledge System): brain-inspired personal knowledge system.
Python backend (FastAPI + Typer CLI) backed by SQLite + FAISS and Ollama for
LLM/VLM/embeddings. React/TS/Vite frontend with a 3D graph view.

The project's domain language is German: CLI output, API error messages, test
assertions, and the spec are German. Preserve German when editing user-facing
strings; tests assert German substrings.

## Layout

- `backend/` — single Python package `backend` (declared in `pyproject.toml`).
  - `main.py` — FastAPI app (API server entrypoint). `cli.py` — Typer CLI.
  - `app_context.py` — `build_context()` wires **all** dependencies (Config,
    SQLite conn, EventLog, GraphBackend, FaissIndex, agents, dispatcher). It is
    called fresh on every request and every CLI invocation; treat it as the
    composition root, not a singleton (the sentiment model is the one cached
    singleton, behind a process-wide lock).
  - `models/` dataclasses (nodes/edges/events) · `storage/` sqlite + faiss ·
    `services/` domain logic + `services/agents/` (temporal=embeddings,
    prefrontal=LLM, pineal=contradictions).
- `frontend/` — React 19 + Vite 8 + oxlint. 3D graph via @react-three/fiber.
- `docker/compose.yml` + `backend/Dockerfile` — backend container only.
- `data/` — **gitignored runtime data** (SQLite db, FAISS index). SQLite is the
  source of truth; NetworkX graph is a write-through cache. Rebuildable from the
  append-only event log.
- `docs/CBKS_SPEC_v1.2.md` — authoritative design spec (Living Document, spiegelt
  den real implementierten Stand).

## Dependencies & environment

- **Real deps live in `backend/requirements.txt`, NOT `pyproject.toml`.** The
  pyproject only declares build metadata + the `cbks` console script. Install
  with `pip install -r backend/requirements.txt`. faiss-cpu pulls PyTorch via the
  CPU extra-index.
- **TTS deps are separate and optional**: `backend/requirements-tts.txt` (it
  `-r`-includes the base file). Split out because the Kokoro fork depends on
  `misaki`, which has no Python 3.13 wheel — keeping it in the base file would
  make CBKS uninstallable on 3.13. Without it, only `GET /nodes/{id}/audio`
  fails (503 via `tts.TTSUnavailableError`); everything else works.
- Python 3.11–3.13 for the core, <3.13 if you want TTS. `.venv/` is the project
  venv (Python 3.12). Use `.venv/bin/python` / `.venv/bin/pytest`.
- Running server/CLI needs **Ollama on the host** (default
  `http://127.0.0.1:11434`) with models pulled: `qwen3:8b` (LLM),
  `qwen2.5vl:7b` (VLM), `bge-m3` (embedding, dim 1024). Docker backend uses
  `network_mode: host` precisely to reach host Ollama on 127.0.0.1.
- Config is env-driven (`CBKS_*` prefix, see `backend/config.py`). `CBKS_API_KEY`
  optionally gates the API (`require_api_key` on the FastAPI app; empty/unset =
  open). `CBKS_DATA_DIR` points at runtime data.

## Commands

Top-level (`Makefile`, mirrors what CI runs):
- `make setup` — venv + backend + frontend deps
- `make test` / `make lint` / `make build` — or `make check` for all three

Backend:
- Run API server: `uvicorn backend.main:app --host 127.0.0.1 --port 8000`
- Run CLI: `python -m backend.cli <command>` (or installed `cbks <command>`)
- Tests: `.venv/bin/pytest` (from repo root). Single test:
  `.venv/bin/pytest backend/tests/test_cli.py::test_stats_on_empty_db`
- **No linter/typecheck step configured for the backend.**

Frontend (run in `frontend/`):
- `npm run dev` (vite, proxies API paths to `127.0.0.1:8000`)
- `npm run build` → `tsc -b && vite build` (this is the typecheck gate)
- `npm run lint` → oxlint
- **No frontend test suite.**

Verify frontend + backend independently — there is no top-level build or test
script.

## Tests

- pytest, files in `backend/tests/`. No conftest.
- Tests **mock Ollama** (autouse fixtures monkeypatch
  `OllamaEmbeddingClient.embed` and `OllamaLLMClient.generate`) and isolate data
  via `CBKS_DATA_DIR=<tmp_path>`. They run **without a live Ollama**.
- Do not add real network/Ollama calls to the unit tests; follow the mock +
  tmp-dir fixture pattern (`test_api.py`, `test_cli.py` are the templates).
- `scripts/benchmark_models.py` is a manual benchmark (uses
  `tests/fixtures/benchmark_events.json`), not part of CI/tests.

## Gotchas

- **`pyproject.toml` has no `[project.dependencies]`** — easy to assume deps are
  there. They are not.
- **TTS needs system binaries `espeak-ng` and `ffmpeg`** (`backend/services/tts.py`)
  plus `backend/requirements-tts.txt`. The Docker image ships all of it; a bare
  `pip install -r backend/requirements.txt` does not. Raise
  `tts.TTSUnavailableError` (→ 503) rather than letting an ImportError escape.
- `build_context()` opens a new SQLite connection per call; do not refactor it to
  a naive global without reconsidering the sentiment singleton lock and
  per-request isolation.
- Backend binds `127.0.0.1` only (local-first by design); the Vite dev proxy
  assumes backend at `127.0.0.1:8000`.
- Commit style: conventional commits (`feat:`, `fix:`, `perf:`, `docs:`,
  `chore:`).
