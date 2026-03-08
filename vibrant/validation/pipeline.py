"""Validation pipeline placeholder."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ValidationPipeline(BaseModel):
    """Tracks validation commands queued for execution."""

    commands: list[str] = Field(default_factory=list)

