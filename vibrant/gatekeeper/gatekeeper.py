"""Gatekeeper orchestration placeholder."""

from __future__ import annotations

from pydantic import BaseModel


class Gatekeeper(BaseModel):
    """Minimal gatekeeper model for Phase 0 scaffolding."""

    status: str = "idle"

