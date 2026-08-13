from typing import Optional

from pydantic import BaseModel


class Event(BaseModel):
    id: Optional[int] = None
    event_type: str
    content_hash: str
    payload: str
    source: str
    status: str = "pending"
    error: Optional[str] = None
    created_at: Optional[str] = None
    processed_at: Optional[str] = None
