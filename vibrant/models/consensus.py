"""Consensus pool document models."""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class ConsensusStatus(str, enum.Enum):
    INIT = "INIT"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


DEFAULT_CONSENSUS_CONTEXT = """## Objectives
Document the current goals, constraints, and open questions here.

## Getting Started
Start by reviewing `docs/spec.md`, `docs/tui.md`, `vibrant/orchestrator/STABLE_API.md`, and `.vibrant/roadmap.md`."""


class ConsensusDocument(BaseModel):
    """Parsed representation of ``consensus.md`` metadata and raw body."""

    model_config = ConfigDict(extra="forbid")

    project: str = "Vibrant"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    version: int = 1
    status: ConsensusStatus = ConsensusStatus.PLANNING
    context: str = DEFAULT_CONSENSUS_CONTEXT

    @model_validator(mode="after")
    def validate_document(self) -> ConsensusDocument:
        if self.version < 0:
            raise ValueError("version must be >= 0")
        if self.created_at and self.updated_at and self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self


class ConsensusPool(ConsensusDocument):
    """Backward-compatible alias for early scaffolding code."""
