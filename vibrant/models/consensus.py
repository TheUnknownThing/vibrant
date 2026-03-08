"""Consensus pool document models."""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConsensusStatus(str, enum.Enum):
    INIT = "INIT"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DecisionAuthor(str, enum.Enum):
    GATEKEEPER = "gatekeeper"
    USER = "user"


class ConsensusDecision(BaseModel):
    """Structured representation of one consensus decision entry."""

    model_config = ConfigDict(extra="forbid")

    title: str
    date: datetime | None = None
    made_by: DecisionAuthor = DecisionAuthor.GATEKEEPER
    context: str = ""
    resolution: str = ""
    impact: str = ""


class ConsensusDocument(BaseModel):
    """Parsed representation of ``consensus.md`` sections."""

    model_config = ConfigDict(extra="forbid")

    project: str = "Vibrant"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    version: int = 1
    status: ConsensusStatus = ConsensusStatus.PLANNING
    objectives: str = ""
    decisions: list[ConsensusDecision] = Field(default_factory=list)
    getting_started: str = ""

    @model_validator(mode="after")
    def validate_document(self) -> ConsensusDocument:
        if self.version < 0:
            raise ValueError("version must be >= 0")
        if self.created_at and self.updated_at and self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class ConsensusPool(ConsensusDocument):
    """Backward-compatible alias for early scaffolding code."""
