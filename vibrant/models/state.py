"""Durable orchestrator runtime state models."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OrchestratorStatus(str, enum.Enum):
    INIT = "init"
    PLANNING = "planning"
    EXECUTING = "executing"
    VALIDATING = "validating"
    PAUSED = "paused"
    COMPLETED = "completed"


class GatekeeperStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    AWAITING_USER = "awaiting_user"


class ProviderRuntimeState(BaseModel):
    """Provider runtime status used to resume or inspect active sessions."""

    model_config = ConfigDict(extra="forbid")

    status: str = "ready"
    provider_thread_id: str | None = None


class OrchestratorState(BaseModel):
    """Durable orchestrator state stored in ``.vibrant/state.json``."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: OrchestratorStatus = OrchestratorStatus.INIT
    active_agents: list[str] = Field(default_factory=list)
    gatekeeper_status: GatekeeperStatus = GatekeeperStatus.IDLE
    pending_questions: list[str] = Field(default_factory=list)
    last_consensus_version: int = 0
    concurrency_limit: int = 4
    provider_runtime: dict[str, ProviderRuntimeState] = Field(default_factory=dict)
    completed_tasks: list[str] = Field(default_factory=list)
    failed_tasks: list[str] = Field(default_factory=list)
    total_agent_spawns: int = 0

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value

        data = dict(value)

        if "provider_runtime" not in data:
            legacy_threads = data.pop("provider_threads", None)
            if isinstance(legacy_threads, list):
                data["provider_runtime"] = _legacy_provider_runtime(legacy_threads)
        else:
            data.pop("provider_threads", None)

        data.pop("pending_requests", None)
        return data

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> object:
        if isinstance(value, OrchestratorStatus):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized == "running":
                return OrchestratorStatus.EXECUTING.value
            return normalized
        return value

    @model_validator(mode="after")
    def validate_state(self) -> OrchestratorState:
        if self.last_consensus_version < 0:
            raise ValueError("last_consensus_version must be >= 0")
        if self.concurrency_limit < 1:
            raise ValueError("concurrency_limit must be >= 1")
        if self.total_agent_spawns < 0:
            raise ValueError("total_agent_spawns must be >= 0")
        return self


def _legacy_provider_runtime(items: list[object]) -> dict[str, ProviderRuntimeState]:
    runtime: dict[str, ProviderRuntimeState] = {}
    for item in items:
        if not isinstance(item, dict):
            continue

        owner_agent_id = item.get("owner_agent_id")
        if not isinstance(owner_agent_id, str) or not owner_agent_id:
            continue

        runtime[owner_agent_id] = ProviderRuntimeState(
            status=_legacy_runtime_status(item),
            provider_thread_id=_legacy_provider_thread_id(item),
        )
    return runtime


def _legacy_runtime_status(item: dict[str, Any]) -> str:
    value = item.get("runtime_state") or item.get("status") or "ready"
    return value if isinstance(value, str) and value else "ready"


def _legacy_provider_thread_id(item: dict[str, Any]) -> str | None:
    value = item.get("provider_thread_id")
    return value if isinstance(value, str) and value else None
