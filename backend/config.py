from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    data_dir: Path
    database_path: Path
    faiss_index_path: Path
    ollama_host: str
    llm_model: str
    vlm_model: str
    embedding_model: str
    embedding_dim: int
    backup_script_path: Path
    api_key: Optional[str]
    vault_path: Optional[str]
    vault_dir: Optional[Path]

    @classmethod
    def from_env(cls) -> "Config":
        data_dir = Path(os.environ.get("CBKS_DATA_DIR", str(REPO_ROOT / "data")))
        return cls(
            data_dir=data_dir,
            database_path=Path(os.environ.get("CBKS_DATABASE_PATH", str(data_dir / "cbks.db"))),
            faiss_index_path=Path(
                os.environ.get("CBKS_FAISS_PATH", str(data_dir / "faiss_index" / "index.faiss"))
            ),
            ollama_host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
            llm_model=os.environ.get("CBKS_LLM_MODEL", "qwen3:8b"),
            vlm_model=os.environ.get("CBKS_VLM_MODEL", "qwen2.5vl:7b"),
            embedding_model=os.environ.get("CBKS_EMBEDDING_MODEL", "bge-m3"),
            embedding_dim=1024,
            backup_script_path=Path(
                os.environ.get("CBKS_BACKUP_SCRIPT", str(data_dir / "backup.sh"))
            ),
            api_key=os.environ.get("CBKS_API_KEY"),
            vault_path=os.environ.get("CBKS_VAULT_PATH"),
            vault_dir=(
                Path(os.environ["CBKS_VAULT_DIR"]) if os.environ.get("CBKS_VAULT_DIR") else None
            ),
        )
