import secrets
from typing import Optional

from fastapi import Header, HTTPException, status

from backend.config import Config


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    config = Config.from_env()
    # Leerer String zählt als "nicht gesetzt" - sonst würde ein versehentlich
    # leer gesetzter CBKS_API_KEY einen ebenfalls leeren Header akzeptieren.
    if not config.api_key:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, config.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungültiger oder fehlender API-Key"
        )
