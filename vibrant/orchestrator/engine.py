"""Orchestrator state machine skeleton for later phases."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OrchestratorEngine(BaseModel):
    """Placeholder orchestration engine established in Phase 0."""

    status: str = "idle"
    pending_tasks: list[str] = Field(default_factory=list)

