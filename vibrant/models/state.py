"""Durable orchestrator runtime state models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProviderRuntimeState(BaseModel):
    status: str = "ready"
    provider_thread_id: str | None = None


class OrchestratorState(BaseModel):
    session_id: str
    started_at: datetime | None = None
    status: str = "running"
    active_agents: list[str] = Field(default_factory=list)
    gatekeeper_status: str = "idle"
    pending_questions: list[str] = Field(default_factory=list)
    last_consensus_version: int = 0
    concurrency_limit: int = 4
    provider_runtime: dict[str, ProviderRuntimeState] = Field(default_factory=dict)
    completed_tasks: list[str] = Field(default_factory=list)
    failed_tasks: list[str] = Field(default_factory=list)
    total_agent_spawns: int = 0

