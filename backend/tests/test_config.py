import os
from pathlib import Path

from backend.config import Config


def test_from_env_defaults(monkeypatch):
    for var in (
        "CBKS_DATA_DIR", "CBKS_DATABASE_PATH", "CBKS_FAISS_PATH",
        "OLLAMA_HOST", "CBKS_LLM_MODEL", "CBKS_EMBEDDING_MODEL",
        "CBKS_BACKUP_SCRIPT",
    ):
        monkeypatch.delenv(var, raising=False)

    config = Config.from_env()

    assert config.data_dir.name == "data"
    assert config.database_path.name == "cbks.db"
    assert config.faiss_index_path.name == "index.faiss"
    assert config.ollama_host == "http://127.0.0.1:11434"
    assert config.llm_model == "qwen3:8b"
    assert config.embedding_model == "bge-m3"
    assert config.embedding_dim == 1024
    assert config.backup_script_path.name == "backup.sh"


def test_from_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("CBKS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OLLAMA_HOST", "http://example.invalid:11434")
    monkeypatch.setenv("CBKS_LLM_MODEL", "qwen3:14b")

    config = Config.from_env()

    assert config.data_dir == tmp_path
    assert config.database_path == tmp_path / "cbks.db"
    assert config.ollama_host == "http://example.invalid:11434"
    assert config.llm_model == "qwen3:14b"


def test_from_env_api_key_default_none(monkeypatch):
    monkeypatch.delenv("CBKS_API_KEY", raising=False)

    config = Config.from_env()

    assert config.api_key is None


def test_from_env_api_key_set(monkeypatch):
    monkeypatch.setenv("CBKS_API_KEY", "secret123")

    config = Config.from_env()

    assert config.api_key == "secret123"


def test_from_env_vault_path_default_none(monkeypatch):
    monkeypatch.delenv("CBKS_VAULT_PATH", raising=False)

    config = Config.from_env()

    assert config.vault_path is None


def test_from_env_vault_path_set(monkeypatch):
    monkeypatch.setenv("CBKS_VAULT_PATH", "/mnt/external/Vault")

    config = Config.from_env()

    assert config.vault_path == "/mnt/external/Vault"


def test_from_env_vault_dir_default_none(monkeypatch):
    monkeypatch.delenv("CBKS_VAULT_DIR", raising=False)

    config = Config.from_env()

    assert config.vault_dir is None


def test_from_env_vault_dir_set(monkeypatch, tmp_path):
    monkeypatch.setenv("CBKS_VAULT_DIR", str(tmp_path))

    config = Config.from_env()

    assert config.vault_dir == tmp_path
