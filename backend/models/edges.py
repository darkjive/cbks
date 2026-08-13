from typing import Literal

from pydantic import BaseModel, Field

RelationType = Literal[
    "related_to", "depends_on", "extends", "contradicts", "supports",
    "mentions", "part_of", "requires", "alternative_to", "causes", "solves",
    "links_to",
]


class Edge(BaseModel):
    id: str
    source: str
    target: str
    relation_type: RelationType

    strength: float = 1.0
    temporal_score: float = 1.0
    emotional_score: float = 0.0
    reinforcement_count: int = 1

    creation_time: str
    last_updated: str

    metadata: dict = Field(default_factory=dict)
