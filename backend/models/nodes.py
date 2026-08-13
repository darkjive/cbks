from typing import Literal, Optional

from pydantic import BaseModel, Field

NodeType = Literal[
    "concept", "document", "task", "note", "project", "commit", "screenshot", "person"
]

Hemisphere = Literal["left", "right", "auto"]


class Node(BaseModel):
    id: str
    title: str
    type: NodeType
    hemisphere: Hemisphere = "auto"
    content: Optional[str] = None
    content_hash: Optional[str] = None

    activation: float = 1.0
    confidence: float = 1.0
    emotional_weight: float = 0.0
    decay_rate: float = 0.001
    importance: float = 0.5

    creation_time: str
    last_access: str
    access_counter: int = 0

    metadata: dict = Field(default_factory=dict)
