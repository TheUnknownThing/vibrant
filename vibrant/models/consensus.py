"""Consensus pool document models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ConsensusDecision(BaseModel):
    title: str
    date: datetime | None = None
    made_by: str = "gatekeeper"
    context: str = ""
    resolution: str = ""
    impact: str = ""


class ConsensusPool(BaseModel):
    project: str = "Vibrant"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    version: int = 1
    status: str = "PLANNING"
    objectives: str = ""
    decisions: list[ConsensusDecision] = Field(default_factory=list)
    getting_started: str = ""

